"""Grounded chat.

    condense -> retrieve -> rerank -> build context -> generate -> verify

Two stages exist purely to keep answers honest, and both are easy to omit
without anything appearing broken:

**Condensing.** "Why does that matter?" retrieves nothing useful on its own.
Rewriting it against the conversation into a standalone question is what makes
a follow-up work at all. It is skipped for the first message, where there is no
history and the call would be pure latency.

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

from app.core.models import (
    ExplanationMode,
    LLMMessage,
    RetrievalStrategy,
    ScoredChunk,
    Source,
)
from app.generation.context import BuiltContext, verify_citations
from app.generation.history import prepare as prepare_history
from app.generation.prompts import (
    NO_CONTEXT_REPLY,
    UNSUPPORTED_REPLY,
    UNSUPPORTED_TOKEN,
    condense_prompt,
    system_prompt,
    user_prompt,
)
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
    prompt_tokens: int = 0
    completion_tokens: int = 0


def is_refusal(text: str) -> bool:
    """Whether the model declined for lack of grounding.

    Checked on a prefix rather than by equality: models append a period, wrap
    the token in quotes, or add a sentence of explanation despite instructions.
    """
    stripped = text.strip().strip("\"'*`").upper()
    return stripped.startswith(UNSUPPORTED_TOKEN)


class ChatService:
    def __init__(
        self,
        retrieval: object,
        context_builder: object,
        llm: object,
        *,
        utility_llm: object | None = None,
        history_turns: int = 8,
        history_tokens: int = 1500,
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

    async def condense(self, request: ChatRequest) -> str:
        """Rewrites a follow-up into a standalone question."""
        if not request.history:
            return request.question

        history = [(t.role, t.content) for t in request.history]
        async with span(
            "condense", question=request.question, turns=len(history)
        ) as observed:
            try:
                response = await self._utility_llm.complete(  # type: ignore[attr-defined]
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
    ) -> tuple[BuiltContext, str, dict[str, int]]:
        """Everything up to generation: condense, retrieve, build context."""
        timings: dict[str, int] = {}

        started = time.perf_counter()
        query = await self.condense(request)
        timings["condense_ms"] = int((time.perf_counter() - started) * 1000)

        started = time.perf_counter()
        async with span(
            "retrieve",
            query=query,
            strategy=request.strategy.value,
            top_k=request.top_k,
        ) as observed:
            chunks: Sequence[ScoredChunk] = await self._retrieval.search(  # type: ignore[attr-defined]
                query,
                user_id=request.user_id,
                strategy=request.strategy,
                document_ids=request.document_ids,
                top_k=request.top_k,
            )
            # The ids and scores are the whole point of a retrieval trace: an
            # answer that cited the wrong passage is diagnosed by looking at
            # what was ranked above the right one.
            observed.output(
                returned=len(chunks),
                chunk_ids=[c.chunk.id for c in chunks[:10]],
                scores=[round(c.score, 4) for c in chunks[:10]],
            )
        timings["retrieve_ms"] = int((time.perf_counter() - started) * 1000)

        async with span("build_context") as observed:
            context = self._context.build(chunks)  # type: ignore[attr-defined]
            observed.output(
                sources=len(context.sources),
                context_tokens=context.token_count,
                dropped=context.dropped,
            )

        timings["retrieved"] = len(chunks)
        return context, query, timings

    def _messages(
        self, request: ChatRequest, context: BuiltContext
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
        messages = [LLMMessage(role="system", content=system_prompt(request.mode))]

        for turn in prepare_history(
            [(t.role, t.content) for t in request.history],
            max_turns=self._history_turns,
            max_tokens=self._history_tokens,
        ):
            messages.append(LLMMessage(role=turn.role, content=turn.content))

        messages.append(
            LLMMessage(
                role="user",
                content=user_prompt(request.question, context.text),
            )
        )
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
        context, query, timings = await self.gather(request)

        if context.is_empty:
            # Nothing retrieved. Asking the model anyway invites exactly the
            # ungrounded answer this design refuses to give.
            return ChatResult(
                text=NO_CONTEXT_REPLY,
                unsupported=True,
                rewritten_query=query,
                retrieved=0,
                timings=timings,
            )

        started = time.perf_counter()
        async with span(
            "generate",
            mode=request.mode.value,
            context_tokens=context.token_count,
            sources=len(context.sources),
        ) as observed:
            response = await self._llm.complete(  # type: ignore[attr-defined]
                self._messages(request, context), temperature=0.2, max_tokens=4000
            )
            timings["generate_ms"] = int((time.perf_counter() - started) * 1000)

            result = self._finish(response.text, context, query, timings)
            result.prompt_tokens = response.usage.prompt_tokens
            result.completion_tokens = response.usage.completion_tokens
            observed.output(
                unsupported=result.unsupported,
                cited=[s.marker for s in result.sources],
                dangling=result.dangling_citations,
                characters=len(result.text),
            )
            observed.meta(
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
            )
        return result

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

    async def stream(
        self, request: ChatRequest
    ) -> AsyncIterator[tuple[str, object]]:
        """Yields ("token", str) as text arrives, then ("done", ChatResult).

        Tokens are buffered as well as forwarded so the final result can be
        verified against the sources: citations cannot be checked until the
        answer is complete.
        """
        context, query, timings = await self.gather(request)

        if context.is_empty:
            yield "token", NO_CONTEXT_REPLY
            yield "done", ChatResult(
                text=NO_CONTEXT_REPLY,
                unsupported=True,
                rewritten_query=query,
                retrieved=0,
                timings=timings,
            )
            return

        started = time.perf_counter()
        started_at = datetime.now(timezone.utc)
        buffer: list[str] = []
        held: list[str] = []
        refused = False

        async for delta in self._llm.stream(  # type: ignore[attr-defined]
            self._messages(request, context), temperature=0.2, max_tokens=4000
        ):
            buffer.append(delta)

            if refused:
                continue

            # Hold the opening tokens back until it is clear whether this is a
            # refusal. Streaming "NOT_SUPPORTED" to the student and then
            # replacing it would be worse than a brief pause.
            if len(held) < 4 and len("".join(held)) < len(UNSUPPORTED_TOKEN) + 4:
                held.append(delta)
                if is_refusal("".join(held)):
                    refused = True
                    continue
                if len("".join(held)) < len(UNSUPPORTED_TOKEN):
                    continue
                yield "token", "".join(held)
                held.clear()
                continue

            yield "token", delta

        if held and not refused:
            yield "token", "".join(held)

        timings["generate_ms"] = int((time.perf_counter() - started) * 1000)
        raw = "".join(buffer).strip()

        if refused or is_refusal(raw):
            await self._record_generation(
                request, context, started_at, timings, unsupported=True
            )
            yield "token", UNSUPPORTED_REPLY
            yield "done", ChatResult(
                text=UNSUPPORTED_REPLY,
                unsupported=True,
                rewritten_query=query,
                retrieved=timings.get("retrieved", 0),
                timings=timings,
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
        )
        yield "done", result
