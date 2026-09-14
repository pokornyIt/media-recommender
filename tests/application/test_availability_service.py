"""Tests for provider-independent availability refresh orchestration."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from media_recommender.application import (
    AvailabilityOffer,
    AvailabilityRefreshService,
    AvailabilityRefreshStatus,
    MatchKind,
    MatchReason,
    MediaIdentityCandidate,
    MediaMatch,
)
from media_recommender.domain import AvailabilityType, ExternalId, MediaId, MediaType, Movie, StreamingService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from media_recommender.domain import StreamingAvailability


class FakeResolver:
    """Return one configured deterministic identity result."""

    def __init__(self, result: MediaMatch) -> None:
        """Store the result returned for every candidate."""
        self.result = result

    async def resolve(self, candidate: MediaIdentityCandidate) -> MediaMatch:
        """Return the configured identity result."""
        del candidate
        return self.result


class FakeAvailabilityProvider:
    """Return configured offers while recording source requests."""

    source: str = "tmdb"
    attribution: str | None = "JustWatch"

    def __init__(self, offers: Sequence[AvailabilityOffer]) -> None:
        """Store normalized provider offers."""
        self.offers = offers
        self.calls: list[tuple[ExternalId, MediaType, str]] = []

    async def get_availability(
        self,
        external_id: ExternalId,
        media_type: MediaType,
        region: str,
    ) -> Sequence[AvailabilityOffer]:
        """Record and return one complete snapshot."""
        self.calls.append((external_id, media_type, region))
        return self.offers


class RecordingAvailabilityRepository:
    """Capture the snapshot supplied by the application service."""

    def __init__(self) -> None:
        """Initialize without a captured snapshot."""
        self.snapshot: tuple[MediaId, str, str, tuple[StreamingAvailability, ...]] | None = None

    async def replace_snapshot(
        self,
        media_id: MediaId,
        region: str,
        source_provider: str,
        availability: Sequence[StreamingAvailability],
    ) -> int:
        """Capture a complete snapshot and report one synthetic removal."""
        self.snapshot = (media_id, region, source_provider, tuple(availability))
        return 1

    async def list_for_media(
        self,
        media_id: MediaId,
        *,
        region: str | None = None,
    ) -> Sequence[StreamingAvailability]:
        """Return no stored records for protocol completeness."""
        del media_id, region
        return ()


def _candidate() -> MediaIdentityCandidate:
    """Return synthetic TMDB identity evidence."""
    return MediaIdentityCandidate(
        media_type=MediaType.MOVIE,
        title="Synthetic Movie",
        release_year=2026,
        external_ids=frozenset({ExternalId("tmdb", "42")}),
    )


def test_refresh_resolves_media_and_persists_netflix_cz_snapshot() -> None:
    """Verify normalized offers are attached only after deterministic resolution."""
    candidate = _candidate()
    movie = Movie(id=MediaId.new(), title=candidate.title, external_ids=candidate.external_ids)
    provider = FakeAvailabilityProvider(
        [AvailabilityOffer(StreamingService("8", "Netflix"), AvailabilityType.SUBSCRIPTION)]
    )
    repository = RecordingAvailabilityRepository()
    observed_at = datetime(2026, 9, 14, 19, tzinfo=UTC)
    service = AvailabilityRefreshService(
        FakeResolver(MediaMatch(MatchKind.EXACT, MatchReason.EXTERNAL_ID, movie, (movie.id,))),
        provider,
        repository,
        clock=lambda: observed_at,
    )

    result = asyncio.run(service.refresh(candidate, "cz"))

    assert result.status is AvailabilityRefreshStatus.REFRESHED
    assert (result.available, result.removed) == (1, 1)
    assert provider.calls == [(ExternalId("tmdb", "42"), MediaType.MOVIE, "CZ")]
    assert repository.snapshot is not None
    _, region, source, availability = repository.snapshot
    assert (region, source) == ("CZ", "tmdb")
    assert availability[0].media_id == movie.id
    assert availability[0].provenance.observed_at == observed_at
    assert availability[0].provenance.attribution == "JustWatch"


def test_refresh_does_not_fetch_or_persist_unresolved_identity() -> None:
    """Verify unavailable identity evidence is surfaced without guessed association."""
    provider = FakeAvailabilityProvider(())
    repository = RecordingAvailabilityRepository()
    service = AvailabilityRefreshService(
        FakeResolver(MediaMatch(MatchKind.NOT_FOUND, MatchReason.NO_CANDIDATE)),
        provider,
        repository,
    )

    result = asyncio.run(service.refresh(_candidate(), "CZ"))

    assert result.status is AvailabilityRefreshStatus.UNRESOLVED
    assert result.reason == MatchReason.NO_CANDIDATE.value
    assert provider.calls == []
    assert repository.snapshot is None
