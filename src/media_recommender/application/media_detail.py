"""Application service combining shared catalog, personal state, and availability.

The service composes existing application boundaries so interface layers can
render one media-detail view without reading persistence or provider DTOs
directly. Shared catalog facts, profile-owned personal state, and known
availability stay explicitly separated in the returned snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from media_recommender.domain import Rating, RatingId, SourceProvenance

if TYPE_CHECKING:
    from collections.abc import Callable

    from media_recommender.application.availability import AvailabilityRepository
    from media_recommender.application.catalog import MediaCatalogReader
    from media_recommender.application.personal import PersonalMediaRepository, ProfileRepository
    from media_recommender.domain import (
        LibraryPresence,
        LikeState,
        Media,
        MediaId,
        Profile,
        ProfileId,
        StreamingAvailability,
        ViewingEvent,
        WatchStatus,
    )

WEB_RATING_PROVIDER = "web"
_WEB_RATING_NAMESPACE = "media-recommender:web-rating"


@dataclass(frozen=True, slots=True)
class MediaDetail:
    """Combined shared catalog, profile-owned, and availability facts for one title."""

    media: Media
    profile: Profile
    watch_status: WatchStatus
    viewing_events: tuple[ViewingEvent, ...]
    ratings: tuple[Rating, ...]
    library_presence: tuple[LibraryPresence, ...]
    streaming_availability: tuple[StreamingAvailability, ...]


class MediaDetailService:
    """Compose media-detail facts and profile-owned edits through application boundaries."""

    def __init__(
        self,
        catalog: MediaCatalogReader,
        profiles: ProfileRepository,
        personal: PersonalMediaRepository,
        availability: AvailabilityRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize the media-detail service boundaries.

        :param catalog: Shared normalized catalog reader.
        :param profiles: Internal profile persistence boundary.
        :param personal: Profile-owned personal media persistence boundary.
        :param availability: Shared regional streaming availability boundary.
        :param clock: Optional timezone-aware timestamp source for profile-owned edits.
        """
        self._catalog = catalog
        self._profiles = profiles
        self._personal = personal
        self._availability = availability
        self._clock = clock or (lambda: datetime.now(UTC))

    async def get_detail(self, media_id: MediaId) -> MediaDetail | None:
        """Return combined detail facts for one shared catalog item.

        :param media_id: Shared catalog identity.
        :return: Combined detail facts, or ``None`` when the item does not exist.
        """
        media = await self._catalog.get(media_id)
        if media is None:
            return None
        profile = await self._profiles.get_or_create_default()
        return MediaDetail(
            media=media,
            profile=profile,
            watch_status=await self._personal.get_watch_status(profile.id, media_id),
            viewing_events=await self._personal.list_viewing_events(profile.id, media_id),
            ratings=await self._personal.list_ratings(profile.id, media_id),
            library_presence=await self._personal.list_library_presence_for_media(profile.id, media_id),
            streaming_availability=tuple(await self._availability.list_for_media(media_id)),
        )

    async def set_rating(
        self,
        media_id: MediaId,
        *,
        value: float | None,
        like_state: LikeState | None,
    ) -> Rating:
        """Create or replace the profile-owned web rating for one catalog item.

        The web-owned rating uses a deterministic identity derived from the
        profile and media, so repeated edits update one record instead of
        accumulating duplicates. Provider-imported ratings are never modified.

        :param media_id: Shared catalog identity.
        :param value: Optional numeric rating between 0 and 10.
        :param like_state: Optional explicit reaction.
        :return: Persisted profile-owned rating.
        :raises ValueError: If neither a numeric value nor a reaction is supplied.
        """
        if value is None and like_state is None:
            msg = "A rating update requires a numeric value or explicit like state"
            raise ValueError(msg)
        profile = await self._profiles.get_or_create_default()
        observed_at = self._clock()
        rating = Rating(
            id=_web_rating_id(profile.id, media_id),
            profile_id=profile.id,
            media_id=media_id,
            value=value,
            like_state=like_state,
            rated_at=observed_at,
            provenance=SourceProvenance(provider=WEB_RATING_PROVIDER, imported_at=observed_at),
        )
        return await self._personal.save_rating(rating)


def _web_rating_id(profile_id: ProfileId, media_id: MediaId) -> RatingId:
    """Return the deterministic identity of a profile's web-owned rating.

    :param profile_id: Internal profile identity.
    :param media_id: Shared catalog identity.
    :return: Stable rating identity used for repeated edits.
    """
    return RatingId(uuid5(NAMESPACE_URL, f"{_WEB_RATING_NAMESPACE}:{profile_id.value}:{media_id.value}"))
