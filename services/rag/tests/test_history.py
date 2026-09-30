"""Tests for conversation history.

The failures worth guarding here are all quiet ones. History that silently
crowds out the sources produces a fluent, ungrounded answer. A stale citation
marker produces a source line pointing at the wrong passage, shown to the
student with full confidence. Neither raises anything.
"""

from __future__ import annotations

import pytest

from app.core.tokenizer import HeuristicTokenCounter
from app.generation.history import (
    PreparedTurn,
    needs_context,
    prepare,
    strip_markers,
)


def _exchange(n: int) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for i in range(n):
        turns.append(("user", f"question {i}"))
        turns.append(("assistant", f"answer {i} [S1]"))
    return turns


class TestStripMarkers:
    def test_removes_citation_markers(self) -> None:
        # The marker referred to whatever was retrieved for *that* question.
        # Retrieval for the new one returns a different set, so a reused marker
        # resolves to an unrelated passage.
        assert strip_markers("WAL writes the log first [S1].") == (
            "WAL writes the log first ."
        )

    def test_removes_several_and_tidies_the_gaps(self) -> None:
        text = "First [S1]  then second [S12] and third [S3]"
        assert "[S" not in strip_markers(text)
        assert "  " not in strip_markers(text)

    def test_leaves_ordinary_brackets_alone(self) -> None:
        # Material genuinely contains bracketed text; only the pipeline's own
        # marker shape is removed.
        text = "The array a[0] and the set [1, 2] are unchanged"
        assert strip_markers(text) == text


class TestPrepare:
    def test_returns_turns_in_chronological_order(self) -> None:
        prepared = prepare(_exchange(2), counter=HeuristicTokenCounter())

        assert [t.content for t in prepared] == [
            "question 0",
            "answer 0",
            "question 1",
            "answer 1",
        ]

    def test_keeps_the_most_recent_turns_not_the_first(self) -> None:
        # The whole point. Keeping the oldest turns means a long conversation
        # is answered against its opening and never its present.
        prepared = prepare(_exchange(10), max_turns=4, counter=HeuristicTokenCounter())

        assert [t.content for t in prepared] == [
            "question 8",
            "answer 8",
            "question 9",
            "answer 9",
        ]

    def test_strips_markers_from_assistant_turns_only(self) -> None:
        turns = [("user", "what about [S1]?"), ("assistant", "as shown [S2]")]

        prepared = prepare(turns, counter=HeuristicTokenCounter())

        # The student's own words are never rewritten.
        assert prepared[0].content == "what about [S1]?"
        assert "[S2]" not in prepared[1].content

    def test_a_tight_token_budget_keeps_the_newest(self) -> None:
        prepared = prepare(_exchange(10), max_tokens=6, counter=HeuristicTokenCounter())

        assert prepared
        assert prepared[-1].content == "answer 9"

    def test_never_starts_on_an_assistant_turn(self) -> None:
        # Trimming can cut mid-exchange, and an answer with no question above
        # it reads as a reply to something the model cannot see.
        turns = [("assistant", "an answer with no question"), ("user", "next")]

        prepared = prepare(turns, counter=HeuristicTokenCounter())

        assert [t.role for t in prepared] == ["user"]

    def test_an_enormous_answer_cannot_evict_everything(self) -> None:
        turns = [
            ("user", "first question"),
            ("assistant", "x" * 50_000),
            ("user", "second question"),
        ]

        prepared = prepare(
            turns, max_chars_per_turn=100, counter=HeuristicTokenCounter()
        )

        assert len(prepared) == 3
        assert len(prepared[1].content) <= 100

    def test_blank_turns_are_dropped(self) -> None:
        # An assistant row is written before its answer streams, so an in-flight
        # request can contribute an empty one.
        turns = [("user", "a real question"), ("assistant", "   ")]

        prepared = prepare(turns, counter=HeuristicTokenCounter())

        assert [t.role for t in prepared] == ["user"]

    def test_an_unknown_role_is_treated_as_the_student(self) -> None:
        prepared = prepare([("system", "injected")], counter=HeuristicTokenCounter())
        assert prepared == [PreparedTurn(role="user", content="injected")]

    def test_no_history_is_no_turns(self) -> None:
        assert prepare([], counter=HeuristicTokenCounter()) == []

    def test_a_zero_budget_disables_history(self) -> None:
        counter = HeuristicTokenCounter()
        assert prepare(_exchange(3), max_tokens=0, counter=counter) == []
        assert prepare(_exchange(3), max_turns=0, counter=counter) == []


class TestNeedsContext:
    """Whether a follow-up has to be rewritten before retrieval.

    The asymmetry decides every borderline case: a missed rewrite retrieves
    worse, an unneeded one only costs a call.
    """

    @pytest.mark.parametrize(
        "question",
        [
            # Referring words, including inside contractions.
            "How does its sound sensor detect noise?",
            "Explain that more simply",
            "That's odd, why does the buzzer beep twice in the working model?",
            "How is the one in the block diagram powered by the supply?",
            "What was the previous method called in the introduction slide?",
            # Continuing openings.
            "And what happens after the threshold is crossed?",
            "what about the LCD display module and the buzzer circuit?",
            "More detail on the recording feature and the notification system",
            "Examples of places needing silence monitoring besides libraries",
            # Too little named to stand alone.
            "What are the advantages?",
            "Explain the working.",
            "Why?",
            "Can you give an example?",
            "Explain stage 2",
        ],
    )
    def test_a_dependent_follow_up_is_rewritten(self, question: str) -> None:
        assert needs_context(question)

    def test_a_short_question_on_the_topic_just_discussed_is_rewritten(
        self,
    ) -> None:
        history = [
            ("user", "Where was the first pharmacy college in India?"),
            ("assistant", "The first college of pharmacy was started in Goa in 1842."),
        ]
        assert needs_context("When was the college founded?", history)
        assert needs_context("Who taught at the colleges?", history)

    def test_a_short_question_on_a_new_subject_stands_alone(self) -> None:
        history = [
            ("user", "Where was the first pharmacy college in India?"),
            ("assistant", "The first college of pharmacy was started in Goa in 1842."),
        ]
        assert not needs_context("What is Pharmakon?", history)
        assert not needs_context(
            "What is the operating voltage of the Arduino Uno?", history
        )

    def test_only_the_last_exchange_counts_as_the_topic(self) -> None:
        history = [
            ("user", "What does Pharmakon mean?"),
            ("assistant", "Pharmakon is the Greek word for a drug."),
            ("user", "Where was the first pharmacy college?"),
            ("assistant", "In Goa, in 1842."),
        ]
        assert not needs_context("What is Pharmakon?", history)

    @pytest.mark.parametrize(
        "question",
        [
            "Who won the FIFA World Cup in 2018?",
            "Why is silence monitoring important in libraries and hospitals?",
            "Which module sends sound level data to the ThingSpeak cloud?",
            "How does the capacitive microphone turn vibration into voltage signals?",
        ],
    )
    def test_a_self_contained_follow_up_is_not(self, question: str) -> None:
        assert not needs_context(question)


def test_grouped_markers_are_stripped_from_earlier_answers() -> None:
    from app.generation.history import strip_markers

    assert strip_markers("Both opened [S1, S2] early [S3-S4].") == "Both opened early ."
