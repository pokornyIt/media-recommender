"""Offline HTTP and mapping tests for shared catalog media."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from media_recommender.domain import Artwork, ArtworkType, Country, ExternalId, Genre, MediaId, Movie, Runtime, TVShow
from media_recommender.web import create_app
from media_recommender.web.routes.media import MediaNotFoundError, get_catalog_service
from media_recommender.web.schemas.media import MovieResponse, TVShowResponse, media_to_response

if TYPE_CHECKING:
    from httpx import Client

MOVIE_ID = UUID("11111111-1111-1111-1111-111111111111")
TV_SHOW_ID = UUID("22222222-2222-2222-2222-222222222222")
MOVIE_RUNTIME_MINUTES = 117

if TYPE_CHECKING:
    from fastapi import FastAPI


class FakeCatalogService:
    """Synthetic catalog boundary recording requested internal identities."""

    def __init__(self, media_by_id: dict[MediaId, Movie | TVShow]) -> None:
        """Initialize the fake with synthetic shared catalog media.

        :param media_by_id: Media returned for each requested internal identity.
        """
        self.media_by_id = media_by_id
        self.requested_ids: list[MediaId] = []

    async def get(self, media_id: MediaId) -> Movie | TVShow | None:
        """Record and return synthetic catalog media.

        :param media_id: Internal media identity requested by the HTTP route.
        :return: Synthetic media when configured for the identity.
        """
        self.requested_ids.append(media_id)
        return self.media_by_id.get(media_id)


def _client(app: FastAPI) -> Client:
    """Return a statically typed client for a synthetic HTTP application.

    :param app: Application to exercise.
    :return: HTTPX-compatible test client.
    """
    return cast("Client", TestClient(app))


def _movie() -> Movie:
    """Create synthetic shared catalog metadata for a movie.

    :return: Movie with normalized catalog facts.
    """
    return Movie(
        id=MediaId(MOVIE_ID),
        title="Synthetic Movie",
        original_title="Original Synthetic Movie",
        released_on=date(2024, 2, 3),
        runtime=Runtime(MOVIE_RUNTIME_MINUTES),
        genres=(Genre("Drama"), Genre("Science Fiction")),
        production_countries=(Country("CZ", "Czechia"),),
        artwork=(Artwork(ArtworkType.POSTER, "https://images.example.test/poster.jpg", "cs"),),
        external_ids=frozenset({ExternalId("imdb", "tt123"), ExternalId("tmdb", "456")}),
    )


def _tv_show() -> TVShow:
    """Create synthetic shared catalog metadata for a TV show.

    :return: TV show with intentionally absent optional metadata.
    """
    return TVShow(id=MediaId(TV_SHOW_ID), title="Synthetic Series")


def test_media_mapper_represents_movie_catalog_facts() -> None:
    """Map every supported shared movie fact into the explicit HTTP DTO."""
    response = media_to_response(_movie())

    assert isinstance(response, MovieResponse)
    assert response.model_dump(mode="json") == {
        "id": str(MOVIE_ID),
        "title": "Synthetic Movie",
        "original_title": "Original Synthetic Movie",
        "release_date": "2024-02-03",
        "release_year": 2024,
        "genres": [{"name": "Drama"}, {"name": "Science Fiction"}],
        "production_countries": [{"code": "CZ", "name": "Czechia"}],
        "artwork": [{"type": "poster", "url": "https://images.example.test/poster.jpg", "language": "cs"}],
        "external_ids": [{"namespace": "imdb", "value": "tt123"}, {"namespace": "tmdb", "value": "456"}],
        "media_type": "movie",
        "runtime_minutes": 117,
    }


def test_media_mapper_represents_tv_show_with_missing_optional_facts() -> None:
    """Keep TV runtime distinct and serialize absent or empty catalog facts stably."""
    response = media_to_response(_tv_show())

    assert isinstance(response, TVShowResponse)
    assert response.model_dump(mode="json") == {
        "id": str(TV_SHOW_ID),
        "title": "Synthetic Series",
        "original_title": None,
        "release_date": None,
        "release_year": None,
        "genres": [],
        "production_countries": [],
        "artwork": [],
        "external_ids": [],
        "media_type": "tv_show",
        "episode_runtime_minutes": None,
    }


def test_media_route_resolves_fake_catalog_service_and_returns_validated_response() -> None:
    """Retrieve media through a replaceable application-service dependency only."""
    fake_catalog_service = FakeCatalogService({MediaId(MOVIE_ID): _movie()})
    app = create_app()
    app.dependency_overrides[get_catalog_service] = lambda: fake_catalog_service

    response = _client(app).get(f"/api/v1/media/{MOVIE_ID}")

    assert response.status_code == HTTPStatus.OK
    assert MovieResponse.model_validate(response.json()).runtime_minutes == MOVIE_RUNTIME_MINUTES
    assert fake_catalog_service.requested_ids == [MediaId(MOVIE_ID)]


def test_media_route_retrieves_a_tv_show() -> None:
    """Return the TV-specific response shape through the catalog service boundary."""
    fake_catalog_service = FakeCatalogService({MediaId(TV_SHOW_ID): _tv_show()})
    app = create_app()
    app.dependency_overrides[get_catalog_service] = lambda: fake_catalog_service

    response = _client(app).get(f"/api/v1/media/{TV_SHOW_ID}")

    assert response.status_code == HTTPStatus.OK
    assert TVShowResponse.model_validate(response.json()).episode_runtime_minutes is None
    assert fake_catalog_service.requested_ids == [MediaId(TV_SHOW_ID)]


def test_media_route_returns_common_safe_not_found_error() -> None:
    """Map an unknown internal identity through the media API's shared error mechanism."""
    fake_catalog_service = FakeCatalogService({})
    app = create_app()
    app.dependency_overrides[get_catalog_service] = lambda: fake_catalog_service

    response = _client(app).get(f"/api/v1/media/{MOVIE_ID}")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json() == {"error": {"code": "media_not_found", "message": "Media not found"}}
    assert MediaNotFoundError in app.exception_handlers


def test_media_route_rejects_malformed_uuid_before_calling_catalog_service() -> None:
    """Let FastAPI reject malformed internal identities through request validation."""
    fake_catalog_service = FakeCatalogService({})
    app = create_app()
    app.dependency_overrides[get_catalog_service] = lambda: fake_catalog_service

    response = _client(app).get("/api/v1/media/not-a-uuid")

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert fake_catalog_service.requested_ids == []


def test_media_route_declares_catalog_dependency_and_openapi_response() -> None:
    """Expose the route's injectable service boundary and discriminated response schema."""
    app = create_app()
    route = next(
        route for route in app.routes if isinstance(route, APIRoute) and route.path == "/api/v1/media/{media_id}"
    )
    schema = app.openapi()

    assert route.dependant.dependencies[0].call is get_catalog_service
    response_schema = schema["paths"]["/api/v1/media/{media_id}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema["oneOf"] == [
        {"$ref": "#/components/schemas/MovieResponse"},
        {"$ref": "#/components/schemas/TVShowResponse"},
    ]
    assert response_schema["discriminator"] == {
        "propertyName": "media_type",
        "mapping": {
            "movie": "#/components/schemas/MovieResponse",
            "tv_show": "#/components/schemas/TVShowResponse",
        },
    }
    assert schema["paths"]["/api/v1/media/{media_id}"]["get"]["responses"]["404"]["content"]["application/json"] == {
        "schema": {"$ref": "#/components/schemas/ErrorResponse"}
    }
