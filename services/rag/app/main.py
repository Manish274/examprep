"""FastAPI application for the RAG service.

Owns everything that touches a document, a vector or a model. Knows nothing
about users, sessions or permissions -- those stay in the Node backend, and
identifiers arrive here as opaque filter keys.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import health
from app.config import get_settings

logger = logging.getLogger("rag")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    logger.info(
        "rag service starting (embedding=%s llm=%s reranker=%s mock=%s)",
        settings.EMBEDDING_PROVIDER,
        settings.LLM_PROVIDER,
        settings.RERANKER_PROVIDER,
        settings.uses_mock_providers,
    )
    if settings.uses_mock_providers:
        logger.warning(
            "No GEMINI_API_KEY set - running on mock providers. "
            "Retrieval and generation results are not meaningful."
        )
    yield
    logger.info("rag service stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ExamPrep RAG Service",
        version="0.1.0",
        description=(
            "Document parsing, chunking, embeddings, hybrid retrieval, "
            "reranking, grounded generation and evaluation."
        ),
        lifespan=lifespan,
    )

    app.include_router(health.router)

    # Feature routers land here as milestones complete:
    #   /ingest  /retrieve  /chat  /generate/test  /generate/flashcards  /eval

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {"code": "internal_error", "message": "Internal server error"}
            },
        )

    return app


app = create_app()


def main() -> Any:
    import uvicorn

    settings = get_settings()
    return uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.RAG_SERVICE_PORT,
        reload=settings.NODE_ENV == "development",
    )


if __name__ == "__main__":
    main()
