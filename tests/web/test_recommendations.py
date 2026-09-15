"""Offline HTTP tests for deterministic recommendation responses."""

from __future__ import annotations

from datetime import date
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from media_recommender.application import (
    AvailabilityCriterion,
    AvailabilitySourceKind,
    ConstraintMatch,
    ConstraintMatchKind,
    FilterExclusion,
    FilterReason,
    GenreMatch,
    KnownAvailability,
    RankedRecommendation,
    RankingReason,
    RankingReasonKind,
    RecommendationCriteria,
    RecommendationDecision,
    RecommendationFilterResult,
    RecommendationResult,
    RecommendationWarning,
    RecommendationWarningKind,
    WatchRequirement,
)
from media_recommender.application.regions import ProductionRegion
from media_recommender.domain import (
    AvailabilityType,
    Genre,
    LikeState,
    MediaId,
    MediaType,
    Movie,
    Runtime,
    TVShow,
    WatchStatus,
)
from media_recommender.web import create_app
from media_recommender.web.routes.recommendations import get_recommendation_service
from media_recommender.web.schemas.recommendations import RecommendationRequest, request_to_criteria

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
    rejected_movie = Movie(id=MediaId(UUID("55555555-5555-5555-5555-555555555555")), title="Rejected private title")
    return RecommendationResult(
        recommendations=recommendations,
        filter_result=RecommendationFilterResult(
            (),
            (
                RecommendationDecision(
                    media=rejected_movie,
                    exclusions=(FilterExclusion(FilterReason.GENRE_EXCLUDED, ("internal-filter-decision",)),),
                ),
            ),
        ),
    )


def _complete_request() -> dict[str, object]:
    """Return every supported request field with synthetic valid values."""
    return {
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
    }


def test_request_to_criteria_maps_every_request_field() -> None:
    """Map each validated HTTP request field to its typed application counterpart."""
    criteria = request_to_criteria(RecommendationRequest.model_validate(_complete_request()))

    assert criteria == RecommendationCriteria(
        media_types=frozenset({MediaType.MOVIE, MediaType.TV_SHOW}),
        include_genres=frozenset({"drama", "science fiction"}),
        genre_match=GenreMatch.ANY,
        exclude_genres=frozenset({"horror"}),
        include_countries=frozenset({"CZ"}),
        exclude_countries=frozenset({"US"}),
        include_regions=frozenset({ProductionRegion.EUROPE}),
        exclude_regions=frozenset({ProductionRegion.ASIA}),
        minimum_runtime_minutes=90,
        maximum_runtime_minutes=120,
        minimum_release_year=2020,
        maximum_release_year=2025,
        released_from=date(2020, 1, 1),
        released_until=date(2025, 1, 1),
        watch=WatchRequirement.NOT_WATCHED,
        minimum_personal_rating=6.5,
        excluded_like_states=frozenset({LikeState.DISLIKED}),
        availability_any_of=(
            AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider="jellyfin"),
            AvailabilityCriterion(
                kind=AvailabilitySourceKind.STREAMING,
                provider="Netflix",
                region="CZ",
                source_provider="tmdb",
                availability_types=frozenset({AvailabilityType.SUBSCRIPTION}),
            ),
        ),
        apply_profile_exclusions=False,
    )


def test_recommendations_route_maps_complete_contract_and_preserves_service_order() -> None:
    """Delegate once while retaining accepted result fields and supplied ordering."""
    fake_service = FakeRecommendationService(_result())
    app = create_app()
    app.dependency_overrides[get_recommendation_service] = lambda: fake_service

    response = _client(app).post(
        "/api/v1/recommendations",
        json=_complete_request(),
    )

    assert response.status_code == HTTPStatus.OK
    assert len(fake_service.criteria) == 1
    assert fake_service.criteria[0] == request_to_criteria(RecommendationRequest.model_validate(_complete_request()))
    assert response.json() == {
        "recommendations": [
            {
                "media": {
                    "id": str(MOVIE_ID),
                    "title": "Synthetic Movie",
                    "original_title": None,
                    "release_date": "2024-01-02",
                    "release_year": 2024,
                    "genres": [{"name": "Drama"}],
                    "production_countries": [],
                    "artwork": [],
                    "external_ids": [],
                    "media_type": "movie",
                    "runtime_minutes": 101,
                },
                "rank": 2,
                "score": 15,
                "matched_constraints": [{"kind": "genre_included", "values": ["drama"]}],
                "ranking_reasons": [{"kind": "liked", "points": 30, "values": ["liked"]}],
                "warnings": [{"kind": "availability_unknown"}],
                "availability": [
                    {
                        "kind": "streaming",
                        "provider": "Netflix",
                        "region": "CZ",
                        "availability_type": "subscription",
                        "source_provider": "tmdb",
                    }
                ],
                "watch_status": "unwatched",
                "personal_rating": 7.5,
                "like_states": ["liked"],
            },
            {
                "media": {
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
                },
                "rank": 1,
                "score": 9,
                "matched_constraints": [],
                "ranking_reasons": [],
                "warnings": [],
                "availability": [],
                "watch_status": "unknown",
                "personal_rating": None,
                "like_states": [],
            },
        ]
    }
    serialized_response = response.text
    assert "filter_result" not in serialized_response
    assert "decisions" not in serialized_response
    assert "Rejected private title" not in serialized_response
    assert "internal-filter-decision" not in serialized_response


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


def test_recommendations_route_rejects_invalid_enums_ranges_and_availability_without_private_data() -> None:
    """Return safe client errors for invalid request-local recommendation inputs."""
    app = create_app()
    app.dependency_overrides[get_recommendation_service] = lambda: FakeRecommendationService(_result())

    for request in (
        {"watch": "sometimes"},
        {"minimum_runtime_minutes": 121, "maximum_runtime_minutes": 120},
        {"minimum_release_year": 2026, "maximum_release_year": 2025},
        {"released_from": "2025-01-02", "released_until": "2025-01-01"},
        {"availability_any_of": [{"kind": "streaming", "provider": "Netflix", "region": "CZE"}]},
        {"availability_any_of": [{"kind": "local_library", "provider": "  "}]},
    ):
        response = _client(app).post("/api/v1/recommendations", json=request)

        assert response.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
        assert "Rejected private title" not in response.text
        assert "internal-filter-decision" not in response.text


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
