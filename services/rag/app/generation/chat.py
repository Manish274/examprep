"""Grounded chat.

    condense -> retrieve -> rerank -> build context -> generate -> verify

Two stages exist purely to keep answers honest, and both are easy to omit
without anything appearing broken:

**Condensing.** "Why does that matter?" retrieves nothing useful on its own.
Rewriting it against the conversation into a standalone question is what makes
a follow-up work at all. It is skipped for the first message, where there is no
history, and for a follow-up that already names its subject, where the call
would be pure latency.

**Skipping the plainly off-topic.** Retrieval always returns something -- the
nearest chunks exist whatever the question. When both the embedding similarity
and the reranker's relevance say those chunks are unrelated, the question gets
the no-material reply without a model call, saving a request from a small
daily quota and the second or so it takes to be refused.

**Rechecking a refusal.** A refusal is sampled like any other output, and the
model sometimes declines a question whose answer is sitting in the top-ranked
source. When the reranker says the evidence is strong and the model still
refuses, the answer is regenerated once, deterministically, with a note that
the sources were judged relevant. The grounding rules are unchanged, so a
question the material genuinely does not cover is still refused -- it has just
cost one extra call, and only on that path.

**Questions about the whole.** "Summarise my notes" or "what will I be tested
on?" name no subject, so retrieval has nothing to match and the question would
be refused. These are recognised by wording and answered from an even spread
of the whole document instead (see `overview`).

**Verification.** After generation, every marker the model emitted is resolved
against the sources it was given. A model can write [S9] when three sources
exist; rendering that would show the student a citation to material that was
never in the context.

The refusal path is a first-class outcome, not an error. A student is better
served by "your notes do not cover this" than by a fluent answer assembled from
the model's own knowledge, which will not match what they are examined on.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.interfaces import Corpus, LLMProvider, Retrieval
from app.core.models import (
    Chunk,
    ExplanationMode,
    LLMMessage,
    RetrievalStrategy,
    ScoredChunk,
    Source,
)
from app.generation.context import BuiltContext, ContextBuilder, verify_citations
from app.generation.history import needs_context
from app.generation.history import prepare as prepare_history
from app.generation.overview import is_overview_question
from app.generation.prompts import (
    NO_CONTEXT_REPLY,
    OVERVIEW_RECHECK_NOTE,
    RECHECK_NOTE,
    UNSUPPORTED_REPLY,
    UNSUPPORTED_TOKEN,
    condense_prompt,
    system_prompt,
    user_prompt,
)
from app.generation.study import spread, usable_chunks
from app.observability.trace import current_trace, span

logger = logging.getLogger(__name__)


@dataclass
class ChatTurn:
    role: str
    content: str


@dataclass
class ChatRequest:
    question: str
    user_id: str
    mode: ExplanationMode = ExplanationMode.DETAILED
    document_ids: list[str] | None = None
    history: list[ChatTurn] = field(default_factory=list)
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_RERANK
    top_k: int = 8


@dataclass
class ChatResult:
    text: str
    sources: list[Source] = field(default_factory=list)
    unsupported: bool = False
    rewritten_query: str | None = None
    retrieved: int = 0
    dangling_citations: list[str] = field(default_factory=list)
    timings: dict[str, int] = field(default_factory=dict)


# What `ChatService.stream` yields: ("stage", {...}) as the answer moves between
# phases, ("token", text) as it arrives, and finally ("done", result).
ChatEvent = tuple[str, str | dict[str, Any] | ChatResult]


def is_refusal(text: str) -> bool:
    """Whether the model declined for lack of grounding.

    Checked on a prefix rather than by equality: models append a period, wrap
    the token in quotes, or add a sentence of explanation despite instructions.
    """
    stripped = text.strip().strip("\"'*`").upper()
    return stripped.startswith(UNSUPPORTED_TOKEN)


@dataclass
class _Attempt:
    """One pass of streamed generation, and what came of it."""

    buffer: list[str] = field(default_factory=list)
    refused: bool = False
    # Whether any text reached the student. A refusal can only be retried
    # while this is still false; otherwise the retry would append a second
    # answer to the first.
    forwarded: bool = False

    @property
    def raw(self) -> str:
        return "".join(self.buffer).strip()


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


@dataclass
class Evidence:
    """How strongly the retrieved material relates to the question.

    Two signals, because neither is enough alone. Dense similarity is always
    there but blurs on terse questions ("buzzer role" scored below "Hi");
    reranker relevance reads the passage but spreads answerable questions
    across almost its whole range. Where both are low, the question is not
    about this material.
    """

    relevance: float | None = None
    similarity: float | None = None
    # The context is a sample of the whole document, not a search result, so
    # there are no scores to judge it by -- and it is on topic by construction.
    whole_document: bool = False

    @classmethod
    def of(cls, chunks: Sequence[ScoredChunk]) -> Evidence:
        relevance = [c.rerank_score for c in chunks if c.rerank_score is not None]
        similarity = [c.dense_score for c in chunks if c.dense_score is not None]
        return cls(
            relevance=max(relevance) if relevance else None,
            similarity=max(similarity) if similarity else None,
        )


def _spread_by_document(chunks: Sequence[Chunk], limit: int) -> list[Chunk]:
    """An even sample across every document, each kept in reading order.

    The budget is shared out by size, so one long PDF does not crowd a short
    deck out of a summary of both.
    """
    by_document: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        by_document.setdefault(chunk.metadata.document_id, []).append(chunk)

    chosen: list[Chunk] = []
    remaining = limit
    groups = sorted(by_document.values(), key=len)
    for index, group in enumerate(groups):
        share = max(1, remaining // (len(groups) - index))
        picked = spread(group, share)
        chosen.extend(picked)
        remaining -= len(picked)
        if remaining <= 0:
            break
    return chosen


class ChatService:
    def __init__(
        self,
        retrieval: Retrieval,
        context_builder: ContextBuilder,
        llm: LLMProvider,
        *,
        utility_llm: LLMProvider | None = None,
        history_turns: int = 8,
        history_tokens: int = 1500,
        recheck_min_relevance: float | None = None,
        off_topic_below: tuple[float, float] | None = None,
        corpus: Corpus | None = None,
        overview_context_builder: ContextBuilder | None = None,
        overview_scan: int = 400,
    ) -> None:
        self._retrieval = retrieval
        self._context = context_builder
        self._llm = llm
        # History has its own budget, kept well below the context budget: the
        # conversation is the setting for an answer, the sources are its
        # evidence, and the setting must never evict the evidence.
        self._history_turns = history_turns
        self._history_tokens = history_tokens
        # A smaller model for query rewriting: high frequency, low difficulty,
        # and it keeps the main model's quota for answering.
        self._utility_llm = utility_llm or llm
        # None disables the recheck. It is only meaningful with a reranker
        # whose scores are calibrated relevance; a rank-derived score says
        # nothing about whether the material covers the question.
        self._recheck_min_relevance = recheck_min_relevance
        # (similarity, relevance). A question scoring below both is answered
        # as unsupported without a model call. None disables it; it needs a
        # calibrated reranker for the same reason the recheck does.
        self._off_topic_below = off_topic_below
        # Reads a document's chunks back without a query, for questions about
        # the whole of it. None sends those questions through retrieval.
        self._corpus = corpus
        self._overview_context = overview_context_builder or context_builder
        self._overview_scan = overview_scan

    def _worth_rechecking(self, evidence: Evidence) -> bool:
        if evidence.whole_document:
            # The context is the document itself; a refusal there is the model
            # declining to judge, not a sign the material is missing.
            return True
        return (
            self._recheck_min_relevance is not None
            and evidence.relevance is not None
            and evidence.relevance >= self._recheck_min_relevance
        )

    def _off_topic(self, evidence: Evidence) -> bool:
        if self._off_topic_below is None or evidence.whole_document:
            return False
        if evidence.similarity is None or evidence.relevance is None:
            return False
        max_similarity, max_relevance = self._off_topic_below
        return (
            evidence.similarity < max_similarity and evidence.relevance < max_relevance
        )

    async def condense(self, request: ChatRequest) -> str:
        """Rewrites a follow-up into a standalone question."""
        if not request.history:
            return request.question

        history = [(t.role, t.content) for t in request.history]
        async with span(
            "condense", question=request.question, turns=len(history)
        ) as observed:
            if not needs_context(request.question):
                observed.output(rewritten=request.question, skipped=True)
                return request.question

            try:
                response = await self._utility_llm.complete(
                    [
                        LLMMessage(
                            role="user",
                            content=condense_prompt(request.question, history),
                        )
                    ],
                    temperature=0.0,
                    max_tokens=2000,
                )
            except Exception as exc:
                logger.warning(
                    "condensing failed (%s); using the question as asked", exc
                )
                # Recorded rather than raised: the fallback is silent to the
                # student, so without this the trace would show a question that
                # was never actually rewritten and no reason why.
                observed.output(rewritten=request.question, fell_back=True)
                observed.meta(reason=str(exc)[:200])
                return request.question

            rewritten = response.text.strip().strip('"')
            # A model that returns nothing, or an essay, has misunderstood the
            # task -- the original question is a safer retrieval query.
            if not rewritten or len(rewritten) > 500:
                observed.output(rewritten=request.question, fell_back=True)
                observed.meta(reason="empty or over-long rewrite")
                return request.question

            observed.output(rewritten=rewritten, fell_back=False)
            return rewritten

    async def gather(
        self, request: ChatRequest
    ) -> tuple[BuiltContext, str, dict[str, int], Evidence]:
        """Everything up to generation: condense, retrieve, build context.

        Also returns the evidence scores, which decide whether the question is
        about this material at all and whether a refusal is worth a second
        look.
        """
        timings: dict[str, int] = {}

        # Checked before condensing: such a question is already complete, and
        # a rewrite would only add the conversation's subject words and send
        # it to a search that cannot answer it.
        if self._corpus is not None and is_overview_question(request.question):
            timings["condense_ms"] = 0
            return await self._gather_whole(
                self._corpus, request, request.question, timings
            )

        started = time.perf_counter()
        query = await self.condense(request)
        timings["condense_ms"] = int((time.perf_counter() - started) * 1000)

        if self._corpus is not None and is_overview_question(query):
            return await self._gather_whole(self._corpus, request, query, timings)

        started = time.perf_counter()
        async with span(
            "retrieve",
            query=query,
            strategy=request.strategy.value,
            top_k=request.top_k,
        ) as observed:
            chunks = await self._retrieval.search(
                query,
                user_id=request.user_id,
                strategy=request.strategy,
                document_ids=request.document_ids,
                top_k=request.top_k,
            )
            evidence = Evidence.of(chunks)
            # The ids and scores are the whole point of a retrieval trace: an
            # answer that cited the wrong passage is diagnosed by looking at
            # what was ranked above the right one.
            observed.output(
                returned=len(chunks),
                chunk_ids=[c.chunk.id for c in chunks[:10]],
                scores=[round(c.score, 4) for c in chunks[:10]],
                similarity=_rounded(evidence.similarity),
                relevance=_rounded(evidence.relevance),
                off_topic=self._off_topic(evidence),
            )
        timings["retrieve_ms"] = int((time.perf_counter() - started) * 1000)

        async with span("build_context") as observed:
            context = self._context.build(chunks)
            observed.output(
                sources=len(context.sources),
                context_tokens=context.token_count,
                dropped=context.dropped,
            )

        timings["retrieved"] = len(chunks)
        return context, query, timings, evidence

    async def _gather_whole(
        self,
        corpus: Corpus,
        request: ChatRequest,
        query: str,
        timings: dict[str, int],
    ) -> tuple[BuiltContext, str, dict[str, int], Evidence]:
        """Context for a question about the material as a whole: an even
        spread across every document in scope, in reading order."""
        started = time.perf_counter()
        builder = self._overview_context
        async with span(
            "sample_whole", query=query, scan=self._overview_scan
        ) as observed:
            chunks = await corpus.sample_chunks(
                user_id=request.user_id,
                document_ids=request.document_ids,
                limit=self._overview_scan,
            )
            # Scaffolding -- references, contents, credits -- says nothing
            # about what is examinable. Kept only if it is all there is.
            candidates = usable_chunks(chunks) or list(chunks)
            chosen = _spread_by_document(candidates, builder.max_chunks)
            context = builder.build([ScoredChunk(chunk=c, score=0.0) for c in chosen])
            observed.output(
                scanned=len(chunks),
                usable=len(candidates),
                sources=len(context.sources),
                context_tokens=context.token_count,
                dropped=context.dropped,
            )
        timings["retrieve_ms"] = int((time.perf_counter() - started) * 1000)
        timings["retrieved"] = len(chosen)
        return context, query, timings, Evidence(whole_document=True)

    def _no_answer(
        self, query: str, timings: dict[str, int], evidence: Evidence
    ) -> ChatResult:
        """The reply when the model is not asked at all.

        Either nothing was retrieved, or what was retrieved is plainly about
        something else. Asking the model anyway costs a call from a small daily
        quota to receive a refusal -- or invites exactly the ungrounded answer
        this design refuses to give.
        """
        return ChatResult(
            text=NO_CONTEXT_REPLY,
            unsupported=True,
            rewritten_query=query,
            retrieved=timings.get("retrieved", 0),
            timings=timings,
        )

    def _messages(
        self,
        request: ChatRequest,
        context: BuiltContext,
        *,
        recheck: bool = False,
        overview: bool = False,
    ) -> list[LLMMessage]:
        """System prompt, then the conversation, then this question.

        The prior turns go in as real turns rather than flattened into the
        prompt text: models follow a conversation they are shown as one, and it
        keeps the sources adjacent to the question they are evidence for.

        The question the model is asked is the one the student actually typed,
        not the condensed rewrite. The rewrite exists to retrieve well -- it is
        a search query, and answering it instead would drop the phrasing and
        emphasis the student chose.
        """
        messages = [
            LLMMessage(
                role="system",
                content=system_prompt(request.mode, overview=overview),
            )
        ]

        for turn in prepare_history(
            [(t.role, t.content) for t in request.history],
            max_turns=self._history_turns,
            max_tokens=self._history_tokens,
        ):
            messages.append(LLMMessage(role=turn.role, content=turn.content))

        content = user_prompt(request.question, context.text)
        if recheck:
            note = OVERVIEW_RECHECK_NOTE if overview else RECHECK_NOTE
            content = f"{content}\n\n{note}"
        messages.append(LLMMessage(role="user", content=content))
        return messages

    def _finish(
        self,
        raw: str,
        context: BuiltContext,
        query: str,
        timings: dict[str, int],
    ) -> ChatResult:
        """Turns raw model output into a verified, citable answer."""
        if is_refusal(raw):
            return ChatResult(
                text=UNSUPPORTED_REPLY,
                sources=[],
                unsupported=True,
                rewritten_query=query,
                retrieved=timings.get("retrieved", 0),
                timings=timings,
            )

        resolved, dangling = verify_citations(raw, context.sources)
        return ChatResult(
            text=raw,
            # Only the sources actually cited are returned. Listing every
            # retrieved chunk as a "source" would imply the answer rests on
            # material it never used.
            sources=resolved,
            unsupported=False,
            rewritten_query=query,
            retrieved=timings.get("retrieved", 0),
            dangling_citations=sorted(dangling),
            timings=timings,
        )

    async def answer(self, request: ChatRequest) -> ChatResult:
        """The whole answer at once: the streamed path, collected.

        One generation path, not two -- whatever a caller of this sees is what
        the student saw arrive over the stream.
        """
        async for _, value in self.stream(request):
            if isinstance(value, ChatResult):
                return value
        raise RuntimeError("the chat stream ended without a result")

    async def _record_generation(
        self,
        request: ChatRequest,
        context: BuiltContext,
        started_at: datetime,
        timings: dict[str, int],
        **output: object,
    ) -> None:
        """Records the generate span of a streamed answer.

        Recorded by hand rather than with `span`, because that would have to
        wrap a loop that yields to the caller -- and a client disconnecting
        mid-answer would then unwind the context manager during generator
        cleanup, where awaiting is a good deal more delicate than it looks.
        """
        trace = current_trace()
        if trace is None:
            return
        await trace.record(
            "generate",
            input={
                "mode": request.mode.value,
                "context_tokens": context.token_count,
                "sources": len(context.sources),
                "streamed": True,
            },
            output=dict(output),
            duration_ms=timings.get("generate_ms"),
            started_at=started_at,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        """Yields ("stage", dict) as the answer moves between phases,
        ("token", str) as text arrives, then ("done", ChatResult).

        The stages exist because most of the wait comes before the first
        token: the rewrite, retrieval and reranking take a couple of seconds
        with nothing to show. Saying which of them is running is the only
        honest progress there is.

        Tokens are buffered as well as forwarded so the final result can be
        verified against the sources: citations cannot be checked until the
        answer is complete.
        """
        yield "stage", {"stage": "searching"}
        context, query, timings, evidence = await self.gather(request)

        if context.is_empty or self._off_topic(evidence):
            yield "token", NO_CONTEXT_REPLY
            yield "done", self._no_answer(query, timings, evidence)
            return

        yield "stage", {"stage": "writing", "passages": len(context.sources)}
        started = time.perf_counter()
        started_at = datetime.now(timezone.utc)

        attempt = _Attempt()
        async for text in self._stream_attempt(
            self._messages(request, context, overview=evidence.whole_document),
            0.2,
            attempt,
        ):
            yield "token", text

        # Nothing has reached the student yet -- the opening of a refusal is
        # held back -- so a second attempt streams as though it were the first.
        rechecked = False
        if (
            attempt.refused
            and not attempt.forwarded
            and self._worth_rechecking(evidence)
        ):
            rechecked = True
            yield "stage", {"stage": "rechecking"}
            attempt = _Attempt()
            async for text in self._stream_attempt(
                self._messages(
                    request,
                    context,
                    recheck=True,
                    overview=evidence.whole_document,
                ),
                0.0,
                attempt,
            ):
                yield "token", text

        timings["generate_ms"] = int((time.perf_counter() - started) * 1000)
        raw = attempt.raw

        if attempt.refused:
            await self._record_generation(
                request,
                context,
                started_at,
                timings,
                unsupported=True,
                rechecked=rechecked,
            )
            yield "token", UNSUPPORTED_REPLY
            yield (
                "done",
                ChatResult(
                    text=UNSUPPORTED_REPLY,
                    unsupported=True,
                    rewritten_query=query,
                    retrieved=timings.get("retrieved", 0),
                    timings=timings,
                ),
            )
            return

        result = self._finish(raw, context, query, timings)
        await self._record_generation(
            request,
            context,
            started_at,
            timings,
            unsupported=result.unsupported,
            cited=[s.marker for s in result.sources],
            dangling=result.dangling_citations,
            characters=len(result.text),
            rechecked=rechecked,
        )
        yield "done", result

    async def _stream_attempt(
        self, messages: list[LLMMessage], temperature: float, attempt: _Attempt
    ) -> AsyncIterator[str]:
        """Streams one generation, holding back the opening until it is clear
        whether the model is refusing.

        Streaming "NOT_SUPPORTED" to the student and then replacing it would be
        worse than a brief pause.
        """
        held: list[str] = []

        async for delta in self._llm.stream(
            messages, temperature=temperature, max_tokens=4000
        ):
            attempt.buffer.append(delta)

            if attempt.refused:
                continue

            if len(held) < 4 and len("".join(held)) < len(UNSUPPORTED_TOKEN) + 4:
                held.append(delta)
                if is_refusal("".join(held)):
                    attempt.refused = True
                    continue
                if len("".join(held)) < len(UNSUPPORTED_TOKEN):
                    continue
                attempt.forwarded = True
                yield "".join(held)
                held.clear()
                continue

            attempt.forwarded = True
            yield delta

        if held and not attempt.refused:
            attempt.forwarded = True
            yield "".join(held)

        if is_refusal(attempt.raw):
            attempt.refused = True
