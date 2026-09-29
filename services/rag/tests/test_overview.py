from __future__ import annotations

import pytest

from app.core.models import Chunk, ChunkMetadata, ExplanationMode
from app.core.tokenizer import HeuristicTokenCounter
from app.generation.chat import (
    ChatRequest,
    ChatService,
    ChatTurn,
    _spread_by_document,
)
from app.generation.context import ContextBuilder
from app.generation.llm import MockLLMProvider
from app.generation.overview import is_overview_question
from app.generation.prompts import (
    NO_CONTEXT_REPLY,
    OVERVIEW_RECHECK_NOTE,
    RECHECK_NOTE,
    system_prompt,
)
from tests.fakes import FakeRetrieval, Scripted, with_scores


class TestRecognition:
    # The first three are the product's own starter prompts, which were being
    # answered with "not in your material".
    @pytest.mark.parametrize(
        "question",
        [
            "What am I most likely to be tested on?",
            "Explain the hardest idea in my notes simply",
            "Summarise what I uploaded",
            "Give me a summary",
            "What are the key points?",
            "what is this about",
            "What does this deck cover?",
            "What are my notes about?",
            "Which topics are most important for the exam?",
            "tl;dr",
            "What should I focus on?",
            "What's the trickiest concept here?",
            "What might come up in the exam?",
        ],
    )
    def test_questions_about_the_whole(self, question: str) -> None:
        assert is_overview_question(question)

    @pytest.mark.parametrize(
        "question",
        [
            # Names a subject: retrieval finds it better than a sample would.
            "Summarise how the ESP8266 sends data",
            "What is the hardest part of calibrating the sound sensor?",
            "What is the most important formula?",
            "What is the sensor about?",
            # No intent to judge the whole.
            "Which microcontroller board is the system built on?",
            "How does it work?",
            "Explain the buzzer simply",
            "Who won the FIFA World Cup in 2018?",
        ],
    )
    def test_questions_about_a_subject(self, question: str) -> None:
        assert not is_overview_question(question)


def _doc_chunk(document: str, index: int, text: str, heading: str = "") -> Chunk:
    return Chunk(
        id=f"{document}-{index}",
        text=text,
        token_count=40,
        metadata=ChunkMetadata(
            document_id=document,
            document_name=f"{document}.pptx",
            chunk_index=index,
            slide_number=index + 1,
            heading=heading or None,
            heading_path=[heading] if heading else [],
            content_hash="h" * 64,
        ),
    )


class FakeCorpus:
    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    async def sample_chunks(self, *, user_id, document_ids=None, limit=25):
        self.calls.append({"user_id": user_id, "document_ids": document_ids})
        return self.chunks[:limit]


_DECK = [
    _doc_chunk("deck", i, f"Slide {i} explains part {i} of the monitor in detail.")
    for i in range(30)
] + [_doc_chunk("deck", 30, "Giordano et al. 2016. Smart agents.", "References")]


def _whole(
    llm: object,
    corpus: FakeCorpus,
    *,
    max_chunks: int = 10,
    scores: tuple[float, float] = (0.40, 0.02),
) -> tuple[ChatService, FakeRetrieval]:
    # By default the search scores what it finds as off topic -- as it did for
    # the starter prompts -- so only the whole-document path can answer.
    retrieval = FakeRetrieval(with_scores(*scores))
    service = ChatService(
        retrieval,
        ContextBuilder(counter=HeuristicTokenCounter()),
        llm,
        off_topic_below=(0.55, 0.10),
        corpus=corpus,
        overview_context_builder=ContextBuilder(
            max_chunks=max_chunks, max_tokens=20_000, counter=HeuristicTokenCounter()
        ),
    )
    return service, retrieval


def _ask(question: str) -> ChatRequest:
    return ChatRequest(question=question, user_id="u1", document_ids=["deck"])


