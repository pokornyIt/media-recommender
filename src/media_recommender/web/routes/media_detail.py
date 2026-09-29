"""Server-rendered media detail and profile-owned personal state view.

The page combines shared catalog facts, profile-owned personal state, and known
availability through the media-detail application service. It never reads
persistence or provider DTOs directly, and it keeps shared catalog facts,
local-library presence, and regional streaming availability conceptually
separate. Supported personal-state edits are delegated to the application
service rather than mutating records in the route.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, cast
from uuid import UUID  # noqa: TC003 - FastAPI resolves this path parameter annotation at runtime.

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.

from media_recommender.application import (
    MediaDetailService,  # noqa: TC001 - FastAPI resolves this dependency annotation at runtime.
)
from media_recommender.domain import ArtworkType, LikeState, MediaId, Movie, WatchStatus
from media_recommender.web.csrf import CsrfValidationError, get_csrf_token, require_csrf_token
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.pages import get_templates
from media_recommender.web.routes.recommendation_workflow import (
    csrf_rejection_handler as recommendation_csrf_rejection_handler,
)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ExceptionHandler

    from media_recommender.application import MediaDetail
    from media_recommender.domain import LibraryPresence, Media, StreamingAvailability

router = APIRouter()

MEDIA_DETAIL_PATH = "/media/{media_id}"

_MAXIMUM_CONTEXT_ITEMS = 20
_MAXIMUM_CONTEXT_LENGTH = 200
_MAXIMUM_RATING = 10

_INVALID_RATING_MESSAGE = "Enter a rating between 0 and 10 or select a reaction."
_EMPTY_RATING_MESSAGE = "Provide a numeric rating or select a reaction before saving."
_FAILURE_MESSAGE = "The personal state could not be updated. Try again later."
_REJECTED_MESSAGE = "Request rejected. Submit the personal-state form from this page."
_NOT_FOUND_MESSAGE = "The requested media item was not found."


@dataclass(frozen=True, slots=True)
class LibraryPresenceView:
    """Presentation facts for one local-library presence record."""

    provider: str
    available: bool
    play_count: int | None
    last_played_at: str | None


@dataclass(frozen=True, slots=True)
class StreamingAvailabilityView:
    """Presentation facts for one regional streaming availability fact."""

    service: str
    region: str
    availability_type: str
    source_provider: str
    observed_at: str
    attribution: str | None


@dataclass(frozen=True, slots=True)
class RecommendationContextView:
    """Recommendation explanation facts carried from a recommendation result."""

    rank: int
    score: int
    constraints: tuple[str, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MediaDetailView:
    """Presentation facts for one media detail page."""

    media_id: str
    title: str
    original_title: str | None
    release_year: int | None
    release_date: str | None
    media_type: str
    runtime_minutes: int | None
    genres: tuple[str, ...]
    production_countries: tuple[str, ...]
    artwork_url: str | None
    artwork_alt: str
    external_ids: tuple[str, ...]
    watch_status: str
    watch_count: int
    last_watched_at: str | None
    personal_rating: float | None
    like_states: tuple[str, ...]
    library_presence: tuple[LibraryPresenceView, ...]
    streaming_availability: tuple[StreamingAvailabilityView, ...]
    unknown_fields: tuple[str, ...]


def get_media_detail_service(request: Request) -> MediaDetailService:
    """Return the media-detail service configured by the composition root.

    :param request: Incoming request carrying the current application instance.
    :return: Media-detail application service.
    :raises RuntimeError: If no media-detail service is configured.
    """
    try:
        return cast("MediaDetailService", request.app.state.media_detail_service)
    except AttributeError as error:
        msg = "Media detail service is not configured"
        raise RuntimeError(msg) from error


@router.get(MEDIA_DETAIL_PATH, response_class=HTMLResponse)
async def media_detail_page(
    request: Request,
    media_id: UUID,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    service: Annotated[MediaDetailService, Depends(get_media_detail_service)],
) -> HTMLResponse:
    """Render combined shared, personal, and availability facts for one title.

    :param request: Incoming browser request.
    :param media_id: Internal UUID assigned to the media item.
    :param templates: Shared Jinja2 template renderer.
    :param service: Injected media-detail application service.
    :return: Shared-layout detail page, or a safe not-found page.
    """
    detail = await service.get_detail(MediaId(media_id))
    if detail is None:
        return _render_not_found(templates, request)
    return _render(templates, request, detail, _recommendation_context(request), None, None)


@router.post(MEDIA_DETAIL_PATH, response_class=HTMLResponse)
async def submit_media_rating(  # noqa: PLR0913, PLR0917 - FastAPI dependency and form parameters.
    request: Request,
    media_id: UUID,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
    service: Annotated[MediaDetailService, Depends(get_media_detail_service)],
    rating_value: Annotated[str, Form()] = "",
    like_state: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Update the profile-owned rating or reaction through the application service.

    The CSRF/origin gate is declared first so it is enforced before the service
    is resolved. Invalid input and unexpected application failures are
    translated into safe messages that never include internal details.

    :param request: Incoming browser request.
    :param media_id: Internal UUID assigned to the media item.
    :param templates: Shared Jinja2 template renderer.
    :param _csrf: CSRF and origin validation dependency.
    :param service: Injected media-detail application service.
    :param rating_value: Optional submitted numeric rating.
    :param like_state: Optional submitted explicit reaction.
    :return: Shared-layout detail page with a safe result message.
    """
    error: str | None = None
    success: str | None = None
    status_code = HTTPStatus.OK
    try:
        value = _parse_rating_value(rating_value)
        reaction = _parse_like_state(like_state)
    except ValueError:
        error = _INVALID_RATING_MESSAGE
        status_code = HTTPStatus.BAD_REQUEST
    else:
        if value is None and reaction is None:
            error = _EMPTY_RATING_MESSAGE
            status_code = HTTPStatus.BAD_REQUEST
        else:
            try:
                await service.set_rating(MediaId(media_id), value=value, like_state=reaction)
            except Exception:  # noqa: BLE001 - translate unexpected application failures into a sanitized result.
                error = _FAILURE_MESSAGE
                status_code = HTTPStatus.INTERNAL_SERVER_ERROR
            else:
                success = "Personal state saved."
    detail = await service.get_detail(MediaId(media_id))
    if detail is None:
        return _render_not_found(templates, request)
    return _render(
        templates,
        request,
        detail,
        _recommendation_context(request),
        error,
        success,
        status_code=status_code,
    )


