"""Application services for provider-independent personal-media imports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from media_recommender.application.identity import MatchKind, MatchReason, MediaMatch
from media_recommender.domain import (
    ProviderProfileMapping,
    ProviderProfileMappingId,
    Rating,
    RatingId,
    SourceProvenance,
    ViewingEvent,
    ViewingEventId,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from media_recommender.application.identity import MediaIdentityCandidate
    from media_recommender.application.personal import PersonalMediaRepository, ProfileRepository
    from media_recommender.domain import LikeState, MediaId, ProfileId


class PersonalImportKind(StrEnum):
    """Kind of provider-independent personal record being imported."""

    VIEWING = "viewing"
    RATING = "rating"


class ImportRecordStatus(StrEnum):
    """Observable outcome for one source record."""

    IMPORTED = "imported"
    SKIPPED = "skipped"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True, kw_only=True)
class PersonalImportRecord:
    """Normalized personal-media source record ready for identity resolution."""

    row_number: int
    kind: PersonalImportKind
    source_record_id: str
    candidate: MediaIdentityCandidate
    alternative_candidate: MediaIdentityCandidate | None = None
    occurred_at: datetime | None = None
    rating_value: float | None = None
    like_state: LikeState | None = None

    def __post_init__(self) -> None:
        """Validate the normalized record shape.

        :raises ValueError: If fields do not match the declared record kind.
        """
        if self.row_number < 1 or not self.source_record_id.strip():
            msg = "Import row number and source record ID must be valid"
            raise ValueError(msg)
        if self.occurred_at is not None and self.occurred_at.utcoffset() is None:
            msg = "Import occurrence timestamp must be timezone-aware"
            raise ValueError(msg)
        if self.kind is PersonalImportKind.VIEWING:
            if self.occurred_at is None or self.rating_value is not None or self.like_state is not None:
                msg = "Viewing imports require an occurrence timestamp and no rating fields"
                raise ValueError(msg)
        elif self.rating_value is None and self.like_state is None:
            msg = "Rating imports require a numeric value or explicit reaction"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class InvalidImportRecord:
    """Safe diagnostic for one provider row that could not be normalized."""

    row_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class ImportRecordResult:
    """Privacy-safe result for one imported or rejected source row."""

    row_number: int
    status: ImportRecordStatus
    reason: str | None = None
    candidate_ids: tuple[MediaId, ...] = ()


@dataclass(frozen=True, slots=True)
class PersonalImportResult:
    """Complete result and summary counts for one import batch."""

    records: tuple[ImportRecordResult, ...]

    def count(self, status: ImportRecordStatus) -> int:
        """Return the number of rows with a particular outcome.

        :param status: Outcome to count.
        :return: Matching record count.
        """
        return sum(record.status is status for record in self.records)

    @property
    def imported(self) -> int:
        """Return the number of newly imported records."""
        return self.count(ImportRecordStatus.IMPORTED)

    @property
    def skipped(self) -> int:
        """Return the number of idempotently skipped records."""
        return self.count(ImportRecordStatus.SKIPPED)

    @property
    def unresolved(self) -> int:
        """Return the number of unmatched records."""
        return self.count(ImportRecordStatus.UNRESOLVED)

    @property
    def ambiguous(self) -> int:
        """Return the number of ambiguous records."""
        return self.count(ImportRecordStatus.AMBIGUOUS)

    @property
    def invalid(self) -> int:
        """Return the number of invalid records."""
        return self.count(ImportRecordStatus.INVALID)


class IdentityResolver(Protocol):
    """Resolve normalized source evidence to shared catalog identities."""

    async def resolve(self, candidate: MediaIdentityCandidate) -> MediaMatch:
        """Return a deterministic identity match for source evidence.

        :param candidate: Provider-independent source identity evidence.
        :return: Exact, resolved, ambiguous, or not-found result.
        """
        ...


class PersonalMediaImportService:
    """Import normalized personal records into the implicit internal profile."""

    def __init__(
        self,
        profiles: ProfileRepository,
        personal_media: PersonalMediaRepository,
        resolver: IdentityResolver,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """Initialize import boundaries and a testable timestamp source.

        :param profiles: Internal profile persistence boundary.
        :param personal_media: Personal-media persistence boundary.
        :param resolver: Shared deterministic media identity resolver.
        :param clock: Optional timezone-aware import timestamp source.
        """
        self._profiles = profiles
        self._personal_media = personal_media
        self._resolver = resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    async def import_records(
        self,
        *,
        provider: str,
        external_profile_id: str,
        synchronization_id: str,
        records: Sequence[PersonalImportRecord],
        invalid_records: Sequence[InvalidImportRecord] = (),
    ) -> PersonalImportResult:
        """Resolve and persist one repeatable provider import batch.

        :param provider: Provider namespace for provenance and profile mapping.
        :param external_profile_id: Provider profile represented by the source file.
        :param synchronization_id: Identity of this import run or source payload.
        :param records: Valid normalized source records.
        :param invalid_records: Parser diagnostics that contain no private row values.
        :return: Per-row outcomes and aggregate counts.
        :raises ValueError: If a provider profile is already owned by another internal profile.
        """
        imported_at = self._clock()
        if imported_at.utcoffset() is None:
            msg = "Import clock must return a timezone-aware timestamp"
            raise ValueError(msg)
        profile = await self._profiles.get_or_create_default()
        await self._ensure_profile_mapping(provider, external_profile_id, profile.id, imported_at)

        outcomes = [
            ImportRecordResult(item.row_number, ImportRecordStatus.INVALID, item.reason) for item in invalid_records
        ]
        outcomes.extend(
            [
                await self._import_record(
                    record,
                    profile.id,
                    provider,
                    synchronization_id,
                    imported_at,
                )
                for record in records
            ]
        )
        outcomes.sort(key=lambda item: item.row_number)
        return PersonalImportResult(tuple(outcomes))

    async def _ensure_profile_mapping(
        self,
        provider: str,
        external_profile_id: str,
        profile_id: ProfileId,
        synchronized_at: datetime,
    ) -> None:
        """Map an external profile to the implicit internal owner.

        :param provider: Provider namespace.
        :param external_profile_id: Provider-owned profile identifier.
        :param profile_id: Implicit internal profile identity.
        :param synchronized_at: Current synchronization timestamp.
        :raises ValueError: If an existing mapping belongs to another owner.
        """
        existing = await self._personal_media.find_provider_mapping(provider, external_profile_id)
        if existing is not None and existing.profile_id != profile_id:
            msg = "Provider profile is already mapped to another internal profile"
            raise ValueError(msg)
        mapping = ProviderProfileMapping(
            id=existing.id if existing is not None else ProviderProfileMappingId.new(),
            profile_id=profile_id,
            provider=provider,
            external_profile_id=external_profile_id,
            synchronized_at=synchronized_at,
        )
        await self._personal_media.save_provider_mapping(mapping)

    async def _import_record(
        self,
        record: PersonalImportRecord,
        profile_id: ProfileId,
        provider: str,
        synchronization_id: str,
        imported_at: datetime,
    ) -> ImportRecordResult:
        """Resolve and persist a single normalized record.

        :param record: Normalized provider record.
        :param profile_id: Internal owner identity.
        :param provider: Provider namespace for provenance.
        :param synchronization_id: Current import payload identity.
        :param imported_at: Current import timestamp.
        :return: Privacy-safe row outcome.
        :raises ValueError: If a validated record or successful match has an invalid shape.
        """
        match = await self._resolve_record(record)
        if match.kind not in {MatchKind.EXACT, MatchKind.RESOLVED}:
            status = (
                ImportRecordStatus.AMBIGUOUS if match.kind is MatchKind.AMBIGUOUS else ImportRecordStatus.UNRESOLVED
            )
            return ImportRecordResult(record.row_number, status, match.reason.value, match.candidate_ids)
        if match.media is None:
            msg = "Successful identity match did not contain media"
            raise ValueError(msg)

        provenance = SourceProvenance(
            provider=provider,
            source_record_id=record.source_record_id,
            synchronization_id=synchronization_id,
            imported_at=imported_at,
        )
        if record.kind is PersonalImportKind.VIEWING:
            if record.occurred_at is None:
                msg = "Validated viewing record is missing its timestamp"
                raise ValueError(msg)
            event = ViewingEvent(
                id=ViewingEventId.new(),
                profile_id=profile_id,
                media_id=match.media.id,
                watched_at=record.occurred_at,
                provenance=provenance,
            )
            stored = await self._personal_media.save_viewing_event(event)
            was_inserted = stored.id == event.id
        else:
            rating = Rating(
                id=RatingId.new(),
                profile_id=profile_id,
                media_id=match.media.id,
                value=record.rating_value,
                like_state=record.like_state,
                rated_at=record.occurred_at,
                provenance=provenance,
            )
            stored_rating = await self._personal_media.save_rating(rating)
            was_inserted = stored_rating.id == rating.id
        status = ImportRecordStatus.IMPORTED if was_inserted else ImportRecordStatus.SKIPPED
        return ImportRecordResult(record.row_number, status)

    async def _resolve_record(self, record: PersonalImportRecord) -> MediaMatch:
        """Resolve primary and optional alternate media-type evidence safely.

        :param record: Normalized source record to resolve.
        :return: One safe match or a combined unresolved outcome.
        """
        matches = [await self._resolver.resolve(record.candidate)]
        if record.alternative_candidate is not None:
            matches.append(await self._resolver.resolve(record.alternative_candidate))
        ambiguous_ids = {
            candidate_id
            for match in matches
            if match.kind is MatchKind.AMBIGUOUS
            for candidate_id in match.candidate_ids
        }
        successful = {
            match.media.id: match
            for match in matches
            if match.kind in {MatchKind.EXACT, MatchKind.RESOLVED} and match.media is not None
        }
        if ambiguous_ids or len(successful) > 1:
            candidate_ids = tuple(sorted(ambiguous_ids | set(successful), key=lambda item: str(item.value)))
            return MediaMatch(MatchKind.AMBIGUOUS, MatchReason.MULTIPLE_CANDIDATES, candidate_ids=candidate_ids)
        if successful:
            return next(iter(successful.values()))
        return matches[0]
