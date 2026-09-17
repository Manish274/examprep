from __future__ import annotations

import pytest

from app.core.models import (
    Chunk,
    ChunkMetadata,
    ContentSource,
    ExplanationMode,
    LLMMessage,
    LLMResponse,
    LLMUsage,
    RetrievalStrategy,
    ScoredChunk,
)
from app.core.tokenizer import HeuristicTokenCounter
from app.generation.chat import (
    ChatRequest,
    ChatService,
    ChatTurn,
    Evidence,
    is_refusal,
)
from app.generation.context import ContextBuilder
from app.generation.llm import GeminiLLMProvider, MockLLMProvider, _split_messages
from app.generation.prompts import (
    NO_CONTEXT_REPLY,
    RECHECK_NOTE,
    UNSUPPORTED_REPLY,
    UNSUPPORTED_TOKEN,
    condense_prompt,
    system_prompt,
)


def _chunk(text: str, index: int = 0, *, vision: bool = False) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(
            id=f"chunk-{index}",
            text=text,
            token_count=20,
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name="lecture.pptx",
                chunk_index=index,
                slide_number=index + 1,
                heading_path=["Smoothing"],
                content_hash="h" * 64,
                source=ContentSource.VISION if vision else ContentSource.TEXT,
            ),
        ),
        score=1.0 - index / 100,
    )


class FakeRetrieval:
    def __init__(self, chunks: list[ScoredChunk] | None = None) -> None:
        self.chunks = chunks if chunks is not None else [_chunk("Some material.")]
        self.queries: list[str] = []

    async def search(self, query, *, user_id, strategy, document_ids=None, top_k=10):
        self.queries.append(query)
        return self.chunks


def _service(
    llm: object, *, chunks: list[ScoredChunk] | None = None, utility: object = None
) -> tuple[ChatService, FakeRetrieval]:
    retrieval = FakeRetrieval(chunks)
    service = ChatService(
        retrieval,
        ContextBuilder(counter=HeuristicTokenCounter()),
        llm,
        utility_llm=utility,
    )
    return service, retrieval


def _request(**kwargs: object) -> ChatRequest:
    options: dict[str, object] = {"question": "what is smoothing", "user_id": "u1"}
    options.update(kwargs)
    return ChatRequest(**options)  # type: ignore[arg-type]


class TestRefusalDetection:
    @pytest.mark.parametrize(
        "text",
        [
            UNSUPPORTED_TOKEN,
            f"{UNSUPPORTED_TOKEN}.",
            f'"{UNSUPPORTED_TOKEN}"',
            f"**{UNSUPPORTED_TOKEN}**",
            f"{UNSUPPORTED_TOKEN} - the notes do not cover it.",
        ],
    )
    def test_recognises_the_shapes_models_actually_emit(self, text: str) -> None:
        # Models append punctuation, add emphasis, and explain themselves
        # despite instructions. Exact equality would miss all of that and let a
        # refusal reach the student as a raw token.
        assert is_refusal(text)

    def test_does_not_mistake_an_answer_for_a_refusal(self) -> None:
        assert not is_refusal("Smoothing is not supported by every model [S1].")


class TestPrompts:
    def test_grounding_rules_are_identical_across_modes(self) -> None:
        # Mode changes style only. A "simple explanation" that relaxes
        # grounding is the most likely place for an invented analogy.
        prompts = {m: system_prompt(m) for m in ExplanationMode}
        rules = [p.split("Style:")[0] for p in prompts.values()]
        assert len(set(rules)) == 1

    def test_each_mode_has_its_own_style_section(self) -> None:
        styles = {system_prompt(m).split("Style:")[1] for m in ExplanationMode}
        assert len(styles) == len(ExplanationMode)

    def test_the_refusal_token_is_stated_in_the_prompt(self) -> None:
        assert UNSUPPORTED_TOKEN in system_prompt(ExplanationMode.SIMPLE)

    def test_condense_prompt_carries_recent_turns(self) -> None:
        prompt = condense_prompt(
            "why does that matter",
            [("user", "what is smoothing"), ("assistant", "It assigns...")],
        )
        assert "what is smoothing" in prompt
        assert "why does that matter" in prompt

    def test_condense_prompt_truncates_a_long_history(self) -> None:
        history = [("user", f"question {i}") for i in range(40)]
        prompt = condense_prompt("follow up", history)
        # Only recent turns; a long history costs tokens on every question and
        # rarely changes what a pronoun refers to.
        assert "question 0" not in prompt
        assert "question 39" in prompt


