"""Where trace records go.

Four sinks behind one interface. The choice is configuration, not code:
`TRACER=postgres`, `TRACER=langfuse`, `TRACER=postgres,langfuse`, `TRACER=none`.

Two rules hold for every implementation here, and they are the reason this
module is more careful than a logger would be:

**Tracing must never fail a request.** A student's answer does not depend on
whether the trace of it was written. Every failure path swallows, counts and
logs -- and the count is itself reported, so a sink that is quietly dropping
everything is visible rather than merely silent.

**Tracing must never slow a request down.** Writes are queued and drained by a
background task, so the request path pays a `put_nowait` and nothing else. The
queue is bounded: under sustained pressure, dropping trace rows is the correct
sacrifice, and dropping the *newest* keeps the beginning of an incident, which
is the part worth having.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx

from app.core.registry import tracers

logger = logging.getLogger(__name__)

# Namespace for turning a non-UUID correlation id (an eval run's name, a
# script's label) into the uuid the traces table requires.
_CORRELATION_NAMESPACE = uuid.UUID("2c9f5b41-6d0a-4c8e-9f37-1b5a4e2d8c60")

# Deep enough to absorb a burst of ingestion spans, shallow enough that a dead
# database cannot grow into a memory leak.
_QUEUE_SIZE = 2000
_BATCH_SIZE = 50


def as_uuid(value: str | None) -> str | None:
    """Coerces an arbitrary correlation id into a UUID string.

    Callers outside the Node backend -- eval scripts, ad-hoc curl -- have no
    reason to invent one, and rejecting their traces because the id was not a
    UUID would lose exactly the runs worth keeping. Hashing is stable, so the
    same label groups the same way on every run.
    """
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return str(uuid.uuid5(_CORRELATION_NAMESPACE, value))


class TraceRecord:
    """One span, flattened. Deliberately plain: sinks serialise it differently
    and neither should have to understand the other's format."""

    __slots__ = (
        "correlation_id",
        "duration_ms",
        "ended_at",
        "error",
        "input",
        "kind",
        "metadata",
        "name",
        "output",
        "started_at",
        "user_id",
    )

    def __init__(
        self,
        *,
        kind: str,
        name: str,
        correlation_id: str,
        user_id: str | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        self.kind = kind
        self.name = name
        self.correlation_id = correlation_id
        self.user_id = user_id
        self.input = input
        self.output = output
        self.metadata = metadata
        self.duration_ms = duration_ms
        self.error = error
        self.started_at = started_at or datetime.now(timezone.utc)
        self.ended_at = ended_at or self.started_at


class NoOpTracer:
    """Records nothing. The honest default when no sink is configured -- a
    tracer that pretends to work is worse than one that says it does not."""

    name = "noop"

    async def record(self, **kwargs: Any) -> None:
        return None

    async def close(self) -> None:
        return None

    def stats(self) -> dict[str, int]:
        return {}


class InMemoryTracer:
    """Keeps records in a list. Used by tests, and by anything that wants to
    assert on what the pipeline reported rather than on what it returned."""

    name = "memory"

    def __init__(self, limit: int = 1000) -> None:
        self.records: list[TraceRecord] = []
        self._limit = limit

    async def record(self, **kwargs: Any) -> None:
        self.records.append(TraceRecord(**kwargs))
        if len(self.records) > self._limit:
            del self.records[0 : len(self.records) - self._limit]

    async def close(self) -> None:
        return None

    def stats(self) -> dict[str, int]:
        return {"recorded": len(self.records)}

    def named(self, name: str) -> list[TraceRecord]:
        return [r for r in self.records if r.name == name]


class _QueuedSink:
    """Shared machinery: a bounded queue drained by one background task.

    Subclasses only implement `_flush`. Keeping the queueing in one place means
    the "never block, never fail" guarantee is written once rather than trusted
    to hold in two implementations.
    """

    name = "queued"

    def __init__(self, queue_size: int = _QUEUE_SIZE) -> None:
        self._queue: asyncio.Queue[TraceRecord | None] = asyncio.Queue(queue_size)
        self._worker: asyncio.Task[None] | None = None
        self._closed = False
        self.dropped = 0
        self.written = 0
        self.failed = 0

    async def record(self, **kwargs: Any) -> None:
        if self._closed:
            return
        self._ensure_worker()
        try:
            self._queue.put_nowait(TraceRecord(**kwargs))
        except asyncio.QueueFull:
            self.dropped += 1
            # One line per hundred: a saturated queue is worth knowing about,
            # and worth not turning into its own flood.
            if self.dropped % 100 == 1:
                logger.warning(
                    "%s trace queue full; %s records dropped", self.name, self.dropped
                )

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            try:
                self._worker = asyncio.get_running_loop().create_task(self._drain())
            except RuntimeError:
                # No loop -- a synchronous caller. Nothing to record onto.
                self._worker = None

    async def _drain(self) -> None:
        while True:
            first = await self._queue.get()
            if first is None:
                return

            batch = [first]
            # Opportunistic batching: whatever else is already waiting goes in
            # the same round trip.
            stopping = False
            while len(batch) < _BATCH_SIZE and not self._queue.empty():
                item = self._queue.get_nowait()
                if item is None:
                    # Close was requested. Flush what is in hand first --
                    # discarding it would lose the shutdown itself.
                    stopping = True
                    break
                batch.append(item)

            await self._safe_flush(batch)
            if stopping:
                return

    async def _safe_flush(self, batch: list[TraceRecord]) -> None:
        try:
            await self._flush(batch)
            self.written += len(batch)
        except Exception as exc:
            self.failed += len(batch)
            logger.warning(
                "%s trace flush failed (%s records): %s",
                self.name,
                len(batch),
                exc,
            )

    async def _flush(self, batch: Sequence[TraceRecord]) -> None:
        raise NotImplementedError

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._worker is None or self._worker.done():
            return
        try:
            self._queue.put_nowait(None)
        except asyncio.QueueFull:
            self._worker.cancel()
            return
        try:
            # Bounded: shutdown must not hang on an unreachable sink.
            await asyncio.wait_for(self._worker, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._worker.cancel()

    def stats(self) -> dict[str, int]:
        return {
            "written": self.written,
            "dropped": self.dropped,
            "failed": self.failed,
            "queued": self._queue.qsize(),
        }


class PostgresTracer(_QueuedSink):
    """Writes to the application's own `traces` table.

    The local sink is the one that always works: no third-party account, no
    egress, and the trace sits in the same database as the message it explains,
    so a single join answers "which passages produced this answer".
    """

    name = "postgres"

    def __init__(self, dsn: str, queue_size: int = _QUEUE_SIZE) -> None:
        super().__init__(queue_size)
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool | None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3, command_timeout=15
            )
        return self._pool

    async def _flush(self, batch: Sequence[TraceRecord]) -> None:
        pool = await self._get_pool()
        if pool is None:
            return

        rows = [
            (
                _valid_uuid(record.user_id),
                record.kind[:64],
                as_uuid(record.correlation_id),
                record.name[:128],
                _dump(record.input),
                _dump(record.output),
                _dump(record.metadata),
                record.duration_ms,
                record.error[:4000] if record.error else None,
                record.started_at,
            )
            for record in batch
        ]
        await pool.executemany(
            "INSERT INTO traces "
            "(user_id, kind, correlation_id, name, input, output, metadata, "
            " duration_ms, error, created_at) "
            "VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8, $9, $10)",
            rows,
        )

    async def close(self) -> None:
        await super().close()
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def _valid_uuid(value: str | None) -> uuid.UUID | None:
    """A user id that is not a real UUID would violate the foreign key and take
    the whole batch down with it. Dropping the attribution keeps the span."""
    if not value:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _dump(value: dict[str, Any] | None) -> str | None:
    if not value:
        return None
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return json.dumps({"unserialisable": str(type(value))})


class LangfuseTracer(_QueuedSink):
    """Ships the same records to Langfuse.

    Speaks the public ingestion API directly rather than through the SDK: the
    payload is three JSON shapes, and a dependency that pulls in its own
    background threads and telemetry is a poor trade for that.

    Each record becomes a trace upsert (Langfuse keys traces by id, so
    repeating one is how a trace accumulates spans) plus one span.
    """

    name = "langfuse"

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        host: str,
        *,
        queue_size: int = _QUEUE_SIZE,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(queue_size)
        self._host = host.rstrip("/")
        self._auth = base64.b64encode(
            f"{public_key}:{secret_key}".encode()
        ).decode()
        self._client = client
        self._owns_client = client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    def _events(self, record: TraceRecord) -> list[dict[str, Any]]:
        trace_id = as_uuid(record.correlation_id)
        span_id = str(uuid.uuid4())
        started = record.started_at.isoformat()
        return [
            {
                "id": str(uuid.uuid4()),
                "type": "trace-create",
                "timestamp": started,
                "body": {
                    "id": trace_id,
                    "name": record.kind,
                    "userId": record.user_id,
                    "timestamp": started,
                    "tags": [record.kind],
                },
            },
            {
                "id": str(uuid.uuid4()),
                "type": "span-create",
                "timestamp": started,
                "body": {
                    "id": span_id,
                    "traceId": trace_id,
                    "name": record.name,
                    "startTime": started,
                    "endTime": record.ended_at.isoformat(),
                    "input": record.input,
                    "output": record.output,
                    "metadata": record.metadata,
                    # Langfuse colours a span by level; an errored stage that
                    # looks identical to a healthy one defeats the point.
                    "level": "ERROR" if record.error else "DEFAULT",
                    "statusMessage": record.error,
                },
            },
        ]

    async def _flush(self, batch: Sequence[TraceRecord]) -> None:
        events: list[dict[str, Any]] = []
        for record in batch:
            events.extend(self._events(record))

        response = await self._get_client().post(
            f"{self._host}/api/public/ingestion",
            json={"batch": events},
            headers={
                # Basic auth in a header, never credentials in the URL.
                "authorization": f"Basic {self._auth}",
                "content-type": "application/json",
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(
                f"langfuse ingestion returned {response.status_code}: "
                f"{response.text[:200]}"
            )

    async def close(self) -> None:
        await super().close()
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


class MultiTracer:
    """Fans one record out to several sinks.

    The local table and Langfuse answer different questions -- one is joinable
    against the student's messages, the other is browsable -- and there is no
    reason to choose. A failure in one sink cannot affect the other.
    """

    name = "multi"

    def __init__(self, sinks: Sequence[Any]) -> None:
        self.sinks = list(sinks)

    async def record(self, **kwargs: Any) -> None:
        for sink in self.sinks:
            try:
                await sink.record(**kwargs)
            except Exception as exc:
                logger.warning("tracer %s rejected a record: %s", sink.name, exc)

    async def close(self) -> None:
        for sink in self.sinks:
            try:
                await sink.close()
            except Exception as exc:
                logger.warning("tracer %s failed to close: %s", sink.name, exc)

    def stats(self) -> dict[str, int]:
        merged: dict[str, int] = {}
        for sink in self.sinks:
            for key, value in sink.stats().items():
                merged[f"{sink.name}_{key}"] = value
        return merged


# Registered so the available sinks are discoverable by name, the same way
# every other swappable stage is.
tracers.register("noop")(lambda **_: NoOpTracer())
tracers.register("memory")(lambda **_: InMemoryTracer())
tracers.register("postgres")(lambda dsn, **_: PostgresTracer(dsn))
tracers.register("langfuse")(
    lambda public_key, secret_key, host, **_: LangfuseTracer(
        public_key, secret_key, host
    )
)
