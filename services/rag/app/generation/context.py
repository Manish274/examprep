"""Context construction.

Turns ranked chunks into the block of text an LLM sees, and into the Source
objects the frontend renders as citations. The two must be built together: a
marker in the prompt that has no matching Source is a citation the student
cannot click, and a Source with no marker is a chunk that never influenced the
answer.

Three jobs:

1. **Deduplicate.** Overlap between chunks means the same sentences can arrive
   twice. Spending a scarce token budget on a repeat is bad; showing the
   student the same citation twice is worse.
2. **Budget.** A model given 50k tokens of context attends worse to the part
   that matters than one given 5k. Chunks are admitted whole, in rank order,
   until the budget is spent.
3. **Label.** Each admitted chunk gets a marker the model is instructed to cite
   and a header naming its document, page and heading, so the model can attach
   a claim to a specific source rather than to the context as a whole.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.models import ContentSource, ScoredChunk, Source
from app.core.tokenizer import TokenCounter, get_token_counter

logger = logging.getLogger(__name__)

# Jaccard overlap above which two chunks are treated as near-duplicates. High
# enough that two passages on the same topic still both survive.
_DUPLICATE_THRESHOLD = 0.85

_MARKER = re.compile(r"\[S(\d+)\]")

_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class BuiltContext:
    """Everything downstream generation needs, and nothing more."""

    text: str
    sources: list[Source] = field(default_factory=list)
    token_count: int = 0
    # Chunks that were retrieved but did not fit the budget or were dropped as
    # duplicates. Recorded so a thin answer can be explained.
    dropped: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.sources


def _shingles(text: str, size: int = 5) -> set[str]:
    """Word n-grams, for near-duplicate detection.

    Compares phrasing rather than vocabulary: two different passages about
    normalization share many words but few five-word sequences.
    """
    words = _WORD.findall(text.lower())
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    return intersection / min(len(a), len(b))


def _snippet(text: str, limit: int = 320) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    # Cut at a word boundary so the citation preview does not end mid-word.
    return collapsed[:limit].rsplit(" ", 1)[0] + "…"


def _location(source_meta: ScoredChunk) -> str:
    meta = source_meta.chunk.metadata
    parts = [meta.document_name]
    if meta.page_number is not None:
        parts.append(f"p.{meta.page_number}")
    elif meta.slide_number is not None:
        parts.append(f"slide {meta.slide_number}")
    if meta.heading_path:
        parts.append(" › ".join(meta.heading_path))
    if meta.source is ContentSource.VISION:
        # Says plainly that this text was read out of a picture rather than
        # lifted from the file, both in the prompt and in the citation the
        # student sees.
        parts.append("read from an image")
    return " · ".join(parts)


class ContextBuilder:
    def __init__(
        self,
        *,
        max_tokens: int = 5000,
        max_chunks: int = 8,
        counter: TokenCounter | None = None,
        duplicate_threshold: float = _DUPLICATE_THRESHOLD,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_chunks = max_chunks
        self.duplicate_threshold = duplicate_threshold
        self._counter = counter or get_token_counter()

    def build(self, chunks: Sequence[ScoredChunk]) -> BuiltContext:
        admitted: list[ScoredChunk] = []
        seen: list[set[str]] = []
        used_tokens = 0
        dropped = 0

        for scored in chunks:
            if len(admitted) >= self.max_chunks:
                dropped += 1
                continue

            text = scored.chunk.text.strip()
            if not text:
                dropped += 1
                continue

            fingerprint = _shingles(text)
            if any(
                _overlap(fingerprint, other) >= self.duplicate_threshold
                for other in seen
            ):
                dropped += 1
                continue

            # Measured on the rendered block, not the raw chunk: the header
            # costs tokens too, and ignoring it overruns the budget.
            rendered = self._render(len(admitted) + 1, scored)
            cost = self._counter.count(rendered)

            if used_tokens + cost > self.max_tokens:
                # Keep going rather than stopping: a later, shorter chunk may
                # still fit, and rank order is preserved among those admitted.
                dropped += 1
                continue

            admitted.append(scored)
            seen.append(fingerprint)
            used_tokens += cost

        blocks = [self._render(i + 1, s) for i, s in enumerate(admitted)]
        sources = [self._source(i + 1, s) for i, s in enumerate(admitted)]

        if dropped:
            logger.debug(
                "context: admitted %s chunks, dropped %s", len(admitted), dropped
            )

        return BuiltContext(
            text="\n\n".join(blocks),
            sources=sources,
            token_count=used_tokens,
            dropped=dropped,
        )

    def _render(self, marker: int, scored: ScoredChunk) -> str:
        return f"[S{marker}] {_location(scored)}\n{scored.chunk.text.strip()}"

    def _source(self, marker: int, scored: ScoredChunk) -> Source:
        meta = scored.chunk.metadata
        return Source(
            marker=f"S{marker}",
            chunk_id=scored.chunk.id,
            document_id=meta.document_id,
            document_name=meta.document_name,
            page_number=meta.page_number,
            slide_number=meta.slide_number,
            heading_path=list(meta.heading_path),
            snippet=_snippet(scored.chunk.text),
        )


def cited_markers(answer: str) -> set[str]:
    """Markers the model actually used, e.g. {"S1", "S3"}."""
    return {f"S{n}" for n in _MARKER.findall(answer)}


def verify_citations(
    answer: str, sources: Sequence[Source]
) -> tuple[list[Source], set[str]]:
    """Splits citations into those that resolve and those that do not.

    A model can emit [S9] when only three sources exist. Rendering that as a
    citation would show the student a source that was never in the context --
    exactly the kind of quiet fabrication this system exists to avoid.

    Returns the sources actually cited, and the set of unresolvable markers.
    """
    available = {source.marker: source for source in sources}
    used = cited_markers(answer)

    ordered = sorted(used, key=lambda marker: int(marker[1:]))
    resolved = [available[m] for m in ordered if m in available]
    dangling = used - available.keys()
    if dangling:
        logger.warning("answer cited unknown sources: %s", sorted(dangling))
    return resolved, dangling
