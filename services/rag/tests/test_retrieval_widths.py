"""How wide each half of a hybrid search casts, and who decides.

The configured widths used to reach only the retrieval lab: chat called the
search with its own defaults, while /health/config reported the configured
values as the ones in force.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.core.models import RetrievalStrategy, ScoredChunk
from app.retrieval.retrievers import RetrievalService


class RecordingHybrid:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
        dense_top_k: int | None = None,
        sparse_top_k: int | None = None,
    ) -> list[ScoredChunk]:
        self.calls.append({"dense_top_k": dense_top_k, "sparse_top_k": sparse_top_k})
        return []


def _service(hybrid: RecordingHybrid) -> RetrievalService:
    return RetrievalService(
        dense=None,  # type: ignore[arg-type]
        sparse=None,  # type: ignore[arg-type]
        hybrid=hybrid,  # type: ignore[arg-type]
        dense_top_k=80,
        sparse_top_k=30,
    )


async def test_a_caller_that_names_no_widths_gets_the_configured_ones() -> None:
    hybrid = RecordingHybrid()
    await _service(hybrid).search(
        "what is 3NF", user_id="u", strategy=RetrievalStrategy.HYBRID
    )
    assert hybrid.calls == [{"dense_top_k": 80, "sparse_top_k": 30}]


async def test_a_caller_can_still_override_them() -> None:
    hybrid = RecordingHybrid()
    await _service(hybrid).search(
        "what is 3NF",
        user_id="u",
        strategy=RetrievalStrategy.HYBRID,
        dense_top_k=10,
        sparse_top_k=5,
    )
    assert hybrid.calls == [{"dense_top_k": 10, "sparse_top_k": 5}]
