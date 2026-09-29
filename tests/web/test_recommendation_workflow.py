"""Offline tests for the server-rendered structured recommendation workflow."""

from __future__ import annotations

import re
from datetime import date
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID

from fastapi.testclient import TestClient

from media_recommender.application import (
    AvailabilityCriterion,
    AvailabilitySourceKind,
    ConstraintMatch,
    ConstraintMatchKind,
    FilterExclusion,
    FilterReason,
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
    Artwork,
    ArtworkType,
    AvailabilityType,
    Country,
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
from media_recommender.web.routes.recommendation_workflow import (
    RECOMMENDATIONS_PATH,
    get_recommendation_workflow_service,
)

if TYPE_CHECKING:
    from httpx import Client, Response

MOVIE_ID = UUID("66666666-6666-6666-6666-666666666666")
SERIES_ID = UUID("77777777-7777-7777-7777-777777777777")
REJECTED_ID = UUID("88888888-8888-8888-8888-888888888888")

_ARTWORK_URL = "https://artwork.synthetic.invalid/poster.jpg"
_REJECTED_TITLE = "Rejected private title"
_REJECTED_REASON = "internal-filter-decision"
_MINIMUM_RUNTIME_MINUTES = 90
_MAXIMUM_RUNTIME_MINUTES = 120


class FakeRecommendationWorkflowService:
    """Synthetic recommendation boundary recording submitted criteria."""

    def __init__(self, result: RecommendationResult) -> None:
        """Initialize the fake with one synthetic application result.

        :param result: Result returned for every recommendation request.
        """
        self.result = result
        self.criteria: list[RecommendationCriteria] = []

    async def recommend(self, criteria: RecommendationCriteria) -> RecommendationResult:
        """Record and return the synthetic deterministic result.

        :param criteria: Application criteria submitted by the web route.
        :return: Configured synthetic recommendation result.
        """
        self.criteria.append(criteria)
        return self.result


class FailingRecommendationWorkflowService:
    """Synthetic recommendation boundary that always fails."""

    def __init__(self) -> None:
        """Initialize the fake with an empty recorded-criteria list."""
        self.criteria: list[RecommendationCriteria] = []

    async def recommend(self, criteria: RecommendationCriteria) -> RecommendationResult:
        """Record the criteria and raise a synthetic unexpected failure.

        :param criteria: Application criteria submitted by the web route.
        :raises RuntimeError: Always, with synthetic private-like text.
        """
        self.criteria.append(criteria)
        message = "synthetic-secret=do-not-disclose"
        raise RuntimeError(message)


def _client(service: object) -> Client:
    """Build a test client with the recommendation facade overridden.

    :param service: Synthetic recommendation facade.
    :return: Offline HTTPX-compatible test client.
    """
    app = create_app()
    app.dependency_overrides[get_recommendation_workflow_service] = lambda: service
    return cast("Client", TestClient(app))


def _csrf_token(client: Client) -> str:
    """Return the CSRF token rendered by the recommendation page.

    :param client: Test client carrying a session cookie.
    :return: Hidden form token value.
    """
    response = client.get(RECOMMENDATIONS_PATH)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _post(client: Client, *, token: str | None = None, data: dict[str, object] | None = None) -> Response:
    """Submit one synthetic recommendation request.

    :param client: Test client carrying a session cookie.
    :param token: CSRF token to submit, or ``None`` to omit it.
    :param data: Optional form fields.
    :return: HTTP response.
    """
    payload: dict[str, object] = dict(data or {})
    if token is not None:
        payload["csrf_token"] = token
    return client.post(RECOMMENDATIONS_PATH, data=payload)


def _result() -> RecommendationResult:
    """Create two ordered synthetic recommendations and a hidden decision set."""
    movie = Movie(
        id=MediaId(MOVIE_ID),
        title="Synthetic Sci-Fi",
        released_on=date(2024, 5, 1),
        runtime=Runtime(101),
        genres=(Genre("Science Fiction"),),
        production_countries=(Country("CZ", "Czechia"),),
        artwork=(Artwork(ArtworkType.POSTER, _ARTWORK_URL),),
    )
    series = TVShow(id=MediaId(SERIES_ID), title="Synthetic Series")
    recommendations = (
        RankedRecommendation(
            media=movie,
            rank=1,
            score=35,
            matched_constraints=(ConstraintMatch(ConstraintMatchKind.GENRE_INCLUDED, ("Science Fiction",)),),
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
            media=series,
            rank=2,
            score=0,
            matched_constraints=(),
            ranking_reasons=(),
            warnings=(RecommendationWarning(RecommendationWarningKind.RUNTIME_UNKNOWN),),
            availability=(),
            watch_status=WatchStatus.UNKNOWN,
            personal_rating=None,
            like_states=(),
        ),
    )
    rejected = Movie(id=MediaId(REJECTED_ID), title=_REJECTED_TITLE)
    return RecommendationResult(
        recommendations=recommendations,
        filter_result=RecommendationFilterResult(
            (),
            (
                RecommendationDecision(
                    media=rejected,
                    exclusions=(FilterExclusion(FilterReason.GENRE_EXCLUDED, (_REJECTED_REASON,)),),
                ),
            ),
        ),
    )


def test_get_renders_accessible_form_with_navigation_and_csrf_token() -> None:
    """Render the structured form with navigation, a CSRF token, and criteria controls."""
    client = _client(FakeRecommendationWorkflowService(_result()))

    response = client.get(RECOMMENDATIONS_PATH)

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/recommendations">Recommendations</a>' in response.text
    assert 'method="post"' in response.text
    assert 'name="csrf_token"' in response.text
    assert 'name="include_genres"' in response.text
    assert 'name="exclude_regions"' in response.text
    assert 'name="availability_streaming_provider"' in response.text
    assert 'name="availability_local_provider"' in response.text
    assert 'name="limit"' in response.text
    assert "<button" in response.text


def test_reference_unseen_sci_fi_request_maps_criteria_and_renders_results() -> None:
    """Map the reference unseen sci-fi request and render the ordered service result."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(
        client,
        token=token,
        data={
            "media_types": ["movie"],
            "include_genres": "Science Fiction",
            "watch": "not_watched",
            "apply_profile_exclusions": "true",
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert len(service.criteria) == 1
    criteria = service.criteria[0]
    assert criteria.media_types == frozenset({MediaType.MOVIE})
    assert criteria.include_genres == frozenset({"science fiction"})
    assert criteria.watch is WatchRequirement.NOT_WATCHED
    assert criteria.apply_profile_exclusions is True
    assert "Synthetic Sci-Fi" in response.text
    assert "Synthetic Series" in response.text


def test_genre_and_region_exclusions_map_to_criteria() -> None:
    """Map excluded genres and production regions to the application criteria."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(
        client,
        token=token,
        data={"exclude_genres": "Horror, Comedy", "exclude_regions": ["asia", "africa"]},
    )

    assert response.status_code == HTTPStatus.OK
    criteria = service.criteria[0]
    assert criteria.exclude_genres == frozenset({"horror", "comedy"})
    assert criteria.exclude_regions == frozenset({ProductionRegion.ASIA, ProductionRegion.AFRICA})


