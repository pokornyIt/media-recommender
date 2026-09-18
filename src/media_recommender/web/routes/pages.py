"""Server-rendered page and operational routes."""

from collections.abc import Sequence  # noqa: TC003 - resolved at runtime by FastAPI dependency wiring.
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.
from pydantic import ValidationError
from pydantic_settings import SettingsError

from media_recommender.application.provider_status import (
    DefaultProviderStatusReader,
    ProviderStatus,
    ProviderStatusReader,
)
from media_recommender.config import JellyfinSettings, Settings, TmdbSettings
from media_recommender.web.schemas.health import LivenessResponse

router = APIRouter()


@dataclass(frozen=True)
class SettingsPageContext:
    """Safe runtime configuration facts rendered by the settings page."""

    tmdb_configured: bool
    jellyfin_configured: bool
    default_region: str


def get_templates(request: Request) -> Jinja2Templates:
    """Return templates initialized by the application factory.

    :param request: Incoming request carrying the current application.
    :return: Shared Jinja2 template renderer.
    """
    return request.app.state.templates


def _is_tmdb_configured() -> bool:
    """Return whether the configured TMDB values can construct its settings model.

    :return: Whether TMDB settings validation succeeds.
    """
    try:
        TmdbSettings()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError, ValidationError:
        return False
    return True


def _is_jellyfin_configured() -> bool:
    """Return whether the configured Jellyfin values can construct its settings model.

    :return: Whether Jellyfin settings validation succeeds.
    """
    try:
        JellyfinSettings()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError, ValidationError:
        return False
    return True


def get_settings_page_context() -> SettingsPageContext:
    """Return the safe runtime configuration facts required by the settings page.

    :return: Safe facts for rendering the settings page.
    """
    settings = Settings()
    return SettingsPageContext(
        tmdb_configured=_is_tmdb_configured(),
        jellyfin_configured=_is_jellyfin_configured(),
        default_region=settings.default_region,
    )


def get_provider_statuses() -> Sequence[ProviderStatus]:
    """Resolve provider status through the typed application-layer contract.

    The default reader performs no provider calls; tests may replace it via
    FastAPI dependency overrides.

    :return: Safe provider status snapshots in a stable order.
    """
    reader: ProviderStatusReader = DefaultProviderStatusReader()
    return reader.read_provider_statuses()


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the minimal application home page.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :return: Shared-layout home page.
    """
    return templates.TemplateResponse(request, "home.html")


@router.get("/settings", response_class=HTMLResponse)
async def settings(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    context: Annotated[SettingsPageContext, Depends(get_settings_page_context)],
) -> HTMLResponse:
    """Render safe runtime configuration status without contacting providers.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param context: Safe configuration status facts for rendering.
    :return: Shared-layout settings page.
    """
    return templates.TemplateResponse(request, "settings.html", {"settings": context})


@router.get("/providers/status", response_class=HTMLResponse)
async def provider_status(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    statuses: Annotated[Sequence[ProviderStatus], Depends(get_provider_statuses)],
) -> HTMLResponse:
    """Render the read-only provider status page without contacting providers.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param statuses: Safe provider status snapshots from the application contract.
    :return: Shared-layout provider status page.
    """
    return templates.TemplateResponse(request, "provider_status.html", {"statuses": statuses})


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Report that the HTTP process is able to serve requests.

    :return: Liveness status without database or provider access.
    """
    return LivenessResponse(status="ok")
