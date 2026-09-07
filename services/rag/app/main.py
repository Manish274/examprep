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

from app.api import health, ingest, retrieve
from app.config import get_settings
from app.container import Container, set_container

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
    container = Container(settings)
    set_container(container)
    try:
        await container.startup()
    except Exception:
        logger.exception("vector store unavailable at startup")

    if settings.uses_mock_providers:
        reason = (
            "no GEMINI_API_KEY set"
            if not settings.has_gemini_key
            else "providers not yet switched over"
        )
        logger.warning(
            "Running on mock providers (%s). Retrieval and generation "
            "results are not meaningful.",
            reason,
        )
    yield

    await container.shutdown()
    set_container(None)
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
    app.include_router(ingest.router)
    app.include_router(retrieve.router)

    # Feature routers land here as milestones complete:
    #   /chat  /generate/test  /generate/flashcards  /eval

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
