"""Token counting for chunk sizing.

Uses tiktoken's cl100k_base as a stable, offline-cacheable proxy. It is not
Gemini's tokenizer -- no public one exists -- but chunk sizing only needs a
consistent yardstick, and being within roughly 10 percent of the real count is
ample for deciding where to split a paragraph.

Falls back to a character heuristic when the BPE vocabulary cannot be fetched,
so parsing and chunking still work with no network access.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

logger = logging.getLogger(__name__)

# Empirical average across English prose and technical material. Only used when
# tiktoken is unavailable.
_CHARS_PER_TOKEN = 4.0


class TokenCounter(Protocol):
    name: str

    def count(self, text: str) -> int: ...

    def truncate(self, text: str, max_tokens: int) -> str: ...


class HeuristicTokenCounter:
    """Character-length approximation. Deterministic and dependency-free."""

    name = "heuristic"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return max(1, round(len(text) / _CHARS_PER_TOKEN))

    def truncate(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        return text[: int(max_tokens * _CHARS_PER_TOKEN)]


class TiktokenCounter:
    """Real BPE token counts."""

    name = "cl100k_base"

    def __init__(self) -> None:
        import tiktoken

        self._encoding = tiktoken.get_encoding("cl100k_base")

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._encoding.encode(text, disallowed_special=()))

    def truncate(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        tokens = self._encoding.encode(text, disallowed_special=())
        if len(tokens) <= max_tokens:
            return text
        return self._encoding.decode(tokens[:max_tokens])


@lru_cache(maxsize=1)
def get_token_counter() -> TokenCounter:
    """The process-wide counter. Cached because loading the BPE table is slow."""
    try:
        counter = TiktokenCounter()
        # Force the vocabulary download now rather than mid-ingest.
        counter.count("warmup")
        return counter
    except Exception as exc:
        logger.warning(
            "tiktoken unavailable (%s); using character heuristic for chunk sizing",
            exc,
        )
        return HeuristicTokenCounter()
