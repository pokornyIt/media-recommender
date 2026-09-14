"""Persistence-independent contracts for profile-owned personal media data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from media_recommender.domain import (
        LibraryPresence,
        Preference,
        Profile,
        ProfileId,
        ProviderProfileMapping,
        Rating,
        ViewingEvent,
        WatchState,
        WatchStatus,
    )
    from media_recommender.domain.media import MediaId


class ProfileRepository(Protocol):
    """Create and retrieve internal profile owners."""

    async def get_or_create_default(self) -> Profile:
        """Return the implicit profile used by the single-user application.

        :return: Existing or newly persisted default profile.
        """
        ...

    async def get_profile(self, profile_id: ProfileId) -> Profile | None:
        """Return a profile by its internal identity.

        :param profile_id: Internal profile identity.
        :return: Matching profile, or ``None`` when absent.
        """
        ...

    async def save_profile(self, profile: Profile) -> None:
        """Persist an internal profile.

        :param profile: Profile to insert or update.
        """
        ...


class PersonalMediaRepository(Protocol):
    """Persist and query provider-independent personal media state."""

    async def save_viewing_event(self, event: ViewingEvent) -> ViewingEvent:
        """Persist one watch, idempotently when source identity is available.

        :param event: Viewing event to persist.
        :return: Persisted event, which may carry an existing internal identity.
        """
        ...

    async def list_viewing_events(self, profile_id: ProfileId, media_id: MediaId) -> tuple[ViewingEvent, ...]:
        """Return a profile's watches of one catalog item.

        :param profile_id: Owner whose history should be queried.
        :param media_id: Shared catalog identity.
        :return: Matching events ordered by watch time and identity.
        """
        ...

    async def save_watch_state(self, state: WatchState) -> WatchState:
        """Persist explicit watched or unwatched state from one provider.

        :param state: Provider-independent explicit watch state.
        :return: Persisted state, which may carry an existing internal identity.
        """
        ...

    async def save_library_presence(self, presence: LibraryPresence) -> LibraryPresence:
        """Persist one provider library-presence record idempotently.

        :param presence: Current library presence and optional playback metadata.
        :return: Persisted presence preserving its existing internal identity.
        """
        ...

    async def list_library_presence(self, profile_id: ProfileId, provider: str) -> tuple[LibraryPresence, ...]:
        """Return all known library-presence records for one provider mapping.

        :param profile_id: Internal owner identity.
        :param provider: Provider namespace.
        :return: Matching presence records ordered by source identity.
        """
        ...

    async def get_watch_status(self, profile_id: ProfileId, media_id: MediaId) -> WatchStatus:
        """Derive watched, unwatched, or unknown without collapsing absence.

        :param profile_id: Owner whose state should be queried.
        :param media_id: Shared catalog identity.
        :return: Derived three-state watch status.
        """
        ...

    async def remove_watch_state(self, profile_id: ProfileId, media_id: MediaId, provider: str) -> None:
        """Remove one provider's explicit state so absence remains unknown.

        :param profile_id: Internal owner identity.
        :param media_id: Shared catalog identity.
        :param provider: Provider namespace whose stale state should be removed.
        """
        ...

    async def save_rating(self, rating: Rating) -> Rating:
        """Persist a rating independently of viewing history.

        :param rating: Rating to persist.
        :return: Persisted rating, which may carry an existing internal identity.
        """
        ...

    async def list_ratings(self, profile_id: ProfileId, media_id: MediaId) -> tuple[Rating, ...]:
        """Return a profile's ratings for one catalog item.

        :param profile_id: Owner whose ratings should be queried.
        :param media_id: Shared catalog identity.
        :return: Matching ratings ordered by identity.
        """
        ...

    async def save_preference(self, preference: Preference) -> None:
        """Insert or update one preference or exclusion.

        :param preference: Preference to persist.
        """
        ...

    async def list_preferences(self, profile_id: ProfileId) -> tuple[Preference, ...]:
        """Return all preferences and exclusions owned by a profile.

        :param profile_id: Owner whose criteria should be returned.
        :return: Matching criteria ordered by identity.
        """
        ...

    async def save_provider_mapping(self, mapping: ProviderProfileMapping) -> None:
        """Insert or update an external profile mapping.

        :param mapping: Provider mapping to persist.
        """
        ...

    async def find_provider_mapping(self, provider: str, external_profile_id: str) -> ProviderProfileMapping | None:
        """Resolve an external owner identity to its internal mapping.

        :param provider: Normalized or unnormalized provider name.
        :param external_profile_id: Provider-owned profile identifier.
        :return: Matching provider mapping, or ``None`` when absent.
        """
        ...

    async def list_provider_mappings(self, profile_id: ProfileId) -> tuple[ProviderProfileMapping, ...]:
        """Return provider mappings owned by an internal profile.

        :param profile_id: Owner whose mappings should be returned.
        :return: Matching mappings ordered by provider and external identity.
        """
        ...
