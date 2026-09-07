from __future__ import annotations

import pytest

from app.core.models import (
    BlockType,
    ContentSource,
    DocumentKind,
    ExtractedImage,
    ParsedBlock,
    ParsedDocument,
)
from app.ingestion.vision_enrichment import (
    enrich_with_vision,
    select_images,
)
from app.vision.providers import (
    GeminiVisionProvider,
    MockVisionProvider,
    NoOpVisionProvider,
    VisionUnavailableError,
)


def _image(
    *,
    width: int = 800,
    height: int = 600,
    order: int = 0,
    slide: int | None = 1,
    content_hash: str = "hash-a",
    data: bytes = b"\x89PNG-fake",
) -> ExtractedImage:
    return ExtractedImage(
        data=data,
        mime_type="image/png",
        order=order,
        slide_number=slide,
        width=width,
        height=height,
        content_hash=content_hash,
    )


def _document(*images: ExtractedImage) -> ParsedDocument:
    return ParsedDocument(
        document_id="doc-1",
        filename="lecture.pptx",
        kind=DocumentKind.PPTX,
        blocks=[
            ParsedBlock(
                text="Slide title",
                type=BlockType.HEADING,
                slide_number=1,
                level=1,
                order=0,
            )
        ],
        images=list(images),
    )


class TestImageSelection:
    def test_skips_images_too_small_to_be_content(self) -> None:
        # A 40x40 image is a bullet glyph or an icon, never study material,
        # and every image sent costs an API call.
        chosen, stats = select_images([_image(width=40, height=40)], min_pixels=40_000)

        assert chosen == []
        assert stats.skipped_small == 1

    def test_keeps_substantive_images(self) -> None:
        chosen, stats = select_images([_image()], min_pixels=40_000)

        assert len(chosen) == 1
        assert stats.skipped_small == 0

    def test_describes_a_repeated_image_only_once(self) -> None:
        # A logo on every slide would otherwise cost one call per slide. On a
        # 15 RPM free tier that is the difference between minutes and an hour.
        images = [
            _image(order=i, slide=i + 1, content_hash="same-logo") for i in range(30)
        ]
        chosen, stats = select_images(images)

        assert len(chosen) == 1
        assert stats.skipped_duplicate == 29

    def test_caps_the_number_of_calls_per_document(self) -> None:
        images = [_image(order=i, content_hash=f"h{i}") for i in range(60)]
        chosen, stats = select_images(images, max_images=10)

        assert len(chosen) == 10
        assert stats.skipped_budget == 50

    def test_an_image_with_unknown_dimensions_is_not_discarded(self) -> None:
        # Some formats do not report a size; dropping those would silently lose
        # real content.
        chosen, _ = select_images([_image(width=0, height=0)], min_pixels=40_000)
        assert len(chosen) == 1


class TestEnrichment:
    async def test_adds_a_figure_block_for_each_image(self) -> None:
        document = _document(_image())
        result = await enrich_with_vision(document, MockVisionProvider())

        figures = [b for b in document.blocks if b.type == BlockType.FIGURE]
        assert len(figures) == 1
        assert result.described == 1

    async def test_figure_blocks_are_marked_as_vision_derived(self) -> None:
        # The marker that follows the text all the way to the citation.
        document = _document(_image())
        await enrich_with_vision(document, MockVisionProvider())

        figure = next(b for b in document.blocks if b.type == BlockType.FIGURE)
        assert figure.source is ContentSource.VISION

    async def test_figures_keep_their_place_in_reading_order(self) -> None:
        document = _document(_image(order=5))
        document.blocks.append(
            ParsedBlock(text="Later text", type=BlockType.PARAGRAPH, order=9)
        )
        await enrich_with_vision(document, MockVisionProvider())

        orders = [b.order for b in document.blocks]
        assert orders == sorted(orders)

    async def test_figures_carry_their_slide_number_for_citation(self) -> None:
        document = _document(_image(slide=14))
        await enrich_with_vision(document, MockVisionProvider())

        figure = next(b for b in document.blocks if b.type == BlockType.FIGURE)
        assert figure.slide_number == 14

    async def test_one_description_is_reused_at_every_occurrence(self) -> None:
        # Deduplication saves the call, but the block still has to appear at
        # each position the image did.
        provider = MockVisionProvider()
        document = _document(
            _image(order=1, slide=1, content_hash="same"),
            _image(order=2, slide=2, content_hash="same"),
        )
        await enrich_with_vision(document, provider)

        figures = [b for b in document.blocks if b.type == BlockType.FIGURE]
        assert provider.calls == 1
        assert {f.slide_number for f in figures} == {1, 2}

    async def test_a_failed_call_is_counted_apart_from_a_blank_image(self) -> None:
        # The distinction that matters when reading an ingest report: "no
        # images worth reading" and "we could not read the images" look
        # identical otherwise.
        class Failing(MockVisionProvider):
            async def describe(self, image, *, mime_type, context_hint=""):
                raise VisionUnavailableError("rate limited")

        document = _document(_image())
        result = await enrich_with_vision(document, Failing())

        assert result.failed == 1
        assert result.no_content == 0
        assert result.described == 0
        # The ingest still succeeds; only this figure is missing.
        assert [b for b in document.blocks if b.type == BlockType.FIGURE] == []

    async def test_a_decorative_image_adds_nothing(self) -> None:
        class Decorative(MockVisionProvider):
            async def describe(self, image, *, mime_type, context_hint=""):
                return None

        document = _document(_image())
        result = await enrich_with_vision(document, Decorative())

        assert [b for b in document.blocks if b.type == BlockType.FIGURE] == []
        assert result.no_content == 1

    async def test_the_noop_provider_changes_nothing(self) -> None:
        document = _document(_image())
        before = len(document.blocks)
        result = await enrich_with_vision(document, NoOpVisionProvider())

        assert len(document.blocks) == before
        assert result.described == 0

    async def test_a_document_with_no_images_is_untouched(self) -> None:
        document = _document()
        result = await enrich_with_vision(document, MockVisionProvider())

        assert result.described == 0
        assert len(document.blocks) == 1


