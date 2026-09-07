"""The span API the pipeline actually calls.

Stages are instrumented without changing their signatures. A retriever three
layers down does not take a `tracer` parameter it would otherwise have no use
for; it asks for the trace in flight:

    async with span("retrieve", query=q) as s:
        results = await search(q)
        s.output(returned=len(results))

If no trace is in flight -- a unit test, a script, a stage called directly --
`span` is a no-op context manager. That is what makes it safe to instrument
code that has callers outside a request.

The trace is carried in a `ContextVar` set by ASGI middleware, which runs in
the same coroutine as the endpoint *and* as the streaming response body. Doing
it in the middleware rather than per endpoint is what makes a span recorded
mid-stream land on the right trace.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_current: ContextVar[Trace | None] = ContextVar("current_trace", default=None)


class SpanHandle:
    """Handed to the body of a `span` block so it can attach what it learned.

    Output is collected rather than returned because the interesting fields are
    only known part way through -- how many chunks came back, whether the model
    refused, what the reranker moved.
    """

    __slots__ = ("_metadata", "_output")

    def __init__(self) -> None:
        self._output: dict[str, Any] = {}
        self._metadata: dict[str, Any] = {}

    def output(self, **fields: Any) -> None:
        self._output.update(fields)

    def meta(self, **fields: Any) -> None:
        self._metadata.update(fields)


class Trace:
    """One logical operation: a question answered, a document ingested, a test
    generated. Spans hang off it and share its correlation id."""

    def __init__(
        self,
        tracer: Any,
        *,
        kind: str,
        correlation_id: str,
        user_id: str | None = None,
    ) -> None:
        self._tracer = tracer
        self.kind = kind
        self.correlation_id = correlation_id
        self.user_id = user_id
        # Set late by the endpoint, which is the first place the body has been
        # parsed and the user is known.
        self.enabled = True

    def identify(self, *, user_id: str | None = None, kind: str | None = None) -> None:
        if user_id:
            self.user_id = user_id
        if kind:
            self.kind = kind

    async def record(
        self,
        name: str,
        *,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            await self._tracer.record(
                kind=self.kind,
                name=name,
                correlation_id=self.correlation_id,
                user_id=self.user_id,
                input=input,
                output=output,
                metadata=metadata,
                duration_ms=duration_ms,
                error=error,
                started_at=started_at,
                ended_at=datetime.now(timezone.utc),
            )
        except Exception as exc:
            # The guarantee is absolute: an unwritable trace must not surface
            # as a failed answer.
            logger.warning("trace record %r failed: %s", name, exc)


def current_trace() -> Trace | None:
    return _current.get()


def set_current_trace(trace: Trace | None) -> Any:
    """Returns the reset token, so the caller can restore the previous value."""
    return _current.set(trace)


def reset_current_trace(token: Any) -> None:
    _current.reset(token)


def identify(*, user_id: str | None = None, kind: str | None = None) -> None:
    """Attaches identity to the trace in flight, if there is one."""
    trace = _current.get()
    if trace is not None:
        trace.identify(user_id=user_id, kind=kind)


@asynccontextmanager
async def span(name: str, **input: Any) -> AsyncIterator[SpanHandle]:
    """Times a stage and records it, whether or not it succeeded.

    A stage that raised is the single most useful thing a trace can hold, so
    the exception is recorded and then re-raised untouched -- tracing observes,
    it never swallows.
    """
    handle = SpanHandle()
    trace = _current.get()
    if trace is None:
        yield handle
        return

    started_at = datetime.now(timezone.utc)
    started = time.perf_counter()
    error: str | None = None
    try:
        yield handle
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        await trace.record(
            name,
            input=input or None,
            output=handle._output or None,
            metadata=handle._metadata or None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=error,
            started_at=started_at,
        )
