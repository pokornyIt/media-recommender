"""Offline tests for the FastAPI and server-rendered Web foundation."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route

from media_recommender.web import create_app
from media_recommender.web.errors import register_exception_handlers

if TYPE_CHECKING:
    from httpx import Client


def _client(app: FastAPI, *, raise_server_exceptions: bool = True) -> Client:
    """Return a statically typed synchronous client for a FastAPI application.

    :param app: Application to exercise.
    :param raise_server_exceptions: Whether endpoint exceptions escape the client.
    :return: HTTPX-compatible test client.
    """
    return cast("Client", TestClient(app, raise_server_exceptions=raise_server_exceptions))


def test_factory_creates_foundation_without_application_services() -> None:
    """Create the HTTP foundation without configuring feature services."""
    app = create_app()

    assert app.dependency_overrides == {}
    paths = {route.path for route in app.routes if isinstance(route, (Mount, Route))}
    assert paths >= {"/", "/health/live", "/static"}


def test_liveness_uses_only_the_http_process() -> None:
    """Return liveness without resolving an unrelated overridden dependency."""
    app = create_app()
    calls = 0

    def unused_dependency() -> object:
        """Record any accidental dependency resolution."""
        nonlocal calls
        calls += 1
        return object()

    app.dependency_overrides[unused_dependency] = unused_dependency

    response = _client(app).get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}
    assert calls == 0


def test_home_uses_shared_accessible_layout_and_static_css() -> None:
    """Render the home page through the shared layout and serve its stylesheet."""
    app = create_app()
    client = _client(app)

    response = client.get("/")

    assert response.status_code == HTTPStatus.OK
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in response.text
    assert '<nav aria-label="Primary navigation">' in response.text
    assert '<main id="main-content" tabindex="-1">' in response.text
    assert 'aria-live="polite"' in response.text
    assert "Media Recommender" in response.text
    css_response = client.get("/static/styles.css")
    assert css_response.status_code == HTTPStatus.OK
    assert "@media" in css_response.text


def test_openapi_includes_liveness_response_schema() -> None:
    """Expose the liveness DTO through generated OpenAPI."""
    schema = create_app().openapi()

    assert "/health/live" in schema["paths"]
    assert schema["paths"]["/health/live"]["get"]["responses"]["200"]["content"]["application/json"] == {
        "schema": {"$ref": "#/components/schemas/LivenessResponse"}
    }


def test_empty_business_router_has_versioned_prefix_without_a_placeholder_route() -> None:
    """Keep the reserved business API prefix free of temporary demonstration endpoints."""
    paths = create_app().openapi()["paths"]

    assert not any(path.startswith("/api/v1/") for path in paths)


def test_framework_http_errors_keep_fastapi_semantics() -> None:
    """Preserve FastAPI's standard JSON shape for unmatched routes."""
    response = _client(create_app()).get("/missing")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"detail": "Not Found"}


def test_registered_specific_handler_precedes_safe_fallback() -> None:
    """Allow later feature handlers to take precedence over the fallback."""
    app = FastAPI()
    router = APIRouter()

    class SyntheticFeatureError(Exception):
        """Synthetic error handled by a future feature route."""

    async def synthetic_feature_handler(_: Request, __: Exception) -> JSONResponse:
        """Return a feature-specific synthetic response."""
        return JSONResponse(status_code=418, content={"handled": True})

    @router.get("/specific")
    async def specific() -> None:
        """Raise an error handled by the explicitly registered handler."""
        raise SyntheticFeatureError

    app.include_router(router)
    register_exception_handlers(app, {SyntheticFeatureError: synthetic_feature_handler})

    response = _client(app, raise_server_exceptions=False).get("/specific")

    assert response.status_code == HTTPStatus.IM_A_TEAPOT
    assert response.json() == {"handled": True}


def test_unexpected_exception_uses_safe_common_error_envelope() -> None:
    """Hide synthetic sensitive exception data behind the generic error response."""
    app = create_app()

    @app.get("/unexpected")
    async def unexpected() -> None:
        """Raise a synthetic unexpected exception containing private-like text."""
        message = "synthetic-secret=do-not-disclose"
        raise RuntimeError(message)

    response = _client(app, raise_server_exceptions=False).get("/unexpected")

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.json() == {"error": {"code": "internal_error", "message": "Internal server error"}}
    assert "synthetic-secret" not in response.text
