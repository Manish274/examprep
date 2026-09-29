"""One ingest per document at a time.

The worker gives up on a slow ingest and tries again, and FastAPI does not stop
a run when its caller hangs up. So every retry used to start a second full
ingest of the same file beside the first: both paying for every image, both
queueing on one rate limiter, each slower than either alone -- which made the
retry time out as well, and the one after it.

Here a run is its own task, keyed by document. A request for work already
running waits on that run rather than starting another, and gets its result.
A request for different work on the same document -- the full pass arriving
while the text-only pass is still finishing -- waits for the first to end, so
two runs never write one document's index at once. Each run also records how
far through its images it is, which the progress endpoint reports.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class Run:
    # What the run is doing. Requests with the same key share it.
    key: str
    task: asyncio.Task[Any] | None = None
    done: int = 0
    total: int = 0

    def report(self, done: int, total: int) -> None:
        self.done, self.total = done, total


_runs: dict[str, Run] = {}


async def run_once(
    document_id: str,
    key: str,
    work: Callable[[Callable[[int, int], None]], Coroutine[Any, Any, T]],
) -> T:
    """Runs `work` for this document, or joins the identical run in progress.

    `work` is handed a progress callback. The run continues if the caller is
    cancelled, so a retry arriving afterwards finds it and waits for it.
    """
    while (current := _runs.get(document_id)) is not None:
        if current.key == key and current.task is not None:
            return await asyncio.shield(current.task)
        if current.task is not None:
            await asyncio.wait([current.task])

    run = Run(key=key)
    task: asyncio.Task[T] = asyncio.create_task(work(run.report))
    run.task = task
    _runs[document_id] = run

    def finished(done: asyncio.Task[T]) -> None:
        if _runs.get(document_id) is run:
            del _runs[document_id]
        # Retrieved so a run nobody is waiting for any more does not log
        # "exception was never retrieved"; its callers saw it already.
        if not done.cancelled() and done.exception() is not None:
            logger.debug("ingest of %s ended with %r", document_id, done.exception())

    task.add_done_callback(finished)
    return await asyncio.shield(task)


def progress(document_id: str) -> Run | None:
    return _runs.get(document_id)


async def cancel(document_id: str) -> bool:
    """Stops the document's run, if it has one, and waits until it has.

    Called before a document's vectors are deleted: a run left going would
    write them back, and keep spending quota on a file nobody wants.
    """
    run = _runs.get(document_id)
    if run is None or run.task is None:
        return False
    run.task.cancel()
    await asyncio.wait([run.task])
    return True
