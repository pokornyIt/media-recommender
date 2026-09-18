"""Server-rendered page and operational routes."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.

from media_recommender.application.provider_status import (
    DefaultProviderStatusReader,
    ProviderStatusReader,
    is_jellyfin_configured,
    is_tmdb_configured,
)
from media_recommender.config import Settings
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


def get_settings_page_context() -> SettingsPageContext:
    """Return the safe runtime configuration facts required by the settings page.

    :return: Safe facts for rendering the settings page.
    """
    settings = Settings()
    return SettingsPageContext(
        tmdb_configured=is_tmdb_configured(),
        jellyfin_configured=is_jellyfin_configured(),
        default_region=settings.default_region,
    )


_DEFAULT_PROVIDER_STATUS_READER = DefaultProviderStatusReader()


def get_provider_status_reader() -> ProviderStatusReader:
    """Return the default safe provider status reader.

    :return: Read-only provider status source replaceable through dependency overrides.
    """
    return _DEFAULT_PROVIDER_STATUS_READER


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
    reader: Annotated[ProviderStatusReader, Depends(get_provider_status_reader)],
) -> HTMLResponse:
    """Render safe provider configuration and latest known operational state.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param reader: Injected read-only provider status source.
    :return: Shared-layout provider status page.
    """
    return templates.TemplateResponse(request, "provider_status.html", {"providers": reader.read_statuses()})


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Report that the HTTP process is able to serve requests.

    :return: Liveness status without database or provider access.
    """
    return LivenessResponse(status="ok")
