"""The chat endpoint.

Called by the Node API, never by a browser. The Node side owns the WebSocket to
the student and relays these events onto it, so this service stays free of
session and identity concerns.

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

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import CorrelationId, InternalAuth
from app.container import get_container
from app.core.models import ExplanationMode, RetrievalStrategy
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


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatInput,
    correlation_id: CorrelationId = None,
) -> StreamingResponse:
    container = get_container()
    identify(user_id=payload.user_id)
    request = _to_request(payload)

    async def events() -> AsyncIterator[str]:
        try:
            async for kind, value in container.chat.stream(request):
                if isinstance(value, str):
                    yield _event("token", {"delta": value})
                elif isinstance(value, ChatResult):
                    yield _event(
                        "sources",
                        {"sources": [s.model_dump() for s in value.sources]},
                    )
                    yield _event(
                        "done",
                        {
                            "unsupported": value.unsupported,
                            "rewritten_query": value.rewritten_query,
                            "retrieved": value.retrieved,
                            "dangling_citations": value.dangling_citations,
                            "timings": value.timings,
                        },
                    )
                else:
                    yield _event(kind, value)
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
