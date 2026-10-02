from __future__ import annotations

from app.parsing.structure import (
    HeadingStack,
    LineRecord,
    body_ceiling,
    body_font_size,
    detect_heading_levels,
)


def _line(
    text: str, size: float, order: int, *, bold: bool = False, page: int = 1
) -> LineRecord:
    return LineRecord(
        text=text,
        font_size=size,
        is_bold=bold,
        page_number=page,
        bbox=(0.0, float(order * 20), 500.0, float(order * 20 + 12)),
        order=order,
    )


class TestBodyFontSize:
    def test_picks_the_size_carrying_the_most_text(self) -> None:
        lines = [
            _line("Title", 24.0, 0),
            _line("A long paragraph of body text here", 11.0, 1),
            _line("Another long paragraph of body text", 11.0, 2),
        ]
        assert body_font_size(lines) == 11.0

    def test_weights_by_characters_not_line_count(self) -> None:
        # A title page has many short large-text lines. Counting lines would
        # make 24pt the "body" size and suppress every real heading after it.
        lines = [_line("Big", 24.0, i) for i in range(5)]
        lines.append(_line("x" * 400, 11.0, 5))
        assert body_font_size(lines) == 11.0

    def test_empty_input_returns_zero(self) -> None:
        assert body_font_size([]) == 0.0


class TestBodyCeiling:
    def test_is_the_body_size_when_there_is_one(self) -> None:
        lines = [_line("Title", 24.0, 0), _line("x" * 400, 11.0, 1)]
        assert body_ceiling(lines) == 11.0

    def test_takes_in_a_second_size_that_carries_real_text(self) -> None:
        # A slide deck with bullets at 20pt and 22pt: both are body copy.
        lines = [_line("x" * 700, 20.0, 0), _line("y" * 300, 22.0, 1)]
        assert body_ceiling(lines) == 22.0

    def test_leaves_out_a_size_carrying_little(self) -> None:
        lines = [_line("x" * 700, 20.0, 0), _line("Slide title", 26.0, 1)]
        assert body_ceiling(lines) == 20.0


class TestDetectHeadingLevels:
    def test_ranks_larger_sizes_as_shallower_levels(self) -> None:
        lines = [
            _line("Document Title", 20.0, 0),
            _line("body text that is long enough to dominate", 11.0, 1),
            _line("Section Heading", 15.0, 2),
            _line("more body text that is long enough here", 11.0, 3),
        ]
        levels = detect_heading_levels(lines)

        assert levels[0] == 1  # 20pt is the largest
        assert levels[2] == 2  # 15pt sits below it
        assert 1 not in levels  # body text is not a heading
        assert 3 not in levels

    def test_merges_near_identical_sizes_into_one_level(self) -> None:
        # 13.98pt and 14.0pt are the same heading level in any real document.
        lines = [
            _line("Heading A", 14.0, 0),
            _line("Heading B", 13.98, 1),
            _line("body text long enough to be dominant here", 11.0, 2),
        ]
        levels = detect_heading_levels(lines)
        assert levels[0] == levels[1]

    def test_bold_body_size_text_can_still_be_a_heading(self) -> None:
        # Some documents mark structure with weight rather than size.
        lines = [
            _line("body text long enough to dominate the document", 11.0, 0),
            _line("Third Normal Form", 11.0, 1, bold=True),
            _line("more body text long enough to dominate here", 11.0, 2),
        ]
        levels = detect_heading_levels(lines)
        assert 1 in levels

    def test_bold_prose_is_not_treated_as_a_heading(self) -> None:
        lines = [
            _line("body text long enough to dominate the document", 11.0, 0),
            _line(
                "This is a full sentence set in bold for emphasis, not a heading.",
                11.0,
                1,
                bold=True,
            ),
        ]
        assert 1 not in detect_heading_levels(lines)

    def test_body_copy_at_a_second_size_is_not_read_as_headings(self) -> None:
        # The Water Pollution deck: a fifth of its text set at 22pt beside
        # 20pt. Read as headings, those slides vanished from the index.
        body = "Point sources discharge pollutants at specific locations " * 3
        lines = [
            _line("Water pollution sources", 26.0, 0),
            *[_line(body, 22.0, i) for i in range(1, 5)],
            *[_line(body, 20.0, i) for i in range(5, 15)],
        ]
        levels = detect_heading_levels(lines)

        assert levels == {0: 1}

    def test_a_large_bullet_glyph_is_not_a_heading(self) -> None:
        lines = [
            _line("body text long enough to dominate the document", 11.0, 0),
            _line("•", 16.0, 1),
        ]
        assert 1 not in detect_heading_levels(lines)

    def test_a_document_with_no_headings_yields_none(self) -> None:
        lines = [_line("uniform body text here", 11.0, i) for i in range(5)]
        assert detect_heading_levels(lines) == {}

    def test_empty_input_is_safe(self) -> None:
        assert detect_heading_levels([]) == {}


class TestHeadingStack:
    def test_builds_a_nested_path(self) -> None:
        stack = HeadingStack()
        stack.push(1, "Unit 3")
        stack.push(2, "Normalization")
        stack.push(3, "Third Normal Form")

        assert stack.path == ["Unit 3", "Normalization", "Third Normal Form"]
        assert stack.current == "Third Normal Form"
        assert stack.section == "Unit 3"

    def test_a_shallower_heading_pops_deeper_ones(self) -> None:
        stack = HeadingStack()
        stack.push(1, "Unit 3")
        stack.push(2, "Normalization")
        stack.push(3, "Third Normal Form")
        stack.push(2, "Denormalization")

        # 3NF must not remain in the path once a sibling section starts.
        assert stack.path == ["Unit 3", "Denormalization"]

    def test_a_same_level_heading_replaces_its_predecessor(self) -> None:
        stack = HeadingStack()
        stack.push(2, "First Normal Form")
        stack.push(2, "Second Normal Form")
        assert stack.path == ["Second Normal Form"]

    def test_handles_skipped_levels(self) -> None:
        stack = HeadingStack()
        stack.push(1, "Unit 3")
        stack.push(4, "Deeply Nested")
        assert stack.path == ["Unit 3", "Deeply Nested"]

    def test_copy_is_independent(self) -> None:
        stack = HeadingStack()
        stack.push(1, "Original")
        clone = stack.copy()
        clone.push(2, "Added")

        assert stack.path == ["Original"]
        assert clone.path == ["Original", "Added"]

    def test_empty_stack_reports_nothing(self) -> None:
        stack = HeadingStack()
        assert stack.path == []
        assert stack.current is None
        assert stack.section is None