async def csrf_rejection_handler(request: Request, error: Exception) -> HTMLResponse:
    """Render a safe rejection page for an invalid CSRF or origin check.

    Submissions for other server-rendered forms are delegated to their own
    handler so this feature does not change existing pages.

    :param request: Incoming rejected request.
    :param error: Rejected CSRF validation error.
    :return: Shared-layout rejection page.
    """
    if request.url.path != MEDIA_DETAIL_PATH:
        return await recommendation_csrf_rejection_handler(request, error)
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "media_detail.html",
        {
            "csrf_token": get_csrf_token(request),
            "detail": None,
            "recommendation": None,
            "error": _REJECTED_MESSAGE,
            "success": None,
        },
        status_code=HTTPStatus.FORBIDDEN,
    )


def register_media_detail_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced by the media detail page.

    :param app: FastAPI application receiving the media-detail-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {CsrfValidationError: csrf_rejection_handler}
    register_exception_handlers(app, handlers)


def _parse_rating_value(value: str) -> float | None:
    """Parse an optional submitted numeric rating.

    :param value: Raw submitted form value.
    :return: Parsed rating, or ``None`` when blank.
    :raises ValueError: If the value is not a number between 0 and 10.
    """
    text = value.strip()
    if not text:
        return None
    rating = float(text)
    if not 0 <= rating <= _MAXIMUM_RATING:
        msg = f"Rating must be between 0 and {_MAXIMUM_RATING}"
        raise ValueError(msg)
    return rating


def _parse_like_state(value: str) -> LikeState | None:
    """Parse an optional submitted explicit reaction.

    :param value: Raw submitted form value.
    :return: Parsed reaction, or ``None`` when blank.
    :raises ValueError: If the value is not a supported reaction.
    """
    text = value.strip()
    if not text:
        return None
    try:
        return LikeState(text)
    except ValueError as error:
        msg = "Reaction must be a supported value"
        raise ValueError(msg) from error


def _recommendation_context(request: Request) -> RecommendationContextView | None:
    """Read optional recommendation explanation facts from the query string.

    The recommendation result page links to this page with the already-computed
    presentation facts, so the detail page never recomputes filtering or
    ranking. Values are bounded to keep the rendered context predictable.

    :param request: Incoming browser request.
    :return: Recommendation context, or ``None`` when it was not supplied.
    """
    params = request.query_params
    rank = _optional_int(params.get("rank"))
    score = _optional_int(params.get("score"))
    if rank is None or score is None:
        return None
    return RecommendationContextView(
        rank=rank,
        score=score,
        constraints=_bounded_values(params.getlist("constraint")),
        reasons=_bounded_values(params.getlist("reason")),
        warnings=_bounded_values(params.getlist("warning")),
    )


def _optional_int(value: str | None) -> int | None:
    """Parse an optional integer query value.

    :param value: Raw query value.
    :return: Parsed integer, or ``None`` when absent or invalid.
    """
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _bounded_values(values: list[str]) -> tuple[str, ...]:
    """Return bounded, trimmed, non-empty context values.

    :param values: Raw repeated query values.
    :return: At most the supported number of bounded values.
    """
    bounded: list[str] = []
    for value in values:
        text = value.strip()[:_MAXIMUM_CONTEXT_LENGTH]
        if text:
            bounded.append(text)
        if len(bounded) >= _MAXIMUM_CONTEXT_ITEMS:
            break
    return tuple(bounded)


