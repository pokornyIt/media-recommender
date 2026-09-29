"""FastAPI application factory for the Web interface."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from media_recommender.composition import attach_services, build_services, run_startup_migrations
from media_recommender.config import Settings
from media_recommender.logging import configure_logging
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.middleware import RequestBodyLimitMiddleware
from media_recommender.web.routes.api import router as api_router
from media_recommender.web.routes.availability import register_availability_exception_handlers
from media_recommender.web.routes.availability import router as availability_router
from media_recommender.web.routes.health import router as health_router
from media_recommender.web.routes.imports import NETFLIX_IMPORT_PATH, register_import_exception_handlers
from media_recommender.web.routes.imports import router as import_router
from media_recommender.web.routes.jellyfin import register_jellyfin_exception_handlers
from media_recommender.web.routes.jellyfin import router as jellyfin_router
from media_recommender.web.routes.media import register_media_exception_handlers
from media_recommender.web.routes.pages import router as page_router
from media_recommender.web.routes.recommendation_workflow import (
    register_recommendation_workflow_exception_handlers,
)
from media_recommender.web.routes.recommendation_workflow import router as recommendation_workflow_router
from media_recommender.web.routes.recommendations import register_recommendation_exception_handlers

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_WEB_ROOT = Path(__file__).parent
_STATIC_DIRECTORY = _WEB_ROOT / "static"
_TEMPLATES_DIRECTORY = _WEB_ROOT / "templates"
_SESSION_COOKIE_NAME = "media_recommender_session"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Apply migrations, build services, and release resources on shutdown.

    Migrations run in a worker thread because the Alembic environment owns its
    own event loop. A migration failure aborts startup so the application never
    serves an incompatible schema.

    :param app: Application whose state carries validated settings.
    :yield: Control while the application serves requests.
    """
    settings: Settings = app.state.settings
    configure_logging(settings.log_level)
    await asyncio.to_thread(run_startup_migrations, settings)
    services = build_services(settings)
    app.state.services = services
    attach_services(app, services)
    try:
        yield
    finally:
        await services.aclose()


def create_app() -> FastAPI:
    """Build the HTTP application with its production lifecycle.

    Feature routes define their own FastAPI ``Depends`` dependencies and resolve
    application services from the state attached by the startup lifespan.

    :return: Configured HTTP application.
    """
    app = FastAPI(title="Media Recommender", lifespan=lifespan)
    settings = Settings()
    app.state.settings = settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.web_session_secret.get_secret_value(),
        session_cookie=_SESSION_COOKIE_NAME,
        same_site="lax",
        https_only=False,
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        path=NETFLIX_IMPORT_PATH,
        max_bytes=settings.netflix_upload_max_bytes,
    )
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIRECTORY))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIRECTORY)), name="static")
    app.include_router(api_router)
    app.include_router(health_router)
    app.include_router(page_router)
    app.include_router(recommendation_workflow_router)
    app.include_router(import_router)
    app.include_router(jellyfin_router)
    app.include_router(availability_router)
    app.state.templates = templates
    register_exception_handlers(app)
    register_media_exception_handlers(app)
    register_recommendation_exception_handlers(app)
    register_import_exception_handlers(app)
    register_jellyfin_exception_handlers(app)
    register_availability_exception_handlers(app)
    register_recommendation_workflow_exception_handlers(app)
    return app
