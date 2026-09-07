"""Flashcard generation.

A flashcard is not a summary. It is one question with one answer, small enough
to recall in a few seconds, and useless if it tries to carry a paragraph. Most
of the prompt is about keeping cards small and atomic, because a model asked
for flashcards will otherwise produce section summaries with a question mark on
the front.

Every card names the chunk it came from, for the same reason questions do: a
card whose answer is not in the student's material teaches them the wrong
thing, and traceability is what makes that checkable.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.models import Chunk
from app.generation.study import (
    DEFAULT_BATCH_SIZE,
    batches,
    messages,
    parse_json_items,
    resolve_source,
    spread,
    usable_chunks,
)

logger = logging.getLogger(__name__)

_SYSTEM = """You write revision flashcards from a student's own study material.

Absolute rules:
- Every card must be answerable from the passages provided and nothing else.
- Give the label of the passage each card came from, exactly as shown (e.g.
  "C2").

What makes a good card:
- One idea per card. If the back needs "and" to join two facts, split it.
- The front is a question or a term. The back is the answer, in one or two
  short sentences.
- Use the passage's own terminology and any exact figures or formulas it gives.
- Prefer cards that test understanding over cards that test trivia: "why does
  a hash index not support range queries" beats "what year was X published".
- Never write a card whose front gives away its own back.
- Never mention the passage labels. "C2" is internal bookkeeping and means
  nothing to a student reading the card.

Skip any passage that is a title, a contents list, or an aside. Fewer good
cards beat more weak ones."""

_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "front": {"type": "STRING"},
            "back": {"type": "STRING"},
            "source": {"type": "STRING"},
        },
        "required": ["front", "back", "source"],
    },
}

# A back longer than this has stopped being a flashcard and become a summary.
MAX_BACK_CHARS = 400


@dataclass
class GeneratedCard:
    front: str
    back: str
    source_chunk_id: str


@dataclass
class FlashcardGenerationResult:
    cards: list[GeneratedCard] = field(default_factory=list)
    batches_run: int = 0
    discarded: int = 0
    failed_batches: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "cards": len(self.cards),
            "batches_run": self.batches_run,
            "discarded": self.discarded,
            "failed_batches": self.failed_batches,
        }


class FlashcardGenerator:
    def __init__(self, llm: object, *, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self._llm = llm
        self._batch_size = batch_size

    async def generate(
        self,
        chunks: Sequence[Chunk],
        *,
        card_count: int = 20,
        on_progress: object | None = None,
    ) -> FlashcardGenerationResult:
        result = FlashcardGenerationResult()

        pool = usable_chunks(chunks)
        if not pool:
            return result

        sampled = spread(pool, min(card_count, len(pool)))
        grouped = batches(sampled, self._batch_size)
        per_batch = max(1, -(-card_count // max(len(grouped), 1)))

        seen_fronts: set[str] = set()

        for index, batch in enumerate(grouped):
            if len(result.cards) >= card_count:
                break

            ask_for = min(per_batch, card_count - len(result.cards))
            user = (
                f"Write up to {ask_for} flashcard(s) from the passages below.\n\n"
                f"Passages:\n\n{batch.text}"
            )

            try:
                response = await self._llm.complete(  # type: ignore[attr-defined]
                    messages(_SYSTEM, user),
                    temperature=0.5,
                    max_tokens=8000,
                    json_schema=_SCHEMA,
                )
                items = parse_json_items(response.text)
            except Exception as exc:
                # One bad batch must not abandon the rest of the set.
                logger.warning("flashcard batch %s failed: %s", index, exc)
                result.failed_batches += 1
                continue

            result.batches_run += 1

            for item in items:
                if len(result.cards) >= card_count:
                    break

                front = str(item.get("front", "")).strip()
                back = str(item.get("back", "")).strip()
                if not front or not back:
                    result.discarded += 1
                    continue

                # A card that has become a summary is worse than no card: it
                # cannot be recalled in the few seconds a card is for.
                if len(back) > MAX_BACK_CHARS:
                    result.discarded += 1
                    continue

                key = front.lower().rstrip("?. ")
                if key in seen_fronts:
                    # Batches overlap in subject matter; duplicates waste the
                    # student's revision time.
                    result.discarded += 1
                    continue

                chunk = resolve_source(item, batch)
                if chunk is None:
                    result.discarded += 1
                    continue

                seen_fronts.add(key)
                result.cards.append(
                    GeneratedCard(front=front, back=back, source_chunk_id=chunk.id)
                )

            if on_progress is not None:
                on_progress(len(result.cards), card_count)  # type: ignore[operator]

            await asyncio.sleep(0)

        logger.info("generated flashcards: %s", result.as_dict())
        return result
