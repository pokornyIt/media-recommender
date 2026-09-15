"""Offline HTTP tests for deterministic recommendation responses."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from media_recommender.application import (
    AvailabilitySourceKind,
    ConstraintMatch,
    ConstraintMatchKind,
    KnownAvailability,
    RankedRecommendation,
    RankingReason,
    RankingReasonKind,
    RecommendationCriteria,
    RecommendationFilterResult,
    RecommendationResult,
    RecommendationWarning,
    RecommendationWarningKind,
)
from media_recommender.domain import AvailabilityType, Genre, LikeState, MediaId, Movie, Runtime, TVShow, WatchStatus
from media_recommender.web import create_app
from media_recommender.web.routes.recommendations import get_recommendation_service

if TYPE_CHECKING:
    from fastapi import FastAPI
    from httpx import Client

MOVIE_ID = UUID("33333333-3333-3333-3333-333333333333")
TV_SHOW_ID = UUID("44444444-4444-4444-4444-444444444444")


class FakeRecommendationService:
    """Synthetic recommendation boundary recording the submitted criteria."""

    def __init__(self, result: RecommendationResult) -> None:
        """Initialize the fake with one synthetic application result.

        :param result: Result returned for every recommendation request.
        """
        self.result = result
        self.criteria: list[RecommendationCriteria] = []

    async def recommend(self, criteria: RecommendationCriteria) -> RecommendationResult:
        """Record and return the synthetic deterministic result.

        :param criteria: Application criteria submitted by the HTTP route.
        :return: Configured synthetic recommendation result.
        """
        self.criteria.append(criteria)
        return self.result


def _client(app: FastAPI) -> Client:
    """Return a statically typed client for a synthetic HTTP application."""
    return cast("Client", TestClient(app))


def _result() -> RecommendationResult:
    """Create two ordered synthetic accepted recommendations and a hidden decision set."""
    movie = Movie(
        id=MediaId(MOVIE_ID),
        title="Synthetic Movie",
        released_on=date(2024, 1, 2),
        runtime=Runtime(101),
        genres=(Genre("Drama"),),
    )
    tv_show = TVShow(id=MediaId(TV_SHOW_ID), title="Synthetic Series")
    recommendations = (
        RankedRecommendation(
            media=movie,
            rank=2,
            score=15,
            matched_constraints=(ConstraintMatch(ConstraintMatchKind.GENRE_INCLUDED, ("drama",)),),
            ranking_reasons=(RankingReason(RankingReasonKind.LIKED, 30, ("liked",)),),
            warnings=(RecommendationWarning(RecommendationWarningKind.AVAILABILITY_UNKNOWN),),
            availability=(
                KnownAvailability(
                    AvailabilitySourceKind.STREAMING,
                    "Netflix",
                    "CZ",
                    AvailabilityType.SUBSCRIPTION,
                    "tmdb",
                ),
            ),
            watch_status=WatchStatus.UNWATCHED,
            personal_rating=7.5,
            like_states=(LikeState.LIKED,),
        ),
        RankedRecommendation(
            media=tv_show,
            rank=1,
            score=9,
            matched_constraints=(),
            ranking_reasons=(),
            warnings=(),
            availability=(),
            watch_status=WatchStatus.UNKNOWN,
            personal_rating=None,
            like_states=(),
        ),
    )
    return RecommendationResult(recommendations=recommendations, filter_result=RecommendationFilterResult((), ()))


def test_recommendations_route_maps_every_criterion_and_preserves_service_order() -> None:
    """Delegate once while retaining accepted result fields and supplied ordering."""
    fake_service = FakeRecommendationService(_result())
    app = create_app()
    app.dependency_overrides[get_recommendation_service] = lambda: fake_service

    response = _client(app).post(
        "/api/v1/recommendations",
        json={
            "media_types": ["movie", "tv_show"],
            "include_genres": ["Drama", "Science Fiction"],
            "genre_match": "any",
            "exclude_genres": ["Horror"],
            "include_countries": ["cz"],
            "exclude_countries": ["us"],
            "include_regions": ["europe"],
            "exclude_regions": ["asia"],
            "minimum_runtime_minutes": 90,
            "maximum_runtime_minutes": 120,
            "minimum_release_year": 2020,
            "maximum_release_year": 2025,
            "released_from": "2020-01-01",
            "released_until": "2025-01-01",
            "watch": "not_watched",
            "minimum_personal_rating": 6.5,
            "excluded_like_states": ["disliked"],
            "availability_any_of": [
                {"kind": "local_library", "provider": "Jellyfin"},
                {
                    "kind": "streaming",
                    "provider": "Netflix",
                    "region": "cz",
                    "source_provider": "tmdb",
                    "availability_types": ["subscription"],
                },
            ],
            "apply_profile_exclusions": False,
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert len(fake_service.criteria) == 1
    criteria = fake_service.criteria[0]
    assert criteria.include_genres == frozenset({"drama", "science fiction"})
    assert criteria.genre_match.value == "any"
    assert criteria.include_countries == frozenset({"CZ"})
    assert criteria.availability_any_of[0].kind.value == "local_library"
    assert criteria.availability_any_of[1].region == "CZ"
    assert criteria.apply_profile_exclusions is False
    payload = response.json()
    assert [item["media"]["id"] for item in payload["recommendations"]] == [str(MOVIE_ID), str(TV_SHOW_ID)]
    assert payload["recommendations"][0]["matched_constraints"] == [{"kind": "genre_included", "values": ["drama"]}]
    assert payload["recommendations"][0]["ranking_reasons"] == [{"kind": "liked", "points": 30, "values": ["liked"]}]
    assert payload["recommendations"][0]["warnings"] == [{"kind": "availability_unknown"}]
    assert payload["recommendations"][0]["availability"][0]["region"] == "CZ"
    assert "filter_result" not in payload
    assert "decisions" not in str(payload)


def test_recommendations_route_returns_an_empty_successful_collection() -> None:
    """Return no accepted candidates as a successful read-only computation."""
    fake_service = FakeRecommendationService(RecommendationResult((), RecommendationFilterResult((), ())))
    app = create_app()
    app.dependency_overrides[get_recommendation_service] = lambda: fake_service

    response = _client(app).post("/api/v1/recommendations", json={})

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"recommendations": []}
    assert len(fake_service.criteria) == 1


def test_recommendations_route_returns_safe_error_for_application_criteria_validation() -> None:
    """Translate invalid application criterion values without exposing internal details."""
    app = create_app()
    app.dependency_overrides[get_recommendation_service] = lambda: FakeRecommendationService(_result())

    response = _client(app).post(
        "/api/v1/recommendations",
        json={"include_countries": ["CZE"]},
    )

    assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert response.json() == {
        "error": {"code": "recommendation_criteria_invalid", "message": "Invalid recommendation criteria"}
    }


def test_recommendations_route_declares_dependency_and_openapi_without_hidden_filter_data() -> None:
    """Expose the injectable endpoint schema without rejected-candidate internals."""
    app = create_app()
    route = next(
        route for route in app.routes if isinstance(route, APIRoute) and route.path == "/api/v1/recommendations"
    )
    schema = app.openapi()
    response_schema = schema["components"]["schemas"]["RecommendationsResponse"]

    assert route.dependant.dependencies[0].call is get_recommendation_service
    assert "RecommendationRequest" in schema["components"]["schemas"]
    assert "filter_result" not in response_schema["properties"]
    assert "RecommendationDecision" not in schema["components"]["schemas"]
