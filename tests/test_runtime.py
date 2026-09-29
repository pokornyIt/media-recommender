"""Offline tests for the production runtime lifecycle."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fastapi.testclient import TestClient

from media_recommender.application import WorkflowFailureReason, WorkflowStatus
from media_recommender.composition import ApplicationServices, build_services
from media_recommender.web import create_app

if TYPE_CHECKING:
    import pytest
    from fastapi import FastAPI
    from httpx import Client

    from media_recommender.config import Settings

REPOSITORY_ROOT = Path(__file__).parents[1]
_DATABASE_PATH_ENV = "MEDIA_RECOMMENDER_DATABASE_PATH"
_SESSION_SECRET_ENV = "MEDIA_RECOMMENDER_WEB_SESSION_SECRET"  # noqa: S105 - environment variable name, not a secret.
_SYNTHETIC_SECRET = "synthetic-session-secret-value"  # noqa: S105 - synthetic test value, not a real secret.
_PROVIDER_ENV_NAMES = (
    "MEDIA_RECOMMENDER_TMDB_API_TOKEN",
    "MEDIA_RECOMMENDER_TMDB_BASE_URL",
    "MEDIA_RECOMMENDER_JELLYFIN_BASE_URL",
    "MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN",
    "MEDIA_RECOMMENDER_JELLYFIN_USER_ID",
)
_MEDIA_ID = "078fa456-ced1-484f-a0f4-e81816a55467"


def _configure(monkeypatch: pytest.MonkeyPatch, database_path: Path) -> None:
    """Point the application at a temporary database and synthetic session secret.

    :param monkeypatch: Pytest environment patching fixture.
    :param database_path: Temporary database path for the test.
    """
    monkeypatch.chdir(REPOSITORY_ROOT)
    monkeypatch.setenv(_DATABASE_PATH_ENV, str(database_path))
    monkeypatch.setenv(_SESSION_SECRET_ENV, _SYNTHETIC_SECRET)
    for name in _PROVIDER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _insert_media(database_path: Path) -> None:
    """Insert one synthetic catalog row directly into a stopped database.

    :param database_path: Database path to modify.
    """
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO media_items (id, media_type, title) VALUES (?, ?, ?)",
            (_MEDIA_ID, "movie", "Synthetic Runtime Movie"),
        )
        connection.commit()


def _client(app: FastAPI) -> Client:
    """Return a statically typed synchronous client for an application.

    :param app: Application to exercise.
    :return: HTTPX-compatible test client.
    """
    return cast("Client", TestClient(app))


def test_fresh_startup_applies_migrations_and_reports_ready(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Apply migrations at startup and report readiness from the database."""
    database_path = tmp_path / "fresh.db"
    _configure(monkeypatch, database_path)

    with _client(create_app()) as client:
        response = client.get("/health/ready")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ready", "database": "ok", "migrations": "current"}
    assert database_path.is_file()


def test_restart_preserves_existing_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve existing SQLite data across a container-style restart."""
    database_path = tmp_path / "restart.db"
    _configure(monkeypatch, database_path)

    with _client(create_app()) as client:
        assert client.get("/health/ready").status_code == HTTPStatus.OK
    _insert_media(database_path)

    with _client(create_app()) as client:
        assert client.get("/health/ready").status_code == HTTPStatus.OK

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute("SELECT id FROM media_items").fetchall()
    assert stored == [(_MEDIA_ID,)]


def test_lifespan_releases_services_on_shutdown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Release provider connections and the engine when the application stops."""
    database_path = tmp_path / "shutdown.db"
    _configure(monkeypatch, database_path)
    closed = False

    class FakeClosable:
        """Synthetic closable resource recording shutdown."""

        async def aclose(self) -> None:
            """Record that the resource was released."""
            nonlocal closed
            closed = True

    def build_with_spy(settings: Settings) -> ApplicationServices:
        """Build real services with a synthetic closable resource.

        :param settings: Validated application settings.
        :return: Services carrying the synthetic closable resource.
        """
        return replace(build_services(settings), closables=(FakeClosable(),))

    monkeypatch.setattr("media_recommender.web.app.build_services", build_with_spy)

    with _client(create_app()) as client:
        assert client.get("/health/live").status_code == HTTPStatus.OK
        assert closed is False

    assert closed is True


def test_provider_configuration_does_not_affect_readiness_or_pages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep readiness and unrelated pages available while a provider is unreachable."""
    database_path = tmp_path / "provider.db"
    _configure(monkeypatch, database_path)
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_BASE_URL", "http://127.0.0.1:1/")
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", "synthetic-tmdb-value")

    with _client(create_app()) as client:
        assert client.get("/health/ready").status_code == HTTPStatus.OK
        assert client.get("/health/providers").status_code == HTTPStatus.OK
        assert client.get("/settings").status_code == HTTPStatus.OK
        assert client.get("/providers/status").status_code == HTTPStatus.OK
        assert client.get("/").status_code == HTTPStatus.OK


def test_unconfigured_provider_workflow_reports_not_configured(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report an explicit safe failure instead of crashing for an unconfigured provider."""
    database_path = tmp_path / "unconfigured.db"
    _configure(monkeypatch, database_path)
    app = create_app()

    with _client(app) as client:
        assert client.get("/health/ready").status_code == HTTPStatus.OK
        orchestrator = app.state.recommendation_service
        library_report = asyncio.run(orchestrator.synchronize_jellyfin_library())
        availability_report = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))

    assert library_report.status is WorkflowStatus.FAILED
    assert library_report.items[0].reason == WorkflowFailureReason.NOT_CONFIGURED.value
    assert availability_report.status is WorkflowStatus.FAILED
    assert availability_report.items[0].reason == WorkflowFailureReason.NOT_CONFIGURED.value
