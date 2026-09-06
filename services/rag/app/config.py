"""Configuration, validated once at import.

Every knob that affects retrieval quality is here rather than scattered through
the pipeline, so an eval run can describe a variant as a config diff.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ── service ──────────────────────────────────────────────
    NODE_ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    RAG_SERVICE_PORT: int = 8000
    INTERNAL_SERVICE_TOKEN: str = "dev_internal_token_change_me"

    # ── infrastructure ───────────────────────────────────────
    DATABASE_URL: str = "postgresql://examprep:examprep_dev_password@localhost:5432/examprep"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "examprep_chunks"

    # ── storage ──────────────────────────────────────────────
    STORAGE_DRIVER: str = "local"
    STORAGE_LOCAL_PATH: Path = Path("../../storage/uploads")

    # ── providers ────────────────────────────────────────────
    # Empty GEMINI_API_KEY keeps the whole service on mock providers, so the
    # pipeline and its tests run with no credentials at all.
    GEMINI_API_KEY: str = ""
    EMBEDDING_PROVIDER: str = "mock"
    EMBEDDING_MODEL: str = "gemini-embedding-001"
    EMBEDDING_DIMENSIONS: int = 768
    LLM_PROVIDER: str = "mock"
    LLM_MODEL: str = "gemini-2.5-flash"
    LLM_UTILITY_MODEL: str = "gemini-2.5-flash-lite"
    RERANKER_PROVIDER: str = "noop"
    JINA_API_KEY: str = ""

    # ── free-tier rate limiting ──────────────────────────────
    EMBEDDING_MAX_RPM: int = 100
    EMBEDDING_BATCH_SIZE: int = 64
    LLM_MAX_RPM: int = 15

    # ── chunking ─────────────────────────────────────────────
    CHUNK_TARGET_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 64
    CHUNK_MIN_TOKENS: int = 64

    # ── retrieval ────────────────────────────────────────────
    RETRIEVAL_DENSE_TOP_K: int = 50
    RETRIEVAL_SPARSE_TOP_K: int = 50
    # RRF damping constant. 60 is the value from the original paper and the
    # usual default; it flattens the contribution of very high ranks.
    RRF_K: int = 60
    RERANK_TOP_N: int = 25
    CONTEXT_TOP_N: int = 8
    CONTEXT_MAX_TOKENS: int = 5000

    # ── observability ────────────────────────────────────────
    TRACER: str = "postgres"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "http://localhost:3000"

    @property
    def uses_mock_providers(self) -> bool:
        return not self.GEMINI_API_KEY

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy needs the asyncpg driver named explicitly."""
        return self.DATABASE_URL.replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