def test_runtime_limit_maps_to_criteria() -> None:
    """Map inclusive runtime bounds to the application criteria."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={"minimum_runtime_minutes": "90", "maximum_runtime_minutes": "120"})

    assert response.status_code == HTTPStatus.OK
    criteria = service.criteria[0]
    assert criteria.minimum_runtime_minutes == _MINIMUM_RUNTIME_MINUTES
    assert criteria.maximum_runtime_minutes == _MAXIMUM_RUNTIME_MINUTES


def test_netflix_and_jellyfin_source_selection_maps_to_availability_alternatives() -> None:
    """Map local-library and regional streaming selections to OR-combined alternatives."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(
        client,
        token=token,
        data={
            "availability_local_provider": "Jellyfin",
            "availability_streaming_provider": "Netflix",
            "availability_streaming_region": "cz",
        },
    )

    assert response.status_code == HTTPStatus.OK
    criteria = service.criteria[0]
    assert criteria.availability_any_of == (
        AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider="jellyfin"),
        AvailabilityCriterion(kind=AvailabilitySourceKind.STREAMING, provider="Netflix", region="CZ"),
    )


def test_streaming_provider_without_region_is_rejected_safely() -> None:
    """Reject an incomplete streaming requirement without invoking the service."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={"availability_streaming_provider": "Netflix"})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "criteria were invalid" in response.text
    assert service.criteria == []


def test_no_match_result_explains_that_no_candidates_satisfied_criteria() -> None:
    """Render an empty successful result as an explained no-match state."""
    service = FakeRecommendationWorkflowService(RecommendationResult((), RecommendationFilterResult((), ())))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={"include_genres": "Science Fiction"})

    assert response.status_code == HTTPStatus.OK
    assert "No candidates satisfied the selected criteria" in response.text
    assert "criteria were invalid" not in response.text


def test_unknown_metadata_and_availability_warnings_are_rendered() -> None:
    """Render structured unknown-data warnings supplied by the service."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={})

    assert response.status_code == HTTPStatus.OK
    assert "Availability unknown" in response.text
    assert "Runtime unknown" in response.text


