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


def _slide(
    index: int,
    heading: str | None,
    text: str,
    tokens: int,
    *,
    path: list[str] | None = None,
) -> Chunk:
    return Chunk(
        id=f"slide-{index}",
        text=text,
        token_count=tokens,
        metadata=ChunkMetadata(
            document_id="doc-1",
            document_name="deck.pptx",
            chunk_index=index,
            slide_number=index + 1,
            heading=heading,
            heading_path=path if path is not None else ([heading] if heading else []),
            content_hash="h" * 64,
        ),
    )


def _usable(chunks: list[Chunk]) -> list[str]:
    return [c.id for c in usable_chunks(chunks)]


class TestSampling:
    def test_drops_fragments_too_small_to_examine(self) -> None:
        chunks = [_chunk(0, tokens=5), _chunk(1, tokens=60)]
        assert [c.id for c in usable_chunks(chunks)] == ["chunk-1"]


class TestWhatIsWorthExamining:
    """Measured on a real deck: the two component-list slides were never used,
    and the reference list supplied two of five quiz questions."""

    def test_a_terse_titled_slide_is_examinable(self) -> None:
        chunks = [
            _slide(0, "Hardware components:", "Arduino uno 16*2 LCD Buzzer", 20),
            _slide(1, "Software components:", "Arduino software Arduino c", 5),
        ]
        assert _usable(chunks) == ["slide-0", "slide-1"]

    def test_a_short_pdf_chunk_is_still_a_fragment(self) -> None:
        # A PDF chunker merges paragraphs, so short means fragment there.
        assert _usable([_chunk(0, text="See Figure 3.", tokens=5)]) == []

    def test_a_slide_fragment_is_still_dropped(self) -> None:
        assert _usable([_slide(0, "Silence Monitoring", "Submitted by", 2)]) == []

    def test_title_slide_credits_are_not_examinable(self) -> None:
        chunks = [
            _slide(0, "Silence Monitoring", "Submitted by: A. Student, B. Student", 9),
            _slide(1, "Silence Monitoring", "Under the guidance of Dr. Rao", 7),
        ]
        assert _usable(chunks) == []

    def test_an_untitled_slide_needs_the_full_minimum(self) -> None:
        assert _usable([_slide(0, "Slide 4", "Arduino c language", 5)]) == []
        assert _usable([_slide(1, None, "Arduino c language", 5)]) == []

    @pytest.mark.parametrize(
        "heading",
        [
            "References:",
            "REFERENCES",
            "7. References",
            "Bibliography",
            "Works Cited",
            "Contents:",
            "Table of Contents",
            "Agenda",
            "Acknowledgements",
            "Thank you!",
            "Any questions?",
        ],
    )
    def test_document_scaffolding_is_skipped(self, heading: str) -> None:
        long_text = "Andrea Giordano, Smart Agents and Fog Computing, 2016. " * 10
        assert _usable([_slide(0, heading, long_text, 247)]) == []

    def test_a_section_nested_under_references_is_skipped(self) -> None:
        chunk = _chunk(0, tokens=120)
        chunk.metadata.heading = "Journal articles"
        chunk.metadata.heading_path = ["References", "Journal articles"]
        assert _usable([chunk]) == []

    @pytest.mark.parametrize(
        "heading",
        [
            "Referential integrity",
            "Reference counting",
            "Contents of a B-tree node",
            "Conclusion",
            "Summary",
            "Index structures",
        ],
    )
    def test_subject_headings_that_merely_resemble_scaffolding_are_kept(
        self, heading: str
    ) -> None:
        assert _usable([_slide(0, heading, "x " * 60, 60)]) == ["slide-0"]

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
        assert _normalise_mcq({"options": self.OPTIONS, "correct_answer": "Gamma"}) == (
            self.OPTIONS,
            "2",
        )

    def test_accepts_the_answer_as_a_letter(self) -> None:
        assert _normalise_mcq({"options": self.OPTIONS, "correct_answer": "C"}) == (
            self.OPTIONS,
            "2",
        )

    def test_accepts_the_answer_as_an_index(self) -> None:
        assert _normalise_mcq({"options": self.OPTIONS, "correct_answer": "2"}) == (
            self.OPTIONS,
            "2",
        )

    def test_recovers_from_trailing_punctuation(self) -> None:
        assert _normalise_mcq(
            {"options": self.OPTIONS, "correct_answer": "gamma."}
        ) == (self.OPTIONS, "2")

    def test_rejects_an_answer_that_is_not_an_option(self) -> None:
        # Unmarkable: there is no defensible verdict for a student who picks
        # any of the four.
        assert (
            _normalise_mcq({"options": self.OPTIONS, "correct_answer": "Epsilon"})
            is None
        )

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
        # Asked for explicitly. True/false is not in the generator's default
        # set, and since the requested types became a filter rather than a
        # suggestion, one that arrives unasked-for is discarded.
        result = await ExamGenerator(llm).generate(
            [_chunk(0)], question_count=1, types=[QuestionType.TRUE_FALSE]
        )
        assert result.questions[0].correct_answer == "true"

    async def test_empty_material_produces_nothing(self) -> None:
        result = await ExamGenerator(ScriptedLLM([[]])).generate([], question_count=5)
        assert result.questions == []


