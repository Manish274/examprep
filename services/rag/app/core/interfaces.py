"""The interfaces the pipeline is written against.

Each swappable stage is a Protocol, and `container.py` picks one
implementation per stage from configuration. Protocols are structural: an
implementation satisfies one by shape alone, so no provider imports from here,
and a test can hand the pipeline a small fake.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol

from app.core.models import (
    Chunk,
    EmbeddingVector,
    LLMMessage,
    LLMResponse,
    RetrievalStrategy,
    ScoredChunk,
    SparseVector,
)


class EmbeddingProvider(Protocol):
    """Produces dense vectors.

    Query and document embeddings are separate methods because several models,
    Gemini included, encode them differently and lose accuracy if conflated.
    """

    model_id: str
    dimensions: int

    async def embed_documents(self, texts: Sequence[str]) -> list[EmbeddingVector]: ...

    async def embed_query(self, text: str) -> EmbeddingVector: ...


class SparseEncoder(Protocol):
    """Turns text into a sparse term-weight vector for keyword search."""

    def encode_document(self, text: str) -> SparseVector: ...

    def encode_query(self, text: str) -> SparseVector: ...


class Reranker(Protocol):
    """Reorders candidates by reading query and passage together.

    The last filter before the model, so its precision sets the ceiling on how
    grounded an answer can be.
    """

    name: str
    # Whether scores are relevance on a fixed scale rather than derived from
    # rank. Only calibrated scores can say a question is off topic, or that a
    # refusal contradicts the evidence.
    calibrated: bool

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_n: int
    ) -> list[ScoredChunk]: ...


class LLMProvider(Protocol):
    """Text generation, whole or streamed."""

    model_id: str

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]: ...


class Tracer(Protocol):
    """Where trace records go: the traces table, Langfuse, memory, nowhere."""

    name: str

    async def record(self, **fields: Any) -> None: ...

    async def close(self) -> None: ...

    def stats(self) -> dict[str, int]: ...


class Retrieval(Protocol):
    """Search over one user's indexed chunks, by a named strategy."""

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        strategy: RetrievalStrategy,
        document_ids: Sequence[str] | None = None,
        top_k: int = 25,
    ) -> list[ScoredChunk]: ...


class Corpus(Protocol):
    """Reads indexed chunks back without a query, in reading order."""

    async def sample_chunks(
        self,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        limit: int = 25,
    ) -> list[Chunk]: ...
