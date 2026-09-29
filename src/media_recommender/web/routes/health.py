"""Operational health endpoints with distinct liveness, readiness, and provider semantics."""

from __future__ import annotations

from http import HTTPStatus

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from media_recommender.application.provider_status import DefaultProviderStatusReader, ProviderStatusReader
from media_recommender.application.readiness import ReadinessProbe, ReadinessReport
from media_recommender.web.schemas.health import LivenessResponse, ProviderHealthResponse, ReadinessResponse

router = APIRouter()


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Report that the HTTP process is able to serve requests.

    :return: Liveness status without database or provider access.
    """
    return LivenessResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    responses={HTTPStatus.SERVICE_UNAVAILABLE: {"model": ReadinessResponse, "description": "Database is not ready."}},
)
async def readiness(request: Request) -> JSONResponse:
    """Report database accessibility and migration state without contacting providers.

    :param request: Incoming request carrying the current application.
    :return: Readiness response with an explicit HTTP status.
    """
    probe: ReadinessProbe | None = getattr(request.app.state, "readiness_probe", None)
    if probe is None:
        report = ReadinessReport(ready=False, database="unavailable", migrations="unknown")
    else:
        report = await probe.check()
    response = ReadinessResponse(
        status="ready" if report.ready else "not_ready",
        database=report.database,
        migrations=report.migrations,
    )
    status_code = HTTPStatus.OK if report.ready else HTTPStatus.SERVICE_UNAVAILABLE
    return JSONResponse(status_code=status_code, content=response.model_dump())


@router.get("/health/providers", response_model=ProviderHealthResponse)
async def provider_health() -> ProviderHealthResponse:
    """Report safe provider configuration and operational state.

    Provider health is deliberately separate from liveness and readiness and
    never makes the application unhealthy because an external service is
    temporarily unavailable.

    :return: Safe provider status snapshots in a stable order.
    """
    reader: ProviderStatusReader = DefaultProviderStatusReader()
    return ProviderHealthResponse(providers=tuple(reader.read_provider_statuses()))
