"""The seven swappable components of the RAG pipeline, plus tracing.

Every stage is a Protocol. Pipeline code depends only on these, never on a
concrete provider, which is what makes it possible to swap Gemini for Ollama,
BM25 for a learned sparse model, or PyMuPDF for Docling, and then to measure
whether the swap actually helped.

Protocols are structural: an implementation satisfies one by shape alone, so
no adapter has to import from here to be usable.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.core.models import (
    Chunk,
    DocumentKind,
    EmbeddingVector,
    LLMMessage,
    LLMResponse,
    ParsedDocument,
    ScoredChunk,
    SparseVector,
)


@runtime_checkable
class DocumentParser(Protocol):
    """Turns a source file into positioned, typed blocks.

    Implementations: PyMuPDFParser, PythonPptxParser, DoclingParser.
    """

    name: str

    def supports(self, kind: DocumentKind) -> bool: ...

    async def parse(
        self, path: Path, *, document_id: str, filename: str
    ) -> ParsedDocument: ...


@runtime_checkable
class Chunker(Protocol):
    """Groups blocks into retrievable chunks, preserving metadata.

    Implementations: StructuralChunker, FixedWindowChunker (eval baseline).
    """

    name: str

    def chunk(self, document: ParsedDocument) -> list[Chunk]: ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Produces dense vectors.

    Query and document embeddings are separate methods because several models,
    Gemini included, encode them differently and lose accuracy if conflated.

    Implementations: GeminiEmbeddingProvider, MockEmbeddingProvider.
    """

    model_id: str
    dimensions: int

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> list[EmbeddingVector]: ...

    async def embed_query(self, text: str) -> EmbeddingVector: ...


@runtime_checkable
class SparseEncoder(Protocol):
    """Turns text into a sparse term-weight vector.

    BM25 needs no model: term frequency, IDF and stemming only. IDF is applied
    by Qdrant at query time using the collection statistics.
    """

    name: str

    def encode_document(self, text: str) -> SparseVector: ...

    def encode_query(self, text: str) -> SparseVector: ...


@runtime_checkable
class DenseRetriever(Protocol):
    """Vector-similarity search over the indexed corpus."""

    name: str

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
    ) -> list[ScoredChunk]: ...


@runtime_checkable
class SparseRetriever(Protocol):
    """Keyword search. Catches exact terms, formulas and abbreviations that
    dense retrieval paraphrases away."""

    name: str

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        document_ids: Sequence[str] | None = None,
        top_k: int = 50,
    ) -> list[ScoredChunk]: ...


@runtime_checkable
class Fuser(Protocol):
    """Merges ranked lists from several retrievers into one.

    Implementations: ReciprocalRankFusion, WeightedScoreFusion. RRF is the
    default because it combines rankings without needing the underlying score
    scales to be comparable, which dense and BM25 scores are not.
    """

    name: str

    def fuse(
        self, rankings: Sequence[Sequence[ScoredChunk]], *, top_k: int
    ) -> list[ScoredChunk]: ...


@runtime_checkable
class Reranker(Protocol):
    """Reorders candidates by reading query and chunk together.

    Retrieval is fast and approximate; reranking is slow and precise. This is
    the last filter before the LLM, so its precision sets the ceiling on how
    grounded an answer can be.

    Implementations: GeminiListwiseReranker, JinaReranker, NoOpReranker.
    """

    name: str

    async def rerank(
        self, query: str, candidates: Sequence[ScoredChunk], *, top_n: int
    ) -> list[ScoredChunk]: ...


@runtime_checkable
class LLMProvider(Protocol):
    """Text generation, streaming and non-streaming.

    Implementations: GeminiLLMProvider, OpenAICompatibleProvider (covers
    Ollama, vLLM, OpenRouter, Together), MockLLMProvider.
    """

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


@runtime_checkable
class StorageProvider(Protocol):
    """Where uploaded documents live.

    Implementations: LocalFilesystemStorage now, S3Storage later. Callers only
    ever hold an opaque storage key.
    """

    name: str

    async def read(self, key: str) -> bytes: ...

    async def write(self, key: str, data: bytes) -> str: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    def local_path(self, key: str) -> Path | None:
        """A real filesystem path when one exists, else None.

        Parsers stream from disk rather than buffering whole files in memory,
        so a local path is worth exposing when the backend has one.
        """
        ...


@runtime_checkable
class Tracer(Protocol):
    """Records what the pipeline did, so retrieval quality is measurable
    rather than anecdotal.

    Implementations: PostgresTracer, LangfuseTracer, NoOpTracer.
    """

    name: str

    async def record(
        self,
        *,
        kind: str,
        name: str,
        correlation_id: str,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
    ) -> None: ...
