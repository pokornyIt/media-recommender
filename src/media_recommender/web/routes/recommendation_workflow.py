"""Server-rendered structured recommendation workflow and result views.

The page builds the existing Phase 2 ``RecommendationCriteria`` contract from
explicit form controls and delegates to the same application facade used by the
JSON API. It never reimplements filtering, ranking, or explanation logic; it only
maps validated application results into presentation facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.
from pydantic import ValidationError

from media_recommender.application import (
    AvailabilitySourceKind,
    ConstraintMatchKind,
    GenreMatch,
    Phase2Orchestrator,
    RankingReasonKind,
    RecommendationWarningKind,
    WatchRequirement,
)
from media_recommender.application.regions import ProductionRegion
from media_recommender.domain import ArtworkType, LikeState, MediaType, Movie
from media_recommender.web.csrf import CsrfValidationError, get_csrf_token, require_csrf_token
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.availability import csrf_rejection_handler as availability_csrf_rejection_handler
from media_recommender.web.routes.pages import get_templates
from media_recommender.web.schemas.recommendations import RecommendationRequest, request_to_criteria

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ExceptionHandler

    from media_recommender.application import (
        ConstraintMatch,
        KnownAvailability,
        RankedRecommendation,
        RankingReason,
        RecommendationResult,
        RecommendationWarning,
    )
    from media_recommender.domain import Media

router = APIRouter()

RECOMMENDATIONS_PATH = "/recommendations"

_DEFAULT_LIMIT = 20
_MAXIMUM_LIMIT = 100

_INVALID_CRITERIA_MESSAGE = "The submitted criteria were invalid. Review the values and try again."
_FAILURE_MESSAGE = "Recommendations could not be computed. Try again later."
_REJECTED_MESSAGE = "Request rejected. Submit the recommendation form from this page."

_CONSTRAINT_LABELS: dict[ConstraintMatchKind, str] = {
    ConstraintMatchKind.MEDIA_TYPE: "Media type",
    ConstraintMatchKind.GENRE_INCLUDED: "Included genre",
    ConstraintMatchKind.GENRE_EXCLUSIONS_CLEAR: "Excluded genres clear",
    ConstraintMatchKind.PRODUCTION_COUNTRY: "Production country",
    ConstraintMatchKind.PRODUCTION_COUNTRY_EXCLUSIONS_CLEAR: "Excluded countries clear",
    ConstraintMatchKind.PRODUCTION_REGION: "Production region",
    ConstraintMatchKind.PRODUCTION_REGION_EXCLUSIONS_CLEAR: "Excluded regions clear",
    ConstraintMatchKind.RUNTIME: "Runtime",
    ConstraintMatchKind.RELEASE_DATE: "Release date",
    ConstraintMatchKind.WATCH_STATUS: "Watch status",
    ConstraintMatchKind.PERSONAL_RATING: "Personal rating",
    ConstraintMatchKind.LIKE_EXCLUSIONS_CLEAR: "Excluded reactions clear",
    ConstraintMatchKind.AVAILABILITY: "Availability",
}

_RANKING_REASON_LABELS: dict[RankingReasonKind, str] = {
    RankingReasonKind.PROFILE_PREFERENCE: "Profile preference",
    RankingReasonKind.PERSONAL_RATING: "Personal rating",
    RankingReasonKind.LIKED: "Liked",
    RankingReasonKind.DISLIKED: "Disliked",
}

_WARNING_LABELS: dict[RecommendationWarningKind, str] = {
    RecommendationWarningKind.GENRES_UNKNOWN: "Genres unknown",
    RecommendationWarningKind.PRODUCTION_COUNTRIES_UNKNOWN: "Production countries unknown",
    RecommendationWarningKind.RUNTIME_UNKNOWN: "Runtime unknown",
    RecommendationWarningKind.RELEASE_DATE_UNKNOWN: "Release date unknown",
    RecommendationWarningKind.WATCH_STATUS_UNKNOWN: "Watch status unknown",
    RecommendationWarningKind.PERSONAL_RATING_UNKNOWN: "Personal rating unknown",
    RecommendationWarningKind.AVAILABILITY_UNKNOWN: "Availability unknown",
}


@dataclass(frozen=True, slots=True)
class RecommendationFormView:
    """Raw submitted criteria retained so the form can be re-rendered safely."""

    media_types: tuple[str, ...] = ()
    include_genres: str = ""
    genre_match: str = GenreMatch.ALL.value
    exclude_genres: str = ""
    include_countries: str = ""
    exclude_countries: str = ""
    include_regions: tuple[str, ...] = ()
    exclude_regions: tuple[str, ...] = ()
    minimum_runtime_minutes: str = ""
    maximum_runtime_minutes: str = ""
    minimum_release_year: str = ""
    maximum_release_year: str = ""
    watch: str = WatchRequirement.ANY.value
    minimum_personal_rating: str = ""
    excluded_like_states: tuple[str, ...] = ()
    apply_profile_exclusions: bool = True
    availability_local_provider: str = ""
    availability_streaming_provider: str = ""
    availability_streaming_region: str = ""
    limit: str = str(_DEFAULT_LIMIT)


@dataclass(frozen=True, slots=True)
class RecommendationCardView:
    """Presentation facts for one accepted ranked recommendation."""

    rank: int
    score: int
    title: str
    release_year: int | None
    media_type: str
    runtime_minutes: int | None
    genres: tuple[str, ...]
    artwork_url: str | None
    artwork_alt: str
    availability: tuple[str, ...]
    watch_status: str
    personal_rating: float | None
    like_states: tuple[str, ...]
    matched_constraints: tuple[str, ...]
    ranking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecommendationResultView:
    """Ordered presentation of one deterministic recommendation result."""

    cards: tuple[RecommendationCardView, ...]
    total: int
    limit: int
    truncated: bool


def get_recommendation_workflow_service(request: Request) -> Phase2Orchestrator:
    """Return the recommendation facade configured by the composition root.

    :param request: Incoming request carrying the current application instance.
    :return: Phase 2 recommendation application facade.
    :raises RuntimeError: If no recommendation service is configured.
    """
    try:
        return cast("Phase2Orchestrator", request.app.state.recommendation_service)
    except AttributeError as error:
        msg = "Recommendation service is not configured"
        raise RuntimeError(msg) from error


@router.get(RECOMMENDATIONS_PATH, response_class=HTMLResponse)
async def recommendations_page(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the empty structured recommendation form.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :return: Shared-layout recommendation page with a session-bound CSRF token.
    """
    return _render(templates, request, RecommendationFormView(), None, None)


