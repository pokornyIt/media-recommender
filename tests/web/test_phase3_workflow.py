"""Offline composed Web-to-application-service workflow coverage."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi.testclient import TestClient

from media_recommender.domain import (
    Country,
    ExternalId,
    Genre,
    MediaId,
    Movie,
    Preference,
    PreferenceEffect,
    PreferenceId,
    PreferenceKind,
    Runtime,
)
from media_recommender.persistence.personal_repository import SqlAlchemyPersonalMediaRepository
from media_recommender.persistence.repository import SqlAlchemyMediaCatalog
from media_recommender.web import create_app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest
    from fastapi import FastAPI
    from httpx import Client

    from media_recommender.composition import ApplicationServices


_DATABASE_PATH_ENV = "MEDIA_RECOMMENDER_DATABASE_PATH"
_SESSION_SECRET_ENV = "MEDIA_RECOMMENDER_WEB_SESSION_SECRET"  # noqa: S105 - synthetic test setting name.
_SYNTHETIC_SECRET = "synthetic-session-secret-value"  # noqa: S105 - synthetic test value.


def _client(app: FastAPI) -> Client:
    """Return a statically typed client for a FastAPI application."""
    return cast("Client", TestClient(app))


def _movie() -> Movie:
    """Return deterministic synthetic catalog data for the composed workflow."""
    return Movie(
        id=MediaId.new(),
        title="Synthetic Phase Three Movie",
        released_on=date(2026, 1, 1),
        runtime=Runtime(101),
        genres=(Genre("Science Fiction"),),
        production_countries=(Country("CZ", "Czechia"),),
        external_ids=frozenset({ExternalId("tmdb", "phase3-46")}),
    )


def _candidate(title: str, *, runtime: int, genres: tuple[str, ...]) -> Movie:
    """Return one synthetic recommendation candidate."""
    return Movie(
        id=MediaId.new(),
        title=title,
        released_on=date(2026, 1, 1),
        runtime=Runtime(runtime),
        genres=tuple(Genre(genre) for genre in genres),
        production_countries=(Country("CZ", "Czechia"),),
        external_ids=frozenset({ExternalId("tmdb", str(runtime))}),
    )


def test_web_reads_real_service_state_after_application_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Persist synthetic state through production services and read it through HTTP after restart."""
    database_path = tmp_path / "phase3-workflow.db"
    monkeypatch.setenv(_DATABASE_PATH_ENV, str(database_path))
    monkeypatch.setenv(_SESSION_SECRET_ENV, _SYNTHETIC_SECRET)
    movie = _movie()
    app = create_app()

    with _client(app) as client:
        services = cast("ApplicationServices", app.state.services)
        catalog = SqlAlchemyMediaCatalog(services.session_factory)
        asyncio.run(catalog.save(movie))
        short = _candidate("Preferred Short", runtime=90, genres=("Science Fiction",))
        excluded = _candidate("Excluded Candidate", runtime=110, genres=("Science Fiction", "Horror"))
        asyncio.run(catalog.save(short))
        asyncio.run(catalog.save(excluded))
        personal = SqlAlchemyPersonalMediaRepository(services.session_factory)
        profile = asyncio.run(personal.get_or_create_default())
        asyncio.run(
            personal.save_preference(
                Preference(
                    id=PreferenceId.new(),
                    profile_id=profile.id,
                    kind=PreferenceKind.RUNTIME_MINUTES,
                    effect=PreferenceEffect.PREFER,
                    maximum=100,
                )
            )
        )
        asyncio.run(
            personal.save_preference(
                Preference(
                    id=PreferenceId.new(),
                    profile_id=profile.id,
                    kind=PreferenceKind.GENRE,
                    effect=PreferenceEffect.EXCLUDE,
                    value="Horror",
                )
            )
        )
        response = client.get(f"/api/v1/media/{movie.id.value}")
        assert response.status_code == HTTPStatus.OK
        assert response.json()["title"] == movie.title

        detail_response = client.get(f"/media/{movie.id.value}")
        assert detail_response.status_code == HTTPStatus.OK
        assert movie.title in detail_response.text
        token_match = re.search(r'name="csrf_token" value="([^"]+)"', detail_response.text)
        assert token_match is not None
        rating_response = client.post(
            f"/media/{movie.id.value}",
            data={"csrf_token": token_match.group(1), "rating_value": "8.5"},
        )
        assert rating_response.status_code == HTTPStatus.OK
        assert "8.5" in rating_response.text

        recommendations = client.post(
            "/recommendations",
            data={
                "csrf_token": _csrf_token(client, "/recommendations"),
                "media_types": "movie",
                "include_genres": "Science Fiction",
                "apply_profile_exclusions": "true",
            },
        )
        assert recommendations.status_code == HTTPStatus.OK
        assert "Preferred Short" in recommendations.text
        assert "Excluded Candidate" not in recommendations.text
        without_exclusions = client.post(
            "/recommendations",
            data={
                "csrf_token": _csrf_token(client, "/recommendations"),
                "media_types": "movie",
                "include_genres": "Science Fiction",
            },
        )
        assert "Excluded Candidate" in without_exclusions.text

    with _client(create_app()) as restarted_client:
        response = restarted_client.get(f"/api/v1/media/{movie.id.value}")
        assert response.status_code == HTTPStatus.OK
        assert response.json()["external_ids"] == [{"namespace": "tmdb", "value": "phase3-46"}]
        detail_response = restarted_client.get(f"/media/{movie.id.value}")
        assert "8.5" in detail_response.text


def _csrf_token(client: Client, path: str) -> str:
    """Return the CSRF token rendered by a server-side form page."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', client.get(path).text)
    assert match is not None
    return match.group(1)
