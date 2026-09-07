from __future__ import annotations

import json
from typing import ClassVar

import pytest

from app.core.models import Chunk, ChunkMetadata, LLMResponse, QuestionType
from app.generation.exams import ExamGenerator, _normalise_mcq
from app.generation.flashcards import MAX_BACK_CHARS, FlashcardGenerator
from app.generation.grading import (
    CORRECT_THRESHOLD,
    GradableAnswer,
    Grader,
    grade_objective,
)
from app.generation.llm import MockLLMProvider
from app.generation.study import (
    batches,
    parse_json_items,
    resolve_source,
    spread,
    usable_chunks,
)


def _chunk(index: int, text: str = "", tokens: int = 60) -> Chunk:
    return Chunk(
        id=f"chunk-{index}",
        text=text or f"Passage {index} about a topic worth examining.",
        token_count=tokens,
        metadata=ChunkMetadata(
            document_id="doc-1",
            document_name="notes.pdf",
            chunk_index=index,
            heading=f"Section {index}",
            content_hash="h" * 64,
        ),
    )


class ScriptedLLM(MockLLMProvider):
    """Returns canned JSON, so generation logic is tested without a model."""

    def __init__(self, payloads: list[object]) -> None:
        super().__init__()
        self.payloads = payloads
        self.index = 0

    async def complete(self, messages, **kwargs):
        payload = self.payloads[min(self.index, len(self.payloads) - 1)]
        self.index += 1
        if isinstance(payload, Exception):
            raise payload
        return LLMResponse(text=json.dumps(payload), model_id="scripted")


class TestSampling:
    def test_drops_fragments_too_small_to_examine(self) -> None:
        chunks = [_chunk(0, tokens=5), _chunk(1, tokens=60)]
        assert [c.id for c in usable_chunks(chunks)] == ["chunk-1"]

    def test_spreads_across_the_whole_document(self) -> None:
        # Taking the first N would build the entire test out of the opening
        # pages and ignore everything a student is also examined on.
        sampled = spread([_chunk(i) for i in range(100)], 5)
        indexes = [c.metadata.chunk_index for c in sampled]

        assert len(indexes) == 5
        assert indexes[0] < 20
        assert indexes[-1] > 70

    def test_returns_everything_when_asked_for_more_than_exists(self) -> None:
        assert len(spread([_chunk(i) for i in range(3)], 10)) == 3

    def test_labels_are_unique_within_a_batch(self) -> None:
        [batch] = batches([_chunk(i) for i in range(4)], size=6)
        assert set(batch.by_label) == {"C1", "C2", "C3", "C4"}
        assert "[C1]" in batch.text

    def test_splits_into_several_batches(self) -> None:
        assert len(batches([_chunk(i) for i in range(13)], size=6)) == 3


class TestSourceResolution:
    def test_maps_a_label_back_to_its_chunk(self) -> None:
        [batch] = batches([_chunk(0), _chunk(1)], size=6)
        assert resolve_source({"source": "C2"}, batch) is batch.by_label["C2"]

    def test_tolerates_brackets_and_case(self) -> None:
        [batch] = batches([_chunk(0)], size=6)
        assert resolve_source({"source": "[c1]"}, batch) is not None

    def test_an_unknown_label_resolves_to_nothing(self) -> None:
        # The item cannot be traced to the student's material, so it must not
        # be shown.
        [batch] = batches([_chunk(0)], size=6)
        assert resolve_source({"source": "C9"}, batch) is None


