"""Shared machinery for generating study material.

Tests and flashcards differ in what they produce and agree on everything else:
both sample chunks from the corpus, hand a batch to the model with each chunk
labelled, and require every generated item to name the chunk it came from.

That last requirement is the whole design. A question whose answer is not in
the source material is worse than no question at all -- a student revises the
wrong thing and finds out in the exam. Tagging each item with a chunk id makes
the claim checkable, and anything referring to a chunk that was not in the
batch is discarded rather than shown.

Batching several chunks per call rather than one is not only cheaper. It lets
the model see related passages together, so it can write a question spanning
two of them and say which one it came from.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.models import Chunk, LLMMessage

logger = logging.getLogger(__name__)

# Chunks per generation call. Large enough for the model to see related
# material, small enough that it can keep the chunk labels straight.
DEFAULT_BATCH_SIZE = 6

# Chunks below this are headings, stubs or fragments -- nothing worth asking a
# question about, and a source that would make a poor citation.
MIN_CHUNK_TOKENS = 25

# Except on a titled slide. A slide is its own section, so a terse list slide
# ("Hardware components: Arduino Uno, sound sensor, LCD, buzzer") stays short
# however it is chunked -- and it is often the most examinable thing in the
# deck. In a PDF the chunker merges paragraphs, so a short chunk really is a
# fragment, and the general minimum still applies there.
MIN_TITLED_SLIDE_TOKENS = 4

# Sections about the document rather than its subject. A question on who wrote
# a cited paper, or on the order of an agenda, tests nothing a student is
# examined on -- and a long reference list is exactly the chunk an even sample
# is most likely to land on.
_NOT_STUDY_MATERIAL = re.compile(
    r"(references?|bibliography|works cited|citations?|further reading"
    r"|acknowledge?ments?|(table of )?contents|agenda|outline"
    r"|thank you|thanks|any questions)"
)

# Title-slide credits. Short, titled, on a slide -- and not study material.
_CREDIT_LINE = re.compile(
    r"^(submitted|presented|prepared|guided|made|done|created)\s+(by|to|under)\b"
    r"|^under the guidance of\b",
    re.IGNORECASE,
)

_PLACEHOLDER_HEADING = re.compile(r"slide \d+")

_JSON_BLOCK = re.compile(r"\[.*\]|\{.*\}", re.DOTALL)


@dataclass
class LabelledBatch:
    """A batch of chunks with the labels the model will cite."""

    text: str
    by_label: dict[str, Chunk]


def _normalise_heading(heading: str) -> str:
    """"7. References:" -> "references"."""
    text = heading.strip().lower()
    text = re.sub(r"^\d+(\.\d+)*\.?\s+", "", text)
    return re.sub(r"[\s:.!?\-]+$", "", text)


def _headings(chunk: Chunk) -> list[str]:
    meta = chunk.metadata
    found = [h for h in meta.heading_path if h]
    if meta.heading:
        found.append(meta.heading)
    return [_normalise_heading(h) for h in found]


def is_study_material(chunk: Chunk) -> bool:
    """Whether a chunk belongs to the subject rather than the document's
    scaffolding (references, contents, agenda, acknowledgements)."""
    return not any(_NOT_STUDY_MATERIAL.fullmatch(h) for h in _headings(chunk))


def _is_titled_slide(chunk: Chunk) -> bool:
    heading = _normalise_heading(chunk.metadata.heading or "")
    return (
        chunk.metadata.slide_number is not None
        and bool(heading)
        and not _PLACEHOLDER_HEADING.fullmatch(heading)
    )


def usable_chunks(
    chunks: Sequence[Chunk], *, min_tokens: int = MIN_CHUNK_TOKENS
) -> list[Chunk]:
    """The chunks worth writing questions and cards from."""
    usable: list[Chunk] = []
    for chunk in chunks:
        text = chunk.text.strip()
        if not text or not is_study_material(chunk):
            continue

        titled_list_slide = (
            _is_titled_slide(chunk)
            and chunk.token_count >= MIN_TITLED_SLIDE_TOKENS
            and not _CREDIT_LINE.search(text)
        )
        if chunk.token_count >= min_tokens or titled_list_slide:
            usable.append(chunk)
    return usable


def spread(chunks: Sequence[Chunk], limit: int) -> list[Chunk]:
    """Takes an evenly spaced sample across the document.

    Taking the first N would build an entire test out of the opening pages.
    Even spacing gives coverage of the whole document, which is what a student
    revising for an exam on it actually needs.
    """
    ordered = sorted(chunks, key=lambda c: c.metadata.chunk_index)
    if limit >= len(ordered):
        return ordered

    step = len(ordered) / limit
    return [ordered[min(int(i * step), len(ordered) - 1)] for i in range(limit)]


def batches(
    chunks: Sequence[Chunk], size: int = DEFAULT_BATCH_SIZE
) -> list[LabelledBatch]:
    """Splits chunks into labelled batches, one per generation call."""
    result: list[LabelledBatch] = []

    for start in range(0, len(chunks), size):
        window = chunks[start : start + size]
        by_label: dict[str, Chunk] = {}
        parts: list[str] = []

        for offset, chunk in enumerate(window, start=1):
            label = f"C{offset}"
            by_label[label] = chunk
            location = chunk.metadata.heading or chunk.metadata.section or ""
            parts.append(f"[{label}] {location}\n{chunk.text.strip()}")

        result.append(LabelledBatch(text="\n\n".join(parts), by_label=by_label))

    return result


def parse_json_items(raw: str) -> list[dict]:
    """Extracts a JSON array from a model response.

    Structured output usually returns clean JSON, but a fenced block or a line
    of preamble still slips through often enough that failing on it would lose
    a whole batch of questions to a formatting quirk.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if match is None:
            raise ValueError(f"no JSON found in response: {raw[:200]}") from None
        parsed = json.loads(match.group(0))

    if isinstance(parsed, dict):
        # Models sometimes wrap the array in an object despite the schema.
        for value in parsed.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [parsed]

    if not isinstance(parsed, list):
        raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")
    return [item for item in parsed if isinstance(item, dict)]


def resolve_source(item: dict, batch: LabelledBatch) -> Chunk | None:
    """Maps an item's claimed source label back to a real chunk.

    Returns None when the model names a label that was not in the batch, which
    means the item cannot be traced to the material and must be discarded.
    """
    label = str(item.get("source") or item.get("source_label") or "").strip()
    label = label.strip("[]").upper()
    return batch.by_label.get(label)


def messages(system: str, user: str) -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content=system),
        LLMMessage(role="user", content=user),
    ]
