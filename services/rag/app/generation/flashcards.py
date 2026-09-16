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
    LabelledBatch,
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
    top_up_calls: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "cards": len(self.cards),
            "batches_run": self.batches_run,
            "discarded": self.discarded,
            "failed_batches": self.failed_batches,
            "top_up_calls": self.top_up_calls,
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

        seen_fronts: set[str] = set()
        # Cards written per chunk, so a top-up goes to the passages the first
        # pass used least.
        used: dict[str, int] = {}

        chunks_left = len(sampled)
        for index, batch in enumerate(grouped):
            wanted = card_count - len(result.cards)
            if wanted <= 0:
                break

            # Shared out by chunk count. An even split asks a two-chunk tail
            # batch for as many cards as a full one, and the full one for too
            # few; the last batch is always asked for everything still owed.
            share = len(batch.by_label)
            ask_for = max(1, round(wanted * share / chunks_left))
            chunks_left -= share

            user = (
                f"Write {ask_for} flashcard(s) from the passages below. If the "
                f"passages cannot support {ask_for} distinct, well-grounded "
                "cards, write as many as they genuinely support.\n\n"
                f"Passages:\n\n{batch.text}"
            )
            await self._run_batch(
                batch, user, index, result, seen_fronts, used, card_count
            )

            if on_progress is not None:
                on_progress(len(result.cards), card_count)  # type: ignore[operator]

            await asyncio.sleep(0)

        # Models under-deliver: asked for four, they often write two. One more
        # call, over the passages used least, recovers most of the shortfall
        # for a bounded cost -- every call comes out of a small daily quota.
        shortfall = card_count - len(result.cards)
        if shortfall > 0:
            least_used = sorted(
                pool, key=lambda c: (used.get(c.id, 0), c.metadata.chunk_index)
            )[: self._batch_size]
            least_used.sort(key=lambda c: c.metadata.chunk_index)
            batch = batches(least_used, self._batch_size)[0]

            written = "\n".join(f"- {card.front}" for card in result.cards)
            existing = (
                "These cards already exist. Do not repeat or rephrase them:\n"
                f"{written}\n\n"
                if written
                else ""
            )
            user = (
                f"Write {shortfall} more flashcard(s) from the passages below. "
                f"If the passages cannot support {shortfall} new, distinct, "
                "well-grounded cards, write as many as they genuinely support.\n\n"
                f"{existing}Passages:\n\n{batch.text}"
            )
            result.top_up_calls += 1
            await self._run_batch(
                batch, user, len(grouped), result, seen_fronts, used, card_count
            )

            if on_progress is not None:
                on_progress(len(result.cards), card_count)  # type: ignore[operator]

        logger.info("generated flashcards: %s", result.as_dict())
        return result

    async def _run_batch(
        self,
        batch: LabelledBatch,
        user: str,
        index: int,
        result: FlashcardGenerationResult,
        seen_fronts: set[str],
        used: dict[str, int],
        card_count: int,
    ) -> None:
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
            return

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
            used[chunk.id] = used.get(chunk.id, 0) + 1
            result.cards.append(
                GeneratedCard(front=front, back=back, source_chunk_id=chunk.id)
            )
