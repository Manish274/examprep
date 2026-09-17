from __future__ import annotations

import asyncio
import time

import pytest

from app.core.models import EmbeddingVector
from app.embedding.cache import (
    CachedEmbeddingProvider,
    InMemoryEmbeddingCache,
    model_key,
)
from app.embedding.gemini import GeminiEmbeddingProvider
from app.embedding.mock import MockEmbeddingProvider
from app.embedding.rate_limit import (
    RateLimiter,
    RateLimitError,
    TransientError,
    with_retries,
)


class TestMockProvider:
    async def test_produces_unit_vectors_of_the_configured_width(self) -> None:
        provider = MockEmbeddingProvider(dimensions=128)
        [vector] = await provider.embed_documents(["a relation in third normal form"])

        assert len(vector.values) == 128
        assert sum(v * v for v in vector.values) == pytest.approx(1.0)

    async def test_is_deterministic(self) -> None:
        # Tests assert on ordering, which requires the same text to embed
        # identically every run.
        provider = MockEmbeddingProvider(dimensions=64)
        first = await provider.embed_query("normalization")
        second = await provider.embed_query("normalization")
        assert first.values == second.values

    async def test_shared_vocabulary_lands_closer_than_unrelated_text(self) -> None:
        # The mock carries real signal rather than noise, so retrieval tests
        # can assert on ranking instead of merely on a response arriving.
        provider = MockEmbeddingProvider(dimensions=256)
        vectors = await provider.embed_documents(
            [
                "third normal form transitive dependency",
                "third normal form candidate key",
                "a b-tree index supports range queries",
            ]
        )

        def dot(a: EmbeddingVector, b: EmbeddingVector) -> float:
            return sum(x * y for x, y in zip(a.values, b.values, strict=True))

        assert dot(vectors[0], vectors[1]) > dot(vectors[0], vectors[2])

    async def test_empty_text_still_yields_a_usable_vector(self) -> None:
        # A zero vector would make cosine similarity undefined.
        [vector] = await MockEmbeddingProvider(dimensions=32).embed_documents(["!!!"])
        assert sum(v * v for v in vector.values) == pytest.approx(1.0)

    async def test_empty_input_returns_empty_output(self) -> None:
        assert await MockEmbeddingProvider().embed_documents([]) == []


class TestGeminiProviderConstruction:
    """Construction-time validation only -- the network paths are exercised by
    the live integration checks, not by mocking out httpx."""

    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ValueError, match="requires an API key"):
            GeminiEmbeddingProvider("")

    def test_rejects_dimensions_above_the_model_width(self) -> None:
        with pytest.raises(ValueError, match="dimensions must be"):
            GeminiEmbeddingProvider("key", dimensions=4096)

    def test_caps_the_batch_size_at_the_measured_limit(self) -> None:
        # 100 draws a 429 from the live API; silently honouring a larger
        # configured value would just fail later under load.
        provider = GeminiEmbeddingProvider("key", batch_size=500)
        assert provider._batch_size <= 64

    def test_full_width_vectors_are_left_alone(self) -> None:
        provider = GeminiEmbeddingProvider("key", dimensions=3072)
        values = [3.0, 4.0]
        assert provider._normalize(values) == values

    def test_truncated_vectors_are_renormalised(self) -> None:
        # Truncation breaks the unit-norm invariant, and cosine against
        # non-unit vectors is subtly wrong.
        provider = GeminiEmbeddingProvider("key", dimensions=768)
        normalized = provider._normalize([3.0, 4.0])
        assert sum(v * v for v in normalized) == pytest.approx(1.0)