class TestGeminiVisionProvider:
    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ValueError, match="requires an API key"):
            GeminiVisionProvider("")

    async def test_empty_image_bytes_short_circuit(self) -> None:
        assert (
            await GeminiVisionProvider("key").describe(b"", mime_type="image/png")
            is None
        )

    async def test_a_failure_is_distinguishable_from_a_blank_image(
        self, monkeypatch
    ) -> None:
        # Returning None for a quota failure would report it as "this image
        # had nothing worth reading", which is actively misleading when the
        # image was the whole point.
        import app.vision.providers as module

        async def boom(*args, **kwargs):
            raise RuntimeError("quota exhausted")

        monkeypatch.setattr(module, "with_retries", boom)
        with pytest.raises(VisionUnavailableError):
            await GeminiVisionProvider("key").describe(
                b"fake-image", mime_type="image/png"
            )


class TestChunkProvenance:
    def test_a_figure_becomes_a_chunk_marked_vision(self) -> None:
        from app.chunking.structural import StructuralChunker
        from app.core.tokenizer import HeuristicTokenCounter

        document = ParsedDocument(
            document_id="doc-1",
            filename="lecture.pptx",
            kind=DocumentKind.PPTX,
            blocks=[
                ParsedBlock(
                    text="IR Evaluation",
                    type=BlockType.HEADING,
                    slide_number=14,
                    level=1,
                    order=0,
                ),
                ParsedBlock(
                    text="Precision measures the proportion of documents in the "
                    "result set that are actually relevant.",
                    type=BlockType.FIGURE,
                    slide_number=14,
                    order=1,
                    source=ContentSource.VISION,
                ),
            ],
        )
        chunks = StructuralChunker(
            counter=HeuristicTokenCounter(), min_tokens=0
        ).chunk(document)

        assert len(chunks) == 1
        assert chunks[0].metadata.source is ContentSource.VISION

    def test_a_text_chunk_stays_marked_text(self) -> None:
        from app.chunking.structural import StructuralChunker
        from app.core.tokenizer import HeuristicTokenCounter

        document = ParsedDocument(
            document_id="doc-1",
            filename="notes.pdf",
            kind=DocumentKind.PDF,
            blocks=[
                ParsedBlock(
                    text="A relation is in third normal form.",
                    type=BlockType.PARAGRAPH,
                    page_number=1,
                    order=0,
                )
            ],
        )
        chunks = StructuralChunker(counter=HeuristicTokenCounter()).chunk(document)
        assert chunks[0].metadata.source is ContentSource.TEXT


class TestCitationMarking:
    def test_a_vision_chunk_says_so_in_its_context_header(self) -> None:
        # Both the prompt and the rendered citation must say the text was read
        # from a picture, so a wrong transcription cannot masquerade as the
        # document's own words.
        from app.core.models import Chunk, ChunkMetadata, ScoredChunk
        from app.core.tokenizer import HeuristicTokenCounter
        from app.generation.context import ContextBuilder

        scored = ScoredChunk(
            chunk=Chunk(
                id="c1",
                text="precision = 30/(30+10) = .75",
                token_count=10,
                metadata=ChunkMetadata(
                    document_id="d1",
                    document_name="lecture.pptx",
                    chunk_index=0,
                    slide_number=14,
                    heading_path=["IR System Evaluation"],
                    content_hash="h" * 64,
                    source=ContentSource.VISION,
                ),
            ),
            score=1.0,
        )
        context = ContextBuilder(counter=HeuristicTokenCounter()).build([scored])

        assert "read from an image" in context.text
        assert "slide 14" in context.text
