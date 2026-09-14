"""Provider-independent application service for external media libraries."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from media_recommender.application.identity import MatchKind
from media_recommender.domain import (
    LibraryPresence,
    LibraryPresenceId,
    ProviderProfileMapping,
    ProviderProfileMappingId,
    SourceProvenance,
    WatchState,
    WatchStateId,
    WatchStatus,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from media_recommender.application.identity import MediaIdentityCandidate
    from media_recommender.application.imports import IdentityResolver
    from media_recommender.application.personal import PersonalMediaRepository, ProfileRepository
    from media_recommender.domain import MediaId, ProfileId


class LibraryItemStatus(StrEnum):
    """Observable synchronization outcome for one provider item."""

    SYNCHRONIZED = "synchronized"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True, kw_only=True)
class LibraryItemSnapshot:
    """Normalized current state of one item visible in a provider library."""

    position: int
    source_record_id: str
    candidate: MediaIdentityCandidate
    watch_status: WatchStatus = WatchStatus.UNKNOWN
    play_count: int | None = None
    last_played_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate normalized source state.

        :raises ValueError: If source identity or playback metadata is invalid.
        """
        source_record_id = self.source_record_id.strip()
        if self.position < 1 or not source_record_id:
            msg = "Library item position and source record ID must be valid"
            raise ValueError(msg)
        if isinstance(self.play_count, bool) or (self.play_count is not None and self.play_count < 0):
            msg = "Library item play count must not be negative"
            raise ValueError(msg)
        if self.last_played_at is not None and self.last_played_at.utcoffset() is None:
            msg = "Library item last-played timestamp must be timezone-aware"
            raise ValueError(msg)
        object.__setattr__(self, "source_record_id", source_record_id)


@dataclass(frozen=True, slots=True)
class InvalidLibraryItem:
    """Safe diagnostic for a provider item that could not be normalized."""

    position: int
    reason: str
    source_record_id: str | None = None


@dataclass(frozen=True, slots=True)
class LibraryItemResult:
    """Privacy-safe result for one provider library item."""

    position: int
    status: LibraryItemStatus
    reason: str | None = None
    candidate_ids: tuple[MediaId, ...] = ()


@dataclass(frozen=True, slots=True)
class LibrarySynchronizationResult:
    """Results and removal count for one complete provider snapshot."""

    records: tuple[LibraryItemResult, ...]
    removed: int

    def count(self, status: LibraryItemStatus) -> int:
        """Return the number of records with one outcome.

        :param status: Outcome to count.
        :return: Matching record count.
        """
        return sum(record.status is status for record in self.records)


@dataclass(frozen=True, slots=True)
class _SynchronizationContext:
    """Shared state for all items in one provider synchronization."""

    profile_id: ProfileId
    provider: str
    synchronization_id: str
    synchronized_at: datetime


