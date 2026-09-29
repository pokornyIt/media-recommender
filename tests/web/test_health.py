"""Offline tests for operational health endpoint semantics."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi.testclient import TestClient

from media_recommender.application import (
    ProviderOperationRecorder,
    WorkflowCounts,
    WorkflowFailureReason,
    WorkflowItem,
    WorkflowItemStatus,
    WorkflowKind,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.application.readiness import ReadinessReport
from media_recommender.web import create_app

if TYPE_CHECKING:
    import pytest
    from httpx import Client

_TMDB_TOKEN_ENV = "MEDIA_RECOMMENDER_TMDB_API_TOKEN"  # noqa: S105 - environment variable name, not a secret.
_SYNTHETIC_TMDB_TOKEN = "synthetic-tmdb-value"  # noqa: S105 - synthetic test value, not a real secret.
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


def _transient_failure_report() -> WorkflowReport:
    """Build a synthetic transient provider failure report.

    :return: Failed synthetic streaming-availability report.
    """
    return WorkflowReport(
        kind=WorkflowKind.STREAMING_AVAILABILITY,
        status=WorkflowStatus.FAILED,
        counts=WorkflowCounts(failed=1),
        items=(WorkflowItem(1, WorkflowItemStatus.FAILED, WorkflowFailureReason.TRANSIENT_FAILURE.value),),
    )


def test_provider_health_reports_recorded_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a recorded provider operation outcome without affecting liveness or readiness."""
    monkeypatch.setenv(_TMDB_TOKEN_ENV, _SYNTHETIC_TMDB_TOKEN)
    app = create_app()
    recorder = ProviderOperationRecorder()
    recorder.record(_transient_failure_report())
    app.state.provider_operations = recorder
    client = cast("Client", TestClient(app))

    providers = {item["provider"]: item for item in client.get("/health/providers").json()["providers"]}

    assert providers["tmdb"]["operational"] == "transient_failure"
    assert providers["tmdb"]["observed_at"] is not None
    assert providers["jellyfin"]["operational"] == "no_recorded_operation"
    assert client.get("/health/live").status_code == HTTPStatus.OK
    assert client.get("/health/ready").status_code == HTTPStatus.SERVICE_UNAVAILABLE


def test_provider_health_resets_without_a_recorder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report no recorded operation for a fresh process without a recorder."""
    monkeypatch.setenv(_TMDB_TOKEN_ENV, _SYNTHETIC_TMDB_TOKEN)

    providers = {item["provider"]: item for item in _client().get("/health/providers").json()["providers"]}

    assert all(item["operational"] == "no_recorded_operation" for item in providers.values())
