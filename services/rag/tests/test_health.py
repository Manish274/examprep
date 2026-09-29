"""Boot test: proves the FastAPI app assembles and serves.

Deliberately makes no assumption about whether Qdrant is reachable. An earlier
version asserted it was down, which quietly encoded "the developer has not
started Docker yet" into the suite and broke the moment infrastructure existed.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import Settings, get_settings
from app.main import create_app


def _client(settings: Settings | None = None) -> httpx.AsyncClient:
    app = create_app()
    if settings is not None:
        app.dependency_overrides[get_settings] = lambda: settings
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


@pytest.fixture
async def client():
    async with _client() as client:
        yield client


class TestHealth:
    async def test_responds_whatever_the_state_of_qdrant(
        self, client: httpx.AsyncClient
    ) -> None:
        # Health must answer rather than raise, reachable or not.
        response = await client.get("/health")

        assert response.status_code == 200
        assert response.json()["service"] == "rag"

    async def test_status_agrees_with_the_qdrant_check(
        self, client: httpx.AsyncClient
    ) -> None:
        # The invariant that actually matters: the summary status is not
        # allowed to claim "ok" while its own dependency check says otherwise.
        body = (await client.get("/health")).json()

        assert isinstance(body["qdrant"], bool)
        assert body["status"] == ("ok" if body["qdrant"] else "degraded")

    async def test_names_the_active_providers(self, client: httpx.AsyncClient) -> None:
        body = (await client.get("/health")).json()

        assert set(body["providers"]) == {"embedding", "llm", "reranker", "tracer"}
        assert isinstance(body["mock_mode"], bool)
        assert isinstance(body["gemini_key_present"], bool)


class TestMockModeReporting:
    """Mock mode must reflect what is configured, not merely whether a key
    exists -- a key can be present while the providers are still stand-ins."""

    async def test_reports_mock_when_providers_are_stand_ins(self) -> None:
        settings = Settings(
            GEMINI_API_KEY="a-real-looking-key",
            EMBEDDING_PROVIDER="mock",
            LLM_PROVIDER="mock",
        )
        async with _client(settings) as client:
            body = (await client.get("/health")).json()

        assert body["mock_mode"] is True
        assert body["gemini_key_present"] is True

    async def test_reports_real_once_providers_are_switched_over(self) -> None:
        settings = Settings(
            GEMINI_API_KEY="a-real-looking-key",
            EMBEDDING_PROVIDER="gemini",
            LLM_PROVIDER="gemini",
        )
        async with _client(settings) as client:
            body = (await client.get("/health")).json()

        assert body["mock_mode"] is False

    async def test_reports_mock_when_no_key_is_present(self) -> None:
        settings = Settings(
            GEMINI_API_KEY="", EMBEDDING_PROVIDER="mock", LLM_PROVIDER="mock"
        )
        async with _client(settings) as client:
            body = (await client.get("/health")).json()

        assert body["mock_mode"] is True
        assert body["gemini_key_present"] is False


class TestConfigEndpoint:
    async def test_reports_the_settings_actually_in_force(self) -> None:
        # Asserted against the injected settings rather than literals, so
        # retuning a knob in .env does not break the suite.
        settings = Settings(
            CHUNK_TARGET_TOKENS=384,
            CHUNK_OVERLAP_TOKENS=48,
            EMBEDDING_DIMENSIONS=1536,
            RRF_K=42,
        )
        async with _client(settings) as client:
            body = (await client.get("/health/config")).json()

        assert body["chunking"]["target_tokens"] == 384
        assert body["chunking"]["overlap_tokens"] == 48
        assert body["embedding"]["dimensions"] == 1536
        assert body["retrieval"]["rrf_k"] == 42

    async def test_exposes_every_retrieval_knob(
        self, client: httpx.AsyncClient
    ) -> None:
        # When an eval result looks surprising, the first question is always
        # which configuration produced it.
        body = (await client.get("/health/config")).json()

        assert set(body["retrieval"]) == {
            "dense_top_k",
            "sparse_top_k",
            "rrf_k",
            "rerank_top_n",
            "context_top_n",
            "context_max_tokens",
        }
