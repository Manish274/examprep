"""Text normalisation, sentence segmentation and hashing.

Chunk boundaries land on sentence edges wherever possible: a chunk that stops
mid-sentence embeds badly and reads worse when shown as a citation.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
import uuid

# Abbreviations whose trailing period does not end a sentence. Deliberately
# short -- over-eager matching costs more than the occasional missed case.
_ABBREVIATIONS = {
    "e.g", "i.e", "etc", "vs", "cf", "al", "fig", "eq", "ref", "no", "vol",
    "ch", "sec", "approx", "dept", "univ", "mr", "mrs", "ms", "dr", "prof",
    "st", "jr", "sr", "inc", "ltd", "co",
}

_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+")
_WHITESPACE = re.compile(r"[ \t\u00a0]+")
_BLANK_LINES = re.compile(r"\n{3,}")
# Hyphen at end of line, as produced by PDF text wrapping.
_LINE_HYPHEN = re.compile(r"(\w)-\n(\w)")
# Control characters PDF extraction sometimes emits.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def normalize(text: str) -> str:
    """Cleans extracted text without changing its meaning.

    Joins hyphenated line breaks, collapses runs of spaces, strips control
    characters and normalises Unicode so that the same visual string always
    hashes identically.
    """
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = _CONTROL.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _LINE_HYPHEN.sub(r"\1\2", text)
    text = _WHITESPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _ends_with_abbreviation(fragment: str) -> bool:
    match = re.search(r"(\w+)\.$", fragment.strip())
    if match is None:
        return False
    word = match.group(1).lower()
    if word in _ABBREVIATIONS:
        return True
    # A single letter followed by a period is almost always an initial
    # ("J. Smith") or an enumerated item, not a sentence end.
    return len(word) == 1


def split_sentences(text: str) -> list[str]:
    """Splits into sentences, keeping trailing punctuation and spacing intact.

    Newlines are treated as hard boundaries: extracted material uses them for
    list items and headings, which are separate units even without punctuation.
    """
    if not text.strip():
        return []

    sentences: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        pieces = _SENTENCE_END.split(line)
        buffer = ""
        for piece in pieces:
            candidate = f"{buffer} {piece}".strip() if buffer else piece
            if _ends_with_abbreviation(candidate):
                buffer = candidate
                continue
            if candidate.strip():
                sentences.append(candidate.strip())
            buffer = ""
        if buffer.strip():
            sentences.append(buffer.strip())
    return sentences


def content_hash(text: str) -> str:
    """Stable sha256 over normalised text.

    Keys the embedding cache, so it must be insensitive to whitespace noise
    that does not change meaning.
    """
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def looks_like_heading(text: str) -> bool:
    """Heuristic for text that reads as a heading regardless of styling.

    Used as a secondary signal when font size alone is inconclusive.
    """
    stripped = text.strip()
    if not stripped or len(stripped) > 120:
        return False
    # Colons are common in slide headings, so allow those specifically.
    if stripped.endswith((".", ",", ";")):
        return False
    words = stripped.split()
    if len(words) > 14:
        return False
    # "3.2 Normalization", "Chapter 4", "UNIT III"
    if re.match(r"^(\d+(\.\d+)*|[IVXLC]+|chapter|unit|section|part)\b", stripped, re.I):
        return True
    # Title Case or ALL CAPS with no terminal punctuation.
    alpha_words = [w for w in words if any(c.isalpha() for c in w)]
    if not alpha_words:
        return False
    capitalised = sum(1 for w in alpha_words if w[0].isupper())
    return capitalised / len(alpha_words) >= 0.7


# Fixed namespace for deriving chunk ids. Arbitrary but must never change:
# every stored chunk id and every gold set that references one depends on it.
_CHUNK_NAMESPACE = uuid.UUID("6f2a1c94-3b7d-4e51-9a08-52d1f7c0e3b6")


def chunk_id_for(document_id: str, text: str) -> str:
    """Deterministic id for a chunk, derived from its document and content.

    Random ids would be regenerated on every ingest, which silently invalidates
    any gold set referencing them -- an evaluation run would then score against
    ids that no longer exist and report zero for everything.

    Deriving from content rather than position also means a chunker change only
    moves the ids of chunks whose text actually changed, so a gold set survives
    re-chunking wherever the passage did.
    """
    return str(uuid.uuid5(_CHUNK_NAMESPACE, f"{document_id}:{content_hash(text)}"))
