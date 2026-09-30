"""Fixed-window chunking: the naive baseline.

This deliberately ignores document structure -- it concatenates everything and
cuts every N tokens. It exists so the evaluation harness can quantify what
structure-aware chunking actually buys, rather than assuming it helps.

If StructuralChunker cannot beat this on Recall@K, that is a finding worth
having, not something to hide.
"""

from __future__ import annotations

from app.chunking.structural import last_page
from app.core.models import (
    BlockType,
    Chunk,
    ChunkMetadata,
    ParsedDocument,
)
from app.core.text import chunk_id_for, content_hash
from app.core.tokenizer import TokenCounter, get_token_counter


class FixedWindowChunker:
    name = "fixed_window"

    def __init__(
        self,
        *,
        target_tokens: int = 512,
        overlap_tokens: int = 64,
        counter: TokenCounter | None = None,
    ) -> None:
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        if overlap_tokens >= target_tokens:
            raise ValueError("overlap_tokens must be smaller than target_tokens")
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens
        self._counter = counter or get_token_counter()

    def chunk(self, document: ParsedDocument) -> list[Chunk]:
        blocks = sorted(document.blocks, key=lambda b: b.order)
        if not blocks:
            return []

        # Word-level windowing approximates token windowing closely enough for
        # a baseline, and keeps the cut points from landing inside a word.
        words: list[str] = []
        # Page/slide of the block each word came from, so the baseline still
        # produces usable citations.
        origins: list[tuple[int | None, int | None]] = []

        for block in blocks:
            text = block.text.strip()
            if not text:
                continue
            for word in text.split():
                words.append(word)
                origins.append((block.page_number, block.slide_number))

        if not words:
            return []

        # Convert the token budget into a word budget once, using the document
        # overall rather than per window.
        sample = " ".join(words[:2000])
        tokens = max(self._counter.count(sample), 1)
        tokens_per_word = tokens / max(len(words[:2000]), 1)
        window = max(int(self.target_tokens / tokens_per_word), 1)
        stride = max(window - int(self.overlap_tokens / tokens_per_word), 1)

        section = next((b.text for b in blocks if b.type == BlockType.HEADING), None)

        chunks: list[Chunk] = []
        start = 0
        index = 0
        while start < len(words):
            end = min(start + window, len(words))
            text = " ".join(words[start:end])
            page, slide = origins[start]

            chunks.append(
                Chunk(
                    id=chunk_id_for(document.document_id, text),
                    text=text,
                    token_count=self._counter.count(text),
                    metadata=ChunkMetadata(
                        document_id=document.document_id,
                        document_name=document.filename,
                        chunk_index=index,
                        page_number=page,
                        page_end=last_page(
                            page, [origin[0] for origin in origins[start:end]]
                        ),
                        slide_number=slide,
                        section=section,
                        heading=None,
                        heading_path=[],
                        char_start=None,
                        char_end=None,
                        content_hash=content_hash(text),
                    ),
                )
            )
            index += 1
            if end >= len(words):
                break
            start += stride

        return chunks