class LibrarySynchronizationService:
    """Resolve and persist complete snapshots of profile-visible libraries."""

    def __init__(
        self,
        profiles: ProfileRepository,
        personal_media: PersonalMediaRepository,
        resolver: IdentityResolver,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize synchronization boundaries and timestamp source.

        :param profiles: Internal profile persistence boundary.
        :param personal_media: Personal-media persistence boundary.
        :param resolver: Shared deterministic media identity resolver.
        :param clock: Optional timezone-aware synchronization timestamp source.
        """
        self._profiles = profiles
        self._personal_media = personal_media
        self._resolver = resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    async def synchronize(
        self,
        *,
        provider: str,
        external_profile_id: str,
        synchronization_id: str,
        items: Sequence[LibraryItemSnapshot],
        invalid_items: Sequence[InvalidLibraryItem] = (),
    ) -> LibrarySynchronizationResult:
        """Persist a complete, successfully retrieved provider library snapshot.

        :param provider: Provider namespace.
        :param external_profile_id: Selected provider user identity.
        :param synchronization_id: Identity of the provider snapshot.
        :param items: Valid normalized library items.
        :param invalid_items: Safe diagnostics for malformed provider items.
        :return: Per-item outcomes and count of items no longer available.
        :raises ValueError: If configuration, ownership, or timestamp data is invalid.
        """
        synchronized_at = self._clock()
        if synchronized_at.utcoffset() is None:
            msg = "Synchronization clock must return a timezone-aware timestamp"
            raise ValueError(msg)
        normalized_provider = provider.strip().lower()
        if not normalized_provider or not external_profile_id.strip() or not synchronization_id.strip():
            msg = "Provider, external profile ID, and synchronization ID must not be empty"
            raise ValueError(msg)

        profile = await self._profiles.get_or_create_default()
        mapping = await self._mapping(normalized_provider, external_profile_id, profile.id, synchronized_at)
        existing = await self._personal_media.list_library_presence(profile.id, normalized_provider)
        existing_by_source = {
            item.provenance.source_record_id: item for item in existing if item.provenance.source_record_id is not None
        }
        observed_ids = {item.source_record_id for item in items}
        observed_ids.update(item.source_record_id for item in invalid_items if item.source_record_id is not None)

        context = _SynchronizationContext(
            profile_id=profile.id,
            provider=normalized_provider,
            synchronization_id=synchronization_id,
            synchronized_at=synchronized_at,
        )

        results = [LibraryItemResult(item.position, LibraryItemStatus.INVALID, item.reason) for item in invalid_items]
        results.extend(
            [
                await self._synchronize_item(
                    item,
                    existing_by_source.get(item.source_record_id),
                    context,
                )
                for item in items
            ]
        )

        removed = 0
        for presence in existing:
            source_record_id = presence.provenance.source_record_id
            if presence.available and source_record_id not in observed_ids:
                await self._personal_media.save_library_presence(
                    replace(
                        presence,
                        available=False,
                        provenance=replace(
                            presence.provenance,
                            synchronization_id=synchronization_id,
                            imported_at=synchronized_at,
                        ),
                    )
                )
                removed += 1

        await self._personal_media.save_provider_mapping(mapping)
        results.sort(key=lambda result: result.position)
        return LibrarySynchronizationResult(tuple(results), removed)

    async def _mapping(
        self,
        provider: str,
        external_profile_id: str,
        profile_id: ProfileId,
        synchronized_at: datetime,
    ) -> ProviderProfileMapping:
        """Return the selected external-user mapping for this synchronization.

        :param provider: Normalized provider namespace.
        :param external_profile_id: Selected provider user identity.
        :param profile_id: Internal profile identity.
        :param synchronized_at: Successful snapshot timestamp.
        :return: New or updated provider-profile mapping.
        :raises ValueError: If the external identity belongs to another profile.
        """
        existing = await self._personal_media.find_provider_mapping(provider, external_profile_id)
        if existing is not None and existing.profile_id != profile_id:
            msg = "Provider profile is already mapped to another internal profile"
            raise ValueError(msg)
        return ProviderProfileMapping(
            id=existing.id if existing is not None else ProviderProfileMappingId.new(),
            profile_id=profile_id,
            provider=provider,
            external_profile_id=external_profile_id,
            synchronized_at=synchronized_at,
        )

    async def _synchronize_item(
        self,
        item: LibraryItemSnapshot,
        existing: LibraryPresence | None,
        context: _SynchronizationContext,
    ) -> LibraryItemResult:
        """Resolve and persist one normalized library item.

        :param item: Current provider item state.
        :param existing: Existing presence carrying the same source identity, if any.
        :param context: Shared state for the current provider synchronization.
        :return: Privacy-safe item outcome.
        """
        match = await self._resolver.resolve(item.candidate)
        if match.kind not in {MatchKind.EXACT, MatchKind.RESOLVED} or match.media is None:
            status = LibraryItemStatus.AMBIGUOUS if match.kind is MatchKind.AMBIGUOUS else LibraryItemStatus.UNRESOLVED
            return LibraryItemResult(item.position, status, match.reason.value, match.candidate_ids)

        provenance = SourceProvenance(
            provider=context.provider,
            source_record_id=item.source_record_id,
            synchronization_id=context.synchronization_id,
            imported_at=context.synchronized_at,
        )
        presence = LibraryPresence(
            id=existing.id if existing is not None else LibraryPresenceId.new(),
            profile_id=context.profile_id,
            media_id=match.media.id,
            available=True,
            play_count=item.play_count,
            last_played_at=item.last_played_at,
            provenance=provenance,
        )
        await self._personal_media.save_library_presence(presence)
        if item.watch_status is WatchStatus.UNKNOWN:
            await self._personal_media.remove_watch_state(context.profile_id, match.media.id, context.provider)
        else:
            await self._personal_media.save_watch_state(
                WatchState(
                    id=WatchStateId.new(),
                    profile_id=context.profile_id,
                    media_id=match.media.id,
                    status=item.watch_status,
                    provenance=provenance,
                )
            )
        return LibraryItemResult(item.position, LibraryItemStatus.SYNCHRONIZED)