@router.post(RECOMMENDATIONS_PATH, response_class=HTMLResponse)
async def submit_recommendations(  # noqa: PLR0913, PLR0917 - FastAPI dependency and form parameters.
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
    service: Annotated[Phase2Orchestrator, Depends(get_recommendation_workflow_service)],
    media_types: Annotated[list[str] | None, Form()] = None,
    include_genres: Annotated[str, Form()] = "",
    genre_match: Annotated[str, Form()] = GenreMatch.ALL.value,
    exclude_genres: Annotated[str, Form()] = "",
    include_countries: Annotated[str, Form()] = "",
    exclude_countries: Annotated[str, Form()] = "",
    include_regions: Annotated[list[str] | None, Form()] = None,
    exclude_regions: Annotated[list[str] | None, Form()] = None,
    minimum_runtime_minutes: Annotated[str, Form()] = "",
    maximum_runtime_minutes: Annotated[str, Form()] = "",
    minimum_release_year: Annotated[str, Form()] = "",
    maximum_release_year: Annotated[str, Form()] = "",
    watch: Annotated[str, Form()] = WatchRequirement.ANY.value,
    minimum_personal_rating: Annotated[str, Form()] = "",
    excluded_like_states: Annotated[list[str] | None, Form()] = None,
    apply_profile_exclusions: Annotated[str, Form()] = "",
    availability_local_provider: Annotated[str, Form()] = "",
    availability_streaming_provider: Annotated[str, Form()] = "",
    availability_streaming_region: Annotated[str, Form()] = "",
    limit: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Validate submitted criteria, delegate once, and render ordered results.

    The CSRF/origin gate is declared first so it is enforced before the facade is
    resolved. Invalid criteria and unexpected application failures are translated
    into safe messages that never include internal validation details or private
    candidate data.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param _csrf: CSRF and origin validation dependency.
    :param service: Injected deterministic application facade.
    :param media_types: Selected media types.
    :param include_genres: Comma-separated included genres.
    :param genre_match: Included-genre matching semantics.
    :param exclude_genres: Comma-separated excluded genres.
    :param include_countries: Comma-separated included production countries.
    :param exclude_countries: Comma-separated excluded production countries.
    :param include_regions: Selected included production regions.
    :param exclude_regions: Selected excluded production regions.
    :param minimum_runtime_minutes: Optional inclusive runtime lower bound.
    :param maximum_runtime_minutes: Optional inclusive runtime upper bound.
    :param minimum_release_year: Optional inclusive release-year lower bound.
    :param maximum_release_year: Optional inclusive release-year upper bound.
    :param watch: Required profile watch state.
    :param minimum_personal_rating: Optional minimum personal rating.
    :param excluded_like_states: Selected excluded reactions.
    :param apply_profile_exclusions: Whether saved profile exclusions apply.
    :param availability_local_provider: Optional local-library provider requirement.
    :param availability_streaming_provider: Optional streaming provider requirement.
    :param availability_streaming_region: Region for the streaming requirement.
    :param limit: Presentation-only maximum number of rendered results.
    :return: Shared-layout recommendation page with ordered results or a safe error.
    """
    form = RecommendationFormView(
        media_types=tuple(media_types or ()),
        include_genres=include_genres,
        genre_match=genre_match,
        exclude_genres=exclude_genres,
        include_countries=include_countries,
        exclude_countries=exclude_countries,
        include_regions=tuple(include_regions or ()),
        exclude_regions=tuple(exclude_regions or ()),
        minimum_runtime_minutes=minimum_runtime_minutes,
        maximum_runtime_minutes=maximum_runtime_minutes,
        minimum_release_year=minimum_release_year,
        maximum_release_year=maximum_release_year,
        watch=watch,
        minimum_personal_rating=minimum_personal_rating,
        excluded_like_states=tuple(excluded_like_states or ()),
        apply_profile_exclusions=apply_profile_exclusions == "true",
        availability_local_provider=availability_local_provider,
        availability_streaming_provider=availability_streaming_provider,
        availability_streaming_region=availability_streaming_region,
        limit=limit,
    )
    try:
        criteria = request_to_criteria(_build_request(form))
        result_limit = _parse_limit(form.limit)
    except ValueError, ValidationError:
        return _render(
            templates,
            request,
            form,
            None,
            _INVALID_CRITERIA_MESSAGE,
            status_code=HTTPStatus.BAD_REQUEST,
        )
    try:
        result = await service.recommend(criteria)
    except Exception:  # noqa: BLE001 - translate unexpected application failures into a sanitized result.
        return _render(
            templates,
            request,
            form,
            None,
            _FAILURE_MESSAGE,
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return _render(templates, request, form, _result_view(result, result_limit), None)


async def csrf_rejection_handler(request: Request, error: Exception) -> HTMLResponse:
    """Render a safe rejection page for an invalid CSRF or origin check.

    Submissions for other server-rendered forms are delegated to their own
    handler so this feature does not change existing pages.

    :param request: Incoming rejected request.
    :param error: Rejected CSRF validation error.
    :return: Shared-layout rejection page.
    """
    if request.url.path != RECOMMENDATIONS_PATH:
        return await availability_csrf_rejection_handler(request, error)
    templates: Jinja2Templates = request.app.state.templates
    return _render(
        templates,
        request,
        RecommendationFormView(),
        None,
        _REJECTED_MESSAGE,
        status_code=HTTPStatus.FORBIDDEN,
    )


def register_recommendation_workflow_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced by the recommendation workflow page.

    :param app: FastAPI application receiving the workflow-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {CsrfValidationError: csrf_rejection_handler}
    register_exception_handlers(app, handlers)


def _build_request(form: RecommendationFormView) -> RecommendationRequest:
    """Build a validated HTTP recommendation request from submitted form values.

    :param form: Raw submitted form values.
    :return: Validated HTTP recommendation request.
    """
    payload: dict[str, object] = {
        "media_types": list(form.media_types),
        "include_genres": _split_values(form.include_genres),
        "genre_match": form.genre_match,
        "exclude_genres": _split_values(form.exclude_genres),
        "include_countries": _split_values(form.include_countries),
        "exclude_countries": _split_values(form.exclude_countries),
        "include_regions": list(form.include_regions),
        "exclude_regions": list(form.exclude_regions),
        "minimum_runtime_minutes": _optional_int(form.minimum_runtime_minutes),
        "maximum_runtime_minutes": _optional_int(form.maximum_runtime_minutes),
        "minimum_release_year": _optional_int(form.minimum_release_year),
        "maximum_release_year": _optional_int(form.maximum_release_year),
        "watch": form.watch,
        "minimum_personal_rating": _optional_float(form.minimum_personal_rating),
        "excluded_like_states": list(form.excluded_like_states),
        "availability_any_of": _availability_payload(form),
        "apply_profile_exclusions": form.apply_profile_exclusions,
    }
    return RecommendationRequest.model_validate(payload)


def _availability_payload(form: RecommendationFormView) -> list[dict[str, str]]:
    """Build OR-combined availability alternatives from submitted form values.

    :param form: Raw submitted form values.
    :return: Availability alternatives accepted by the HTTP request contract.
    :raises ValueError: If a streaming provider is supplied without a region.
    """
    alternatives: list[dict[str, str]] = []
    local_provider = form.availability_local_provider.strip()
    if local_provider:
        alternatives.append({"kind": "local_library", "provider": local_provider})
    streaming_provider = form.availability_streaming_provider.strip()
    streaming_region = form.availability_streaming_region.strip()
    if streaming_provider and not streaming_region:
        msg = "Streaming availability requires a region"
        raise ValueError(msg)
    if streaming_provider and streaming_region:
        alternatives.append({"kind": "streaming", "provider": streaming_provider, "region": streaming_region})
    return alternatives


def _split_values(value: str) -> list[str]:
    """Split a comma-separated form value into non-empty trimmed items.

    :param value: Raw comma-separated form value.
    :return: Non-empty trimmed items.
    """
    return [item.strip() for item in value.split(",") if item.strip()]


def _optional_int(value: str) -> int | None:
    """Parse an optional integer form value.

    :param value: Raw form value.
    :return: Parsed integer, or ``None`` when blank.
    """
    text = value.strip()
    return int(text) if text else None


def _optional_float(value: str) -> float | None:
    """Parse an optional decimal form value.

    :param value: Raw form value.
    :return: Parsed number, or ``None`` when blank.
    """
    text = value.strip()
    return float(text) if text else None


def _parse_limit(value: str) -> int:
    """Parse the presentation-only result limit.

    :param value: Raw form value.
    :return: Accepted result limit.
    :raises ValueError: If the value is not an integer within the supported range.
    """
    text = value.strip()
    if not text:
        return _DEFAULT_LIMIT
    limit = int(text)
    if not 1 <= limit <= _MAXIMUM_LIMIT:
        msg = f"Result limit must be between 1 and {_MAXIMUM_LIMIT}"
        raise ValueError(msg)
    return limit


def _result_view(result: RecommendationResult, limit: int) -> RecommendationResultView:
    """Map an application result to ordered presentation facts.

    The service ordering is preserved exactly; the limit only truncates the
    rendered slice and never reorders or re-scores recommendations.

    :param result: Deterministic application result.
    :param limit: Presentation-only maximum number of rendered results.
    :return: Ordered presentation of the accepted recommendations.
    """
    total = len(result.recommendations)
    return RecommendationResultView(
        cards=tuple(_card_view(item) for item in result.recommendations[:limit]),
        total=total,
        limit=limit,
        truncated=total > limit,
    )


def _card_view(item: RankedRecommendation) -> RecommendationCardView:
    """Map one accepted ranked recommendation to presentation facts.

    :param item: Accepted ranked application recommendation.
    :return: Presentation facts for one result card.
    """
    media = item.media
    runtime = media.runtime if isinstance(media, Movie) else media.episode_runtime
    return RecommendationCardView(
        rank=item.rank,
        score=item.score,
        title=media.title,
        release_year=media.release_year,
        media_type=media.media_type.value,
        runtime_minutes=runtime.minutes if runtime is not None else None,
        genres=tuple(genre.name for genre in media.genres),
        artwork_url=_artwork_url(media),
        artwork_alt=f"{media.title} artwork",
        availability=tuple(_availability_label(fact) for fact in item.availability),
        watch_status=item.watch_status.value,
        personal_rating=item.personal_rating,
        like_states=tuple(state.value for state in item.like_states),
        matched_constraints=tuple(_constraint_label(match) for match in item.matched_constraints),
        ranking_reasons=tuple(_ranking_reason_label(reason) for reason in item.ranking_reasons),
        warnings=tuple(_warning_label(warning) for warning in item.warnings),
    )


def _artwork_url(media: Media) -> str | None:
    """Return the preferred artwork URL for one media item.

    :param media: Shared catalog media.
    :return: Poster URL when available, otherwise the first artwork URL.
    """
    posters = [artwork for artwork in media.artwork if artwork.type is ArtworkType.POSTER]
    if posters:
        return posters[0].url
    return media.artwork[0].url if media.artwork else None


def _availability_label(fact: KnownAvailability) -> str:
    """Describe one known availability fact without inventing access claims.

    :param fact: Normalized local or streaming availability fact.
    :return: Human-readable availability description.
    """
    if fact.kind is AvailabilitySourceKind.LOCAL_LIBRARY:
        return f"Local library: {fact.provider}"
    parts = [fact.provider]
    if fact.region:
        parts.append(fact.region)
    if fact.availability_type is not None:
        parts.append(fact.availability_type.value)
    return f"Streaming: {', '.join(parts)}"


def _constraint_label(match: ConstraintMatch) -> str:
    """Describe one satisfied hard constraint from structured service output.

    :param match: Typed hard constraint satisfied by an accepted candidate.
    :return: Human-readable constraint description.
    """
    label = _CONSTRAINT_LABELS.get(match.kind, match.kind.value)
    return f"{label}: {', '.join(match.values)}" if match.values else label


def _ranking_reason_label(reason: RankingReason) -> str:
    """Describe one signed score contribution from structured service output.

    :param reason: Typed signed score contribution.
    :return: Human-readable score-contribution description.
    """
    label = _RANKING_REASON_LABELS.get(reason.kind, reason.kind.value)
    detail = f" ({', '.join(reason.values)})" if reason.values else ""
    return f"{label}: {reason.points:+d}{detail}"


def _warning_label(warning: RecommendationWarning) -> str:
    """Describe one known data warning from structured service output.

    :param warning: Typed unknown-data warning.
    :return: Human-readable warning description.
    """
    return _WARNING_LABELS.get(warning.kind, warning.kind.value)


def _criteria_summary(form: RecommendationFormView) -> tuple[str, ...]:  # noqa: C901, PLR0912 - one branch per criterion.
    """Describe the active submitted criteria for the result context.

    :param form: Raw submitted form values.
    :return: Human-readable active-criteria descriptions.
    """
    summary: list[str] = []
    if form.media_types:
        summary.append(f"Media types: {', '.join(form.media_types)}")
    if form.include_genres.strip():
        summary.append(f"Included genres ({form.genre_match}): {form.include_genres.strip()}")
    if form.exclude_genres.strip():
        summary.append(f"Excluded genres: {form.exclude_genres.strip()}")
    if form.include_countries.strip():
        summary.append(f"Included countries: {form.include_countries.strip()}")
    if form.exclude_countries.strip():
        summary.append(f"Excluded countries: {form.exclude_countries.strip()}")
    if form.include_regions:
        summary.append(f"Included regions: {', '.join(form.include_regions)}")
    if form.exclude_regions:
        summary.append(f"Excluded regions: {', '.join(form.exclude_regions)}")
    if form.minimum_runtime_minutes.strip() or form.maximum_runtime_minutes.strip():
        summary.append(
            f"Runtime: {form.minimum_runtime_minutes.strip() or 'any'} to "
            f"{form.maximum_runtime_minutes.strip() or 'any'} minutes"
        )
    if form.minimum_release_year.strip() or form.maximum_release_year.strip():
        summary.append(
            f"Release year: {form.minimum_release_year.strip() or 'any'} to "
            f"{form.maximum_release_year.strip() or 'any'}"
        )
    if form.watch != WatchRequirement.ANY.value:
        summary.append(f"Watch state: {form.watch}")
    if form.minimum_personal_rating.strip():
        summary.append(f"Minimum personal rating: {form.minimum_personal_rating.strip()}")
    if form.excluded_like_states:
        summary.append(f"Excluded reactions: {', '.join(form.excluded_like_states)}")
    if not form.apply_profile_exclusions:
        summary.append("Saved profile exclusions: not applied")
    if form.availability_local_provider.strip():
        summary.append(f"Local library: {form.availability_local_provider.strip()}")
    if form.availability_streaming_provider.strip() and form.availability_streaming_region.strip():
        summary.append(
            f"Streaming: {form.availability_streaming_provider.strip()} in {form.availability_streaming_region.strip()}"
        )
    return tuple(summary)


def _render(  # noqa: PLR0913 - explicit render context parameters.
    templates: Jinja2Templates,
    request: Request,
    form: RecommendationFormView,
    result: RecommendationResultView | None,
    error: str | None,
    *,
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the recommendation page with a fresh CSRF token and safe context.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :param form: Raw submitted criteria retained for re-rendering.
    :param result: Optional ordered presentation of accepted recommendations.
    :param error: Optional safe error message.
    :param status_code: HTTP status for the rendered response.
    :return: Shared-layout recommendation page.
    """
    return templates.TemplateResponse(
        request,
        "recommendations.html",
        {
            "csrf_token": get_csrf_token(request),
            "form": form,
            "result": result,
            "error": error,
            "summary": _criteria_summary(form),
            "media_types": tuple(MediaType),
            "regions": tuple(ProductionRegion),
            "watch_requirements": tuple(WatchRequirement),
            "like_states": tuple(LikeState),
            "genre_matches": tuple(GenreMatch),
        },
        status_code=status_code,
    )
