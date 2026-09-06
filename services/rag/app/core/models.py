"""Domain models shared across the RAG pipeline.

These mirror the Zod schemas in packages/shared. When one side changes, the
other must change with it -- the contract test in tests/test_contract.py exists
to make that drift loud.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentKind(str, Enum):
    PDF = "pdf"
    PPTX = "pptx"
    PPT = "ppt"


class BlockType(str, Enum):
    """Semantic role of an extracted block, used to drive chunking."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"
    SPEAKER_NOTE = "speaker_note"


class ExplanationMode(str, Enum):
    SIMPLE = "simple"
    DETAILED = "detailed"
    EXAM = "exam"


class RetrievalStrategy(str, Enum):
    """The four strategies the evaluation harness compares."""

    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"
    HYBRID_RERANK = "hybrid_rerank"


class ParsedBlock(BaseModel):
    """One positioned unit of content extracted from a source document."""

    text: str
    type: BlockType
    page_number: int | None = None
    slide_number: int | None = None
    # Heading level 1..6; None for non-heading blocks.
    level: int | None = None
    # Font size drives heading detection for PDFs, which carry no real structure.
    font_size: float | None = None
    bbox: tuple[float, float, float, float] | None = None
    order: int = 0


class ParsedDocument(BaseModel):
    """Normalised output of any DocumentParser, regardless of source format."""

    document_id: str
    filename: str
    kind: DocumentKind
    blocks: list[ParsedBlock] = Field(default_factory=list)
    page_count: int = 0
    parser: str = "unknown"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkMetadata(BaseModel):
    """Preserved end to end, from ingestion to the citation shown to a student."""

    document_id: str
    document_name: str
    chunk_index: int
    page_number: int | None = None
    slide_number: int | None = None
    section: str | None = None
    heading: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    char_start: int | None = None
    char_end: int | None = None
    content_hash: str


class Chunk(BaseModel):
    """A retrievable unit of study material."""

    id: str
    text: str
    token_count: int
    metadata: ChunkMetadata

    def embedding_text(self) -> str:
        """Text actually sent to the embedding model.

        The heading path is prepended so an isolated chunk still carries the
        context it was written under. A paragraph reading "It must also satisfy
        2NF" is close to meaningless alone, but recovers its subject once
        prefixed with "notes.pdf > Normalization > Third Normal Form".
        """
        trail = " > ".join([self.metadata.document_name, *self.metadata.heading_path])
        return f"{trail}\n\n{self.text}" if trail else self.text


class ScoredChunk(BaseModel):
    """A chunk with the scores from every retrieval stage that touched it.

    Keeping per-stage scores rather than collapsing to one number is what lets
    the eval harness attribute a win to fusion or to reranking specifically.
    """

    chunk: Chunk
    score: float
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    dense_rank: int | None = None
    sparse_rank: int | None = None


class EmbeddingVector(BaseModel):
    values: list[float]
    model_id: str
    dimensions: int


class SparseVector(BaseModel):
    """Qdrant sparse vector: parallel index and value arrays."""

    indices: list[int]
    values: list[float]


class LLMMessage(BaseModel):
    role: str
    content: str


class LLMUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMResponse(BaseModel):
    text: str
    usage: LLMUsage = Field(default_factory=LLMUsage)
    model_id: str = "unknown"
    finish_reason: str | None = None


class Source(BaseModel):
    """A citation, keyed by the marker used in the answer text."""

    marker: str
    chunk_id: str
    document_id: str
    document_name: str
    page_number: int | None = None
    slide_number: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    snippet: str


class GroundedAnswer(BaseModel):
    """The contract every generation path returns, chat and study tools alike."""

    text: str
    sources: list[Source] = Field(default_factory=list)
    # True when retrieval found nothing that supports an answer. The system
    # says so plainly rather than filling the gap from model priors.
    unsupported: bool = False
    usage: LLMUsage = Field(default_factory=LLMUsage)
