"""BM25 sparse encoding.

No model, no API, no quota: tokenise, stem, count. That independence is the
point -- when a student asks about "3NF" or "BCNF", exact term matching finds
the definition immediately, while a dense vector may rank a paraphrase above it.

Term weighting is split across two places deliberately. Term frequency
saturation is computed here, per document. Inverse document frequency is left
to Qdrant, which applies it at query time from the collection's own statistics
via the IDF modifier. Computing IDF here would freeze it at ingest time and
make it wrong for every document added afterwards.

Terms map to vector indices by hashing, which means no vocabulary has to be
built, persisted or kept in sync between ingest and query. The trade is rare
collisions between unrelated terms; at 32 bits those are vanishingly unlikely
to matter for a student's own corpus.
"""

from __future__ import annotations

import re
from collections import Counter
from functools import lru_cache

import snowballstemmer

from app.core.models import SparseVector
from app.core.registry import sparse_encoders

# Words carrying no retrieval signal. Kept short on purpose: an over-eager list
# removes terms that matter in technical material ("no" in "no partial
# dependency", "not" in a definition).
_STOPWORDS = frozenset(
    """
    a an the and or but if then else of in on at to for from by with as is are
    was were be been being it its this that these those there here we you they
    i he she them his her their our your do does did doing have has had having
    will would shall should can could may might must about into over under
    """.split()
)

# Keeps intra-word periods and hyphens so "3.5", "B-tree" and "F.D." survive as
# single terms rather than fragmenting into noise.
_TOKEN = re.compile(r"[a-z0-9]+(?:[.\-][a-z0-9]+)*")

_MASK_32 = 0xFFFFFFFF

# BM25 term-frequency saturation. The standard k1; past a few occurrences,
# repeating a word again says little more about relevance.
_K1 = 1.5


@lru_cache(maxsize=1)
def _stemmer() -> object:
    return snowballstemmer.stemmer("english")


def term_index(term: str) -> int:
    """Stable 32-bit index for a term.

    Must be deterministic across processes and restarts, which rules out
    Python's salted hash().
    """
    value = 2166136261
    for byte in term.encode("utf-8"):
        value = ((value ^ byte) * 16777619) & _MASK_32
    return value


def tokenize(text: str) -> list[str]:
    """Lowercase, split, drop stopwords, stem.

    Stemming is what lets "normalization" in a question reach "normalized" in
    the source material.
    """
    raw = _TOKEN.findall(text.lower())
    kept = [t for t in raw if t not in _STOPWORDS and len(t) > 1]
    if not kept:
        return []
    return _stemmer().stemWords(kept)  # type: ignore[attr-defined,no-any-return]


class Bm25Encoder:
    name = "bm25"

    def __init__(self, k1: float = _K1) -> None:
        self.k1 = k1

    def _weights(self, text: str, saturate: bool) -> SparseVector:
        terms = tokenize(text)
        if not terms:
            return SparseVector(indices=[], values=[])

        counts = Counter(terms)
        indices: list[int] = []
        values: list[float] = []
        for term, count in counts.items():
            # Length normalisation is Qdrant's job along with IDF; this is the
            # saturating term-frequency component.
            weight = (
                (count * (self.k1 + 1)) / (count + self.k1)
                if saturate
                else float(count)
            )
            indices.append(term_index(term))
            values.append(weight)

        return SparseVector(indices=indices, values=values)

    def encode_document(self, text: str) -> SparseVector:
        return self._weights(text, saturate=True)

    def encode_query(self, text: str) -> SparseVector:
        # A query term appearing twice should not double its influence, so the
        # query side is binary presence rather than a count.
        vector = self._weights(text, saturate=False)
        return SparseVector(
            indices=vector.indices, values=[1.0 for _ in vector.values]
        )


@sparse_encoders.register("bm25")
def _create_bm25_encoder(k1: float = _K1) -> Bm25Encoder:
    return Bm25Encoder(k1=k1)