class TestMessageSplitting:
    def test_system_goes_to_its_own_field(self) -> None:
        # Folding the system prompt into the first user turn measurably weakens
        # instruction following, which here means weakening grounding.
        system, contents = _split_messages(
            [
                LLMMessage(role="system", content="RULES"),
                LLMMessage(role="user", content="question"),
            ]
        )
        assert system == "RULES"
        assert len(contents) == 1

    def test_assistant_is_renamed_to_model(self) -> None:
        _, contents = _split_messages(
            [LLMMessage(role="assistant", content="previous answer")]
        )
        assert contents[0]["role"] == "model"


class TestAnswering:
    async def test_produces_a_cited_answer(self) -> None:
        service, _ = _service(MockLLMProvider())
        result = await service.answer(_request())

        assert not result.unsupported
        assert "[S1]" in result.text
        assert [s.marker for s in result.sources] == ["S1"]

    async def test_a_refusal_becomes_a_readable_reply(self) -> None:
        # The student should never see the raw token.
        service, _ = _service(MockLLMProvider(reply=UNSUPPORTED_TOKEN))
        result = await service.answer(_request())

        assert result.unsupported
        assert UNSUPPORTED_TOKEN not in result.text
        assert result.sources == []

    async def test_nothing_retrieved_never_reaches_the_model(self) -> None:
        # Asking anyway invites exactly the ungrounded answer this refuses to
        # give.
        llm = MockLLMProvider(reply="I would happily make something up.")
        service, _ = _service(llm, chunks=[])
        result = await service.answer(_request())

        assert result.unsupported
        assert llm.calls == []

    async def test_only_cited_sources_are_returned(self) -> None:
        # Listing every retrieved chunk would imply the answer rests on
        # material it never used.
        service, _ = _service(
            MockLLMProvider(reply="Answer citing only the first [S1]."),
            chunks=[_chunk("First.", 0), _chunk("Second.", 1), _chunk("Third.", 2)],
        )
        result = await service.answer(_request())

        assert [s.marker for s in result.sources] == ["S1"]

    async def test_an_invented_citation_is_reported(self) -> None:
        service, _ = _service(
            MockLLMProvider(reply="As shown in [S9], this is true."),
            chunks=[_chunk("Only source.")],
        )
        result = await service.answer(_request())

        assert result.dangling_citations == ["S9"]
        assert result.sources == []

    async def test_the_mode_reaches_the_prompt(self) -> None:
        llm = MockLLMProvider(reply="answer [S1]")
        service, _ = _service(llm)
        await service.answer(_request(mode=ExplanationMode.EXAM))

        system = next(m.content for m in llm.calls[0] if m.role == "system")
        assert "as if for an exam" in system

    async def test_vision_provenance_reaches_the_prompt(self) -> None:
        # The model needs to know a source was read from a picture in order to
        # follow the rule about flagging it.
        llm = MockLLMProvider(reply="answer [S1]")
        service, _ = _service(llm, chunks=[_chunk("Transcribed.", vision=True)])
        await service.answer(_request())

        user = next(m.content for m in llm.calls[0] if m.role == "user")
        assert "read from an image" in user


