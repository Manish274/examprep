"""Chat endpoints.

Called by the Node worker or API, never by a browser. The Node side owns the
WebSocket to the student and relays these events onto it, so this service stays
free of session and identity concerns.

Streaming is Server-Sent Events rather than a WebSocket because the traffic is
one-directional and short-lived: the request carries everything, and the
response is a sequence of deltas ending in a summary. SSE survives proxies that
mangle WebSocket upgrades and needs no framing of its own.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import CorrelationId, InternalAuth, SettingsDep
from app.container import get_container
from app.core.models import ExplanationMode, RetrievalStrategy, Source
from app.generation.chat import ChatRequest, ChatResult, ChatTurn
from app.observability.trace import identify

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"], dependencies=[InternalAuth])


class TurnInput(BaseModel):
    role: str
    content: str


class ChatInput(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    user_id: str
    mode: ExplanationMode = ExplanationMode.DETAILED
    document_ids: list[str] | None = None
    history: list[TurnInput] = Field(default_factory=list)
    strategy: RetrievalStrategy = RetrievalStrategy.HYBRID_RERANK
    top_k: Annotated[int, Field(ge=1, le=25)] = 8


class ChatResponse(BaseModel):
    text: str
    sources: list[Source]
    unsupported: bool
    rewritten_query: str | None
    retrieved: int
    dangling_citations: list[str]
    timings: dict[str, int]
    prompt_tokens: int
    completion_tokens: int


def _to_request(payload: ChatInput) -> ChatRequest:
    return ChatRequest(
        question=payload.question,
        user_id=payload.user_id,
        mode=payload.mode,
        document_ids=payload.document_ids,
        history=[ChatTurn(role=t.role, content=t.content) for t in payload.history],
        strategy=payload.strategy,
        top_k=payload.top_k,
    )


def _to_response(result: ChatResult) -> ChatResponse:
    return ChatResponse(
        text=result.text,
        sources=result.sources,
        unsupported=result.unsupported,
        rewritten_query=result.rewritten_query,
        retrieved=result.retrieved,
        dangling_citations=result.dangling_citations,
        timings=result.timings,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatInput,
    settings: SettingsDep,
    correlation_id: CorrelationId = None,
) -> ChatResponse:
    """Non-streaming answer. Used by tests and by callers that want the whole
    result in one piece."""
    container = get_container()
    if container.chat is None:
        raise HTTPException(503, "Chat is not configured on this service")

    identify(user_id=payload.user_id)
    try:
        result = await container.chat.answer(_to_request(payload))
    except Exception as exc:
        logger.exception("chat failed (correlation_id=%s)", correlation_id)
        raise HTTPException(502, f"Chat failed: {exc}") from exc

    logger.info(
        "chat answered in %sms (unsupported=%s, sources=%s)",
        sum(result.timings.get(k, 0) for k in ("retrieve_ms", "generate_ms")),
        result.unsupported,
        len(result.sources),
    )
    return _to_response(result)


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatInput,
    settings: SettingsDep,
    correlation_id: CorrelationId = None,
) -> StreamingResponse:
    container = get_container()
    if container.chat is None:
        raise HTTPException(503, "Chat is not configured on this service")

    identify(user_id=payload.user_id)
    request = _to_request(payload)

    async def events() -> AsyncIterator[str]:
        try:
            async for kind, value in container.chat.stream(request):
                if kind == "token":
                    yield _event("token", {"delta": value})
                    continue

                result: ChatResult = value  # type: ignore[assignment]
                yield _event(
                    "sources",
                    {"sources": [s.model_dump() for s in result.sources]},
                )
                yield _event(
                    "done",
                    {
                        "unsupported": result.unsupported,
                        "rewritten_query": result.rewritten_query,
                        "retrieved": result.retrieved,
                        "dangling_citations": result.dangling_citations,
                        "timings": result.timings,
                    },
                )
        except Exception as exc:
            # The response has already begun, so the failure has to travel as
            # an event rather than as a status code.
            logger.exception("chat stream failed (correlation_id=%s)", correlation_id)
            yield _event("error", {"message": str(exc)[:300]})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            # Tells nginx not to buffer, which would defeat streaming entirely.
            "x-accel-buffering": "no",
        },
    )
