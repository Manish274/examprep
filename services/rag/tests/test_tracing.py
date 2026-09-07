"""Tests for tracing.

The properties worth guarding are not "does it write a row" -- they are the two
promises the rest of the pipeline relies on: a trace never fails a request, and
a trace never slows one down. Both are easy to break with a well-meaning change
and neither shows up as a test failure anywhere else.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import pytest

from app.config import Settings
from app.container import build_tracer
from app.observability.middleware import TracingMiddleware, kind_for_path
from app.observability.sinks import (
    InMemoryTracer,
    LangfuseTracer,
    MultiTracer,
    NoOpTracer,
    PostgresTracer,
    TraceRecord,
    _dump,
    _QueuedSink,
    _valid_uuid,
    as_uuid,
)
from app.observability.trace import (
    Trace,
    current_trace,
    identify,
    reset_current_trace,
    set_current_trace,
    span,
)


class _Recorder:
    """A tracer that only remembers, so a test can assert on the call."""

    name = "recorder"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))

    async def close(self) -> None:
        return None

    def stats(self) -> dict[str, int]:
        return {}


class _Exploding:
    """A sink that fails every write, which is the case that must not
    propagate."""

    name = "exploding"

    def __init__(self) -> None:
        self.attempts = 0

    async def record(self, **kwargs: object) -> None:
        self.attempts += 1
        raise RuntimeError("sink is down")

    async def close(self) -> None:
        raise RuntimeError("close is down too")

    def stats(self) -> dict[str, int]:
        return {"attempts": self.attempts}


class TestSpan:
    async def test_is_a_no_op_without_a_trace_in_flight(self) -> None:
        # Stages are called directly by scripts and tests. Instrumentation
        # that only works inside a request would make them uninstrumentable.
        async with span("retrieve", query="x") as observed:
            observed.output(returned=3)

        assert current_trace() is None

    async def test_records_input_output_and_duration(self) -> None:
        recorder = _Recorder()
        token = set_current_trace(
            Trace(recorder, kind="chat", correlation_id="c-1", user_id="u-1")
        )
        try:
            async with span("retrieve", query="what is a bigram") as observed:
                observed.output(returned=5)
                observed.meta(strategy="dense")
        finally:
            reset_current_trace(token)

        (call,) = recorder.calls
        assert call["name"] == "retrieve"
        assert call["kind"] == "chat"
        assert call["user_id"] == "u-1"
        assert call["input"] == {"query": "what is a bigram"}
        assert call["output"] == {"returned": 5}
        assert call["metadata"] == {"strategy": "dense"}
        assert call["duration_ms"] >= 0
        assert call["error"] is None

    async def test_records_the_failure_and_re_raises_it(self) -> None:
        # A stage that raised is the most useful thing a trace can hold, and
        # recording it must not turn the failure into a success.
        recorder = _Recorder()
        token = set_current_trace(Trace(recorder, kind="chat", correlation_id="c-1"))
        try:
            with pytest.raises(ValueError, match="qdrant"):
                async with span("retrieve"):
                    raise ValueError("qdrant refused the connection")
        finally:
            reset_current_trace(token)

        (call,) = recorder.calls
        assert call["error"] == "ValueError: qdrant refused the connection"

    async def test_a_broken_sink_cannot_fail_the_stage(self) -> None:
        token = set_current_trace(
            Trace(_Exploding(), kind="chat", correlation_id="c-1")
        )
        try:
            async with span("retrieve") as observed:
                observed.output(returned=1)
        finally:
            reset_current_trace(token)
        # Reaching here at all is the assertion.

    async def test_identify_attaches_the_user_late(self) -> None:
        # The middleware creates the trace before the body has been parsed, so
        # the user is only known once inside the endpoint.
        recorder = _Recorder()
        trace = Trace(recorder, kind="chat", correlation_id="c-1")
        token = set_current_trace(trace)
        try:
            identify(user_id="u-9")
            async with span("generate"):
                pass
        finally:
            reset_current_trace(token)

        assert recorder.calls[0]["user_id"] == "u-9"


class TestCoercion:
    def test_a_uuid_correlation_id_passes_through(self) -> None:
        value = str(uuid.uuid4())
        assert as_uuid(value) == value

    def test_a_label_is_hashed_stably(self) -> None:
        # Scripts pass names, not uuids. Rejecting them would lose exactly the
        # runs worth keeping, and a random id would scatter one run's spans.
        first = as_uuid("eval-run-2026-09-07")
        assert first == as_uuid("eval-run-2026-09-07")
        assert first != as_uuid("eval-run-2026-09-08")
        uuid.UUID(first)

    def test_a_non_uuid_user_id_becomes_null_rather_than_a_failed_insert(
        self,
    ) -> None:
        # The column is a foreign key. One bad id would take the whole batch
        # down; dropping the attribution keeps the span.
        assert _valid_uuid("not-a-uuid") is None
        assert _valid_uuid(None) is None
        assert _valid_uuid(str(uuid.uuid4())) is not None

    def test_unserialisable_payloads_do_not_lose_the_span(self) -> None:
        assert _dump(None) is None
        assert json.loads(_dump({"a": 1}) or "{}") == {"a": 1}
        # datetimes and enums reach here routinely.
        assert "unserialisable" not in (_dump({"x": object()}) or "")


class _CountingSink(_QueuedSink):
    name = "counting"

    def __init__(self, queue_size: int = 4) -> None:
        super().__init__(queue_size)
        self.flushed: list[TraceRecord] = []
        self.fail = False

    async def _flush(self, batch) -> None:  # type: ignore[no-untyped-def]
        if self.fail:
            raise RuntimeError("nope")
        self.flushed.extend(batch)


class TestQueuedSink:
    async def test_writes_are_drained_in_the_background(self) -> None:
        sink = _CountingSink()
        await sink.record(kind="chat", name="retrieve", correlation_id="c-1")
        await sink.close()

        assert [r.name for r in sink.flushed] == ["retrieve"]
        assert sink.stats()["written"] == 1

    async def test_a_full_queue_drops_and_counts_rather_than_blocking(self) -> None:
        # Under pressure, losing trace rows is the correct sacrifice. Blocking
        # the request path to write one is not.
        # `record` has no await inside it, so the drain task cannot run
        # between these calls -- which is precisely the guarantee under test.
        sink = _CountingSink(queue_size=2)
        for _ in range(10):
            await sink.record(kind="chat", name="x", correlation_id="c-1")

        assert sink.dropped > 0
        await sink.close()

    async def test_a_failing_flush_is_counted_not_raised(self) -> None:
        sink = _CountingSink()
        sink.fail = True
        await sink.record(kind="chat", name="retrieve", correlation_id="c-1")
        await sink.close()

        assert sink.stats()["failed"] == 1
        assert sink.flushed == []

    async def test_close_flushes_what_is_still_in_hand(self) -> None:
        # The last spans before a shutdown are the ones explaining it.
        sink = _CountingSink(queue_size=100)
        for i in range(5):
            await sink.record(kind="chat", name=f"span-{i}", correlation_id="c-1")
        await sink.close()

        assert len(sink.flushed) == 5


class TestLangfuse:
    async def test_sends_a_trace_and_a_span_with_basic_auth(self) -> None:
        seen: dict[str, object] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(207, json={"successes": [], "errors": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sink = LangfuseTracer(
            "pk-test", "sk-test", "http://langfuse.local/", client=client
        )
        await sink.record(
            kind="chat",
            name="retrieve",
            correlation_id=str(uuid.uuid4()),
            user_id="u-1",
            output={"returned": 5},
        )
        await sink.close()
        await client.aclose()

        assert seen["url"] == "http://langfuse.local/api/public/ingestion"
        # Credentials in a header, never in the URL.
        assert str(seen["auth"]).startswith("Basic ")
        types = [e["type"] for e in seen["body"]["batch"]]  # type: ignore[index]
        assert types == ["trace-create", "span-create"]

    async def test_an_error_status_is_counted_not_raised(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="bad key")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        sink = LangfuseTracer("pk", "sk", "http://langfuse.local", client=client)
        await sink.record(kind="chat", name="retrieve", correlation_id="c-1")
        await sink.close()
        await client.aclose()

        assert sink.stats()["failed"] == 1

    def test_an_errored_span_is_marked_so_it_is_visible(self) -> None:
        sink = LangfuseTracer("pk", "sk", "http://x")
        events = sink._events(
            TraceRecord(
                kind="chat", name="generate", correlation_id="c-1", error="boom"
            )
        )
        assert events[1]["body"]["level"] == "ERROR"
        assert events[1]["body"]["statusMessage"] == "boom"


class TestMultiTracer:
    async def test_one_broken_sink_does_not_stop_the_other(self) -> None:
        good = InMemoryTracer()
        bad = _Exploding()
        multi = MultiTracer([bad, good])

        await multi.record(kind="chat", name="retrieve", correlation_id="c-1")
        await multi.close()

        assert len(good.records) == 1
        assert bad.attempts == 1

    async def test_stats_are_namespaced_by_sink(self) -> None:
        multi = MultiTracer([InMemoryTracer(), NoOpTracer()])
        await multi.record(kind="chat", name="x", correlation_id="c-1")
        assert multi.stats()["memory_recorded"] == 1


class TestBuildTracer:
    def test_none_selects_the_noop_sink(self) -> None:
        assert isinstance(build_tracer(Settings(TRACER="none")), NoOpTracer)

    def test_postgres_is_the_default_local_sink(self) -> None:
        assert isinstance(build_tracer(Settings(TRACER="postgres")), PostgresTracer)

    def test_a_comma_separated_list_fans_out(self) -> None:
        tracer = build_tracer(
            Settings(
                TRACER="postgres,langfuse",
                LANGFUSE_PUBLIC_KEY="pk",
                LANGFUSE_SECRET_KEY="sk",
            )
        )
        assert isinstance(tracer, MultiTracer)
        assert [s.name for s in tracer.sinks] == ["postgres", "langfuse"]

    def test_langfuse_without_keys_refuses_at_startup(self) -> None:
        # Discovering during an incident that tracing had silently switched
        # itself off is the one failure worth being loud about.
        with pytest.raises(RuntimeError, match="LANGFUSE_PUBLIC_KEY"):
            build_tracer(
                Settings(
                    TRACER="langfuse",
                    LANGFUSE_PUBLIC_KEY="",
                    LANGFUSE_SECRET_KEY="",
                )
            )

    def test_an_unknown_name_lists_the_alternatives(self) -> None:
        with pytest.raises(ValueError, match="Available"):
            build_tracer(Settings(TRACER="jaeger"))


class TestMiddleware:
    def test_health_probes_are_not_traced(self) -> None:
        # They run every few seconds and would bury the answers they share a
        # table with.
        assert kind_for_path("/health") is None
        assert kind_for_path("/health/config") is None

    def test_the_longest_matching_prefix_wins(self) -> None:
        assert kind_for_path("/generate/test") == "test_generation"
        assert kind_for_path("/generate/flashcards") == "flashcard_generation"
        assert kind_for_path("/chat/stream") == "chat"
        assert kind_for_path("/ingest") == "ingestion"

    async def test_a_traced_request_records_status_and_latency(self) -> None:
        recorder = _Recorder()

        async def app(scope, receive, send):  # type: ignore[no-untyped-def]
            assert current_trace() is not None
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        wrapped = TracingMiddleware(app, lambda: recorder)
        correlation = str(uuid.uuid4())
        await wrapped(
            {
                "type": "http",
                "path": "/chat",
                "method": "POST",
                "headers": [(b"x-correlation-id", correlation.encode())],
            },
            None,
            lambda message: asyncio.sleep(0),
        )

        (call,) = recorder.calls
        assert call["name"] == "request"
        assert call["kind"] == "chat"
        # The id supplied by the Node backend is what joins this span to the
        # message it explains.
        assert call["correlation_id"] == correlation
        assert call["output"] == {"status": 200}

    async def test_an_untraced_path_creates_no_trace(self) -> None:
        recorder = _Recorder()

        async def app(scope, receive, send):  # type: ignore[no-untyped-def]
            assert current_trace() is None

        wrapped = TracingMiddleware(app, lambda: recorder)
        await wrapped(
            {"type": "http", "path": "/health", "method": "GET", "headers": []},
            None,
            None,
        )
        assert recorder.calls == []