class TestEmbeddingCache:
    async def test_second_call_is_served_from_cache(self) -> None:
        inner = MockEmbeddingProvider(dimensions=32)
        provider = CachedEmbeddingProvider(inner, InMemoryEmbeddingCache())

        await provider.embed_documents(["alpha", "beta"])
        assert (provider.hits, provider.misses) == (0, 2)

        await provider.embed_documents(["alpha", "beta"])
        assert (provider.hits, provider.misses) == (2, 2)

    async def test_a_repeated_query_is_embedded_once(self) -> None:
        # The retrieval inspector runs one question through several strategies.
        calls: list[str] = []

        class Counting(MockEmbeddingProvider):
            async def embed_query(self, text):  # type: ignore[override]
                calls.append(text)
                return await super().embed_query(text)

        provider = CachedEmbeddingProvider(
            Counting(dimensions=32), InMemoryEmbeddingCache()
        )
        first = await provider.embed_query("what is a bigram")
        second = await provider.embed_query("what  is a bigram ")

        assert calls == ["what is a bigram"]
        assert first.values == second.values
        assert (provider.query_hits, provider.query_misses) == (1, 1)

    async def test_query_hits_do_not_count_as_document_hits(self) -> None:
        # Ingestion reports `hits` as its own cache hits.
        provider = CachedEmbeddingProvider(
            MockEmbeddingProvider(dimensions=32), InMemoryEmbeddingCache()
        )
        await provider.embed_query("q")
        await provider.embed_query("q")

        assert (provider.hits, provider.misses) == (0, 0)

    async def test_queries_never_reach_the_persistent_cache(self) -> None:
        cache = InMemoryEmbeddingCache()
        provider = CachedEmbeddingProvider(MockEmbeddingProvider(dimensions=32), cache)
        await provider.embed_query("a question asked once")

        assert cache._store == {}

    async def test_the_query_cache_is_bounded(self) -> None:
        calls: list[str] = []

        class Counting(MockEmbeddingProvider):
            async def embed_query(self, text):  # type: ignore[override]
                calls.append(text)
                return await super().embed_query(text)

        provider = CachedEmbeddingProvider(
            Counting(dimensions=32), InMemoryEmbeddingCache(), query_cache_size=2
        )
        for text in ["a", "b", "a", "c", "b"]:
            await provider.embed_query(text)

        # "a" was used recently, so "b" was the one evicted when "c" arrived.
        assert calls == ["a", "b", "c", "b"]

    async def test_a_zero_sized_query_cache_disables_it(self) -> None:
        calls: list[str] = []

        class Counting(MockEmbeddingProvider):
            async def embed_query(self, text):  # type: ignore[override]
                calls.append(text)
                return await super().embed_query(text)

        provider = CachedEmbeddingProvider(
            Counting(dimensions=32), InMemoryEmbeddingCache(), query_cache_size=0
        )
        await provider.embed_query("q")
        await provider.embed_query("q")

        assert calls == ["q", "q"]

    async def test_a_repeated_string_is_embedded_once(self) -> None:
        # Identical text appears more than once inside a single document.
        calls: list[int] = []

        class Counting(MockEmbeddingProvider):
            async def embed_documents(self, texts):  # type: ignore[override]
                calls.append(len(texts))
                return await super().embed_documents(texts)

        provider = CachedEmbeddingProvider(
            Counting(dimensions=32), InMemoryEmbeddingCache()
        )
        result = await provider.embed_documents(["same", "same", "different"])

        assert calls == [2]
        assert len(result) == 3
        assert result[0].values == result[1].values

    async def test_results_stay_aligned_with_the_input_order(self) -> None:
        # A misalignment here attaches one chunk's text to another's vector,
        # which nothing downstream would catch.
        inner = MockEmbeddingProvider(dimensions=32)
        provider = CachedEmbeddingProvider(inner, InMemoryEmbeddingCache())

        await provider.embed_documents(["beta"])
        mixed = await provider.embed_documents(["alpha", "beta", "gamma"])
        direct = await inner.embed_documents(["alpha", "beta", "gamma"])

        assert [m.values for m in mixed] == [d.values for d in direct]

    async def test_dimensions_are_part_of_the_cache_key(self) -> None:
        # The same model at two widths returns different vectors; sharing a key
        # would serve a truncated vector to a caller expecting a full one.
        cache = InMemoryEmbeddingCache()
        narrow = CachedEmbeddingProvider(MockEmbeddingProvider(dimensions=32), cache)
        wide = CachedEmbeddingProvider(MockEmbeddingProvider(dimensions=64), cache)

        await narrow.embed_documents(["alpha"])
        [vector] = await wide.embed_documents(["alpha"])

        assert len(vector.values) == 64
        assert wide.misses == 1

    def test_model_key_includes_both_parts(self) -> None:
        assert model_key("gemini-embedding-2", 3072) == "gemini-embedding-2@3072"

    async def test_empty_input_short_circuits(self) -> None:
        provider = CachedEmbeddingProvider(
            MockEmbeddingProvider(), InMemoryEmbeddingCache()
        )
        assert await provider.embed_documents([]) == []


class TestRateLimiter:
    async def test_allows_an_initial_burst(self) -> None:
        limiter = RateLimiter(rate_per_minute=600, burst=5)
        started = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        assert time.monotonic() - started < 0.2

    async def test_throttles_once_the_bucket_empties(self) -> None:
        # 600/min refills one token every 100ms.
        limiter = RateLimiter(rate_per_minute=600, burst=1)
        await limiter.acquire()

        started = time.monotonic()
        await limiter.acquire()
        assert time.monotonic() - started >= 0.05

    async def test_rejects_a_nonsensical_rate(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            RateLimiter(rate_per_minute=0)

    async def test_a_cost_larger_than_capacity_still_completes(self) -> None:
        # Otherwise an oversized request waits forever for a token the bucket
        # can never hold.
        limiter = RateLimiter(rate_per_minute=6000, burst=2)
        await asyncio.wait_for(limiter.acquire(cost=10), timeout=2.0)


class TestRetries:
    async def test_returns_the_result_when_the_call_succeeds(self) -> None:
        async def ok() -> str:
            return "done"

        assert await with_retries(ok, attempts=3, base_delay=0.01) == "done"

    async def test_retries_a_rate_limit_then_succeeds(self) -> None:
        attempts = {"n": 0}

        async def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RateLimitError("429", retry_after=0.01)
            return "done"

        assert await with_retries(flaky, attempts=5, base_delay=0.01) == "done"
        assert attempts["n"] == 3

    async def test_retries_transient_failures(self) -> None:
        attempts = {"n": 0}

        async def flaky() -> str:
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise TransientError("503")
            return "done"

        assert await with_retries(flaky, attempts=3, base_delay=0.01) == "done"

    async def test_gives_up_after_the_attempt_budget(self) -> None:
        async def always_limited() -> str:
            raise RateLimitError("429")

        with pytest.raises(RuntimeError, match="failed after 3 attempts"):
            await with_retries(always_limited, attempts=3, base_delay=0.01)

    async def test_does_not_retry_a_permanent_error(self) -> None:
        # A malformed request will not improve on retry, and retrying wastes
        # the budget before surfacing the real problem.
        calls = {"n": 0}

        async def bad_request() -> str:
            calls["n"] += 1
            raise ValueError("400 invalid argument")

        with pytest.raises(ValueError):
            await with_retries(bad_request, attempts=5, base_delay=0.01)
        assert calls["n"] == 1
