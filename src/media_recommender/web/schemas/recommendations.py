"""HTTP schemas and mappings for deterministic recommendations."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - Pydantic resolves this request-field annotation at runtime.
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from media_recommender.application import (
    AvailabilityCriterion,
    AvailabilitySourceKind,
    ConstraintMatch,
    GenreMatch,
    KnownAvailability,
    RankedRecommendation,
    RankingReason,
    RecommendationCriteria,
    RecommendationWarning,
    WatchRequirement,
)
from media_recommender.application.regions import ProductionRegion  # noqa: TC001 - Pydantic resolves this annotation.
from media_recommender.domain import (  # noqa: TC001 - Pydantic resolves these annotations at runtime.
    AvailabilityType,
    LikeState,
    MediaType,
    WatchStatus,
)
from media_recommender.web.schemas.media import MediaResponse, media_to_response


class LocalLibraryAvailabilityRequest(BaseModel):
    """One local-library alternative in an availability requirement."""

    kind: Literal["local_library"]
    provider: str


class StreamingAvailabilityRequest(BaseModel):
    """One regional streaming alternative in an availability requirement."""

    kind: Literal["streaming"]
    provider: str
    region: str
    source_provider: str | None = None
    availability_types: tuple[AvailabilityType, ...] = ()


AvailabilityRequest = Annotated[
    LocalLibraryAvailabilityRequest | StreamingAvailabilityRequest,
    Field(discriminator="kind"),
]


class RecommendationRequest(BaseModel):
    """Explicit deterministic hard constraints accepted by the recommendation API."""

    media_types: tuple[MediaType, ...] = ()
    include_genres: tuple[str, ...] = ()
    genre_match: GenreMatch = GenreMatch.ALL
    exclude_genres: tuple[str, ...] = ()
    include_countries: tuple[str, ...] = ()
    exclude_countries: tuple[str, ...] = ()
    include_regions: tuple[ProductionRegion, ...] = ()
    exclude_regions: tuple[ProductionRegion, ...] = ()
    minimum_runtime_minutes: int | None = Field(default=None, ge=1)
    maximum_runtime_minutes: int | None = Field(default=None, ge=1)
    minimum_release_year: int | None = Field(default=None, ge=1870)
    maximum_release_year: int | None = Field(default=None, ge=1870)
    released_from: date | None = None
    released_until: date | None = None
    watch: WatchRequirement = WatchRequirement.ANY
    minimum_personal_rating: float | None = Field(default=None, ge=0, le=10)
    excluded_like_states: tuple[LikeState, ...] = ()
    availability_any_of: tuple[AvailabilityRequest, ...] = ()
    apply_profile_exclusions: bool = True

    @model_validator(mode="after")
    def validate_ranges(self) -> RecommendationRequest:
        """Reject inverted request-local numeric and date ranges.

        :return: Validated request model.
        :raises ValueError: If a minimum bound exceeds its maximum counterpart.
        """
        if (
            self.minimum_runtime_minutes is not None
            and self.maximum_runtime_minutes is not None
            and self.minimum_runtime_minutes > self.maximum_runtime_minutes
        ):
            message = "Runtime minimum must not exceed maximum"
            raise ValueError(message)
        if (
            self.minimum_release_year is not None
            and self.maximum_release_year is not None
            and self.minimum_release_year > self.maximum_release_year
        ):
            message = "Release year minimum must not exceed maximum"
            raise ValueError(message)
        if (
            self.released_from is not None
            and self.released_until is not None
            and self.released_from > self.released_until
        ):
            message = "Release date minimum must not exceed maximum"
            raise ValueError(message)
        return self


class ConstraintMatchResponse(BaseModel):
    """One satisfied hard constraint retained as structured factual data."""

    kind: str
    values: tuple[str, ...]


class RankingReasonResponse(BaseModel):
    """One explicit score contribution retained as structured factual data."""

    kind: str
    points: int
    values: tuple[str, ...]


class RecommendationWarningResponse(BaseModel):
    """One known data gap retained as structured factual data."""

    kind: str


class KnownAvailabilityResponse(BaseModel):
    """One known local or streaming availability fact."""

    kind: str
    provider: str
    region: str | None
    availability_type: str | None
    source_provider: str | None


class RecommendationResponse(BaseModel):
    """One accepted, ranked deterministic recommendation."""

    media: MediaResponse
    rank: int
    score: int
    matched_constraints: tuple[ConstraintMatchResponse, ...]
    ranking_reasons: tuple[RankingReasonResponse, ...]
    warnings: tuple[RecommendationWarningResponse, ...]
    availability: tuple[KnownAvailabilityResponse, ...]
    watch_status: WatchStatus
    personal_rating: float | None
    like_states: tuple[LikeState, ...]


class RecommendationsResponse(BaseModel):
    """Accepted ranked recommendations from one read-only computation."""

    recommendations: tuple[RecommendationResponse, ...]


def request_to_criteria(request: RecommendationRequest) -> RecommendationCriteria:
    """Map an explicit HTTP request to the typed application criteria contract.

    :param request: Validated HTTP recommendation criteria.
    :return: Equivalent application-layer hard constraints.
    """
    return RecommendationCriteria(
        media_types=frozenset(request.media_types),
        include_genres=frozenset(request.include_genres),
        genre_match=request.genre_match,
        exclude_genres=frozenset(request.exclude_genres),
        include_countries=frozenset(request.include_countries),
        exclude_countries=frozenset(request.exclude_countries),
        include_regions=frozenset(request.include_regions),
        exclude_regions=frozenset(request.exclude_regions),
        minimum_runtime_minutes=request.minimum_runtime_minutes,
        maximum_runtime_minutes=request.maximum_runtime_minutes,
        minimum_release_year=request.minimum_release_year,
        maximum_release_year=request.maximum_release_year,
        released_from=request.released_from,
        released_until=request.released_until,
        watch=request.watch,
        minimum_personal_rating=request.minimum_personal_rating,
        excluded_like_states=frozenset(request.excluded_like_states),
        availability_any_of=tuple(_availability_to_criterion(item) for item in request.availability_any_of),
        apply_profile_exclusions=request.apply_profile_exclusions,
    )


def recommendation_to_response(recommendation: RankedRecommendation) -> RecommendationResponse:
    """Map one accepted ranked application result to its HTTP representation.

    :param recommendation: Accepted recommendation supplied by the application service.
    :return: Explicit HTTP response containing factual structured explanation data.
    """
    return RecommendationResponse(
        media=media_to_response(recommendation.media),
        rank=recommendation.rank,
        score=recommendation.score,
        matched_constraints=tuple(_constraint_match_to_response(item) for item in recommendation.matched_constraints),
        ranking_reasons=tuple(_ranking_reason_to_response(item) for item in recommendation.ranking_reasons),
        warnings=tuple(_warning_to_response(item) for item in recommendation.warnings),
        availability=tuple(_availability_to_response(item) for item in recommendation.availability),
        watch_status=recommendation.watch_status,
        personal_rating=recommendation.personal_rating,
        like_states=recommendation.like_states,
    )


def _availability_to_criterion(request: AvailabilityRequest) -> AvailabilityCriterion:
    """Map one HTTP availability alternative to the application value object.

    :param request: Validated local-library or streaming HTTP alternative.
    :return: Equivalent typed application availability criterion.
    """
    if isinstance(request, LocalLibraryAvailabilityRequest):
        return AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider=request.provider)
    return AvailabilityCriterion(
        kind=AvailabilitySourceKind.STREAMING,
        provider=request.provider,
        region=request.region,
        source_provider=request.source_provider,
        availability_types=frozenset(request.availability_types),
    )


def _constraint_match_to_response(match: ConstraintMatch) -> ConstraintMatchResponse:
    """Map one satisfied constraint without inventing explanatory prose.

    :param match: Typed hard constraint satisfied by an accepted candidate.
    :return: Structured HTTP constraint representation.
    """
    return ConstraintMatchResponse(kind=match.kind.value, values=match.values)


def _ranking_reason_to_response(reason: RankingReason) -> RankingReasonResponse:
    """Map one signed score contribution without inventing explanatory prose.

    :param reason: Typed signed score contribution.
    :return: Structured HTTP score-contribution representation.
    """
    return RankingReasonResponse(kind=reason.kind.value, points=reason.points, values=reason.values)


def _warning_to_response(warning: RecommendationWarning) -> RecommendationWarningResponse:
    """Map one known data warning without inventing explanatory prose.

    :param warning: Typed unknown-data warning.
    :return: Structured HTTP warning representation.
    """
    return RecommendationWarningResponse(kind=warning.kind.value)


def _availability_to_response(availability: KnownAvailability) -> KnownAvailabilityResponse:
    """Map one normalized local or streaming availability fact.

    :param availability: Typed known availability fact.
    :return: Structured HTTP availability representation.
    """
    return KnownAvailabilityResponse(
        kind=availability.kind.value,
        provider=availability.provider,
        region=availability.region,
        availability_type=availability.availability_type.value if availability.availability_type is not None else None,
        source_provider=availability.source_provider,
    )
