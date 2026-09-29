"""Configuration, read from the repo-root .env and validated once.

Every knob that affects retrieval quality is here rather than scattered through
the pipeline, so an eval run can describe a variant as a config diff.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> app -> rag -> services -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Absolute, so the same file is read no matter where the service
        # is launched from.
        env_file=REPO_ROOT / ".env",
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
    DATABASE_URL: str = (
        "postgresql://examprep:examprep_dev_password@localhost:5432/examprep"
    )
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str = ""
    QDRANT_COLLECTION: str = "examprep_chunks"

    # ── storage ──────────────────────────────────────────────
    STORAGE_LOCAL_PATH: Path = Path("./storage/uploads")

    # ── providers ────────────────────────────────────────────
    # The mock providers need no credentials, so the pipeline and its tests
    # run with no key at all. The model names are the ones a new key can use:
    # the Gemini 2.5 family answers 404 for them.
    GEMINI_API_KEY: str = ""
    EMBEDDING_PROVIDER: str = "mock"
    EMBEDDING_MODEL: str = "gemini-embedding-2"
    EMBEDDING_DIMENSIONS: int = 3072
    LLM_PROVIDER: str = "mock"
    LLM_MODEL: str = "gemini-3.8-flash"
    LLM_UTILITY_MODEL: str = "gemini-3.5-flash-lite"
    RERANKER_PROVIDER: str = "noop"
    JINA_API_KEY: str = ""
    RERANKER_MAX_RPM: int = 60

    # ── vision ───────────────────────────────────────────────
    # Reads content out of images: screenshotted tables, pasted formulas,
    # diagrams, and scanned PDF pages that carry no text layer at all.
    VISION_PROVIDER: str = "noop"
    # A model of its own: the free tier's daily allowance is per model, and
    # sharing one with LLM_UTILITY_MODEL would let one scanned upload use up
    # the query rewriting every chat question needs.
    VISION_MODEL: str = "gemini-3.1-flash-lite"
    VISION_MAX_RPM: int = 15
    # Images per request. The free tier limits requests a day, not tokens, so
    # four to a request reads four times the material before the day is spent.
    VISION_BATCH_SIZE: int = 4
    # Requests in flight at once, still within VISION_MAX_RPM.
    VISION_CONCURRENCY: int = 2
    # Below this an image is decoration -- a bullet glyph, a rule, a logo.
    VISION_MIN_PIXELS: int = 40000
    # New readings per document, so one pathological file cannot drain a day.
    # Images already in the vision cache do not count against it.
    VISION_MAX_IMAGES: int = 40

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
    # Questions about the whole document ("summarise my notes") are answered
    # from an even spread of it rather than a search, and need more of it in
    # view than a single fact does.
    CHAT_OVERVIEW_MAX_CHUNKS: int = 24
    CHAT_OVERVIEW_MAX_TOKENS: int = 12000

    # ── conversation ─────────────────────────────────────────
    # How much of the conversation the answering model is shown. Deliberately
    # a small fraction of the context budget: prior turns say what a follow-up
    # refers to, the sources say what is true, and the first must never crowd
    # out the second.
    CHAT_HISTORY_TURNS: int = 8
    CHAT_HISTORY_MAX_TOKENS: int = 1500
    # A refusal is regenerated once when the best reranker relevance is at
    # least this. Measured with Jina on a real deck, 30 answerable questions
    # scored 0.07-0.79 at the top, 10 on-topic questions the deck does not
    # answer 0.04-0.50, and 13 off-topic ones 0.03-0.17. At 0.2 the recheck
    # covers 22 of the 30 answerable questions (0.4 covered 10) and none of the
    # off-topic ones; the near misses it does reach were still refused on
    # recheck. Only applies to a calibrated reranker.
    CHAT_RECHECK_MIN_RELEVANCE: float = 0.2
    # A question whose best chunk scores below BOTH of these is answered as
    # unsupported without a model call. Measured on the same deck: the lowest
    # answerable question scored 0.548 similarity ("buzzer role") but 0.136
    # relevance, and no answerable question was below both; 11 of 13 off-topic
    # questions were ("Hi" and "Thanks!" pass through to the model, which
    # refuses them anyway). The similarity bound is specific to the embedding
    # model -- re-measure it if EMBEDDING_MODEL changes.
    CHAT_OFF_TOPIC_MAX_SIMILARITY: float = 0.55
    CHAT_OFF_TOPIC_MAX_RELEVANCE: float = 0.10

    # ── observability ────────────────────────────────────────
    TRACER: str = "postgres"
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    @field_validator("STORAGE_LOCAL_PATH")
    @classmethod
    def _anchor_to_repo_root(cls, value: Path) -> Path:
        """Resolves a relative storage path against the repo root.

        The Node services run from the repo root and this one runs from
        services/rag, so the same "./storage/uploads" in .env would otherwise
        point at two different directories and uploads would vanish between
        them.
        """
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @property
    def uses_mock_providers(self) -> bool:
        """Whether any neural stage is running on a stand-in.

        Reports what is actually configured rather than merely whether a key
        exists: a key can be present while a provider is still set to mock,
        and reporting "real" then would make health output misleading.
        """
        return "mock" in {self.EMBEDDING_PROVIDER, self.LLM_PROVIDER}

    @property
    def has_gemini_key(self) -> bool:
        return bool(self.GEMINI_API_KEY)


@lru_cache
def get_settings() -> Settings:
    return Settings()