class TestQuestionTypeRestriction:
    """The requested types are enforced, not merely asked for.

    A written question that reaches a student who was promised multiple choice
    cannot be displayed by the quiz screen, and would cost a grading model call
    the caller deliberately avoided by asking for objective questions only.
    """

    @staticmethod
    def _mixed() -> ScriptedLLM:
        return ScriptedLLM(
            [
                [
                    {
                        "type": "short_answer",
                        "prompt": "Explain write ahead logging.",
                        "correct_answer": "The log is written first.",
                        "explanation": "The passage says so.",
                        "source": "C1",
                    },
                    {
                        "type": "mcq",
                        "prompt": "What does WAL write first?",
                        "options": ["The log", "The page", "The index", "Nothing"],
                        "correct_answer": "The log",
                        "explanation": "The passage says so.",
                        "source": "C1",
                    },
                ]
            ]
        )

    async def test_multiple_choice_only_drops_a_written_question(self) -> None:
        result = await ExamGenerator(self._mixed()).generate(
            [_chunk(i) for i in range(2)],
            question_count=2,
            types=[QuestionType.MCQ],
        )

        assert [q.type for q in result.questions] == [QuestionType.MCQ]
        # Counted rather than silently dropped, so a run that keeps losing half
        # its output is visible in the stats.
        assert result.discarded == 1

    async def test_both_survive_when_both_were_asked_for(self) -> None:
        result = await ExamGenerator(self._mixed()).generate(
            [_chunk(i) for i in range(2)],
            question_count=2,
            types=[QuestionType.MCQ, QuestionType.SHORT_ANSWER],
        )

        assert {q.type for q in result.questions} == {
            QuestionType.MCQ,
            QuestionType.SHORT_ANSWER,
        }


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
        # Every call, the top-up included, wrote the same overlong card.
        assert result.discarded == result.batches_run

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


def _card(n: int, source: str = "C1") -> dict[str, str]:
    return {"front": f"Question {n}?", "back": f"Answer {n}.", "source": source}


class RecordingLLM(ScriptedLLM):
    """ScriptedLLM that also keeps each prompt it was sent."""

    def __init__(self, payloads: list[object]) -> None:
        super().__init__(payloads)
        self.prompts: list[str] = []

    async def complete(self, messages, **kwargs):
        self.prompts.append(messages[-1].content)
        return await super().complete(messages, **kwargs)


class TestFlashcardShortfall:
    """Measured on a real deck: eight cards asked for, four delivered."""

    async def test_a_shortfall_is_topped_up(self) -> None:
        llm = RecordingLLM(
            [
                [_card(1), _card(2)],
                [_card(3), _card(4)],
                [_card(5), _card(6), _card(7), _card(8)],
            ]
        )
        chunks = [_chunk(i) for i in range(8)]
        result = await FlashcardGenerator(llm, batch_size=4).generate(
            chunks, card_count=8
        )

        assert len(result.cards) == 8
        assert result.top_up_calls == 1
        assert len(llm.prompts) == 3

    async def test_the_top_up_lists_what_already_exists(self) -> None:
        llm = RecordingLLM([[_card(1)], [_card(2)]])
        await FlashcardGenerator(llm).generate([_chunk(0)], card_count=3)

        top_up = llm.prompts[-1]
        assert "Write 2 more flashcard(s)" in top_up
        assert "- Question 1?" in top_up

    async def test_a_top_up_duplicate_is_still_discarded(self) -> None:
        llm = RecordingLLM([[_card(1)], [_card(1)]])
        result = await FlashcardGenerator(llm).generate([_chunk(0)], card_count=2)

        assert [c.front for c in result.cards] == ["Question 1?"]
        assert result.discarded == 1

    async def test_there_is_only_one_top_up(self) -> None:
        # Every call comes out of a small daily quota.
        llm = RecordingLLM([[]])
        result = await FlashcardGenerator(llm).generate(
            [_chunk(i) for i in range(3)], card_count=10
        )

        assert result.cards == []
        assert result.top_up_calls == 1
        assert len(llm.prompts) == 2

    async def test_a_full_set_needs_no_top_up(self) -> None:
        llm = RecordingLLM([[_card(1), _card(2)]])
        result = await FlashcardGenerator(llm).generate([_chunk(0)], card_count=2)

        assert len(result.cards) == 2
        assert result.top_up_calls == 0
        assert len(llm.prompts) == 1

    async def test_the_top_up_reads_the_least_used_passages(self) -> None:
        # Chunk 0 supplied every card in the first pass; the top-up must look
        # elsewhere first.
        llm = RecordingLLM([[_card(1), _card(2)], []])
        chunks = [
            _chunk(0, text="Passage zero on hashing."),
            _chunk(1, text="Passage one on B-trees."),
        ]
        await FlashcardGenerator(llm, batch_size=1).generate(chunks, card_count=4)

        # batch 1 = chunk 0 (2 cards), batch 2 = chunk 1 (0 cards), top-up = chunk 1
        assert "Passage one on B-trees." in llm.prompts[-1]
        assert "Passage zero on hashing." not in llm.prompts[-1]

    async def test_cards_are_shared_out_by_batch_size(self) -> None:
        # Seven chunks in batches of six: the full batch is asked for six of
        # seven cards, the one-chunk tail for the last one.
        llm = RecordingLLM([[_card(i) for i in range(1, 7)], [_card(7)]])
        await FlashcardGenerator(llm).generate(
            [_chunk(i) for i in range(7)], card_count=7
        )

        assert llm.prompts[0].startswith("Write 6 flashcard(s)")
        assert llm.prompts[1].startswith("Write 1 flashcard(s)")


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
        llm = ScriptedLLM([[{"id": "q2", "awarded": 1.0, "feedback": "Good."}]])
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
