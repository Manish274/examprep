"""One ingest per document: retries join the run, deletes stop it."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from app.ingestion import inflight


class Gate:
    """Work that runs until the test lets it finish, counting how often it
    was started."""

    def __init__(self) -> None:
        self.started = 0
        self.release = asyncio.Event()

    async def work(self, report: Callable[[int, int], None]) -> str:
        self.started += 1
        report(1, 4)
        await self.release.wait()
        return f"run {self.started}"


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


class TestRunOnce:
    async def test_a_retry_joins_the_run_in_progress(self) -> None:
        # The worker timing out and asking again must not start a second
        # ingest of the same file -- that is what used to double every cost.
        gate = Gate()
        first = asyncio.create_task(inflight.run_once("doc-a", "defer", gate.work))
        await _settle()
        second = asyncio.create_task(inflight.run_once("doc-a", "defer", gate.work))
        await _settle()

        gate.release.set()
        assert await first == await second == "run 1"
        assert gate.started == 1

    async def test_a_run_survives_its_caller_giving_up(self) -> None:
        gate = Gate()
        caller = asyncio.create_task(inflight.run_once("doc-b", "defer", gate.work))
        await _settle()
        caller.cancel()
        await _settle()

        # The retry finds the same run still going and gets its result.
        retry = asyncio.create_task(inflight.run_once("doc-b", "defer", gate.work))
        await _settle()
        gate.release.set()
        assert await retry == "run 1"
        assert gate.started == 1

    async def test_different_work_on_one_document_waits_its_turn(self) -> None:
        # The full pass must not write the index while the text pass still is.
        order: list[str] = []
        text_pass = asyncio.Event()

        async def defer(report: Callable[[int, int], None]) -> None:
            order.append("defer started")
            await text_pass.wait()
            order.append("defer finished")

        async def read(report: Callable[[int, int], None]) -> None:
            order.append("read started")

        first = asyncio.create_task(inflight.run_once("doc-c", "defer", defer))
        await _settle()
        second = asyncio.create_task(inflight.run_once("doc-c", "read", read))
        await _settle()
        text_pass.set()
        await asyncio.gather(first, second)

        assert order == ["defer started", "defer finished", "read started"]

    async def test_other_documents_are_not_held_up(self) -> None:
        gate = Gate()
        slow = asyncio.create_task(inflight.run_once("doc-d", "defer", gate.work))
        await _settle()

        async def quick(report: Callable[[int, int], None]) -> str:
            return "done"

        assert await inflight.run_once("doc-e", "defer", quick) == "done"
        gate.release.set()
        await slow

    async def test_a_failure_reaches_everyone_waiting(self) -> None:
        release = asyncio.Event()

        async def failing(report: Callable[[int, int], None]) -> None:
            await release.wait()
            raise ValueError("unreadable file")

        first = asyncio.create_task(inflight.run_once("doc-f", "defer", failing))
        await _settle()
        second = asyncio.create_task(inflight.run_once("doc-f", "defer", failing))
        await _settle()
        release.set()

        for waiter in (first, second):
            with pytest.raises(ValueError, match="unreadable"):
                await waiter
        assert inflight.progress("doc-f") is None


class TestProgressAndCancel:
    async def test_a_run_reports_how_far_it_is(self) -> None:
        gate = Gate()
        task = asyncio.create_task(inflight.run_once("doc-g", "read", gate.work))
        await _settle()

        run = inflight.progress("doc-g")
        assert run is not None and (run.done, run.total) == (1, 4)

        gate.release.set()
        await task
        assert inflight.progress("doc-g") is None

    async def test_cancel_stops_the_run(self) -> None:
        # Deleting a document mid-ingest must stop the run, or it writes the
        # vectors straight back and keeps spending quota.
        gate = Gate()
        task = asyncio.create_task(inflight.run_once("doc-h", "read", gate.work))
        await _settle()

        assert await inflight.cancel("doc-h") is True
        with pytest.raises(asyncio.CancelledError):
            await task
        assert inflight.progress("doc-h") is None

    async def test_cancelling_nothing_is_harmless(self) -> None:
        assert await inflight.cancel("never-ran") is False
