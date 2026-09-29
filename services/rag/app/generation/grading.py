"""Grading.

Multiple choice and true/false are graded by comparison -- no model, no cost,
no chance of a wrong verdict. Only short answers need judgement, and those are
graded in one batched call rather than one per answer.

Two things matter more than the score itself:

**The feedback has to be grounded.** Telling a student they are wrong is only
useful alongside what the material actually says, so the source passage is
supplied with each answer and the explanation must come from it.

**Partial credit has to be real.** A student who gets the idea but omits a
condition has not failed the question. Grading to a 0-1 scale rather than
right/wrong reflects that, and the threshold for "correct" is stated rather
than implied.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.interfaces import LLMProvider
from app.core.models import QuestionType
from app.generation.study import messages, parse_json_items

logger = logging.getLogger(__name__)

# Awarded credit at or above which an answer counts as correct. Below it the
# answer is marked wrong but keeps its partial credit in the score.
CORRECT_THRESHOLD = 0.7

_SYSTEM = """You mark a student's short answers against their own study material.

For each answer, judge whether it says what the model answer says. Grade the
meaning, not the wording: different phrasing, different order and different
level of detail are all fine if the substance is right.

Award between 0 and 1:
  1.0  says everything the model answer says
  0.7  right idea, a minor omission or imprecision
  0.4  partly right, or right but missing a key condition
  0.0  wrong, or says nothing relevant

Write feedback that a student can learn from:
- If they are right, say briefly what made it right.
- If they are wrong or partial, say what is missing or mistaken, using what the
  source passage actually says.
- Never introduce facts that are not in the source passage.
- Address the student directly. Two or three sentences at most.
- Never refer to "the passage" by a label such as C2. Those labels are internal
  and mean nothing to a student; say "your notes" or name the topic.

An empty or nonsense answer scores 0 with feedback saying what was wanted."""

_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "id": {"type": "STRING"},
            "awarded": {"type": "NUMBER"},
            "feedback": {"type": "STRING"},
        },
        "required": ["id", "awarded", "feedback"],
    },
}


@dataclass
class GradableAnswer:
    question_id: str
    question_type: QuestionType
    prompt: str
    correct_answer: str
    explanation: str
    response: str | None
    source_text: str = ""
    options: list[str] | None = None


@dataclass
class GradedAnswer:
    question_id: str
    is_correct: bool
    awarded: float
    feedback: str


def grade_objective(answer: GradableAnswer) -> GradedAnswer:
    """Grades a question with one defensible answer, without a model."""
    response = (answer.response or "").strip()

    if answer.question_type is QuestionType.MCQ:
        # Stored as an index; accept the index, the option text or the letter,
        # since the client may send any of them.
        correct_index = answer.correct_answer.strip()
        options = answer.options or []
        chosen = response

        if response and not response.isdigit():
            if response in options:
                chosen = str(options.index(response))
            elif len(response) == 1 and response.upper().isalpha():
                chosen = str(ord(response.upper()) - ord("A"))

        is_correct = chosen == correct_index
    else:
        is_correct = response.strip().lower() == answer.correct_answer.strip().lower()

    return GradedAnswer(
        question_id=answer.question_id,
        is_correct=is_correct,
        awarded=1.0 if is_correct else 0.0,
        # The explanation is shown either way: a student who guessed correctly
        # still needs to know why it was right.
        feedback=answer.explanation,
    )


class Grader:
    def __init__(self, llm: LLMProvider, *, batch_size: int = 8) -> None:
        self._llm = llm
        self._batch_size = batch_size

    async def grade(self, answers: Sequence[GradableAnswer]) -> list[GradedAnswer]:
        objective = [
            a for a in answers if a.question_type is not QuestionType.SHORT_ANSWER
        ]
        written = [a for a in answers if a.question_type is QuestionType.SHORT_ANSWER]

        graded: dict[str, GradedAnswer] = {
            a.question_id: grade_objective(a) for a in objective
        }

        for start in range(0, len(written), self._batch_size):
            window = written[start : start + self._batch_size]
            graded.update(await self._grade_written(window))

        # Returned in the order asked, so a caller can zip it against its own
        # question list without matching on id.
        return [graded[a.question_id] for a in answers if a.question_id in graded]

    async def _grade_written(
        self, answers: Sequence[GradableAnswer]
    ) -> dict[str, GradedAnswer]:
        blocks: list[str] = []
        for answer in answers:
            student = (answer.response or "").strip() or "(no answer given)"
            blocks.append(
                f"---\nid: {answer.question_id}\n"
                f"Question: {answer.prompt}\n"
                f"Model answer: {answer.correct_answer}\n"
                f"Source passage: {answer.source_text[:1500]}\n"
                f"Student answer: {student}"
            )

        user = "Mark each answer below.\n\n" + "\n".join(blocks)

        try:
            response = await self._llm.complete(
                messages(_SYSTEM, user),
                temperature=0.0,
                max_tokens=6000,
                json_schema=_SCHEMA,
            )
            items = parse_json_items(response.text)
        except Exception as exc:
            logger.warning("short-answer grading failed: %s", exc)
            # A student must never lose marks because the grader was
            # unavailable. Withholding the verdict is the honest outcome.
            return {
                a.question_id: GradedAnswer(
                    question_id=a.question_id,
                    is_correct=False,
                    awarded=0.0,
                    feedback=(
                        "This answer could not be marked automatically. "
                        f"A full-mark answer would say: {a.correct_answer}"
                    ),
                )
                for a in answers
            }

        by_id = {a.question_id: a for a in answers}
        graded: dict[str, GradedAnswer] = {}

        for item in items:
            question_id = str(item.get("id", "")).strip()
            asked = by_id.get(question_id)
            if asked is None:
                continue

            try:
                awarded = float(item.get("awarded", 0.0))
            except (TypeError, ValueError):
                awarded = 0.0
            awarded = max(0.0, min(1.0, awarded))

            graded[question_id] = GradedAnswer(
                question_id=question_id,
                is_correct=awarded >= CORRECT_THRESHOLD,
                awarded=awarded,
                feedback=str(item.get("feedback", "")).strip() or asked.explanation,
            )

        # Anything the model skipped still needs a verdict.
        for question_id, answer in by_id.items():
            if question_id not in graded:
                graded[question_id] = GradedAnswer(
                    question_id=question_id,
                    is_correct=False,
                    awarded=0.0,
                    feedback=(
                        "This answer was not marked. A full-mark answer would "
                        f"say: {answer.correct_answer}"
                    ),
                )

        return graded
