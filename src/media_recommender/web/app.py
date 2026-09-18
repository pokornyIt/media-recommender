"""FastAPI application factory for the Web interface."""

from __future__ import annotations

from collections.abc import Awaitable, Callable  # noqa: TC003 - used in runtime middleware annotations.
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
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

_UPLOAD_LIMIT_OVERHEAD_BYTES = 64 * 1024
_TOO_LARGE_DETAIL = "The uploaded file is too large."
_NETFLIX_IMPORT_PATH = "/imports/netflix"


async def _reject_oversized_netflix_upload(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
    max_upload_bytes: int,
) -> Response:
    """Reject declared-oversized Netflix uploads before multipart spooling.

    This receiving-boundary check uses the declared ``Content-Length`` so an
    oversized submission cannot consume arbitrary temporary disk or upload
    time. The actual streamed byte count is still enforced separately.

    :param request: Incoming request.
    :param call_next: Remaining application call chain.
    :param max_upload_bytes: Configured maximum upload byte count.
    :return: The downstream response, or an immediate rejection response.
    """
    if request.method == "POST" and request.url.path == _NETFLIX_IMPORT_PATH:
        declared = request.headers.get("content-length")
        allowed = max_upload_bytes + _UPLOAD_LIMIT_OVERHEAD_BYTES
        if declared is not None and declared.isdigit() and int(declared) > allowed:
            return JSONResponse(status_code=413, content={"detail": _TOO_LARGE_DETAIL})
    return await call_next(request)


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

    @app.middleware("http")
    async def limit_netflix_upload(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        """Reject oversized Netflix uploads at the receiving boundary.

        The limit is read from application state at request time so test or
        composition-root overrides behave identically to production settings.

        :param request: Incoming request.
        :param call_next: Remaining application call chain.
        :return: Downstream response or the immediate rejection.
        """
        return await _reject_oversized_netflix_upload(
            request,
            call_next,
            int(request.app.state.netflix_max_upload_bytes),
        )

    register_exception_handlers(app)
    register_media_exception_handlers(app)
    register_recommendation_exception_handlers(app)
    return app