def test_service_ordering_is_preserved_by_the_ui() -> None:
    """Preserve the deterministic service ordering in the rendered result list."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={})

    assert response.status_code == HTTPStatus.OK
    assert response.text.index("Synthetic Sci-Fi") < response.text.index("Synthetic Series")


def test_reasons_and_availability_are_rendered_from_service_output() -> None:
    """Render structured reasons, constraints, and availability from service output."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={})

    assert response.status_code == HTTPStatus.OK
    assert "Liked: +30 (liked)" in response.text
    assert "Included genre: Science Fiction" in response.text
    assert "Streaming: Netflix, CZ, subscription" in response.text
    assert "Watch state: unwatched" in response.text
    assert "Personal rating: 7.5" in response.text
    assert _ARTWORK_URL in response.text


def test_active_criteria_remain_visible_after_submission() -> None:
    """Keep the submitted criteria visible in the re-rendered form and summary."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(
        client,
        token=token,
        data={"include_genres": "Science Fiction", "maximum_runtime_minutes": "120"},
    )

    assert response.status_code == HTTPStatus.OK
    assert 'value="Science Fiction"' in response.text
    assert 'value="120"' in response.text
    assert "Included genres (all): Science Fiction" in response.text
    assert "Runtime: any to 120 minutes" in response.text


def test_result_limit_truncates_without_reordering() -> None:
    """Apply the presentation-only limit while preserving service ordering."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={"limit": "1"})

    assert response.status_code == HTTPStatus.OK
    assert "1 of 2 eligible titles shown" in response.text
    assert "Synthetic Sci-Fi" in response.text
    assert "Synthetic Series" not in response.text


def test_invalid_criteria_return_a_safe_client_error() -> None:
    """Translate invalid criteria into a safe message without internal details."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={"include_countries": "CZE"})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "criteria were invalid" in response.text
    assert service.criteria == []
    for internal in ("Traceback", "pydantic", "Input should be", "value_error"):
        assert internal not in response.text


def test_unexpected_failure_is_translated_without_leakage() -> None:
    """Translate an unexpected facade exception into a sanitized response."""
    client = _client(FailingRecommendationWorkflowService())
    token = _csrf_token(client)

    response = _post(client, token=token, data={})

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "could not be computed" in response.text
    assert "synthetic-secret" not in response.text


def test_missing_or_incorrect_csrf_token_is_rejected_before_recommendation() -> None:
    """Reject submissions without a valid session CSRF token."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    _csrf_token(client)

    missing = _post(client, data={})
    incorrect = _post(client, token="synthetic-incorrect-token", data={})  # noqa: S106 - synthetic test value.

    assert missing.status_code == HTTPStatus.FORBIDDEN
    assert incorrect.status_code == HTTPStatus.FORBIDDEN
    assert "Request rejected" in missing.text
    assert service.criteria == []


def test_rejected_candidates_and_private_filter_data_are_not_rendered() -> None:
    """Never render rejected candidates or internal filter decisions."""
    service = FakeRecommendationWorkflowService(_result())
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, data={})

    assert response.status_code == HTTPStatus.OK
    assert _REJECTED_TITLE not in response.text
    assert _REJECTED_REASON not in response.text
    assert "filter_result" not in response.text
