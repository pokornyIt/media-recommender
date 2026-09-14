"""Application service for refreshing normalized streaming availability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from media_recommender.application.identity import MatchKind
from media_recommender.domain import AvailabilityProvenance, StreamingAvailability

REGION_CODE_LENGTH = 2

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from media_recommender.application.identity import MediaIdentityCandidate
    from media_recommender.application.imports import IdentityResolver
    from media_recommender.domain import AvailabilityType, ExternalId, MediaId, MediaType, StreamingService


@dataclass(frozen=True, slots=True)
class AvailabilityOffer:
    """Provider-independent offer returned by an availability integration."""

    service: StreamingService
    availability_type: AvailabilityType


class AvailabilityProvider(Protocol):
    """Retrieve complete regional availability snapshots from one source."""

    source: str
    attribution: str | None

    async def get_availability(
        self,
        external_id: ExternalId,
        media_type: MediaType,
        region: str,
    ) -> Sequence[AvailabilityOffer]:
        """Return the current offers for one source media identity and region.

        :param external_id: Identity understood by this source.
        :param media_type: Kind of media being queried.
        :param region: ISO 3166-1 alpha-2 availability region.
        :return: Complete normalized offer snapshot; empty means unavailable.
        """
        ...


class AvailabilityRepository(Protocol):
    """Persist and query current shared streaming availability."""

    async def replace_snapshot(
        self,
        media_id: MediaId,
        region: str,
        source_provider: str,
        availability: Sequence[StreamingAvailability],
    ) -> int:
        """Atomically replace one media, region, and source snapshot.

        :param media_id: Shared catalog identity.
        :param region: Region covered by the complete snapshot.
        :param source_provider: Provider that supplied the snapshot.
        :param availability: Current normalized availability facts.
        :return: Number of disappeared facts removed from persistence.
        """
        ...

    async def list_for_media(
        self,
        media_id: MediaId,
        *,
        region: str | None = None,
    ) -> Sequence[StreamingAvailability]:
        """Return current availability for one shared catalog item.

        :param media_id: Shared catalog identity.
        :param region: Optional region restriction.
        :return: Deterministically ordered availability facts.
        """
        ...


class AvailabilityRefreshStatus(StrEnum):
    """Observable outcome of one regional availability refresh."""

    REFRESHED = "refreshed"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    SOURCE_ID_MISSING = "source_id_missing"


@dataclass(frozen=True, slots=True)
class AvailabilityRefreshResult:
    """Privacy-safe summary of one refresh attempt."""

    status: AvailabilityRefreshStatus
    available: int = 0
    removed: int = 0
    reason: str | None = None
    candidate_ids: tuple[MediaId, ...] = ()


class AvailabilityRefreshService:
    """Resolve source media and persist complete regional availability snapshots."""

    def __init__(
        self,
        resolver: IdentityResolver,
        provider: AvailabilityProvider,
        repository: AvailabilityRepository,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize the refresh service boundaries.

        :param resolver: Shared deterministic media identity resolver.
        :param provider: Regional availability source.
        :param repository: Shared availability persistence boundary.
        :param clock: Optional timezone-aware observation timestamp source.
        """
        self._resolver = resolver
        self._provider = provider
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    async def refresh(self, candidate: MediaIdentityCandidate, region: str) -> AvailabilityRefreshResult:
        """Refresh one title's complete snapshot after deterministic identity resolution.

        :param candidate: Provider-independent identity evidence.
        :param region: ISO 3166-1 alpha-2 availability region.
        :return: Refresh outcome and persisted change counts.
        :raises ValueError: If provider identity, region, or clock data is invalid.
        """
        normalized_region = _normalize_region(region)
        source = self._provider.source.strip().lower()
        if not source:
            msg = "Availability provider source must not be empty"
            raise ValueError(msg)

        match = await self._resolver.resolve(candidate)
        if match.kind not in {MatchKind.EXACT, MatchKind.RESOLVED} or match.media is None:
            status = (
                AvailabilityRefreshStatus.AMBIGUOUS
                if match.kind is MatchKind.AMBIGUOUS
                else AvailabilityRefreshStatus.UNRESOLVED
            )
            return AvailabilityRefreshResult(status, reason=match.reason.value, candidate_ids=match.candidate_ids)

        source_id = next(
            (external_id for external_id in match.media.external_ids if external_id.namespace == source),
            None,
        )
        if source_id is None:
            return AvailabilityRefreshResult(
                AvailabilityRefreshStatus.SOURCE_ID_MISSING,
                reason=f"Missing {source} media identity",
                candidate_ids=(match.media.id,),
            )

        offers = await self._provider.get_availability(source_id, match.media.media_type, normalized_region)
        observed_at = self._clock()
        if observed_at.utcoffset() is None:
            msg = "Availability refresh clock must return a timezone-aware timestamp"
            raise ValueError(msg)
        provenance = AvailabilityProvenance(source, observed_at, self._provider.attribution)
        availability = tuple(
            StreamingAvailability(
                media_id=match.media.id,
                service=offer.service,
                region=normalized_region,
                availability_type=offer.availability_type,
                provenance=provenance,
            )
            for offer in offers
        )
        removed = await self._repository.replace_snapshot(
            match.media.id,
            normalized_region,
            source,
            availability,
        )
        return AvailabilityRefreshResult(
            AvailabilityRefreshStatus.REFRESHED,
            available=len(availability),
            removed=removed,
            candidate_ids=(match.media.id,),
        )


def _normalize_region(region: str) -> str:
    """Return a validated ISO 3166-1 alpha-2 region code.

    :param region: Region value to normalize.
    :return: Uppercase two-letter region.
    :raises ValueError: If the region is invalid.
    """
    normalized = region.strip().upper()
    if len(normalized) != REGION_CODE_LENGTH or not normalized.isascii() or not normalized.isalpha():
        msg = "Availability region must be a two-letter ISO 3166-1 alpha-2 code"
        raise ValueError(msg)
    return normalized
