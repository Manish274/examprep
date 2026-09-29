from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow `import app.*` when pytest runs from the service root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings, get_settings

# What every test starts from, whatever the developer's .env says.
_STAND_INS = {
    "GEMINI_API_KEY": "",
    "JINA_API_KEY": "",
    "EMBEDDING_PROVIDER": "mock",
    "LLM_PROVIDER": "mock",
    "RERANKER_PROVIDER": "noop",
    "VISION_PROVIDER": "noop",
}


@pytest.fixture(autouse=True)
def _no_hosted_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the suite off real providers.

    `Settings()` reads the repo-root .env, which is where a developer's real
    key and providers live -- so a test building settings without naming every
    provider would call Gemini with that key: spending the day's quota, and
    passing or failing on whatever the model said. Environment variables
    outrank the file, and a test that wants a provider still names it
    explicitly, which outranks both.
    """
    for name, value in _STAND_INS.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()


@pytest.fixture
def settings() -> Settings:
    get_settings.cache_clear()
    return Settings(
        GEMINI_API_KEY="",
        EMBEDDING_PROVIDER="mock",
        LLM_PROVIDER="mock",
        RERANKER_PROVIDER="noop",
    )
