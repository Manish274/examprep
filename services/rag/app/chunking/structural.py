"""Structure-aware chunking.

Chunk boundaries decide what retrieval can possibly return, so this is the
highest-leverage code in the ingestion path. Three rules drive it:

1. Never merge across a heading. Two topics in one chunk means every query
   matching either one drags in the other as noise.
2. Never split mid-sentence. A truncated sentence embeds poorly and reads badly
   as a citation.
3. Never lose the metadata. Page, slide and heading path ride along on every
   chunk, because a grounded answer is only as good as the source it points at.

Overlap is applied within a section only. Carrying sentences across a heading
would reintroduce exactly the contamination rule 1 exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.models import (
    BlockType,
    Chunk,
    ChunkMetadata,
    ContentSource,
    ParsedBlock,
    ParsedDocument,
)
from app.core.text import chunk_id_for, content_hash, split_sentences
from app.core.tokenizer import TokenCounter, get_token_counter
from app.parsing.structure import HeadingStack

# A single atomic piece (a table, or a runaway sentence) may exceed the target
# by this factor before it is force-split. Splitting a table destroys the
# row/column relationship, so some overflow is the better trade.
_OVERSIZE_TOLERANCE = 2.0

# Overlap is capped at this fraction of the target regardless of configuration,
# so a large overlap setting cannot starve a chunk of new content.
_MAX_OVERLAP_FRACTION = 0.5


@dataclass
class _Piece:
    """The smallest unit the packer moves around."""

    text: str
    tokens: int
    block: ParsedBlock
    char_start: int
    char_end: int
    atomic: bool = False


@dataclass
class _Segment:
    """A run of content under one heading path."""

    heading: str | None
    section: str | None
    heading_path: list[str]
    pieces: list[_Piece] = field(default_factory=list)


class StructuralChunker:
    name = "structural"

    def __init__(
        self,
        *,
        target_tokens: int = 512,
        overlap_tokens: int = 64,
        min_tokens: int = 64,
        counter: TokenCounter | None = None,
    ) -> None:
        if target_tokens <= 0:
            raise ValueError("target_tokens must be positive")
        self.target_tokens = target_tokens
        self.min_tokens = min_tokens
        self.overlap_tokens = min(
            overlap_tokens, int(target_tokens * _MAX_OVERLAP_FRACTION)
        )
        self._counter = counter or get_token_counter()

    # ── segmentation ────────────────────────────────────────

    def _build_segments(self, document: ParsedDocument) -> list[_Segment]:
        """Splits the block stream into segments, one per heading.

        Character offsets are assigned against a virtual document formed by
        joining block texts with blank lines. They are approximate against the
        original file -- PDFs have no single canonical text offset -- but they
        are consistent within the document, which is what a highlight needs.
        """
        stack = HeadingStack()
        segments: list[_Segment] = []
        current = _Segment(heading=None, section=None, heading_path=[])
        offset = 0

        for block in sorted(document.blocks, key=lambda b: b.order):
            text = block.text.strip()
            if not text:
                continue

            if block.type == BlockType.HEADING:
                if current.pieces:
                    segments.append(current)
                stack.push(block.level or 1, text)
                current = _Segment(
                    heading=stack.current,
                    section=stack.section,
                    heading_path=stack.path,
                )
                # The heading occupies space in the virtual document even
                # though it becomes metadata rather than chunk content.
                offset += len(text) + 2
                continue

            current.pieces.extend(self._to_pieces(block, offset))
            offset += len(text) + 2

        if current.pieces:
            segments.append(current)
        return segments

    def _to_pieces(self, block: ParsedBlock, offset: int) -> list[_Piece]:
        text = block.text.strip()

        # Tables, speaker notes and figure descriptions are kept whole: a
        # table loses its meaning when split, a note is a single thought, and
        # splitting a transcribed image would mix generated text with extracted
        # text inside one chunk, making its provenance unrepresentable.
        if block.type in {
            BlockType.TABLE,
            BlockType.SPEAKER_NOTE,
            BlockType.FIGURE,
        }:
            return [
                _Piece(
                    text=text,
                    tokens=self._counter.count(text),
                    block=block,
                    char_start=offset,
                    char_end=offset + len(text),
                    atomic=True,
                )
            ]

        pieces: list[_Piece] = []
        cursor = 0
        for sentence in split_sentences(text):
            found = text.find(sentence, cursor)
            start = found if found >= 0 else cursor
            end = start + len(sentence)
            cursor = end
            pieces.append(
                _Piece(
                    text=sentence,
                    tokens=self._counter.count(sentence),
                    block=block,
                    char_start=offset + start,
                    char_end=offset + end,
                )
            )

        if not pieces:
            pieces.append(
                _Piece(
                    text=text,
                    tokens=self._counter.count(text),
                    block=block,
                    char_start=offset,
                    char_end=offset + len(text),
                )
            )
        return pieces

    # ── packing ─────────────────────────────────────────────

    def _overlap_tail(self, pieces: list[_Piece]) -> list[_Piece]:
        """The trailing sentences to repeat at the head of the next chunk."""
        if self.overlap_tokens <= 0:
            return []

        tail: list[_Piece] = []
        total = 0
        for piece in reversed(pieces):
            if piece.atomic:
                break
            if total + piece.tokens > self.overlap_tokens:
                break
            tail.insert(0, piece)
            total += piece.tokens
        return tail

    def _pack(self, segment: _Segment) -> list[list[_Piece]]:
        groups: list[list[_Piece]] = []
        current: list[_Piece] = []
        current_tokens = 0

        for piece in segment.pieces:
            # An oversized atomic piece becomes its own chunk rather than
            # dragging a partial neighbour along with it.
            if piece.atomic and piece.tokens > self.target_tokens:
                if current:
                    groups.append(current)
                    current, current_tokens = [], 0
                groups.extend(self._split_oversized(piece))
                continue

            if current and current_tokens + piece.tokens > self.target_tokens:
                groups.append(current)
                current = self._overlap_tail(current)
                current_tokens = sum(p.tokens for p in current)

                # If the overlap alone leaves no room, drop it: new content
                # matters more than continuity.
                if current_tokens + piece.tokens > self.target_tokens:
                    current, current_tokens = [], 0

            current.append(piece)
            current_tokens += piece.tokens

        if current:
            groups.append(current)
        return groups

    def _split_oversized(self, piece: _Piece) -> list[list[_Piece]]:
        """Force-splits a piece that exceeds even the overflow tolerance."""
        if piece.tokens <= self.target_tokens * _OVERSIZE_TOLERANCE:
            return [[piece]]

        groups: list[list[_Piece]] = []
        current: list[_Piece] = []
        current_tokens = 0
        cursor = piece.char_start

        # Tables split on row boundaries; anything else on line boundaries.
        for line in piece.text.split("\n"):
            if not line.strip():
                continue
            tokens = self._counter.count(line)
            if current and current_tokens + tokens > self.target_tokens:
                groups.append(current)
                current, current_tokens = [], 0
            current.append(
                _Piece(
                    text=line,
                    tokens=tokens,
                    block=piece.block,
                    char_start=cursor,
                    char_end=cursor + len(line),
                    atomic=True,
                )
            )
            current_tokens += tokens
            cursor += len(line) + 1

        if current:
            groups.append(current)
        return groups or [[piece]]

    # ── assembly ────────────────────────────────────────────

    def _merge_undersized(
        self, groups: list[tuple[_Segment, list[_Piece]]]
    ) -> list[tuple[_Segment, list[_Piece]]]:
        """Folds a too-small chunk into the next one when they belong together.

        A heading with a single sentence under it ("3.1 Overview") would
        otherwise become a chunk too thin to ever rank. Merging is only allowed
        into a descendant or sibling-of-same-path segment, so unrelated topics
        are never joined.
        """
        if self.min_tokens <= 0:
            return groups

        merged: list[tuple[_Segment, list[_Piece]]] = []
        index = 0
        while index < len(groups):
            segment, pieces = groups[index]
            tokens = sum(p.tokens for p in pieces)

            if (
                tokens < self.min_tokens
                and index + 1 < len(groups)
                and self._can_merge(segment, groups[index + 1][0])
            ):
                next_segment, next_pieces = groups[index + 1]
                combined = pieces + next_pieces
                ceiling = self.target_tokens * _OVERSIZE_TOLERANCE
                if sum(p.tokens for p in combined) <= ceiling:
                    # Keep the deeper heading path. _can_merge only permits a
                    # descendant or an identical path, so the deeper one has
                    # the shallower as its prefix and is strictly more
                    # informative. Keeping the parent instead would erase the
                    # child heading from the index entirely -- and a heading
                    # like "Third Normal Form" is exactly the phrase a student
                    # searches for.
                    deeper = (
                        next_segment
                        if len(next_segment.heading_path) > len(segment.heading_path)
                        else segment
                    )
                    merged.append((deeper, combined))
                    index += 2
                    continue

            merged.append((segment, pieces))
            index += 1
        return merged

    @staticmethod
    def _can_merge(first: _Segment, second: _Segment) -> bool:
        a, b = first.heading_path, second.heading_path
        if a == b:
            return True
        # Second is nested under first: "3.1" followed by "3.1.1".
        return len(b) > len(a) and b[: len(a)] == a

    def chunk(self, document: ParsedDocument) -> list[Chunk]:
        segments = self._build_segments(document)

        groups: list[tuple[_Segment, list[_Piece]]] = []
        for segment in segments:
            for pieces in self._pack(segment):
                if pieces:
                    groups.append((segment, pieces))

        groups = self._merge_undersized(groups)

        chunks: list[Chunk] = []
        for index, (segment, pieces) in enumerate(groups):
            text = " ".join(p.text for p in pieces).strip()
            if not text:
                continue

            first = pieces[0].block
            chunks.append(
                Chunk(
                    id=chunk_id_for(document.document_id, text),
                    text=text,
                    token_count=self._counter.count(text),
                    metadata=ChunkMetadata(
                        document_id=document.document_id,
                        document_name=document.filename,
                        chunk_index=index,
                        page_number=first.page_number,
                        slide_number=first.slide_number,
                        section=segment.section,
                        heading=segment.heading,
                        heading_path=list(segment.heading_path),
                        char_start=pieces[0].char_start,
                        char_end=pieces[-1].char_end,
                        content_hash=content_hash(text),
                        # A chunk is vision-derived only if every piece in it
                        # is; a mixed chunk would misrepresent the extracted
                        # half as generated or the reverse.
                        source=(
                            ContentSource.VISION
                            if all(
                                p.block.source is ContentSource.VISION for p in pieces
                            )
                            else ContentSource.TEXT
                        ),
                    ),
                )
            )
        return chunks