class TestJsonParsing:
    def test_parses_a_plain_array(self) -> None:
        assert parse_json_items('[{"a": 1}]') == [{"a": 1}]

    def test_strips_a_fenced_block(self) -> None:
        assert parse_json_items('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_finds_an_array_amid_prose(self) -> None:
        # Losing a whole batch of questions to a line of preamble would be a
        # poor trade.
        assert parse_json_items('Here you go:\n[{"a": 1}]\nHope that helps') == [
            {"a": 1}
        ]

    def test_unwraps_an_object_containing_the_array(self) -> None:
        assert parse_json_items('{"questions": [{"a": 1}]}') == [{"a": 1}]

    def test_rejects_a_response_with_no_json(self) -> None:
        with pytest.raises(ValueError, match="no JSON"):
            parse_json_items("I could not do that.")


class TestMcqNormalisation:
    OPTIONS: ClassVar[list[str]] = ["Alpha", "Beta", "Gamma", "Delta"]

    def test_accepts_the_answer_as_text(self) -> None:
        assert _normalise_mcq(
            {"options": self.OPTIONS, "correct_answer": "Gamma"}
        ) == (self.OPTIONS, "2")

    def test_accepts_the_answer_as_a_letter(self) -> None:
        assert _normalise_mcq(
            {"options": self.OPTIONS, "correct_answer": "C"}
        ) == (self.OPTIONS, "2")

    def test_accepts_the_answer_as_an_index(self) -> None:
        assert _normalise_mcq(
            {"options": self.OPTIONS, "correct_answer": "2"}
        ) == (self.OPTIONS, "2")

    def test_recovers_from_trailing_punctuation(self) -> None:
        assert _normalise_mcq(
            {"options": self.OPTIONS, "correct_answer": "gamma."}
        ) == (self.OPTIONS, "2")

    def test_rejects_an_answer_that_is_not_an_option(self) -> None:
        # Unmarkable: there is no defensible verdict for a student who picks
        # any of the four.
        assert _normalise_mcq(
            {"options": self.OPTIONS, "correct_answer": "Epsilon"}
        ) is None

    def test_rejects_too_few_options(self) -> None:
        assert _normalise_mcq({"options": ["Only"], "correct_answer": "Only"}) is None


class TestExamGeneration:
    async def test_produces_questions_traced_to_their_source(self) -> None:
        llm = ScriptedLLM(
            [
                [
                    {
                        "type": "mcq",
                        "prompt": "Which is correct?",
                        "options": ["A", "B", "C", "D"],
                        "correct_answer": "B",
                        "explanation": "Because the passage says so.",
                        "source": "C1",
                    }
                ]
            ]
        )
        result = await ExamGenerator(llm).generate(
            [_chunk(i) for i in range(4)], question_count=1
        )

        assert len(result.questions) == 1
        question = result.questions[0]
        assert question.type is QuestionType.MCQ
        assert question.correct_answer == "1"
        assert question.source_chunk_id in {f"chunk-{i}" for i in range(4)}

    async def test_discards_a_question_with_an_untraceable_source(self) -> None:
        # A question whose answer is not in the material is worse than no
        # question: the student revises the wrong thing.
        llm = ScriptedLLM(
            [
                [
                    {
                        "type": "short_answer",
                        "prompt": "Q",
                        "correct_answer": "A",
                        "explanation": "E",
                        "source": "C99",
                    }
                ]
            ]
        )
        result = await ExamGenerator(llm).generate([_chunk(0)], question_count=1)

        assert result.questions == []
        assert result.discarded == 1

    async def test_discards_an_unmarkable_mcq(self) -> None:
        llm = ScriptedLLM(
            [
                [
                    {
                        "type": "mcq",
                        "prompt": "Q",
                        "options": ["A", "B"],
                        "correct_answer": "Z",
                        "explanation": "E",
                        "source": "C1",
                    }
                ]
            ]
        )
        result = await ExamGenerator(llm).generate([_chunk(0)], question_count=1)
        assert result.questions == []

    async def test_a_failed_batch_does_not_abandon_the_test(self) -> None:
        llm = ScriptedLLM(
            [
                RuntimeError("rate limited"),
                [
                    {
                        "type": "short_answer",
                        "prompt": "Q",
                        "correct_answer": "A",
                        "explanation": "E",
                        "source": "C1",
                    }
                ],
            ]
        )
        result = await ExamGenerator(llm, batch_size=1).generate(
            [_chunk(0), _chunk(1)], question_count=2
        )

        assert result.failed_batches == 1
        assert len(result.questions) == 1

    async def test_never_exceeds_the_requested_count(self) -> None:
        llm = ScriptedLLM(
            [
                [
                    {
                        "type": "short_answer",
                        "prompt": f"Q{i}",
                        "correct_answer": "A",
                        "explanation": "E",
                        "source": "C1",
                    }
                    for i in range(20)
                ]
            ]
        )
        result = await ExamGenerator(llm).generate(
            [_chunk(i) for i in range(6)], question_count=3
        )
        assert len(result.questions) == 3

    async def test_normalises_true_false_answers(self) -> None:
        llm = ScriptedLLM(
            [
                [
                    {
                        "type": "true_false",
                        "prompt": "Statement",
                        "correct_answer": "True",
                        "explanation": "E",
                        "source": "C1",
                    }
                ]
            ]
        )
        result = await ExamGenerator(llm).generate([_chunk(0)], question_count=1)
        assert result.questions[0].correct_answer == "true"

    async def test_empty_material_produces_nothing(self) -> None:
        result = await ExamGenerator(ScriptedLLM([[]])).generate([], question_count=5)
        assert result.questions == []


class TestFlashcardGeneration:
    async def test_produces_cards_traced_to_their_source(self) -> None:
        llm = ScriptedLLM(
            [[{"front": "What is X?", "back": "X is Y.", "source": "C1"}]]
        )
        result = await FlashcardGenerator(llm).generate([_chunk(0)], card_count=1)

        assert len(result.cards) == 1
        assert result.cards[0].source_chunk_id == "chunk-0"

    async def test_discards_a_card_that_has_become_a_summary(self) -> None:
        # A card too long to recall in a few seconds has stopped being a card.
        llm = ScriptedLLM(
            [
                [
                    {
                        "front": "What is X?",
                        "back": "x" * (MAX_BACK_CHARS + 50),
                        "source": "C1",
                    }
                ]
            ]
        )
        result = await FlashcardGenerator(llm).generate([_chunk(0)], card_count=1)

        assert result.cards == []
        assert result.discarded == 1

    async def test_deduplicates_across_batches(self) -> None:
        # Batches overlap in subject matter; a duplicate wastes revision time.
        llm = ScriptedLLM(
            [[{"front": "What is X?", "back": "Answer one.", "source": "C1"}]]
        )
        result = await FlashcardGenerator(llm, batch_size=1).generate(
            [_chunk(0), _chunk(1)], card_count=4
        )
        assert len(result.cards) == 1
        assert result.discarded >= 1

    async def test_discards_a_card_with_an_untraceable_source(self) -> None:
        llm = ScriptedLLM([[{"front": "F", "back": "B", "source": "C42"}]])
        result = await FlashcardGenerator(llm).generate([_chunk(0)], card_count=1)
        assert result.cards == []


class TestObjectiveGrading:
    def _mcq(self, response: str | None) -> GradableAnswer:
        return GradableAnswer(
            question_id="q1",
            question_type=QuestionType.MCQ,
            prompt="Which?",
            correct_answer="2",
            explanation="Because.",
            response=response,
            options=["A", "B", "C", "D"],
        )

    def test_marks_a_correct_index(self) -> None:
        assert grade_objective(self._mcq("2")).is_correct

    def test_marks_a_correct_option_text(self) -> None:
        assert grade_objective(self._mcq("C")).is_correct

    def test_marks_a_wrong_choice(self) -> None:
        graded = grade_objective(self._mcq("0"))
        assert not graded.is_correct
        assert graded.awarded == 0.0

    def test_a_blank_answer_is_wrong_not_skipped(self) -> None:
        assert not grade_objective(self._mcq(None)).is_correct

    def test_the_explanation_is_shown_even_when_correct(self) -> None:
        # A student who guessed right still needs to know why.
        assert grade_objective(self._mcq("2")).feedback == "Because."

    def test_true_false_is_case_insensitive(self) -> None:
        answer = GradableAnswer(
            question_id="q2",
            question_type=QuestionType.TRUE_FALSE,
            prompt="Statement",
            correct_answer="true",
            explanation="E",
            response="True",
        )
        assert grade_objective(answer).is_correct


class TestWrittenGrading:
    def _written(self, response: str) -> GradableAnswer:
        return GradableAnswer(
            question_id="q1",
            question_type=QuestionType.SHORT_ANSWER,
            prompt="Define recall.",
            correct_answer="Relevant documents retrieved over all relevant.",
            explanation="From the passage.",
            response=response,
            source_text="Recall measures the proportion of all relevant documents.",
        )

    async def test_awards_partial_credit(self) -> None:
        # A student who has the idea but omits a condition has not failed.
        llm = ScriptedLLM(
            [[{"id": "q1", "awarded": 0.4, "feedback": "Missing the denominator."}]]
        )
        [graded] = await Grader(llm).grade([self._written("Relevant docs found.")])

        assert graded.awarded == 0.4
        assert not graded.is_correct

    async def test_the_threshold_decides_correctness(self) -> None:
        llm = ScriptedLLM(
            [[{"id": "q1", "awarded": CORRECT_THRESHOLD, "feedback": "Close enough."}]]
        )
        [graded] = await Grader(llm).grade([self._written("Nearly right.")])
        assert graded.is_correct

    async def test_clamps_a_score_outside_the_range(self) -> None:
        llm = ScriptedLLM([[{"id": "q1", "awarded": 5, "feedback": "Great."}]])
        [graded] = await Grader(llm).grade([self._written("Right.")])
        assert graded.awarded == 1.0

    async def test_a_grader_failure_does_not_silently_mark_wrong(self) -> None:
        # A student must never lose marks because the grader was unavailable,
        # and the feedback has to say so rather than implying a verdict.
        llm = ScriptedLLM([RuntimeError("quota exhausted")])
        [graded] = await Grader(llm).grade([self._written("An answer.")])

        assert "could not be marked" in graded.feedback
        assert graded.awarded == 0.0

    async def test_an_answer_the_model_skipped_still_gets_a_verdict(self) -> None:
        llm = ScriptedLLM([[]])
        [graded] = await Grader(llm).grade([self._written("An answer.")])
        assert "not marked" in graded.feedback

    async def test_objective_questions_never_reach_the_model(self) -> None:
        # No model, no cost, no chance of a wrong verdict on a question that
        # has one defensible answer.
        llm = ScriptedLLM([[]])
        objective = GradableAnswer(
            question_id="q1",
            question_type=QuestionType.MCQ,
            prompt="Which?",
            correct_answer="1",
            explanation="E",
            response="1",
            options=["A", "B"],
        )
        [graded] = await Grader(llm).grade([objective])

        assert graded.is_correct
        assert llm.index == 0

    async def test_results_come_back_in_the_order_asked(self) -> None:
        llm = ScriptedLLM(
            [[{"id": "q2", "awarded": 1.0, "feedback": "Good."}]]
        )
        objective = GradableAnswer(
            question_id="q1",
            question_type=QuestionType.MCQ,
            prompt="Which?",
            correct_answer="0",
            explanation="E",
            response="0",
            options=["A", "B"],
        )
        written = GradableAnswer(
            question_id="q2",
            question_type=QuestionType.SHORT_ANSWER,
            prompt="Define.",
            correct_answer="A definition.",
            explanation="E",
            response="A definition.",
        )
        graded = await Grader(llm).grade([objective, written])

        assert [g.question_id for g in graded] == ["q1", "q2"]