class TestCondensing:
    async def test_the_first_question_is_not_rewritten(self) -> None:
        # No history to resolve against; the call would be pure latency.
        utility = MockLLMProvider(reply="rewritten")
        service, retrieval = _service(MockLLMProvider(), utility=utility)
        await service.answer(_request())

        assert retrieval.queries == ["what is smoothing"]
        assert utility.calls == []

    async def test_a_follow_up_is_rewritten_before_retrieval(self) -> None:
        utility = MockLLMProvider(reply="how does a bigram differ from a trigram")
        service, retrieval = _service(MockLLMProvider(), utility=utility)

        await service.answer(
            _request(
                question="how does it differ",
                history=[
                    ChatTurn(role="user", content="what is a bigram"),
                    ChatTurn(role="assistant", content="A bigram is n=2."),
                ],
            )
        )
        assert retrieval.queries == ["how does a bigram differ from a trigram"]

    async def test_a_self_contained_follow_up_skips_the_rewrite(self) -> None:
        # The rewrite spends a call from the quota vision reads slides with.
        utility = MockLLMProvider(reply="should never be used")
        service, retrieval = _service(MockLLMProvider(), utility=utility)
        question = "How does a trigram language model estimate word probabilities?"

        await service.answer(
            _request(
                question=question,
                history=[
                    ChatTurn(role="user", content="what is a bigram"),
                    ChatTurn(role="assistant", content="A bigram is n=2."),
                ],
            )
        )

        assert utility.calls == []
        assert retrieval.queries == [question]

    async def test_the_skipped_follow_up_still_reaches_the_model_with_history(
        self,
    ) -> None:
        # Skipping the rewrite changes retrieval only; the answering model still
        # sees the conversation.
        llm = MockLLMProvider(reply="Answer [S1].")
        service, _ = _service(llm, utility=MockLLMProvider(reply="unused"))

        await service.answer(
            _request(
                question="How does a trigram language model estimate probabilities?",
                history=[
                    ChatTurn(role="user", content="what is a bigram"),
                    ChatTurn(role="assistant", content="A bigram is n=2."),
                ],
            )
        )

        assert [m.role for m in llm.calls[0]] == [
            "system",
            "user",
            "assistant",
            "user",
        ]

    async def test_a_failed_rewrite_falls_back_to_the_question(self) -> None:
        class Broken(MockLLMProvider):
            async def complete(self, messages, **kwargs):
                raise RuntimeError("utility model down")

        service, retrieval = _service(MockLLMProvider(), utility=Broken())
        await service.answer(
            _request(history=[ChatTurn(role="user", content="earlier")])
        )
        assert retrieval.queries == ["what is smoothing"]

    async def test_a_rambling_rewrite_is_discarded(self) -> None:
        # A model that returns an essay has misunderstood the task; the
        # original question is a safer retrieval query.
        utility = MockLLMProvider(reply="x" * 900)
        service, retrieval = _service(MockLLMProvider(), utility=utility)
        await service.answer(
            _request(history=[ChatTurn(role="user", content="earlier")])
        )
        assert retrieval.queries == ["what is smoothing"]


class TestStreaming:
    async def _collect(self, service: ChatService, request: ChatRequest):
        tokens: list[str] = []
        final = None
        async for kind, value in service.stream(request):
            if kind == "token":
                tokens.append(str(value))
            else:
                final = value
        return "".join(tokens), final

    async def test_streams_tokens_then_a_final_result(self) -> None:
        service, _ = _service(MockLLMProvider(reply="Streamed answer [S1]."))
        text, final = await self._collect(service, _request())

        assert "Streamed answer" in text
        assert final is not None
        assert not final.unsupported
        assert [s.marker for s in final.sources] == ["S1"]

    async def test_a_refusal_never_leaks_the_raw_token(self) -> None:
        # The opening tokens are held back until it is clear whether this is a
        # refusal; streaming "NOT_SUPPORTED" and replacing it would be worse
        # than a brief pause.
        service, _ = _service(MockLLMProvider(reply=UNSUPPORTED_TOKEN))
        text, final = await self._collect(service, _request())

        assert UNSUPPORTED_TOKEN not in text
        assert final is not None
        assert final.unsupported

    async def test_an_empty_context_streams_the_explanation(self) -> None:
        service, _ = _service(MockLLMProvider(), chunks=[])
        text, final = await self._collect(service, _request())

        assert text.strip()
        assert final is not None
        assert final.unsupported

    async def test_the_streamed_answer_matches_the_final_text(self) -> None:
        service, _ = _service(MockLLMProvider(reply="A cited answer [S1]."))
        text, final = await self._collect(service, _request())

        assert final is not None
        assert text.strip() == final.text.strip()


