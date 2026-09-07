"""Puts a trace in flight for the requests worth tracing.

Written as raw ASGI rather than as a `BaseHTTPMiddleware` subclass on purpose.
`BaseHTTPMiddleware` runs the endpoint in a separate task, which gets a *copy*
of the context -- so a `ContextVar` set here would be invisible to spans
recorded inside a streaming response body. Raw ASGI keeps the whole request,
including the SSE body, in one coroutine and one context.

Only paths that do real work are traced. Health probes run every few seconds
and would bury the table they share with the answers.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.observability.trace import Trace, reset_current_trace, set_current_trace

# Longest prefix wins, so "/generate/test" is not swallowed by "/generate".
_KINDS: tuple[tuple[str, str], ...] = (
    ("/generate/flashcards", "flashcard_generation"),
    ("/generate/test", "test_generation"),
    ("/eval/gold-set", "eval"),
    ("/eval/retrieval", "eval"),
    ("/chat/stream", "chat"),
    ("/documents/delete", "ingestion"),
    ("/retrieve", "retrieval"),
    ("/ingest", "ingestion"),
    ("/grade", "grading"),
    ("/chat", "chat"),
)


def kind_for_path(path: str) -> str | None:
    for prefix, kind in _KINDS:
        if path.startswith(prefix):
            return kind
    return None


class TracingMiddleware:
    """ASGI middleware. Wraps every traced request in one trace.

    The sink is resolved per request rather than captured at construction: the
    middleware is installed while the app is being built, and the container
    that owns the sink is not created until startup runs.
    """

    def __init__(self, app: Any, resolve_tracer: Callable[[], Any]) -> None:
        self.app = app
        self._resolve = resolve_tracer

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        kind = kind_for_path(scope.get("path", ""))
        if kind is None:
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        tracer = self._resolve()
        if tracer is None:
            await self.app(scope, receive, send)
            return

        trace = Trace(
            tracer,
            kind=kind,
            # The Node backend supplies one so a span here joins the message it
            # explains. Absent that, one is minted -- an untied trace is still
            # better than none.
            correlation_id=headers.get("x-correlation-id") or str(uuid.uuid4()),
        )

        status = 500
        first_byte_ms: int | None = None
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()

        async def observed_send(message: dict) -> None:
            nonlocal status, first_byte_ms
            if message["type"] == "http.response.start":
                status = message["status"]
                first_byte_ms = int((time.perf_counter() - started) * 1000)
            await send(message)

        token = set_current_trace(trace)
        error: str | None = None
        try:
            await self.app(scope, receive, observed_send)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            await trace.record(
                "request",
                input={"path": scope.get("path"), "method": scope.get("method")},
                output={"status": status},
                metadata={
                    # For a streamed answer these differ by the whole
                    # generation, which is exactly the number worth having.
                    "first_byte_ms": first_byte_ms,
                    "total_ms": int((time.perf_counter() - started) * 1000),
                },
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=error,
                started_at=started_at,
            )
            reset_current_trace(token)
