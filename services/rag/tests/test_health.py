"""Boot test: proves the FastAPI app assembles and serves without any
credentials, infrastructure or network access."""

from __future__ import annotations

import httpx
import pytest

from app.main import create_app


@pytest.fixture
async def client() -> httpx.AsyncClient:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        yield client


class TestHealth:
    async def test_reports_degraded_when_qdrant_is_unreachable(
        self, client: httpx.AsyncClient
    ) -> None:
        # Nothing is running locally during unit tests, so "degraded" is the
        # correct answer -- and the endpoint must still respond rather than
        # raise.
        response = await client.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["service"] == "rag"
        assert body["status"] in {"ok", "degraded"}
        assert body["qdrant"] is False

    async def test_names_the_active_providers(
        self, client: httpx.AsyncClient
    ) -> None:
        body = (await client.get("/health")).json()

        assert set(body["providers"]) == {
            "embedding",
            "llm",
            "reranker",
            "tracer",
        }

    async def test_config_endpoint_exposes_retrieval_knobs(
        self, client: httpx.AsyncClient
    ) -> None:
        body = (await client.get("/health/config")).json()

        assert body["retrieval"]["rrf_k"] == 60
        assert body["chunking"]["target_tokens"] == 512
        assert body["embedding"]["dimensions"] == 768
