"""Offline tests for the FastAPI and server-rendered Web foundation."""

from __future__ import annotations

from http import HTTPStatus
from inspect import signature
from typing import TYPE_CHECKING, cast

import pytest  # noqa: TC002 - Pytest resolves this fixture annotation at runtime.
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount, Route

from media_recommender.web import create_app
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.api import router as api_router
from media_recommender.web.routes.pages import get_templates

if TYPE_CHECKING:
    from httpx import Client


_PROVIDER_COUNT = 2


def _client(app: FastAPI, *, raise_server_exceptions: bool = True) -> Client:
    """Return a statically typed synchronous client for a FastAPI application.

    :param app: Application to exercise.
    :param raise_server_exceptions: Whether endpoint exceptions escape the client.
    :return: HTTPX-compatible test client.
    """
    return cast("Client", TestClient(app, raise_server_exceptions=raise_server_exceptions))


def test_factory_is_parameterless_and_leaves_dependencies_for_feature_routes() -> None:
    """Create the HTTP foundation without accepting or configuring feature services."""
    app = create_app()

    assert signature(create_app).parameters == {}
    assert app.dependency_overrides == {}
    paths = {route.path for route in app.routes if isinstance(route, (Mount, Route))}
    assert paths >= {"/", "/health/live", "/static"}


def test_dependency_overrides_replace_a_route_dependency() -> None:
    """Use FastAPI's native overrides to replace the home-page template dependency."""
    app = create_app()
    calls = 0

    def overridden_templates(request: Request) -> object:
        """Record FastAPI resolving the native template dependency override."""
        nonlocal calls
        calls += 1
        return request.app.state.templates

    app.dependency_overrides[get_templates] = overridden_templates

    response = _client(app).get("/")

    assert response.status_code == HTTPStatus.OK
    assert calls == 1


def test_liveness_has_no_declared_dependencies() -> None:
    """Keep liveness isolated from templates, application services, and providers."""
    route = next(route for route in create_app().routes if isinstance(route, APIRoute) and route.path == "/health/live")

    assert route.dependant.dependencies == []


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


def test_settings_shows_safe_unconfigured_status_and_default_region(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render the offline settings page without revealing missing provider configuration."""
    monkeypatch.delenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", raising=False)
    monkeypatch.setenv("MEDIA_RECOMMENDER_DEFAULT_REGION", "CZ")

    response = _client(create_app()).get("/settings")

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/settings">Settings</a>' in response.text
    assert response.text.count("Not configured") == _PROVIDER_COUNT
    assert "CZ" in response.text


def test_settings_shows_configured_status_without_exposing_runtime_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render only provider status and region for complete synthetic configuration."""
    tmdb_value = "synthetic-tmdb-value"
    jellyfin_url = "https://jellyfin.synthetic.invalid/"
    jellyfin_value = "synthetic-jellyfin-value"
    jellyfin_user_id = "synthetic-user"
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", tmdb_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", jellyfin_url)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", jellyfin_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", jellyfin_user_id)
    monkeypatch.setenv("MEDIA_RECOMMENDER_DEFAULT_REGION", "US")

    response = _client(create_app()).get("/settings")

    assert response.status_code == HTTPStatus.OK
    assert response.text.count("Configured") == _PROVIDER_COUNT
    assert "US" in response.text
    for value in (tmdb_value, jellyfin_url, jellyfin_value, jellyfin_user_id):
        assert value not in response.text


def test_openapi_includes_liveness_response_schema() -> None:
    """Expose the liveness DTO through generated OpenAPI."""
    schema = create_app().openapi()

    assert "/health/live" in schema["paths"]
    assert schema["paths"]["/health/live"]["get"]["responses"]["200"]["content"]["application/json"] == {
        "schema": {"$ref": "#/components/schemas/LivenessResponse"}
    }


def test_business_router_uses_versioned_prefix_for_feature_routes() -> None:
    """Keep business API routes beneath the shared versioned prefix."""
    paths = create_app().openapi()["paths"]

    assert api_router.prefix == "/api/v1"
    assert "/api/v1/media/{media_id}" in paths


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
