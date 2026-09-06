from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `import app.*` when pytest runs from the service root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return Settings(
        GEMINI_API_KEY="",
        EMBEDDING_PROVIDER="mock",
        LLM_PROVIDER="mock",
        RERANKER_PROVIDER="noop",
    )
