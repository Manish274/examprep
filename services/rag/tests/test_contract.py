"""Guards the contract between the Python and TypeScript sides.

The two backends duplicate a handful of enums and metadata field names. Nothing
prevents them drifting except this test, which reads the TypeScript source and
compares it against the Python definitions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.models import (
    BlockType,
    ExplanationMode,
    QuestionType,
    RetrievalStrategy,
)

SHARED_SRC = (
    Path(__file__).resolve().parents[3] / "packages" / "shared" / "src"
)


def _zod_enum_values(source: str, schema_name: str) -> set[str]:
    """Extracts the string literals from `export const X = z.enum([...])`."""
    match = re.search(
        rf"export const {schema_name} = z\.enum\(\[(.*?)\]\)",
        source,
        re.DOTALL,
    )
    if match is None:
        pytest.fail(f"{schema_name} not found in shared schemas")
    return set(re.findall(r'"([^"]+)"', match.group(1)))


@pytest.mark.skipif(
    not SHARED_SRC.exists(), reason="shared package not present"
)
class TestSharedContract:
    def test_explanation_modes_match(self) -> None:
        source = (SHARED_SRC / "schemas" / "common.ts").read_text(encoding="utf-8")
        assert _zod_enum_values(source, "explanationModeSchema") == {
            m.value for m in ExplanationMode
        }

    def test_retrieval_strategies_match(self) -> None:
        source = (SHARED_SRC / "schemas" / "common.ts").read_text(encoding="utf-8")
        assert _zod_enum_values(source, "retrievalStrategySchema") == {
            s.value for s in RetrievalStrategy
        }

    def test_question_types_match_the_database_enum(self) -> None:
        """Generated questions are written straight into a Postgres enum
        column; a mismatch is an insert failure at generation time."""
        source = (
            SHARED_SRC.parents[1] / "db" / "src" / "schema.ts"
        ).read_text(encoding="utf-8")
        match = re.search(
            r'pgEnum\("question_type", \[(.*?)\]\)', source, re.DOTALL
        )
        assert match is not None, "question_type enum not found"

        assert set(re.findall(r'"([^"]+)"', match.group(1))) == {
            q.value for q in QuestionType
        }

    def test_chunk_metadata_fields_match(self) -> None:
        """Metadata is what citations are built from; a mismatch here shows up
        as a source the frontend cannot render."""
        source = (SHARED_SRC / "schemas" / "chunks.ts").read_text(encoding="utf-8")
        block = re.search(
            r"export const chunkMetadataSchema = z\.object\(\{(.*?)\n\}\);",
            source,
            re.DOTALL,
        )
        assert block is not None, "chunkMetadataSchema not found"

        ts_fields = set(re.findall(r"^\s{2}(\w+):", block.group(1), re.MULTILINE))
        expected = {
            "documentId",
            "documentName",
            "chunkIndex",
            "pageNumber",
            "slideNumber",
            "section",
            "heading",
            "headingPath",
            "charStart",
            "charEnd",
            "contentHash",
        }
        assert ts_fields == expected


class TestBlockTypes:
    def test_speaker_notes_are_representable(self) -> None:
        # PPTX speaker notes often hold the explanation the slide omits, so
        # they must survive parsing as first-class blocks.
        assert BlockType.SPEAKER_NOTE.value == "speaker_note"
