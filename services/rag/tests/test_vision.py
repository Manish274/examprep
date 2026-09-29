from __future__ import annotations

import json
from collections.abc import Sequence

import httpx
import pytest

from app.core.gemini import rate_limit_error
from app.core.models import (
    BlockType,
    ContentSource,
    DocumentKind,
    ExtractedImage,
    ParsedBlock,
    ParsedDocument,
)
from app.embedding.rate_limit import QuotaExhaustedError, RateLimitError
from app.ingestion.vision_enrichment import (
    batch_images,
    enrich_with_vision,
    select_images,
)
from app.vision.cache import InMemoryVisionCache
from app.vision.providers import (
    GeminiVisionProvider,
    MockVisionProvider,
    VisionQuotaExhaustedError,
    VisionUnavailableError,
    read_batch_response,
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


def _images(count: int) -> list[ExtractedImage]:
    return [_image(order=i, slide=i + 1, content_hash=f"h{i}") for i in range(count)]


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


def _figures(document: ParsedDocument) -> list[ParsedBlock]:
    return [b for b in document.blocks if b.type == BlockType.FIGURE]


class Scripted(MockVisionProvider):
    """Answers each batch from a script: a reading, or an exception to raise."""

    def __init__(self, *outcomes: object) -> None:
        super().__init__()
        self.outcomes = list(outcomes)
        self.batches: list[list[str]] = []

    async def describe(self, images: Sequence[ExtractedImage]) -> dict[str, str | None]:
        self.batches.append([i.content_hash for i in images])
        outcome = self.outcomes.pop(0) if self.outcomes else "read"
        if isinstance(outcome, Exception):
            raise outcome
        return {i.content_hash: outcome for i in images}  # type: ignore[misc]


class TestImageSelection:
    def test_skips_images_too_small_to_be_content(self) -> None:
        # A 40x40 image is a bullet glyph or an icon, never study material,
        # and every image sent costs quota.
        chosen, stats = select_images([_image(width=40, height=40)], min_pixels=40_000)

        assert chosen == []
        assert stats.skipped_small == 1

    def test_keeps_substantive_images(self) -> None:
        chosen, stats = select_images([_image()], min_pixels=40_000)

        assert len(chosen) == 1
        assert stats.skipped_small == 0

    def test_reads_a_repeated_image_only_once(self) -> None:
        # A logo on every slide would otherwise cost a reading per slide.
        images = [
            _image(order=i, slide=i + 1, content_hash="same-logo") for i in range(30)
        ]
        chosen, stats = select_images(images)

        assert len(chosen) == 1
        assert stats.skipped_duplicate == 29

    def test_an_image_with_unknown_dimensions_is_not_discarded(self) -> None:
        # Some formats do not report a size; dropping those would silently lose
        # real content.
        chosen, _ = select_images([_image(width=0, height=0)], min_pixels=40_000)
        assert len(chosen) == 1


class TestBatching:
    def test_groups_images_up_to_the_batch_size_in_document_order(self) -> None:
        batches = batch_images(_images(10), 4)
        assert [[i.content_hash for i in b] for b in batches] == [
            ["h0", "h1", "h2", "h3"],
            ["h4", "h5", "h6", "h7"],
            ["h8", "h9"],
        ]

    def test_starts_a_new_request_before_the_size_limit(self) -> None:
        # Gemini refuses a request over 20 MB; three 5 MB pages must not share one.
        big = [
            _image(order=i, content_hash=f"p{i}", data=b"x" * (5 * 1024 * 1024))
            for i in range(3)
        ]
        assert [len(b) for b in batch_images(big, 4)] == [2, 1]


class TestEnrichment:
    async def test_adds_a_figure_block_for_each_image(self) -> None:
        document = _document(_image())
        result = await enrich_with_vision(
            document, MockVisionProvider(), InMemoryVisionCache()
        )

        assert len(_figures(document)) == 1
        assert result.described == 1

    async def test_figure_blocks_are_marked_as_vision_derived(self) -> None:
        # The marker that follows the text all the way to the citation.
        document = _document(_image())
        await enrich_with_vision(document, MockVisionProvider(), InMemoryVisionCache())

        assert _figures(document)[0].source is ContentSource.VISION

    async def test_figures_keep_their_place_in_reading_order(self) -> None:
        document = _document(_image(order=5))
        document.blocks.append(
            ParsedBlock(text="Later text", type=BlockType.PARAGRAPH, order=9)
        )
        await enrich_with_vision(document, MockVisionProvider(), InMemoryVisionCache())

        orders = [b.order for b in document.blocks]
        assert orders == sorted(orders)

    async def test_figures_carry_their_slide_number_for_citation(self) -> None:
        document = _document(_image(slide=14))
        await enrich_with_vision(document, MockVisionProvider(), InMemoryVisionCache())

        assert _figures(document)[0].slide_number == 14

    async def test_one_reading_is_reused_at_every_occurrence(self) -> None:
        # Deduplication saves the reading, but the block still has to appear
        # at each position the image did.
        provider = MockVisionProvider()
        document = _document(
            _image(order=1, slide=1, content_hash="same"),
            _image(order=2, slide=2, content_hash="same"),
        )
        await enrich_with_vision(document, provider, InMemoryVisionCache())

        assert provider.images_read == 1
        assert {f.slide_number for f in _figures(document)} == {1, 2}

    async def test_images_are_read_several_to_a_request(self) -> None:
        provider = MockVisionProvider()
        await enrich_with_vision(
            _document(*_images(10)), provider, InMemoryVisionCache(), batch_size=4
        )

        assert provider.calls == 3
        assert provider.images_read == 10

    async def test_a_failed_call_is_counted_apart_from_a_blank_image(self) -> None:
        # "No images worth reading" and "we could not read the images" look
        # identical in an ingest report otherwise.
        document = _document(_image())
        result = await enrich_with_vision(
            document,
            Scripted(VisionUnavailableError("upstream 503")),
            InMemoryVisionCache(),
        )

        assert result.failed == 1
        assert result.no_content == 0
        assert result.described == 0
        # The ingest still succeeds; only this figure is missing.
        assert _figures(document) == []

    async def test_an_image_the_model_did_not_answer_counts_as_failed(self) -> None:
        class Partial(MockVisionProvider):
            async def describe(self, images):
                return {images[0].content_hash: "the first one"}

        cache = InMemoryVisionCache()
        result = await enrich_with_vision(
            _document(*_images(2)), Partial(), cache, batch_size=2
        )

        assert result.described == 1
        assert result.failed == 1
        # Unanswered is not the same as empty: it must be asked again later.
        assert await cache.get_many(Partial.cache_key, ["h1"]) == {}

    async def test_a_decorative_image_adds_nothing(self) -> None:
        document = _document(_image())
        result = await enrich_with_vision(
            document, Scripted(None), InMemoryVisionCache()
        )

        assert _figures(document) == []
        assert result.no_content == 1

    async def test_a_document_with_no_images_is_untouched(self) -> None:
        document = _document()
        result = await enrich_with_vision(
            document, MockVisionProvider(), InMemoryVisionCache()
        )

        assert result.described == 0
        assert len(document.blocks) == 1

    async def test_reports_progress_as_batches_finish(self) -> None:
        seen: list[tuple[int, int]] = []
        await enrich_with_vision(
            _document(*_images(5)),
            MockVisionProvider(),
            InMemoryVisionCache(),
            batch_size=2,
            concurrency=1,
            on_progress=lambda done, total: seen.append((done, total)),
        )

        assert seen == [(0, 5), (2, 5), (4, 5), (5, 5)]


class TestCache:
    async def test_a_second_ingest_reads_nothing_again(self) -> None:
        # A retried upload, or a second student's copy of the same slides.
        cache = InMemoryVisionCache()
        first = MockVisionProvider()
        await enrich_with_vision(_document(*_images(3)), first, cache)

        second = MockVisionProvider()
        document = _document(*_images(3))
        result = await enrich_with_vision(document, second, cache)

        assert second.calls == 0
        assert result.cached == 3
        assert len(_figures(document)) == 3

    async def test_a_decorative_verdict_is_cached_too(self) -> None:
        cache = InMemoryVisionCache()
        await enrich_with_vision(_document(_image()), Scripted(None), cache)

        again = MockVisionProvider()
        result = await enrich_with_vision(_document(_image()), again, cache)

        assert again.calls == 0
        assert result.no_content == 1

    async def test_a_failure_is_not_cached(self) -> None:
        cache = InMemoryVisionCache()
        await enrich_with_vision(
            _document(_image()), Scripted(VisionUnavailableError("503")), cache
        )

        again = MockVisionProvider()
        await enrich_with_vision(_document(_image()), again, cache)
        assert again.images_read == 1

    async def test_readings_are_filed_under_the_prompt_version(self) -> None:
        # A prompt change must not serve readings made under the old one.
        provider = GeminiVisionProvider("key", model_id="gemini-x")
        assert provider.cache_key.startswith("gemini-x:v")


class TestDeferral:
    async def test_a_cache_only_pass_reads_nothing_and_counts_what_is_left(
        self,
    ) -> None:
        provider = MockVisionProvider()
        document = _document(*_images(3))
        result = await enrich_with_vision(
            document, provider, InMemoryVisionCache(), read=False
        )

        assert provider.calls == 0
        assert result.pending == 3
        assert _figures(document) == []

    async def test_a_cache_only_pass_still_uses_what_is_cached(self) -> None:
        cache = InMemoryVisionCache()
        await enrich_with_vision(_document(*_images(2)), MockVisionProvider(), cache)

        document = _document(*_images(3))
        result = await enrich_with_vision(
            document, MockVisionProvider(), cache, read=False
        )

        assert len(_figures(document)) == 2
        assert result.pending == 1


class TestBudget:
    async def test_caps_new_readings_per_document(self) -> None:
        provider = MockVisionProvider()
        result = await enrich_with_vision(
            _document(*_images(60)), provider, InMemoryVisionCache(), max_images=10
        )

        assert provider.images_read == 10
        assert result.skipped_budget == 50

    async def test_cached_images_do_not_count_against_the_cap(self) -> None:
        cache = InMemoryVisionCache()
        await enrich_with_vision(
            _document(*_images(10)), MockVisionProvider(), cache, max_images=10
        )

        provider = MockVisionProvider()
        result = await enrich_with_vision(
            _document(*_images(20)), provider, cache, max_images=10
        )

        assert provider.images_read == 10
        assert result.described == 20
        assert result.skipped_budget == 0


class TestQuota:
    async def test_a_spent_daily_quota_stops_the_rest(self) -> None:
        # Before, every remaining image went through its own round of retries
        # and failed anyway -- a quarter of an hour to fail a scanned document.
        provider = Scripted(
            "read", VisionQuotaExhaustedError("daily quota"), "read", "read"
        )
        document = _document(*_images(8))
        result = await enrich_with_vision(
            document, provider, InMemoryVisionCache(), batch_size=2, concurrency=1
        )

        assert len(provider.batches) == 2
        assert result.described == 2
        assert result.skipped_quota == 6
        assert result.failed == 0

    async def test_what_was_read_before_the_quota_ran_out_is_kept(self) -> None:
        cache = InMemoryVisionCache()
        await enrich_with_vision(
            _document(*_images(4)),
            Scripted("read", VisionQuotaExhaustedError("daily quota")),
            cache,
            batch_size=2,
            concurrency=1,
        )

        # Tomorrow's attempt reads only the half that is missing.
        provider = MockVisionProvider()
        await enrich_with_vision(_document(*_images(4)), provider, cache)
        assert provider.images_read == 2


class TestOverloadedModel:
    async def test_two_failed_requests_in_a_row_stop_the_rest(self) -> None:
        # "This model is currently experiencing high demand" after a full
        # round of retries rarely clears for the next batch; waiting out the
        # same retries for every remaining batch only delays the ingest.
        overloaded = VisionUnavailableError("vision upstream 503")
        provider = Scripted(overloaded, overloaded, "read", "read")
        result = await enrich_with_vision(
            _document(*_images(8)),
            provider,
            InMemoryVisionCache(),
            batch_size=2,
            concurrency=1,
        )

        assert len(provider.batches) == 2
        assert result.failed == 8
        assert result.described == 0

    async def test_a_single_failure_does_not_stop_the_pass(self) -> None:
        provider = Scripted(VisionUnavailableError("503"), "read", "read")
        result = await enrich_with_vision(
            _document(*_images(6)),
            provider,
            InMemoryVisionCache(),
            batch_size=2,
            concurrency=1,
        )

        assert len(provider.batches) == 3
        assert result.failed == 2
        assert result.described == 4


def _gemini_429(quota_id: str, retry_delay: str | None = None) -> httpx.Response:
    details: list[dict[str, object]] = [
        {
            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
            "violations": [{"quotaId": quota_id}],
        }
    ]
    if retry_delay:
        details.append(
            {
                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                "retryDelay": retry_delay,
            }
        )
    return httpx.Response(
        429,
        json={
            "error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": details}
        },
    )