def _detail_view(detail: MediaDetail) -> MediaDetailView:
    """Map combined application facts to presentation facts.

    :param detail: Combined shared, personal, and availability facts.
    :return: Presentation facts for the detail page.
    """
    media = detail.media
    runtime = media.runtime if isinstance(media, Movie) else media.episode_runtime
    ratings = [rating.value for rating in detail.ratings if rating.value is not None]
    like_states = sorted(
        {rating.like_state for rating in detail.ratings if rating.like_state is not None},
        key=lambda state: state.value,
    )
    last_watched = max((event.watched_at for event in detail.viewing_events), default=None)
    return MediaDetailView(
        media_id=str(media.id.value),
        title=media.title,
        original_title=media.original_title,
        release_year=media.release_year,
        release_date=media.release_date.isoformat() if media.release_date is not None else None,
        media_type=media.media_type.value,
        runtime_minutes=runtime.minutes if runtime is not None else None,
        genres=tuple(genre.name for genre in media.genres),
        production_countries=tuple(f"{country.name} ({country.code})" for country in media.production_countries),
        artwork_url=_artwork_url(media),
        artwork_alt=f"{media.title} artwork",
        external_ids=tuple(
            f"{external_id.namespace}: {external_id.value}"
            for external_id in sorted(media.external_ids, key=lambda item: item.namespace)
        ),
        watch_status=detail.watch_status.value,
        watch_count=len(detail.viewing_events),
        last_watched_at=last_watched.isoformat() if last_watched is not None else None,
        personal_rating=max(ratings, default=None),
        like_states=tuple(state.value for state in like_states),
        library_presence=tuple(_library_presence_view(item) for item in detail.library_presence),
        streaming_availability=tuple(_streaming_view(item) for item in detail.streaming_availability),
        unknown_fields=_unknown_fields(detail, runtime_minutes=runtime.minutes if runtime is not None else None),
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


def _library_presence_view(presence: LibraryPresence) -> LibraryPresenceView:
    """Map one local-library presence record to presentation facts.

    :param presence: Normalized library presence.
    :return: Presentation facts for one presence record.
    """
    return LibraryPresenceView(
        provider=presence.provenance.provider,
        available=presence.available,
        play_count=presence.play_count,
        last_played_at=presence.last_played_at.isoformat() if presence.last_played_at is not None else None,
    )


def _streaming_view(availability: StreamingAvailability) -> StreamingAvailabilityView:
    """Map one streaming availability fact to presentation facts.

    :param availability: Normalized regional streaming availability.
    :return: Presentation facts for one availability fact.
    """
    return StreamingAvailabilityView(
        service=availability.service.name,
        region=availability.region,
        availability_type=availability.availability_type.value,
        source_provider=availability.provenance.provider,
        observed_at=availability.provenance.observed_at.isoformat(),
        attribution=availability.provenance.attribution,
    )


def _unknown_fields(detail: MediaDetail, *, runtime_minutes: int | None) -> tuple[str, ...]:
    """Return explicit unknown-data indicators for the detail page.

    :param detail: Combined shared, personal, and availability facts.
    :param runtime_minutes: Known runtime in minutes, or ``None``.
    :return: Human-readable unknown-data labels.
    """
    media = detail.media
    unknown: list[str] = []
    if runtime_minutes is None:
        unknown.append("Runtime")
    if not media.genres:
        unknown.append("Genres")
    if not media.production_countries:
        unknown.append("Production countries")
    if media.release_date is None:
        unknown.append("Release date")
    if detail.watch_status is WatchStatus.UNKNOWN:
        unknown.append("Watch state")
    if not any(rating.value is not None for rating in detail.ratings):
        unknown.append("Personal rating")
    if not detail.library_presence and not detail.streaming_availability:
        unknown.append("Availability")
    return tuple(unknown)


def _render_not_found(templates: Jinja2Templates, request: Request) -> HTMLResponse:
    """Render a safe not-found page for an absent catalog item.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :return: Shared-layout not-found page.
    """
    return templates.TemplateResponse(
        request,
        "media_detail.html",
        {
            "csrf_token": get_csrf_token(request),
            "detail": None,
            "recommendation": None,
            "error": _NOT_FOUND_MESSAGE,
            "success": None,
        },
        status_code=HTTPStatus.NOT_FOUND,
    )


def _render(  # noqa: PLR0913, PLR0917 - explicit render context parameters.
    templates: Jinja2Templates,
    request: Request,
    detail: MediaDetail,
    recommendation: RecommendationContextView | None,
    error: str | None,
    success: str | None,
    *,
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the detail page with a fresh CSRF token and safe context.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :param detail: Combined application facts to render.
    :param recommendation: Optional recommendation explanation context.
    :param error: Optional safe error message.
    :param success: Optional safe success message.
    :param status_code: HTTP status for the rendered response.
    :return: Shared-layout detail page.
    """
    return templates.TemplateResponse(
        request,
        "media_detail.html",
        {
            "csrf_token": get_csrf_token(request),
            "detail": _detail_view(detail),
            "recommendation": recommendation,
            "error": error,
            "success": success,
            "like_states": tuple(LikeState),
        },
        status_code=status_code,
    )
