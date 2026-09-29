"""Offline tests for operational health endpoint semantics."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi.testclient import TestClient

from media_recommender.application.readiness import ReadinessReport
from media_recommender.web import create_app

if TYPE_CHECKING:
    import pytest
    from httpx import Client

_PROVIDER_ENV_NAMES = (
    "MEDIA_RECOMMENDER_TMDB_API_TOKEN",
    "MEDIA_RECOMMENDER_TMDB_BASE_URL",
    "MEDIA_RECOMMENDER_JELLYFIN_BASE_URL",
    "MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN",
    "MEDIA_RECOMMENDER_JELLYFIN_USER_ID",
)


def _client() -> Client:
    """Return a statically typed synchronous client for a fresh application.

    :return: HTTPX-compatible test client.
    """
    return cast("Client", TestClient(create_app()))


def test_liveness_reports_ok_without_dependencies() -> None:
    """Report liveness without database or provider access."""
    response = _client().get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}


def test_readiness_is_unavailable_without_a_started_lifespan() -> None:
    """Report not-ready when no readiness probe has been attached."""
    response = _client().get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json() == {"status": "not_ready", "database": "unavailable", "migrations": "unknown"}


def test_readiness_reports_ready_from_the_attached_probe() -> None:
    """Report ready when the attached probe confirms database and migration state."""
    app = create_app()

    class ReadyProbe:
        """Synthetic readiness probe reporting a current database."""

        async def check(self) -> ReadinessReport:
            """Return a ready report."""
            return ReadinessReport(ready=True, database="ok", migrations="current")

    app.state.readiness_probe = ReadyProbe()

    response = cast("Client", TestClient(app)).get("/health/ready")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ready", "database": "ok", "migrations": "current"}


def test_readiness_reports_not_ready_from_the_attached_probe() -> None:
    """Report not-ready when the attached probe reports pending migrations."""
    app = create_app()

    class PendingProbe:
        """Synthetic readiness probe reporting pending migrations."""

        async def check(self) -> ReadinessReport:
            """Return a not-ready report."""
            return ReadinessReport(ready=False, database="ok", migrations="pending")

    app.state.readiness_probe = PendingProbe()

    response = cast("Client", TestClient(app)).get("/health/ready")

    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json() == {"status": "not_ready", "database": "ok", "migrations": "pending"}


def test_provider_health_is_separate_and_never_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report provider health separately without making the application unhealthy."""
    for name in _PROVIDER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    response = _client().get("/health/providers")

    assert response.status_code == HTTPStatus.OK
    providers = response.json()["providers"]
    assert {item["provider"] for item in providers} == {"tmdb", "jellyfin"}
    assert all(item["configuration"] == "not_configured" for item in providers)