class TestGeminiRateLimits:
    def test_a_daily_quota_is_told_apart_from_a_per_minute_limit(self) -> None:
        daily = rate_limit_error(
            _gemini_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "40s"),
            "vision",
        )
        minute = rate_limit_error(
            _gemini_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "37s"),
            "vision",
        )

        assert isinstance(daily, QuotaExhaustedError)
        assert isinstance(minute, RateLimitError)

    def test_the_servers_retry_delay_is_honoured(self) -> None:
        error = rate_limit_error(
            _gemini_429(
                "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "37.5s"
            ),
            "vision",
        )
        assert isinstance(error, RateLimitError)
        assert error.retry_after == 37.5

    def test_an_unstructured_429_is_an_ordinary_rate_limit(self) -> None:
        error = rate_limit_error(httpx.Response(429, text="slow down"), "embedding")
        assert isinstance(error, RateLimitError)
        assert error.retry_after is None


def _response(entries: object) -> dict[str, object]:
    text = entries if isinstance(entries, str) else json.dumps(entries)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class TestGeminiVisionProvider:
    def test_requires_an_api_key(self) -> None:
        with pytest.raises(ValueError, match="requires an API key"):
            GeminiVisionProvider("")

    async def test_empty_image_bytes_short_circuit(self) -> None:
        assert await GeminiVisionProvider("key").describe([_image(data=b"")]) == {}

    def test_labels_every_image_in_the_request(self) -> None:
        images = _images(3)
        images[1].context_hint = "Figure 2: the B-tree after a split"
        payload = GeminiVisionProvider("key")._payload(images)
        texts = [p["text"] for p in payload["contents"][0]["parts"] if "text" in p]

        assert texts[1:] == [
            "Image 1",
            "Image 2 -- the surrounding text says: Figure 2: the B-tree after a split",
            "Image 3",
        ]
        assert payload["generationConfig"]["responseMimeType"] == "application/json"

    def test_readings_are_matched_by_number_not_position(self) -> None:
        # A model answering out of order must not shift descriptions onto the
        # wrong pictures.
        images = _images(3)
        readings = read_batch_response(
            _response(
                [
                    {"image": 3, "content": "third"},
                    {"image": 1, "content": "first"},
                    {"image": 2, "content": "NO_CONTENT"},
                ]
            ),
            images,
        )
        assert readings == {"h0": "first", "h1": None, "h2": "third"}

    def test_an_image_left_out_of_the_answer_is_left_out_of_the_readings(
        self,
    ) -> None:
        readings = read_batch_response(
            _response([{"image": 1, "content": "first"}, {"image": 9, "content": "?"}]),
            _images(2),
        )
        assert readings == {"h0": "first"}

    def test_an_unreadable_answer_fails_the_batch(self) -> None:
        with pytest.raises(VisionUnavailableError):
            read_batch_response(_response("not json at all"), _images(1))

    async def test_a_failure_is_distinguishable_from_a_blank_image(
        self, monkeypatch
    ) -> None:
        # Returning nothing for a failure would report it as "this image had
        # nothing worth reading", misleading when the image was the point.
        import app.vision.providers as module

        async def boom(*args, **kwargs):
            raise RuntimeError("upstream exploded")

        monkeypatch.setattr(module, "with_retries", boom)
        with pytest.raises(VisionUnavailableError):
            await GeminiVisionProvider("key").describe([_image()])

    async def test_a_spent_quota_is_reported_as_such(self, monkeypatch) -> None:
        import app.vision.providers as module

        async def spent(*args, **kwargs):
            raise QuotaExhaustedError("vision: daily quota used up")

        monkeypatch.setattr(module, "with_retries", spent)
        with pytest.raises(VisionQuotaExhaustedError):
            await GeminiVisionProvider("key").describe([_image()])


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
        chunks = StructuralChunker(counter=HeuristicTokenCounter(), min_tokens=0).chunk(
            document
        )

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
