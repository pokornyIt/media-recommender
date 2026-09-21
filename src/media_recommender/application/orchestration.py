"""Phase 2 synchronization and recommendation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from media_recommender.application.availability import AvailabilityRefreshStatus
from media_recommender.application.errors import (
    SourceAuthenticationError,
    SourceTransientError,
    SourceWorkflowError,
)
from media_recommender.application.imports import ImportRecordStatus
from media_recommender.application.library import LibraryItemStatus

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from media_recommender.application.availability import AvailabilityRefreshResult
    from media_recommender.application.identity import MediaIdentityCandidate
    from media_recommender.application.imports import PersonalImportResult
    from media_recommender.application.library import LibrarySynchronizationResult
    from media_recommender.application.personal import ProfileRepository
    from media_recommender.application.ranking import RecommendationResult
    from media_recommender.application.recommendations import RecommendationCriteria
    from media_recommender.domain import MediaId, ProfileId


class WorkflowKind(StrEnum):
    """Phase 2 source operation represented by a report."""

    NETFLIX_VIEWING = "netflix_viewing"
    NETFLIX_RATINGS = "netflix_ratings"
    JELLYFIN_LIBRARY = "jellyfin_library"
    STREAMING_AVAILABILITY = "streaming_availability"


class WorkflowStatus(StrEnum):
    """Aggregate completion state suitable for future interfaces."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class WorkflowItemStatus(StrEnum):
    """Normalized result of one imported or synchronized source record."""

    IMPORTED = "imported"
    SKIPPED = "skipped"
    SYNCHRONIZED = "synchronized"
    REFRESHED = "refreshed"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    INVALID = "invalid"
    SOURCE_ID_MISSING = "source_id_missing"
    FAILED = "failed"


class WorkflowFailureReason(StrEnum):
    """Privacy-safe class of a source-level failure."""

    PROVIDER_FAILURE = "provider_failure"
    PROVIDER_AUTHENTICATION_FAILURE = "provider_authentication_failure"
    PROVIDER_TRANSIENT_FAILURE = "provider_transient_failure"
    LOCAL_SOURCE_FAILURE = "local_source_failure"


