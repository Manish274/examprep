"""Stand-ins shared by the chat tests: a retriever with fixed results, a
model that replies from a script, and passages carrying chosen scores."""

from __future__ import annotations

from app.core.models import Chunk, ChunkMetadata, ContentSource, ScoredChunk
from app.generation.llm import MockLLMProvider


def scored_chunk(text: str, index: int = 0, *, vision: bool = False) -> ScoredChunk:
    return ScoredChunk(
        chunk=Chunk(
            id=f"chunk-{index}",
            text=text,
            token_count=20,
            metadata=ChunkMetadata(
                document_id="doc-1",
                document_name="lecture.pptx",
                chunk_index=index,
                slide_number=index + 1,
                heading_path=["Smoothing"],
                content_hash="h" * 64,
                source=ContentSource.VISION if vision else ContentSource.TEXT,
            ),
        ),
        score=1.0 - index / 100,
    )


def with_scores(similarity: float | None, relevance: float | None) -> list[ScoredChunk]:
    chunk = scored_chunk("The sensor uses a capacitive microphone.")
    chunk.dense_score = similarity
    chunk.rerank_score = relevance
    return [chunk]


class FakeRetrieval:
    def __init__(self, chunks: list[ScoredChunk] | None = None) -> None:
        self.chunks = chunks if chunks is not None else [scored_chunk("Some material.")]
        self.queries: list[str] = []

    async def search(self, query, *, user_id, strategy, document_ids=None, top_k=10):
        self.queries.append(query)
        return self.chunks


class Scripted(MockLLMProvider):
    """Replies in order, one per call, and records each call's temperature."""

    def __init__(self, *replies: str) -> None:
        super().__init__()
        self.replies = list(replies)
        self.temperatures: list[float] = []

    def _answer(self, messages):
        self.calls.append(list(messages))
        return self.replies[min(len(self.calls), len(self.replies)) - 1]

    async def complete(self, messages, *, temperature=0.2, **kwargs):
        self.temperatures.append(temperature)
        return await super().complete(messages, temperature=temperature, **kwargs)

    async def stream(self, messages, *, temperature=0.2, **kwargs):
        self.temperatures.append(temperature)
        async for piece in super().stream(messages, temperature=temperature, **kwargs):
            yield piece
