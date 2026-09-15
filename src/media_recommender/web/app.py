"""FastAPI application factory for the Web interface."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.api import router as api_router
from media_recommender.web.routes.pages import router as page_router

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
    app = FastAPI(title="Media Recommender")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIRECTORY))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIRECTORY)), name="static")
    app.include_router(api_router)
    app.include_router(page_router)
    app.state.templates = templates
    register_exception_handlers(app)
    return app