@dataclass(frozen=True, slots=True)
class WorkflowItem:
    """Safe status for one source record without private source values."""

    position: int
    status: WorkflowItemStatus
    reason: str | None = None
    candidate_ids: tuple[MediaId, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkflowCounts:
    """Normalized counts shared by import and synchronization operations."""

    succeeded: int = 0
    skipped: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    invalid: int = 0
    failed: int = 0
    removed: int = 0


@dataclass(frozen=True, slots=True)
class WorkflowReport:
    """Complete normalized result for one Phase 2 source operation."""

    kind: WorkflowKind
    status: WorkflowStatus
    counts: WorkflowCounts
    items: tuple[WorkflowItem, ...]


@dataclass(frozen=True, slots=True)
class AvailabilityRefreshRequest:
    """One regional title refresh within a synchronization run."""

    position: int
    candidate: MediaIdentityCandidate
    region: str

    def __post_init__(self) -> None:
        """Validate stable request position and non-empty region.

        :raises ValueError: If the position or region is invalid.
        """
        if self.position < 1 or not self.region.strip():
            msg = "Availability refresh position and region must be valid"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class Phase2SynchronizationRequest:
    """Configured source inputs for one in-process synchronization pass."""

    netflix_profile: str | None = None
    netflix_viewing_path: Path | None = None
    netflix_ratings_path: Path | None = None
    availability: tuple[AvailabilityRefreshRequest, ...] = ()

    def __post_init__(self) -> None:
        """Require a profile label whenever a Netflix file is selected.

        :raises ValueError: If Netflix inputs do not include a usable profile label.
        """
        has_netflix_file = self.netflix_viewing_path is not None or self.netflix_ratings_path is not None
        if has_netflix_file and (self.netflix_profile is None or not self.netflix_profile.strip()):
            msg = "A Netflix profile label is required for Netflix imports"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Phase2SynchronizationResult:
    """Ordered source reports from one complete orchestration pass."""

    reports: tuple[WorkflowReport, ...]

    @property
    def status(self) -> WorkflowStatus:
        """Return the aggregate status across configured source operations.

        :return: Failed, partial, or successful aggregate status.
        """
        if self.reports and all(report.status is WorkflowStatus.FAILED for report in self.reports):
            return WorkflowStatus.FAILED
        if any(report.status is WorkflowStatus.FAILED for report in self.reports):
            return WorkflowStatus.PARTIAL
        if any(report.status is WorkflowStatus.PARTIAL for report in self.reports):
            return WorkflowStatus.PARTIAL
        return WorkflowStatus.SUCCESS


class NetflixImportWorkflow(Protocol):
    """Import supported local Netflix personal-data files."""

    async def import_viewing_activity(self, path: Path, *, external_profile_id: str) -> PersonalImportResult:
        """Import one viewing-history file.

        :param path: Private local CSV path.
        :param external_profile_id: External profile label.
        :return: Provider-independent import result.
        """
        ...

    async def import_ratings(self, path: Path, *, external_profile_id: str) -> PersonalImportResult:
        """Import one supported ratings file.

        :param path: Private local CSV path.
        :param external_profile_id: External profile label.
        :return: Provider-independent import result.
        """
        ...


class LibrarySynchronizationWorkflow(Protocol):
    """Retrieve and synchronize a configured personal media library."""

    async def synchronize(self) -> LibrarySynchronizationResult:
        """Synchronize one complete provider snapshot.

        :return: Provider-independent library result.
        """
        ...


class AvailabilityRefreshWorkflow(Protocol):
    """Refresh one resolved regional streaming-availability snapshot."""

    async def refresh(self, candidate: MediaIdentityCandidate, region: str) -> AvailabilityRefreshResult:
        """Refresh availability for one candidate and region.

        :param candidate: Provider-independent identity evidence.
        :param region: Selected ISO country code.
        :return: Provider-independent refresh result.
        """
        ...


class RecommendationWorkflow(Protocol):
    """Execute deterministic recommendations for one internal profile."""

    async def recommend(self, profile_id: ProfileId, criteria: RecommendationCriteria) -> RecommendationResult:
        """Return ranked recommendations for one profile.

        :param profile_id: Internal profile identity.
        :param criteria: Structured hard constraints.
        :return: Ranked explainable recommendations.
        """
        ...


class Phase2Orchestrator:
    """Coordinate provider workflows through existing application boundaries."""

    def __init__(
        self,
        profiles: ProfileRepository,
        netflix: NetflixImportWorkflow,
        library: LibrarySynchronizationWorkflow,
        availability: AvailabilityRefreshWorkflow,
        recommendations: RecommendationWorkflow,
    ) -> None:
        """Initialize all completed Phase 2 workflow boundaries.

        :param profiles: Internal profile persistence boundary.
        :param netflix: Supported local Netflix import integration.
        :param library: Configured local-library synchronization integration.
        :param availability: Regional streaming refresh application service.
        :param recommendations: Deterministic recommendation application service.
        """
        self._profiles = profiles
        self._netflix = netflix
        self._library = library
        self._availability = availability
        self._recommendations = recommendations

    async def synchronize(self, request: Phase2SynchronizationRequest) -> Phase2SynchronizationResult:
        """Run configured sources independently in deterministic order.

        A handled source failure becomes a safe report and does not prevent later
        sources from running or overwrite their previously valid state.

        :param request: Selected private files and regional refresh requests.
        :return: Ordered normalized source reports.
        """
        reports: list[WorkflowReport] = []
        if request.netflix_viewing_path is not None:
            reports.append(await self._import_netflix_viewing(request))
        if request.netflix_ratings_path is not None:
            reports.append(await self._import_netflix_ratings(request))
        reports.append(await self._synchronize_library())
        if request.availability:
            reports.append(await self._refresh_availability(request.availability))
        return Phase2SynchronizationResult(tuple(reports))

    async def synchronize_jellyfin_library(self) -> WorkflowReport:
        """Synchronize the configured Jellyfin library without running other sources.

        This narrow entry point lets interfaces perform a Jellyfin-only
        synchronization without triggering Netflix imports or availability
        refreshes.

        :return: Normalized Jellyfin library report.
        """
        return await self._synchronize_library()

    async def import_netflix_viewing(self, path: Path, *, external_profile_id: str) -> WorkflowReport:
        """Import one Netflix viewing-activity file without running other sources.

        This narrow entry point lets interfaces perform a Netflix-only import
        without triggering library synchronization or availability refresh.

        :param path: Private local CSV path.
        :param external_profile_id: Stable caller-supplied Netflix profile label.
        :return: Normalized Netflix viewing-activity report.
        """
        request = Phase2SynchronizationRequest(
            netflix_profile=external_profile_id,
            netflix_viewing_path=path,
        )
        return await self._import_netflix_viewing(request)

    async def recommend(self, criteria: RecommendationCriteria) -> RecommendationResult:
        """Recommend for the implicit default profile.

        :param criteria: Structured hard constraints and source requirements.
        :return: Ranked factual recommendations for the active single-user profile.
        """
        profile = await self._profiles.get_or_create_default()
        return await self._recommendations.recommend(profile.id, criteria)

    async def _import_netflix_viewing(self, request: Phase2SynchronizationRequest) -> WorkflowReport:
        """Run one selected viewing-history import safely.

        :param request: Synchronization inputs containing the required path and profile.
        :return: Normalized operation report.
        :raises ValueError: If a validated request is unexpectedly incomplete.
        """
        if request.netflix_viewing_path is None or request.netflix_profile is None:
            msg = "Validated viewing import request is incomplete"
            raise ValueError(msg)
        try:
            result = await self._netflix.import_viewing_activity(
                request.netflix_viewing_path,
                external_profile_id=request.netflix_profile,
            )
        except OSError:
            return _failed_report(WorkflowKind.NETFLIX_VIEWING, WorkflowFailureReason.LOCAL_SOURCE_FAILURE)
        return _personal_report(WorkflowKind.NETFLIX_VIEWING, result)

    async def _import_netflix_ratings(self, request: Phase2SynchronizationRequest) -> WorkflowReport:
        """Run one selected ratings import safely.

        :param request: Synchronization inputs containing the required path and profile.
        :return: Normalized operation report.
        :raises ValueError: If a validated request is unexpectedly incomplete.
        """
        if request.netflix_ratings_path is None or request.netflix_profile is None:
            msg = "Validated ratings import request is incomplete"
            raise ValueError(msg)
        try:
            result = await self._netflix.import_ratings(
                request.netflix_ratings_path,
                external_profile_id=request.netflix_profile,
            )
        except OSError:
            return _failed_report(WorkflowKind.NETFLIX_RATINGS, WorkflowFailureReason.LOCAL_SOURCE_FAILURE)
        return _personal_report(WorkflowKind.NETFLIX_RATINGS, result)

    async def _synchronize_library(self) -> WorkflowReport:
        """Run one complete library retrieval without hiding safe source failures.

        :return: Normalized operation report.
        """
        try:
            result = await self._library.synchronize()
        except SourceAuthenticationError:
            return _failed_report(
                WorkflowKind.JELLYFIN_LIBRARY,
                WorkflowFailureReason.PROVIDER_AUTHENTICATION_FAILURE,
            )
        except SourceTransientError:
            return _failed_report(
                WorkflowKind.JELLYFIN_LIBRARY,
                WorkflowFailureReason.PROVIDER_TRANSIENT_FAILURE,
            )
        except SourceWorkflowError:
            return _failed_report(WorkflowKind.JELLYFIN_LIBRARY, WorkflowFailureReason.PROVIDER_FAILURE)
        return _library_report(result)

    async def _refresh_availability(
        self,
        requests: Sequence[AvailabilityRefreshRequest],
    ) -> WorkflowReport:
        """Refresh regional items independently so one source error is isolated.

        :param requests: Ordered availability requests.
        :return: Combined normalized availability report.
        """
        items: list[WorkflowItem] = []
        removed = 0
        for request in sorted(requests, key=lambda item: item.position):
            try:
                result = await self._availability.refresh(request.candidate, request.region)
            except SourceWorkflowError:
                items.append(
                    WorkflowItem(
                        request.position,
                        WorkflowItemStatus.FAILED,
                        WorkflowFailureReason.PROVIDER_FAILURE.value,
                    )
                )
                continue
            items.append(_availability_item(request.position, result))
            removed += result.removed
        return _report(WorkflowKind.STREAMING_AVAILABILITY, tuple(items), removed=removed)


def _personal_report(kind: WorkflowKind, result: PersonalImportResult) -> WorkflowReport:
    """Normalize a personal import result.

    :param kind: Specific personal import operation.
    :param result: Provider-independent import result.
    :return: Unified workflow report.
    """
    status_mapping = {
        ImportRecordStatus.IMPORTED: WorkflowItemStatus.IMPORTED,
        ImportRecordStatus.SKIPPED: WorkflowItemStatus.SKIPPED,
        ImportRecordStatus.UNRESOLVED: WorkflowItemStatus.UNRESOLVED,
        ImportRecordStatus.AMBIGUOUS: WorkflowItemStatus.AMBIGUOUS,
        ImportRecordStatus.INVALID: WorkflowItemStatus.INVALID,
    }
    items = tuple(
        WorkflowItem(record.row_number, status_mapping[record.status], record.reason, record.candidate_ids)
        for record in result.records
    )
    return _report(kind, items)


def _library_report(result: LibrarySynchronizationResult) -> WorkflowReport:
    """Normalize a complete library synchronization result.

    :param result: Provider-independent library result.
    :return: Unified workflow report.
    """
    status_mapping = {
        LibraryItemStatus.SYNCHRONIZED: WorkflowItemStatus.SYNCHRONIZED,
        LibraryItemStatus.UNRESOLVED: WorkflowItemStatus.UNRESOLVED,
        LibraryItemStatus.AMBIGUOUS: WorkflowItemStatus.AMBIGUOUS,
        LibraryItemStatus.INVALID: WorkflowItemStatus.INVALID,
    }
    items = tuple(
        WorkflowItem(record.position, status_mapping[record.status], record.reason, record.candidate_ids)
        for record in result.records
    )
    return _report(WorkflowKind.JELLYFIN_LIBRARY, items, removed=result.removed)


def _availability_item(position: int, result: AvailabilityRefreshResult) -> WorkflowItem:
    """Normalize one availability refresh result.

    :param position: Stable request position.
    :param result: Provider-independent refresh result.
    :return: Unified item result.
    """
    status_mapping = {
        AvailabilityRefreshStatus.REFRESHED: WorkflowItemStatus.REFRESHED,
        AvailabilityRefreshStatus.UNRESOLVED: WorkflowItemStatus.UNRESOLVED,
        AvailabilityRefreshStatus.AMBIGUOUS: WorkflowItemStatus.AMBIGUOUS,
        AvailabilityRefreshStatus.SOURCE_ID_MISSING: WorkflowItemStatus.SOURCE_ID_MISSING,
    }
    return WorkflowItem(position, status_mapping[result.status], result.reason, result.candidate_ids)


def _report(kind: WorkflowKind, items: tuple[WorkflowItem, ...], *, removed: int = 0) -> WorkflowReport:
    """Build aggregate counts and status for normalized items.

    :param kind: Source operation kind.
    :param items: Normalized item results.
    :param removed: Records removed by a complete snapshot.
    :return: Complete workflow report.
    """
    successful = {WorkflowItemStatus.IMPORTED, WorkflowItemStatus.SYNCHRONIZED, WorkflowItemStatus.REFRESHED}
    counts = WorkflowCounts(
        succeeded=sum(item.status in successful for item in items),
        skipped=sum(item.status is WorkflowItemStatus.SKIPPED for item in items),
        unresolved=sum(
            item.status in {WorkflowItemStatus.UNRESOLVED, WorkflowItemStatus.SOURCE_ID_MISSING} for item in items
        ),
        ambiguous=sum(item.status is WorkflowItemStatus.AMBIGUOUS for item in items),
        invalid=sum(item.status is WorkflowItemStatus.INVALID for item in items),
        failed=sum(item.status is WorkflowItemStatus.FAILED for item in items),
        removed=removed,
    )
    problems = counts.unresolved + counts.ambiguous + counts.invalid + counts.failed
    if counts.failed and counts.succeeded == 0 and counts.skipped == 0:
        status = WorkflowStatus.FAILED
    elif problems:
        status = WorkflowStatus.PARTIAL
    else:
        status = WorkflowStatus.SUCCESS
    return WorkflowReport(kind, status, counts, items)


def _failed_report(kind: WorkflowKind, reason: WorkflowFailureReason) -> WorkflowReport:
    """Return one privacy-safe failed source report.

    :param kind: Failed operation kind.
    :param reason: Sanitized failure classification.
    :return: Failed workflow report.
    """
    return _report(kind, (WorkflowItem(1, WorkflowItemStatus.FAILED, reason.value),))