class TestAnsweringFromTheWhole:
    async def test_a_starter_prompt_is_answered_not_refused(self) -> None:
        # Retrieval would score this as off topic and never ask the model.
        llm = MockLLMProvider(reply="The notes put most weight on part 3 [S2].")
        corpus = FakeCorpus(_DECK)
        service, retrieval = _whole(llm, corpus)

        result = await service.answer(_ask("What am I most likely to be tested on?"))

        assert not result.unsupported
        assert result.text != NO_CONTEXT_REPLY
        assert len(llm.calls) == 1
        assert retrieval.queries == []
        assert corpus.calls == [{"user_id": "u1", "document_ids": ["deck"]}]

    async def test_the_context_spans_the_document_in_order(self) -> None:
        llm = MockLLMProvider(reply="Summary [S1].")
        service, _ = _whole(llm, FakeCorpus(_DECK), max_chunks=10)

        result = await service.answer(_ask("Summarise what I uploaded"))

        prompt = llm.calls[0][-1].content
        positions = [prompt.index(f"Slide {i} explains") for i in (0, 9, 18, 27)]
        assert positions == sorted(positions)
        assert result.retrieved == 10

    async def test_scaffolding_is_left_out(self) -> None:
        llm = MockLLMProvider(reply="Summary [S1].")
        service, _ = _whole(llm, FakeCorpus(_DECK), max_chunks=40)

        await service.answer(_ask("Summarise what I uploaded"))

        assert "Giordano" not in llm.calls[0][-1].content

    async def test_the_model_is_told_it_may_judge_the_material(self) -> None:
        llm = MockLLMProvider(reply="Part 3 is hardest [S1].")
        service, _ = _whole(llm, FakeCorpus(_DECK))

        await service.answer(_ask("Explain the hardest idea in my notes simply"))

        system = llm.calls[0][0].content
        assert system == system_prompt(ExplanationMode.DETAILED, overview=True)
        assert "as a whole" in system

    async def test_a_subject_question_still_goes_through_retrieval(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        corpus = FakeCorpus(_DECK)
        service, retrieval = _whole(llm, corpus, scores=(0.70, 0.60))

        await service.answer(_ask("Summarise how the ESP8266 sends data"))

        assert retrieval.queries == ["Summarise how the ESP8266 sends data"]
        assert corpus.calls == []
        assert "as a whole" not in llm.calls[0][0].content

    async def test_streaming_takes_the_same_path(self) -> None:
        llm = MockLLMProvider(reply="The notes stress part 3 [S1].")
        service, _ = _whole(llm, FakeCorpus(_DECK))

        events = [e async for e in service.stream(_ask("What are the key points?"))]

        text = "".join(str(v) for k, v in events if k == "token")
        assert text.strip() == "The notes stress part 3 [S1]."
        assert {"stage": "writing", "passages": 10} in [
            v for k, v in events if k == "stage"
        ]

    async def test_nothing_uploaded_is_still_no_material(self) -> None:
        llm = MockLLMProvider(reply="Invented [S1].")
        service, _ = _whole(llm, FakeCorpus([]))

        result = await service.answer(_ask("Summarise what I uploaded"))

        assert result.text == NO_CONTEXT_REPLY
        assert llm.calls == []

    async def test_without_a_corpus_the_question_is_searched(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        retrieval = FakeRetrieval()
        service = ChatService(
            retrieval, ContextBuilder(counter=HeuristicTokenCounter()), llm
        )

        await service.answer(_ask("Summarise what I uploaded"))

        assert retrieval.queries == ["Summarise what I uploaded"]


class TestSpreadAcrossDocuments:
    def test_a_long_document_does_not_crowd_out_a_short_one(self) -> None:
        long = [_doc_chunk("pdf", i, "x") for i in range(100)]
        short = [_doc_chunk("deck", i, "y") for i in range(4)]

        chosen = _spread_by_document(long + short, 12)

        assert len(chosen) == 12
        assert sum(c.metadata.document_id == "deck" for c in chosen) == 4

    def test_each_document_stays_in_reading_order(self) -> None:
        a = [_doc_chunk("a", i, "x") for i in reversed(range(20))]
        b = [_doc_chunk("b", i, "y") for i in range(20)]

        chosen = _spread_by_document(a + b, 8)

        for document in ("a", "b"):
            indexes = [
                c.metadata.chunk_index
                for c in chosen
                if c.metadata.document_id == document
            ]
            assert indexes == sorted(indexes)
            assert len(indexes) == 4


class TestRefusedJudgment:
    async def test_a_refusal_is_retried_with_the_whole_document_note(self) -> None:
        # Measured: "Explain the hardest idea in my notes simply" was refused
        # with nine passages of the deck in context.
        llm = Scripted("NOT_SUPPORTED", "The block diagram has the most parts [S3].")
        service, _ = _whole(llm, FakeCorpus(_DECK))

        result = await service.answer(_ask("Explain the hardest idea in my notes"))

        assert not result.unsupported
        assert len(llm.calls) == 2
        assert llm.temperatures == [0.2, 0.0]
        assert OVERVIEW_RECHECK_NOTE in llm.calls[1][-1].content
        assert RECHECK_NOTE not in llm.calls[1][-1].content

    async def test_streaming_retries_too(self) -> None:
        llm = Scripted("NOT_SUPPORTED", "The block diagram has the most parts [S3].")
        service, _ = _whole(llm, FakeCorpus(_DECK))

        events = [e async for e in service.stream(_ask("What is the hardest idea?"))]

        assert {"stage": "rechecking"} in [v for k, v in events if k == "stage"]
        text = "".join(str(v) for k, v in events if k == "token")
        assert text.strip() == "The block diagram has the most parts [S3]."


class TestNoRewrite:
    async def test_a_follow_up_about_the_whole_is_not_condensed(self) -> None:
        # A rewrite would add the conversation's subject ("...of the silence
        # monitor") and send the question back to a search that cannot answer.
        llm = MockLLMProvider(reply="Summary [S1].")
        utility = MockLLMProvider(reply="Summarise the silence monitor's ESP8266")
        service, retrieval = _whole(llm, FakeCorpus(_DECK))
        service._utility_llm = utility

        request = _ask("Summarise what I uploaded")
        request.history = [
            ChatTurn(role="user", content="What does the ESP8266 do?"),
            ChatTurn(role="assistant", content="It connects to the cloud [S1]."),
        ]
        result = await service.answer(request)

        assert utility.calls == []
        assert retrieval.queries == []
        assert result.rewritten_query == "Summarise what I uploaded"
