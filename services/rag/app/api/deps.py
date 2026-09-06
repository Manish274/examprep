"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def require_internal_token(
    settings: SettingsDep,
    x_internal_token: Annotated[str | None, Header()] = None,
) -> None:
    """Guards service-to-service endpoints.

    The RAG service is never exposed to browsers -- only the Node backend and
    worker call it -- so a shared secret is the right weight of check here.
    Authorisation of the student is the Node service's job.
    """
    if x_internal_token != settings.INTERNAL_SERVICE_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal service token",
        )


InternalAuth = Depends(require_internal_token)


def get_correlation_id(
    x_correlation_id: Annotated[str | None, Header()] = None,
) -> str | None:
    """Ties spans here to the originating request in the Node backend."""
    return x_correlation_id


CorrelationId = Annotated[str | None, Depends(get_correlation_id)]
