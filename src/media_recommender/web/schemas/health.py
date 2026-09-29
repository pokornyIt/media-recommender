"""Operational HTTP response schemas."""

from typing import Literal

from pydantic import BaseModel

from media_recommender.application.provider_status import (  # noqa: TC001 - Pydantic resolves this annotation at runtime.
    ProviderStatus,
)


class LivenessResponse(BaseModel):
    """Response proving only that the HTTP process can serve a request."""

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    """Response describing database accessibility and migration state."""

    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unavailable"]
    migrations: Literal["current", "pending", "unknown"]


class ProviderHealthResponse(BaseModel):
    """Response describing safe provider configuration and operational state."""

    providers: tuple[ProviderStatus, ...]
