"""FastAPI application factory for the Web interface."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from media_recommender.config import Settings
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.api import router as api_router
from media_recommender.web.routes.imports import router as imports_router
from media_recommender.web.routes.media import register_media_exception_handlers
from media_recommender.web.routes.pages import router as page_router
from media_recommender.web.routes.recommendations import register_recommendation_exception_handlers

_WEB_ROOT = Path(__file__).parent
_STATIC_DIRECTORY = _WEB_ROOT / "static"
_TEMPLATES_DIRECTORY = _WEB_ROOT / "templates"


def create_app() -> FastAPI:
    """Build the base HTTP application without constructing application services.

    Later feature routes define their own FastAPI ``Depends`` dependencies and
    use the returned application's native ``dependency_overrides`` mapping in
    tests or composition roots.

    :return: Configured HTTP application foundation.
    """
    settings = Settings()
    app = FastAPI(title="Media Recommender")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIRECTORY))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIRECTORY)), name="static")
    app.include_router(api_router)
    app.include_router(page_router)
    app.include_router(imports_router)
    app.state.templates = templates
    app.state.web_session_secret = settings.web_session_secret.get_secret_value().encode()
    app.state.netflix_max_upload_bytes = settings.netflix_import_max_upload_bytes
    register_exception_handlers(app)
    register_media_exception_handlers(app)
    register_recommendation_exception_handlers(app)
    return app
