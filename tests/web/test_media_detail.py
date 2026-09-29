"""Offline tests for the server-rendered media detail and personal state view."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID

from fastapi.testclient import TestClient

from media_recommender.application import MediaDetail
from media_recommender.domain import (
    Artwork,
    ArtworkType,
    AvailabilityProvenance,
    AvailabilityType,
    Country,
    ExternalId,
    Genre,
    LibraryPresence,
    LibraryPresenceId,
    LikeState,
    MediaId,
    Movie,
    Rating,
    RatingId,
    Runtime,
    SourceProvenance,
    StreamingAvailability,
    StreamingService,
    TVShow,
    ViewingEvent,
    ViewingEventId,
    WatchStatus,
    default_profile,
)
from media_recommender.web import create_app
from media_recommender.web.routes.media_detail import get_media_detail_service

if TYPE_CHECKING:
    from httpx import Client

MOVIE_ID = UUID("11111111-1111-1111-1111-111111111111")
SERIES_ID = UUID("22222222-2222-2222-2222-222222222222")
MISSING_ID = UUID("33333333-3333-3333-3333-333333333333")

_ARTWORK_URL = "https://artwork.synthetic.invalid/poster.jpg"
_SYNTHETIC_SYNC_SECRET = "synthetic-sync-secret"  # noqa: S105 - synthetic test value.
_SYNTHETIC_NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


class FakeMediaDetailService:
    """Synthetic media-detail boundary returning one configured snapshot."""

    def __init__(self, detail: MediaDetail | None) -> None:
        """Initialize the fake with one synthetic detail snapshot.

        :param detail: Snapshot returned for its own media identity.
        """
        self.detail = detail
        self.updates: list[tuple[MediaId, float | None, LikeState | None]] = []

    async def get_detail(self, media_id: MediaId) -> MediaDetail | None:
        """Return the configured snapshot for its own identity.

        :param media_id: Requested shared catalog identity.
        :return: Configured snapshot, or ``None`` for another identity.
        """
        if self.detail is None or self.detail.media.id != media_id:
            return None
        return self.detail

    async def set_rating(
        self,
        media_id: MediaId,
        *,
        value: float | None,
        like_state: LikeState | None,
    ) -> Rating:
        """Record one synthetic personal-state update.

        :param media_id: Shared catalog identity.
        :param value: Submitted numeric rating.
        :param like_state: Submitted explicit reaction.
        :return: Synthetic persisted rating.
        """
        self.updates.append((media_id, value, like_state))
        return Rating(
            id=RatingId.new(),
            profile_id=default_profile().id,
            media_id=media_id,
            value=value,
            like_state=like_state,
            rated_at=_SYNTHETIC_NOW,
            provenance=SourceProvenance(provider="web", imported_at=_SYNTHETIC_NOW),
        )


def _client(service: object) -> Client:
    """Build a test client with the media-detail service overridden.

    :param service: Synthetic media-detail service.
    :return: Offline HTTPX-compatible test client.
    """
    app = create_app()
    app.dependency_overrides[get_media_detail_service] = lambda: service
    return cast("Client", TestClient(app))


def _csrf_token(client: Client, path: str) -> str:
    """Return the CSRF token rendered by the media detail page.

    :param client: Test client carrying a session cookie.
    :param path: Media detail path to request.
    :return: Hidden form token value.
    """
    response = client.get(path)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _movie_detail() -> MediaDetail:
    """Create one synthetic movie detail snapshot with all fact kinds."""
    movie = Movie(
        id=MediaId(MOVIE_ID),
        title="Synthetic Detail Movie",
        original_title="Synthetic Original Title",
        released_on=date(2024, 3, 1),
        runtime=Runtime(101),
        genres=(Genre("Drama"),),
        production_countries=(Country("CZ", "Czechia"),),
        artwork=(Artwork(ArtworkType.POSTER, _ARTWORK_URL),),
        external_ids=frozenset({ExternalId("tmdb", "42")}),
    )
    profile = default_profile()
    return MediaDetail(
        media=movie,
        profile=profile,
        watch_status=WatchStatus.WATCHED,
        viewing_events=(
            ViewingEvent(
                id=ViewingEventId.new(),
                profile_id=profile.id,
                media_id=movie.id,
                watched_at=datetime(2026, 9, 10, 18, tzinfo=UTC),
                provenance=SourceProvenance(
                    provider="netflix",
                    synchronization_id=_SYNTHETIC_SYNC_SECRET,
                    imported_at=_SYNTHETIC_NOW,
                ),
            ),
        ),
        ratings=(
            Rating(
                id=RatingId.new(),
                profile_id=profile.id,
                media_id=movie.id,
                value=7.5,
                like_state=LikeState.LIKED,
                rated_at=_SYNTHETIC_NOW,
                provenance=SourceProvenance(provider="netflix", imported_at=_SYNTHETIC_NOW),
            ),
        ),
        library_presence=(
            LibraryPresence(
                id=LibraryPresenceId.new(),
                profile_id=profile.id,
                media_id=movie.id,
                available=True,
                play_count=2,
                last_played_at=datetime(2026, 9, 11, 20, tzinfo=UTC),
                provenance=SourceProvenance(
                    provider="jellyfin",
                    source_record_id="item-1",
                    imported_at=_SYNTHETIC_NOW,
                ),
            ),
        ),
        streaming_availability=(
            StreamingAvailability(
                media_id=movie.id,
                service=StreamingService("8", "Netflix"),
                region="CZ",
                availability_type=AvailabilityType.SUBSCRIPTION,
                provenance=AvailabilityProvenance("tmdb", _SYNTHETIC_NOW, "JustWatch"),
            ),
        ),
    )


def _series_detail() -> MediaDetail:
    """Create one synthetic TV-show detail snapshot with partial metadata."""
    series = TVShow(id=MediaId(SERIES_ID), title="Synthetic Detail Series")
    profile = default_profile()
    return MediaDetail(
        media=series,
        profile=profile,
        watch_status=WatchStatus.UNKNOWN,
        viewing_events=(),
        ratings=(),
        library_presence=(),
        streaming_availability=(),
    )


def test_movie_detail_renders_shared_personal_and_availability_facts() -> None:
    """Render shared catalog facts, personal state, and known availability separately."""
    client = _client(FakeMediaDetailService(_movie_detail()))

    response = client.get(f"/media/{MOVIE_ID}")

    assert response.status_code == HTTPStatus.OK
    assert "Synthetic Detail Movie" in response.text
    assert "Synthetic Original Title" in response.text
    assert "Shared catalog facts" in response.text
    assert "Personal state" in response.text
    assert "Local library presence" in response.text
    assert "Regional streaming availability" in response.text
    assert "101 minutes" in response.text
    assert "Drama" in response.text
    assert "Czechia (CZ)" in response.text
    assert "tmdb: 42" in response.text
    assert "watched" in response.text
    assert "7.5" in response.text
    assert "liked" in response.text
    assert "jellyfin" in response.text
    assert "Netflix" in response.text
    assert "CZ" in response.text
    assert "subscription" in response.text
    assert "JustWatch" in response.text
    assert _ARTWORK_URL in response.text


def test_tv_show_detail_renders_unknown_indicators() -> None:
    """Render a TV show with explicit unknown-data indicators rather than inferred values."""
    client = _client(FakeMediaDetailService(_series_detail()))

    response = client.get(f"/media/{SERIES_ID}")

    assert response.status_code == HTTPStatus.OK
    assert "Synthetic Detail Series" in response.text
    assert "tv_show" in response.text
    assert "Unknown data" in response.text
    assert "Runtime" in response.text
    assert "Genres" in response.text
    assert "Availability" in response.text
    assert "No local-library presence is known" in response.text
    assert "No regional streaming availability is known" in response.text


def test_absent_media_returns_a_safe_not_found_page() -> None:
    """Return a safe not-found page for an absent catalog item."""
    client = _client(FakeMediaDetailService(None))

    response = client.get(f"/media/{MISSING_ID}")

    assert response.status_code == HTTPStatus.NOT_FOUND
    assert "was not found" in response.text
    assert "Traceback" not in response.text


def test_recommendation_context_is_rendered_when_supplied() -> None:
    """Render recommendation explanation facts carried from a recommendation result."""
    client = _client(FakeMediaDetailService(_movie_detail()))

    response = client.get(
        f"/media/{MOVIE_ID}",
        params={
            "rank": "1",
            "score": "35",
            "constraint": "Included genre: Drama",
            "reason": "Liked: +30",
            "warning": "Availability unknown",
        },
    )

    assert response.status_code == HTTPStatus.OK
    assert "Recommendation context" in response.text
    assert "Rank 1" in response.text
    assert "Score 35" in response.text
    assert "Included genre: Drama" in response.text
    assert "Liked: +30" in response.text
    assert "Availability unknown" in response.text


def test_personal_state_update_uses_the_application_service() -> None:
    """Delegate a supported personal-state edit to the application service."""
    service = FakeMediaDetailService(_movie_detail())
    client = _client(service)
    token = _csrf_token(client, f"/media/{MOVIE_ID}")

    response = client.post(
        f"/media/{MOVIE_ID}",
        data={"csrf_token": token, "rating_value": "8.5", "like_state": "disliked"},
    )

    assert response.status_code == HTTPStatus.OK
    assert service.updates == [(MediaId(MOVIE_ID), 8.5, LikeState.DISLIKED)]
    assert "Personal state saved" in response.text


def test_empty_personal_state_update_is_rejected_without_calling_the_service() -> None:
    """Reject an empty personal-state update without invoking the application service."""
    service = FakeMediaDetailService(_movie_detail())
    client = _client(service)
    token = _csrf_token(client, f"/media/{MOVIE_ID}")

    response = client.post(f"/media/{MOVIE_ID}", data={"csrf_token": token})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "Provide a numeric rating or select a reaction" in response.text
    assert service.updates == []


def test_invalid_rating_value_is_rejected_safely() -> None:
    """Reject an out-of-range rating without internal validation details."""
    service = FakeMediaDetailService(_movie_detail())
    client = _client(service)
    token = _csrf_token(client, f"/media/{MOVIE_ID}")

    response = client.post(f"/media/{MOVIE_ID}", data={"csrf_token": token, "rating_value": "11"})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "Enter a rating between 0 and 10" in response.text
    assert service.updates == []
    for internal in ("Traceback", "pydantic", "value_error"):
        assert internal not in response.text


def test_missing_csrf_token_is_rejected_before_the_service() -> None:
    """Reject a personal-state submission without a valid session CSRF token."""
    service = FakeMediaDetailService(_movie_detail())
    client = _client(service)
    _csrf_token(client, f"/media/{MOVIE_ID}")

    response = client.post(f"/media/{MOVIE_ID}", data={"rating_value": "8.5"})

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert "Request rejected" in response.text
    assert service.updates == []


def test_provider_synchronization_identifiers_are_not_rendered() -> None:
    """Never render internal provider synchronization identifiers or raw records."""
    client = _client(FakeMediaDetailService(_movie_detail()))

    response = client.get(f"/media/{MOVIE_ID}")

    assert response.status_code == HTTPStatus.OK
    assert _SYNTHETIC_SYNC_SECRET not in response.text
    for internal in ("source_record_id", "synchronization_id", "RatingRecord", "LibraryPresenceRecord"):
        assert internal not in response.text