class TestGeminiProviderConstruction:
    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ValueError, match="requires an API key"):
            GeminiLLMProvider("")

    def test_thinking_config_is_sent_when_requested(self) -> None:
        provider = GeminiLLMProvider("key", thinking_budget=0)
        payload = provider._payload([LLMMessage(role="user", content="q")], 0.2, 100)
        assert payload["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}

    def test_thinking_config_is_dropped_once_a_model_rejects_it(self) -> None:
        # The lite models reject thinkingConfig with a 400. Hardcoding which
        # models support it would drift; this self-heals instead.
        provider = GeminiLLMProvider("key", thinking_budget=0)
        provider._thinking_unsupported = True

        payload = provider._payload([LLMMessage(role="user", content="q")], 0.2, 100)
        assert "thinkingConfig" not in payload["generationConfig"]

    def test_a_400_with_thinking_becomes_retryable(self) -> None:
        import httpx

        from app.embedding.rate_limit import TransientError

        provider = GeminiLLMProvider("key", thinking_budget=0)
        response = httpx.Response(400, text="invalid argument")

        with pytest.raises(TransientError):
            provider._check(response, sent_thinking=True)
        assert provider._thinking_unsupported is True

    def test_a_400_without_thinking_stays_permanent(self) -> None:
        import httpx

        provider = GeminiLLMProvider("key")
        with pytest.raises(RuntimeError, match="llm request failed"):
            provider._check(httpx.Response(400, text="bad"), sent_thinking=False)


class TestMockProvider:
    async def test_refuses_when_the_prompt_carries_no_sources(self) -> None:
        # Honouring the grounding contract is what lets pipeline tests assert
        # on refusal rather than only on plumbing.
        response = await MockLLMProvider().complete(
            [LLMMessage(role="user", content="no sources here")]
        )
        assert response.text == UNSUPPORTED_TOKEN

    async def test_answers_when_sources_are_present(self) -> None:
        response = await MockLLMProvider().complete(
            [LLMMessage(role="user", content="[S1] material")]
        )
        assert "[S1]" in response.text

    async def test_streams_in_more_than_one_piece(self) -> None:
        chunks = [
            c
            async for c in MockLLMProvider(reply="one two three").stream(
                [LLMMessage(role="user", content="q")]
            )
        ]
        assert len(chunks) > 1


class TestUsageIsReported:
    async def test_token_counts_travel_with_the_answer(self) -> None:
        class Counting(MockLLMProvider):
            async def complete(self, messages, **kwargs):
                return LLMResponse(
                    text="answer [S1]",
                    usage=LLMUsage(prompt_tokens=1200, completion_tokens=80),
                    model_id="test",
                )

        service, _ = _service(Counting())
        result = await service.answer(_request())

        assert result.prompt_tokens == 1200
        assert result.completion_tokens == 80


class TestStrategyIsHonoured:
    async def test_the_requested_strategy_reaches_retrieval(self) -> None:
        captured: list[RetrievalStrategy] = []

        class Capturing(FakeRetrieval):
            async def search(
                self, query, *, user_id, strategy, document_ids=None, top_k=10
            ):
                captured.append(strategy)
                return await super().search(
                    query,
                    user_id=user_id,
                    strategy=strategy,
                    document_ids=document_ids,
                    top_k=top_k,
                )

        service = ChatService(
            Capturing(),
            ContextBuilder(counter=HeuristicTokenCounter()),
            MockLLMProvider(),
        )
        await service.answer(_request(strategy=RetrievalStrategy.DENSE))
        assert captured == [RetrievalStrategy.DENSE]


class TestConversationMemory:
    """A follow-up must reach the model with the exchange that preceded it.

    Condensing already made *retrieval* conversation-aware. Until the turns
    reached the answering model too, the right passages were found and then
    written up as though the student had asked nothing before -- so "explain
    that more simply" produced a fresh lecture rather than a simpler version of
    the answer just given.
    """

    @staticmethod
    def _history() -> list[ChatTurn]:
        return [
            ChatTurn(role="user", content="what is a bigram"),
            ChatTurn(role="assistant", content="A bigram is a sequence of two [S1]."),
        ]

    async def test_prior_turns_reach_the_model(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        service, _ = _service(llm, utility=MockLLMProvider(reply="what is a trigram"))

        await service.answer(
            _request(question="and a trigram?", history=self._history())
        )

        sent = llm.calls[0]
        assert [m.role for m in sent] == ["system", "user", "assistant", "user"]
        assert sent[1].content == "what is a bigram"
        assert "A bigram is a sequence of two" in sent[2].content

    async def test_stale_citation_markers_are_stripped_from_prior_answers(
        self,
    ) -> None:
        # [S1] in the earlier answer pointed at whatever was retrieved then.
        # This question retrieved a different set, so a reused marker would
        # resolve to an unrelated passage and show the student a wrong source.
        llm = MockLLMProvider(reply="Answer [S1].")
        service, _ = _service(llm, utility=MockLLMProvider(reply="rewritten"))

        await service.answer(
            _request(question="and a trigram?", history=self._history())
        )

        assistant_turn = llm.calls[0][2]
        assert "[S1]" not in assistant_turn.content

    async def test_the_question_asked_is_the_one_the_student_typed(self) -> None:
        # The condensed rewrite is a search query. Answering it instead would
        # discard the phrasing and emphasis the student chose.
        llm = MockLLMProvider(reply="Answer [S1].")
        service, retrieval = _service(
            llm, utility=MockLLMProvider(reply="what is a trigram in n-gram models")
        )

        await service.answer(
            _request(question="and a trigram?", history=self._history())
        )

        assert retrieval.queries == ["what is a trigram in n-gram models"]
        assert "and a trigram?" in llm.calls[0][-1].content

    async def test_sources_stay_adjacent_to_the_question(self) -> None:
        # The material is evidence for the question being asked, not for the
        # conversation, so it belongs in the final turn.
        llm = MockLLMProvider(reply="Answer [S1].")
        service, _ = _service(llm, utility=MockLLMProvider(reply="rewritten"))

        await service.answer(_request(history=self._history()))

        final = llm.calls[0][-1]
        assert final.role == "user"
        assert "Study material:" in final.content

    async def test_history_cannot_crowd_out_the_material(self) -> None:
        # An answer's evidence must survive a long conversation. The history
        # budget is separate and far smaller than the context budget.
        llm = MockLLMProvider(reply="Answer [S1].")
        service, _ = _service(llm, utility=MockLLMProvider(reply="rewritten"))
        long_history = [
            ChatTurn(role="user" if i % 2 == 0 else "assistant", content="x" * 4000)
            for i in range(40)
        ]

        await service.answer(_request(history=long_history))

        sent = llm.calls[0]
        assert "Study material:" in sent[-1].content
        # 8 turns plus the system prompt and the current question.
        assert len(sent) <= 10

    async def test_a_first_message_sends_no_turns(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        service, _ = _service(llm)

        await service.answer(_request())

        assert [m.role for m in llm.calls[0]] == ["system", "user"]

    async def test_streaming_carries_the_conversation_too(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        service, _ = _service(llm, utility=MockLLMProvider(reply="rewritten"))

        async for _kind, _value in service.stream(
            _request(question="and a trigram?", history=self._history())
        ):
            pass

        assert [m.role for m in llm.calls[0]] == [
            "system",
            "user",
            "assistant",
            "user",
        ]


class Scripted(MockLLMProvider):
    """Replies in order, one per call, and records each call's temperature."""

    def __init__(self, *replies: str) -> None:
        super().__init__()
        self.replies = list(replies)
        self.temperatures: list[float] = []

    def _answer(self, messages):
        self.calls.append(list(messages))
        return self.replies[min(len(self.calls), len(self.replies)) - 1]

    async def complete(self, messages, *, temperature=0.2, **kwargs):
        self.temperatures.append(temperature)
        return await super().complete(messages, temperature=temperature, **kwargs)

    async def stream(self, messages, *, temperature=0.2, **kwargs):
        self.temperatures.append(temperature)
        async for piece in super().stream(messages, temperature=temperature, **kwargs):
            yield piece


def _relevant(score: float | None) -> list[ScoredChunk]:
    chunk = _chunk("The sensor uses a capacitive microphone.")
    chunk.rerank_score = score
    return [chunk]


def _rechecking(
    llm: object, *, relevance: float | None, threshold: float | None = 0.4
) -> ChatService:
    return ChatService(
        FakeRetrieval(_relevant(relevance)),
        ContextBuilder(counter=HeuristicTokenCounter()),
        llm,
        recheck_min_relevance=threshold,
    )


class TestRefusalRecheck:
    """A refusal that contradicts strong retrieval gets exactly one more look."""

    async def _stream(self, service: ChatService) -> tuple[str, object]:
        tokens: list[str] = []
        final = None
        async for kind, value in service.stream(_request()):
            if kind == "token":
                tokens.append(str(value))
            else:
                final = value
        return "".join(tokens), final

    async def test_a_refusal_despite_strong_evidence_is_regenerated(self) -> None:
        llm = Scripted(UNSUPPORTED_TOKEN, "It uses a capacitive microphone [S1].")
        result = await _rechecking(llm, relevance=0.56).answer(_request())

        assert not result.unsupported
        assert "capacitive microphone" in result.text
        assert [s.marker for s in result.sources] == ["S1"]
        assert len(llm.calls) == 2

    async def test_the_recheck_is_deterministic_and_says_why(self) -> None:
        llm = Scripted(UNSUPPORTED_TOKEN, "Answer [S1].")
        await _rechecking(llm, relevance=0.56).answer(_request())

        assert llm.temperatures == [0.2, 0.0]
        assert RECHECK_NOTE not in llm.calls[0][-1].content
        assert llm.calls[1][-1].content.endswith(RECHECK_NOTE)
        # The grounding rules are not relaxed for the second attempt.
        assert llm.calls[0][0].content == llm.calls[1][0].content

    async def test_weak_evidence_is_refused_without_a_second_call(self) -> None:
        # An off-topic question scored 0.03 against a real deck.
        llm = Scripted(UNSUPPORTED_TOKEN, "Invented answer [S1].")
        result = await _rechecking(llm, relevance=0.03).answer(_request())

        assert result.unsupported
        assert len(llm.calls) == 1

    async def test_no_threshold_means_no_recheck(self) -> None:
        llm = Scripted(UNSUPPORTED_TOKEN, "Answer [S1].")
        result = await _rechecking(llm, relevance=0.9, threshold=None).answer(
            _request()
        )

        assert result.unsupported
        assert len(llm.calls) == 1

    async def test_no_reranker_score_means_no_recheck(self) -> None:
        # Without a reranker there is no calibrated evidence to contradict.
        llm = Scripted(UNSUPPORTED_TOKEN, "Answer [S1].")
        result = await _rechecking(llm, relevance=None).answer(_request())

        assert result.unsupported
        assert len(llm.calls) == 1

    async def test_an_answer_is_never_rechecked(self) -> None:
        llm = Scripted("Answer [S1].", "A different answer [S1].")
        result = await _rechecking(llm, relevance=0.9).answer(_request())

        assert result.text == "Answer [S1]."
        assert len(llm.calls) == 1

    async def test_a_refusal_that_stands_is_still_a_refusal(self) -> None:
        llm = Scripted(UNSUPPORTED_TOKEN, UNSUPPORTED_TOKEN)
        result = await _rechecking(llm, relevance=0.56).answer(_request())

        assert result.unsupported
        assert result.sources == []
        assert len(llm.calls) == 2

    async def test_streaming_shows_only_the_rechecked_answer(self) -> None:
        llm = Scripted(UNSUPPORTED_TOKEN, "It uses a capacitive microphone [S1].")
        text, final = await self._stream(_rechecking(llm, relevance=0.56))

        assert text.strip() == "It uses a capacitive microphone [S1]."
        assert UNSUPPORTED_TOKEN not in text
        assert UNSUPPORTED_REPLY not in text
        assert final is not None
        assert not final.unsupported
        assert llm.temperatures == [0.2, 0.0]

    async def test_streaming_a_refusal_that_stands_explains_it_once(self) -> None:
        llm = Scripted(UNSUPPORTED_TOKEN, UNSUPPORTED_TOKEN)
        text, final = await self._stream(_rechecking(llm, relevance=0.56))

        assert text.strip() == UNSUPPORTED_REPLY
        assert final is not None
        assert final.unsupported
        assert len(llm.calls) == 2

    async def test_streaming_weak_evidence_is_not_rechecked(self) -> None:
        llm = Scripted(UNSUPPORTED_TOKEN, "Answer [S1].")
        _, final = await self._stream(_rechecking(llm, relevance=0.03))

        assert final is not None
        assert final.unsupported
        assert len(llm.calls) == 1


class TestRecheckThreshold:
    def test_only_a_calibrated_reranker_enables_the_recheck(self) -> None:
        from app.config import Settings
        from app.container import recheck_threshold
        from app.reranking.rerankers import (
            GeminiListwiseReranker,
            JinaReranker,
            NoOpReranker,
        )

        settings = Settings(CHAT_RECHECK_MIN_RELEVANCE=0.4)
        assert recheck_threshold(settings, JinaReranker("key")) == 0.4
        assert recheck_threshold(settings, GeminiListwiseReranker("key")) is None
        assert recheck_threshold(settings, NoOpReranker()) is None

    def test_only_a_calibrated_reranker_enables_the_off_topic_check(self) -> None:
        from app.config import Settings
        from app.container import off_topic_thresholds
        from app.reranking.rerankers import (
            GeminiListwiseReranker,
            JinaReranker,
            NoOpReranker,
        )

        settings = Settings(
            CHAT_OFF_TOPIC_MAX_SIMILARITY=0.55, CHAT_OFF_TOPIC_MAX_RELEVANCE=0.1
        )
        assert off_topic_thresholds(settings, JinaReranker("key")) == (0.55, 0.1)
        assert off_topic_thresholds(settings, GeminiListwiseReranker("key")) is None
        assert off_topic_thresholds(settings, NoOpReranker()) is None


def _scored(similarity: float | None, relevance: float | None) -> list[ScoredChunk]:
    chunk = _chunk("The sensor uses a capacitive microphone.")
    chunk.dense_score = similarity
    chunk.rerank_score = relevance
    return [chunk]


def _gated(
    llm: object,
    *,
    similarity: float | None,
    relevance: float | None,
    bounds: tuple[float, float] | None = (0.55, 0.10),
) -> ChatService:
    return ChatService(
        FakeRetrieval(_scored(similarity, relevance)),
        ContextBuilder(counter=HeuristicTokenCounter()),
        llm,
        off_topic_below=bounds,
    )


class TestEvidence:
    def test_takes_the_best_of_each_signal(self) -> None:
        low, high = _chunk("a", 0), _chunk("b", 1)
        low.dense_score, low.rerank_score = 0.7, 0.1
        high.dense_score, high.rerank_score = 0.5, 0.6

        evidence = Evidence.of([low, high])

        assert evidence.similarity == 0.7
        assert evidence.relevance == 0.6

    def test_a_missing_signal_stays_missing(self) -> None:
        evidence = Evidence.of(_scored(None, None))
        assert evidence.similarity is None
        assert evidence.relevance is None


class TestOffTopicQuestions:
    """Measured scores from a real deck set the cases below."""

    async def test_plainly_unrelated_questions_never_reach_the_model(self) -> None:
        # "Who won the FIFA World Cup in 2018?": 0.475 similarity, 0.033 relevance.
        llm = MockLLMProvider(reply="Argentina [S1].")
        result = await _gated(llm, similarity=0.475, relevance=0.033).answer(_request())

        assert result.unsupported
        assert result.text == NO_CONTEXT_REPLY
        assert result.sources == []
        assert result.retrieved == 1
        assert llm.calls == []

    async def test_a_terse_real_question_is_not_turned_away(self) -> None:
        # "buzzer role": similarity below the bound, relevance above it.
        llm = MockLLMProvider(reply="It beeps [S1].")
        result = await _gated(llm, similarity=0.548, relevance=0.136).answer(_request())

        assert not result.unsupported
        assert len(llm.calls) == 1

    async def test_high_similarity_alone_is_enough_to_ask(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        result = await _gated(llm, similarity=0.61, relevance=0.068).answer(_request())

        assert not result.unsupported
        assert len(llm.calls) == 1

    async def test_no_reranker_score_means_no_shortcut(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        await _gated(llm, similarity=0.40, relevance=None).answer(_request())
        assert len(llm.calls) == 1

    async def test_disabled_means_every_question_is_asked(self) -> None:
        llm = MockLLMProvider(reply="Answer [S1].")
        await _gated(llm, similarity=0.40, relevance=0.01, bounds=None).answer(
            _request()
        )
        assert len(llm.calls) == 1

    async def test_streaming_takes_the_same_shortcut(self) -> None:
        llm = MockLLMProvider(reply="Argentina [S1].")
        service = _gated(llm, similarity=0.475, relevance=0.033)

        tokens: list[str] = []
        final = None
        async for kind, value in service.stream(_request()):
            if kind == "token":
                tokens.append(str(value))
            else:
                final = value

        assert "".join(tokens) == NO_CONTEXT_REPLY
        assert final is not None
        assert final.unsupported
        assert llm.calls == []
