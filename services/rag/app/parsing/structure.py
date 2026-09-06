"""Infers document structure from typography.

A PDF carries no headings -- only glyphs at sizes and positions. Recovering the
hierarchy matters more here than it might elsewhere: the heading path is what
gives an isolated chunk its subject, and it is what a student sees as the
citation next to an answer.

The approach is font-size clustering. Establish the body text size, treat
anything meaningfully larger as a heading, then rank the distinct heading sizes
into levels. Bold short lines at body size are a secondary signal, which
recovers headings in documents that use weight rather than size.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from app.core.text import looks_like_heading

# A line must be at least this much larger than body text to count on size
# alone. Below ~8 percent the difference is usually incidental leading.
_SIZE_RATIO = 1.08

# Sizes within this fraction of each other are treated as the same level, so
# 13.98pt and 14.0pt do not become two separate heading levels.
_LEVEL_TOLERANCE = 0.03

_MAX_LEVEL = 6


@dataclass
class LineRecord:
    """One extracted line, with the typography needed to classify it."""

    text: str
    font_size: float
    is_bold: bool
    page_number: int
    bbox: tuple[float, float, float, float]
    order: int


def body_font_size(lines: list[LineRecord]) -> float:
    """The dominant text size, weighted by how much text is set in it.

    Weighting by character count rather than line count matters: a title page
    can hold many short large-text lines, which would otherwise be mistaken for
    the body size and suppress every real heading in the document.
    """
    if not lines:
        return 0.0

    weights: Counter[float] = Counter()
    for line in lines:
        weights[round(line.font_size, 1)] += max(len(line.text.strip()), 1)

    if not weights:
        return 0.0
    return weights.most_common(1)[0][0]


def _is_heading(line: LineRecord, body_size: float) -> bool:
    text = line.text.strip()
    if not text or len(text) > 200:
        return False

    if body_size > 0 and line.font_size >= body_size * _SIZE_RATIO:
        return True

    # Same size as body: require bold *and* a heading-like shape, since bold is
    # also used for emphasis inside paragraphs.
    return bool(line.is_bold and looks_like_heading(text))


def detect_heading_levels(lines: list[LineRecord]) -> dict[int, int]:
    """Maps line order index to heading level (1-6). Absent means body text.

    Levels come from ranking the distinct heading sizes largest-first, so the
    result is relative to the document rather than to absolute point sizes --
    which is what makes it work across differently-styled material.
    """
    if not lines:
        return {}

    body_size = body_font_size(lines)
    candidates = [line for line in lines if _is_heading(line, body_size)]
    if not candidates:
        return {}

    # Cluster the candidate sizes, largest first, merging near-identical sizes.
    distinct: list[float] = []
    for size in sorted({round(c.font_size, 2) for c in candidates}, reverse=True):
        spread = abs(distinct[-1] - size) / max(distinct[-1], 1e-6) if distinct else 1.0
        if not distinct or spread > _LEVEL_TOLERANCE:
            distinct.append(size)

    size_to_level = {size: min(i + 1, _MAX_LEVEL) for i, size in enumerate(distinct)}

    def level_for(size: float) -> int:
        rounded = round(size, 2)
        if rounded in size_to_level:
            return size_to_level[rounded]
        # Snap to the nearest cluster it was merged into.
        nearest = min(distinct, key=lambda d: abs(d - rounded))
        return size_to_level[nearest]

    levels: dict[int, int] = {}
    for candidate in candidates:
        level = level_for(candidate.font_size)
        # A bold body-size heading sits below every size-based heading level.
        if body_size > 0 and candidate.font_size < body_size * _SIZE_RATIO:
            level = min(len(distinct) + 1, _MAX_LEVEL)
        levels[candidate.order] = level
    return levels


class HeadingStack:
    """Tracks the active heading ancestry while walking a document in order.

    Pushing a level-2 heading discards any deeper headings still on the stack,
    which is what keeps heading_path correct when a document skips levels.
    """

    def __init__(self) -> None:
        self._stack: list[tuple[int, str]] = []

    def push(self, level: int, text: str) -> None:
        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        self._stack.append((level, text.strip()))

    @property
    def path(self) -> list[str]:
        return [text for _, text in self._stack]

    @property
    def current(self) -> str | None:
        return self._stack[-1][1] if self._stack else None

    @property
    def section(self) -> str | None:
        """The outermost heading -- the chapter or unit a chunk belongs to."""
        return self._stack[0][1] if self._stack else None

    def copy(self) -> HeadingStack:
        clone = HeadingStack()
        clone._stack = list(self._stack)
        return clone
