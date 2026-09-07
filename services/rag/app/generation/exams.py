"""Test generation.

Writing a good multiple-choice question is harder than writing a good answer,
and the difficulty is entirely in the distractors. Options that are obviously
wrong teach nothing; options that are arguably right make the question unfair.
Most of the prompt here is about that.

Every question carries the chunk it came from. A question whose answer is not
in the student's material is worse than no question -- they revise the wrong
thing and discover it in the exam -- so anything that cannot be traced back to
a supplied chunk is discarded rather than shown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.models import Chunk, QuestionType
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

_SYSTEM = """You write exam questions from a student's own study material.

Absolute rules:
- Every question must be answerable from the passages provided, and from
  nothing else. Never use outside knowledge.
- Give the label of the passage each question came from, exactly as shown
  (e.g. "C2"). If a question draws on two passages, name the one carrying the
  answer.
- The correct answer must be stated or directly implied by that passage.
- The explanation must say why the answer is right, in the terms the passage
  uses.
- Never mention the passage labels in the question or the explanation. "C2" is
  internal bookkeeping; a student reading "Passage C2 states..." is being shown
  machinery that means nothing to them. Write "the material states..." or name
  the topic instead.

Writing multiple choice:
- Exactly four options. One unambiguously correct.
- Distractors must be plausible to someone who has not learned the material:
  a neighbouring concept from the passages, a common confusion, a partially
  correct statement. Never filler, never obviously absurd, never "all of the
  above".
- Do not make the correct option the longest or the most detailed. That is the
  single most common tell in generated questions.

Writing short answer:
- Ask for something with a definite answer of one or two sentences.
- The model answer is what a full-mark response would say.

Writing true/false:
- The statement must be decidable from the passage, not a matter of judgement.

If a passage carries nothing worth examining -- a title, a contents list, an
aside -- write no question for it rather than a weak one."""

_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "type": {"type": "STRING", "enum": ["mcq", "short_answer", "true_false"]},
            "prompt": {"type": "STRING"},
            "options": {"type": "ARRAY", "items": {"type": "STRING"}},
            "correct_answer": {"type": "STRING"},
            "explanation": {"type": "STRING"},
            "source": {"type": "STRING"},
        },
        "required": ["type", "prompt", "correct_answer", "explanation", "source"],
    },
}


@dataclass
class GeneratedQuestion:
    type: QuestionType
    prompt: str
    correct_answer: str
    explanation: str
    source_chunk_id: str
    options: list[str] | None = None


@dataclass
class ExamGenerationResult:
    questions: list[GeneratedQuestion] = field(default_factory=list)
    batches_run: int = 0
    discarded: int = 0
    failed_batches: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "questions": len(self.questions),
            "batches_run": self.batches_run,
            "discarded": self.discarded,
            "failed_batches": self.failed_batches,
        }


def _normalise_mcq(item: dict) -> tuple[list[str], str] | None:
    """Validates an MCQ and returns its options and the correct index.

    The correct answer is stored as an index rather than the option text, so a
    later edit to the wording cannot silently orphan it. A question whose
    stated answer is not among its options is unmarkable and is dropped.
    """
    options = [str(o).strip() for o in (item.get("options") or []) if str(o).strip()]
    if len(options) < 2:
        return None

    answer = str(item.get("correct_answer", "")).strip()

    # Models answer with the text, the letter, or the index. Accept all three.
    if answer in options:
        return options, str(options.index(answer))

    if len(answer) == 1 and answer.upper().isalpha():
        index = ord(answer.upper()) - ord("A")
        if 0 <= index < len(options):
            return options, str(index)

    if answer.isdigit() and 0 <= int(answer) < len(options):
        return options, answer

    # A near-miss on whitespace or punctuation is common and recoverable.
    simplified = [o.lower().rstrip(".") for o in options]
    if answer.lower().rstrip(".") in simplified:
        return options, str(simplified.index(answer.lower().rstrip(".")))

    return None


def _to_question(item: dict, chunk: Chunk) -> GeneratedQuestion | None:
    prompt = str(item.get("prompt", "")).strip()
    explanation = str(item.get("explanation", "")).strip()
    if not prompt or not explanation:
        return None

    raw_type = str(item.get("type", "")).strip().lower()
    if raw_type not in {t.value for t in QuestionType}:
        return None
    question_type = QuestionType(raw_type)

    if question_type is QuestionType.MCQ:
        normalised = _normalise_mcq(item)
        if normalised is None:
            return None
        options, correct = normalised
        return GeneratedQuestion(
            type=question_type,
            prompt=prompt,
            options=options,
            correct_answer=correct,
            explanation=explanation,
            source_chunk_id=chunk.id,
        )

    answer = str(item.get("correct_answer", "")).strip()
    if not answer:
        return None

    if question_type is QuestionType.TRUE_FALSE:
        lowered = answer.lower()
        if lowered not in {"true", "false"}:
            return None
        answer = lowered

    return GeneratedQuestion(
        type=question_type,
        prompt=prompt,
        correct_answer=answer,
        explanation=explanation,
        source_chunk_id=chunk.id,
    )


class ExamGenerator:
    def __init__(self, llm: object, *, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self._llm = llm
        self._batch_size = batch_size

    async def generate(
        self,
        chunks: Sequence[Chunk],
        *,
        question_count: int = 10,
        types: Sequence[QuestionType] | None = None,
        difficulty: str = "mixed",
        on_progress: object | None = None,
    ) -> ExamGenerationResult:
        wanted = list(types or [QuestionType.MCQ, QuestionType.SHORT_ANSWER])
        result = ExamGenerationResult()

        pool = usable_chunks(chunks)
        if not pool:
            return result

        # Sample roughly two chunks per question so the model has material to
        # choose from and can skip passages carrying nothing examinable.
        sampled = spread(pool, min(question_count * 2, len(pool)))
        grouped = batches(sampled, self._batch_size)
        per_batch = max(1, -(-question_count // max(len(grouped), 1)))

        for index, batch in enumerate(grouped):
            if len(result.questions) >= question_count:
                break

            remaining = question_count - len(result.questions)
            ask_for = min(per_batch, remaining)

            user = (
                f"Write {ask_for} exam question(s) from the passages below.\n"
                f"Question types to use: {', '.join(t.value for t in wanted)}.\n"
                f"Difficulty: {difficulty}.\n\n"
                f"Passages:\n\n{batch.text}"
            )

            try:
                response = await self._llm.complete(  # type: ignore[attr-defined]
                    messages(_SYSTEM, user),
                    temperature=0.6,
                    max_tokens=8000,
                    json_schema=_SCHEMA,
                )
                items = parse_json_items(response.text)
            except Exception as exc:
                # One bad batch must not abandon the rest of the test.
                logger.warning("test batch %s failed: %s", index, exc)
                result.failed_batches += 1
                continue

            result.batches_run += 1

            for item in items:
                if len(result.questions) >= question_count:
                    break

                chunk = resolve_source(item, batch)
                if chunk is None:
                    # Cannot be traced to the material the student uploaded.
                    result.discarded += 1
                    continue

                question = _to_question(item, chunk)
                if question is None:
                    result.discarded += 1
                    continue
                result.questions.append(question)

            if on_progress is not None:
                on_progress(len(result.questions), question_count)  # type: ignore[operator]

            await asyncio.sleep(0)

        logger.info("generated test: %s", result.as_dict())
        return result
