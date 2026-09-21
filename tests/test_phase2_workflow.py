"""Offline end-to-end validation of the complete Phase 2 workflow."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config

from media_recommender.application import (
    AvailabilityCriterion,
    AvailabilityOffer,
    AvailabilityRefreshRequest,
    AvailabilityRefreshResult,
    AvailabilityRefreshService,
    AvailabilityRefreshStatus,
    AvailabilitySourceKind,
    ImportRecordResult,
    ImportRecordStatus,
    LibraryItemResult,
    LibraryItemStatus,
    LibrarySynchronizationResult,
    MediaIdentityCandidate,
    MediaIdentityResolver,
    PersonalImportResult,
    Phase2Orchestrator,
    Phase2SynchronizationRequest,
    ProductionRegion,
    RecommendationCriteria,
    RecommendationResult,
    RecommendationService,
    SourceWorkflowError,
    WatchRequirement,
    WorkflowFailureReason,
    WorkflowItemStatus,
    WorkflowKind,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.application.imports import PersonalMediaImportService
from media_recommender.application.library import LibrarySynchronizationService
from media_recommender.config import Settings
from media_recommender.domain import (
    AvailabilityType,
    Country,
    ExternalId,
    Genre,
    MediaId,
    MediaType,
    Movie,
    Preference,
    PreferenceEffect,
    PreferenceId,
    PreferenceKind,
    Profile,
    ProfileId,
    Runtime,
    StreamingService,
    TVShow,
)
from media_recommender.integrations import ProviderAuthenticationError, ProviderUnavailableError
from media_recommender.integrations.jellyfin import JellyfinLibrarySynchronizer
from media_recommender.integrations.jellyfin.models import JellyfinItemsResponse, JellyfinUser
from media_recommender.integrations.netflix import NetflixFileImporter
from media_recommender.persistence import (
    SqlAlchemyAvailabilityRepository,
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    SqlAlchemyRecommendationDataSource,
    create_engine,
    create_session_factory,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from media_recommender.domain import Media
    from media_recommender.persistence.database import SessionFactory

SYNTHETIC_NOW = datetime(2026, 9, 14, 20, tzinfo=UTC)
PHASE_ONE_REVISION = "14fde284fd0d"
EXPECTED_AVAILABILITY_REFRESHES = 3
EXPECTED_AVAILABILITY_TARGETS = 3
EXPECTED_AMBIGUOUS_CANDIDATES = 2
EXPECTED_PROVIDER_MAPPINGS = 2
EXPECTED_REPEAT_REFRESHES = 2


class SyntheticIdentityEnricher:
    """Add deterministic synthetic metadata to sparse Netflix title evidence."""

    def __init__(self, years: dict[str, int]) -> None:
        """Store release years keyed by synthetic title."""
        self._years = years

    async def enrich(self, candidate: MediaIdentityCandidate) -> MediaIdentityCandidate:
        """Return candidate evidence with a configured release year."""
        year = self._years.get(candidate.title)
        return replace(candidate, release_year=year) if year is not None else candidate


class SyntheticJellyfinClient:
    """Return a complete synthetic library or one transient provider failure."""

    def __init__(self, items: list[dict[str, object]]) -> None:
        """Store the current synthetic library snapshot."""
        self.items = items
        self.fail = False

    async def validate_user(self) -> JellyfinUser:
        """Return one selected user unless failure is enabled."""
        if self.fail:
            raise ProviderUnavailableError
        return JellyfinUser.model_validate({"Id": "synthetic-user", "Name": "Synthetic User"})

    async def get_library_items(self) -> JellyfinItemsResponse:
        """Return the current complete synthetic item response."""
        return JellyfinItemsResponse.model_validate({"Items": self.items})


class SyntheticAvailabilityProvider:
    """Return Netflix subscription availability with selectable transient failures."""

    source = "tmdb"
    attribution: str | None = "JustWatch"

    def __init__(self) -> None:
        """Initialize with no failing TMDB identities."""
        self.failing_ids: set[str] = set()

    async def get_availability(
        self,
        external_id: ExternalId,
        media_type: MediaType,
        region: str,
    ) -> Sequence[AvailabilityOffer]:
        """Return one normalized offer or simulate a transient provider failure."""
        del media_type, region
        if external_id.value in self.failing_ids:
            raise ProviderUnavailableError
        return (AvailabilityOffer(StreamingService("8", "Netflix"), AvailabilityType.SUBSCRIPTION),)


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary SQLite database."""
    config = Config(Path(__file__).parents[1] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _movie(  # noqa: PLR0913 - mirrors the catalog metadata varied by the end-to-end scenario.
    title: str,
    tmdb_id: str,
    *,
    year: int,
    runtime: int,
    genres: tuple[str, ...] = ("Science Fiction",),
    country: str = "US",
) -> Movie:
    """Build one fully normalized synthetic catalog movie."""
    country_names = {"US": "United States", "FR": "France", "JP": "Japan"}
    return Movie(
        id=MediaId.new(),
        title=title,
        released_on=date(year, 1, 1),
        runtime=Runtime(runtime),
        genres=tuple(Genre(genre) for genre in genres),
        production_countries=(Country(country, country_names[country]),),
        external_ids=frozenset({ExternalId("tmdb", tmdb_id)}),
    )


async def _build_orchestrator(
    database_path: Path,
) -> tuple[
    AsyncEngine,
    SessionFactory,
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    SyntheticJellyfinClient,
    SyntheticAvailabilityProvider,
    Phase2Orchestrator,
    dict[str, Movie],
]:
    """Create all real application services over a migrated Phase 1 database."""
    engine = create_engine(Settings(database_path=database_path))
    sessions = create_session_factory(engine)
    catalog = SqlAlchemyMediaCatalog(sessions)
    personal = SqlAlchemyPersonalMediaRepository(sessions)
    availability_repository = SqlAlchemyAvailabilityRepository(sessions)
    movies = {
        "local": _movie("Local Short", "1", year=2025, runtime=95, country="FR"),
        "netflix": _movie("Netflix Long", "2", year=2024, runtime=140),
        "watched": _movie("Watched Movie", "3", year=2023, runtime=100),
        "excluded": _movie(
            "Excluded Horror",
            "4",
            year=2024,
            runtime=80,
            genres=("Science Fiction", "Horror"),
            country="JP",
        ),
        "ambiguous_one": _movie("Ambiguous Movie", "5", year=2020, runtime=100),
        "ambiguous_two": _movie("Ambiguous Movie", "6", year=2020, runtime=100),
    }
    for movie in movies.values():
        await catalog.save(movie)

    resolver = MediaIdentityResolver(
        catalog,
        enricher=SyntheticIdentityEnricher(
            {
                "Local Short": 2025,
                "Watched Movie": 2023,
                "Ambiguous Movie": 2020,
            }
        ),
    )
    netflix = NetflixFileImporter(PersonalMediaImportService(personal, personal, resolver, clock=lambda: SYNTHETIC_NOW))
    jellyfin_client = SyntheticJellyfinClient(
        [
            {
                "Id": "local-short",
                "Name": "Local Short",
                "Type": "Movie",
                "ProviderIds": {"Tmdb": "1"},
                "UserData": {"Played": False, "PlayCount": 0},
            },
            {
                "Id": "unresolved",
                "Name": "Unknown Library Movie",
                "Type": "Movie",
                "ProductionYear": 2022,
            },
            {"Id": "invalid", "Name": "Missing media type"},
        ]
    )
    library = JellyfinLibrarySynchronizer(
        jellyfin_client,
        LibrarySynchronizationService(personal, personal, resolver, clock=lambda: SYNTHETIC_NOW),
        synchronization_id_factory=lambda: "synthetic-jellyfin-snapshot",
    )
    availability_provider = SyntheticAvailabilityProvider()
    availability = AvailabilityRefreshService(
        resolver,
        availability_provider,
        availability_repository,
        clock=lambda: SYNTHETIC_NOW,
    )
    recommendations = RecommendationService(SqlAlchemyRecommendationDataSource(sessions))
    orchestrator = Phase2Orchestrator(personal, netflix, library, availability, recommendations, catalog=catalog)
    return (
        engine,
        sessions,
        catalog,
        personal,
        jellyfin_client,
        availability_provider,
        orchestrator,
        movies,
    )


def _write_netflix_files(directory: Path) -> tuple[Path, Path]:
    """Write private synthetic Netflix inputs into the temporary test directory."""
    viewing = directory / "NetflixViewingHistory.csv"
    viewing.write_text(
        "Title,Date\nWatched Movie,9/14/26\nAmbiguous Movie,9/13/26\n",
        encoding="utf-8",
    )
    ratings = directory / "Ratings.csv"
    ratings.write_text(
        "Profile Name,Title Name,Rating Type,Star Value,Thumbs Value,Event Utc Ts,Region View Date\n"
        "Synthetic profile,Local Short,Thumbs,0,2,2026-09-14T12:00:00Z,9/14/26\n",
        encoding="utf-8",
    )
    return viewing, ratings


def _refresh_requests(movies: dict[str, Movie]) -> tuple[AvailabilityRefreshRequest, ...]:
    """Return the regional synthetic availability batch."""
    return tuple(
        AvailabilityRefreshRequest(
            position=position,
            candidate=MediaIdentityCandidate(
                media_type=MediaType.MOVIE,
                title=movies[key].title,
                external_ids=frozenset({next(iter(movies[key].external_ids))}),
            ),
            region="CZ",
        )
        for position, key in enumerate(("netflix", "watched", "excluded"), start=1)
    )


def _report(result: Sequence[WorkflowReport], kind: WorkflowKind) -> WorkflowReport:
    """Return one report by operation kind."""
    return next(report for report in result if report.kind is kind)


async def _exercise_phase2_workflow(database_path: Path, temporary_directory: Path) -> None:
    """Validate all Phase 2 workflows, repetition, failure isolation, and recommendations."""
    (
        engine,
        _sessions,
        _catalog,
        personal,
        jellyfin_client,
        availability_provider,
        orchestrator,
        movies,
    ) = await _build_orchestrator(database_path)
    viewing_path, ratings_path = _write_netflix_files(temporary_directory)
    request = Phase2SynchronizationRequest(
        netflix_profile="Synthetic profile",
        netflix_viewing_path=viewing_path,
        netflix_ratings_path=ratings_path,
        availability=_refresh_requests(movies),
    )

    first = await orchestrator.synchronize(request)
    second = await orchestrator.synchronize(request)
    first_viewing = _report(first.reports, WorkflowKind.NETFLIX_VIEWING)
    first_library = _report(first.reports, WorkflowKind.JELLYFIN_LIBRARY)
    first_availability = _report(first.reports, WorkflowKind.STREAMING_AVAILABILITY)
    second_viewing = _report(second.reports, WorkflowKind.NETFLIX_VIEWING)

    assert first.status is WorkflowStatus.PARTIAL
    assert (first_viewing.counts.succeeded, first_viewing.counts.ambiguous) == (1, 1)
    assert any(
        item.status is WorkflowItemStatus.AMBIGUOUS and len(item.candidate_ids) == EXPECTED_AMBIGUOUS_CANDIDATES
        for item in first_viewing.items
    )
    assert (first_library.counts.succeeded, first_library.counts.unresolved, first_library.counts.invalid) == (1, 1, 1)
    assert first_availability.counts.succeeded == EXPECTED_AVAILABILITY_REFRESHES
    assert second_viewing.counts.skipped == 1

    missing_file = await orchestrator.synchronize(
        Phase2SynchronizationRequest(
            netflix_profile="Synthetic profile",
            netflix_viewing_path=temporary_directory / "missing.csv",
        )
    )
    missing_file_report = _report(missing_file.reports, WorkflowKind.NETFLIX_VIEWING)
    assert missing_file.status is WorkflowStatus.PARTIAL
    assert missing_file_report.status is WorkflowStatus.FAILED
    assert missing_file_report.counts.failed == 1

    profile = await personal.get_or_create_default()
    assert profile.is_default
    assert len(await personal.list_viewing_events(profile.id, movies["watched"].id)) == 1
    assert len(await personal.list_ratings(profile.id, movies["local"].id)) == 1
    assert len(await personal.list_provider_mappings(profile.id)) == EXPECTED_PROVIDER_MAPPINGS
    await personal.save_preference(
        Preference(
            id=PreferenceId.new(),
            profile_id=profile.id,
            kind=PreferenceKind.RUNTIME_MINUTES,
            effect=PreferenceEffect.PREFER,
            maximum=100,
        )
    )
    await personal.save_preference(
        Preference(
            id=PreferenceId.new(),
            profile_id=profile.id,
            kind=PreferenceKind.PROVIDER,
            effect=PreferenceEffect.PREFER,
            value="Jellyfin",
        )
    )

    criteria = RecommendationCriteria(
        media_types=frozenset({MediaType.MOVIE}),
        include_genres=frozenset({"Science Fiction"}),
        exclude_genres=frozenset({"Horror", "Comedy"}),
        exclude_regions=frozenset({ProductionRegion.AFRICA, ProductionRegion.ASIA, ProductionRegion.SOUTH_AMERICA}),
        maximum_runtime_minutes=150,
        watch=WatchRequirement.NOT_WATCHED,
        availability_any_of=(
            AvailabilityCriterion(kind=AvailabilitySourceKind.STREAMING, provider="Netflix", region="CZ"),
            AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider="Jellyfin"),
        ),
    )
    recommendation = await orchestrator.recommend(criteria)
    assert tuple((item.media.title, item.score) for item in recommendation.recommendations) == (
        ("Local Short", 230),
        ("Netflix Long", 0),
    )
    assert all(item.matched_constraints and item.availability for item in recommendation.recommendations)

    jellyfin_client.fail = True
    availability_provider.failing_ids.add("2")
    failed = await orchestrator.synchronize(request)
    failed_library = _report(failed.reports, WorkflowKind.JELLYFIN_LIBRARY)
    failed_availability = _report(failed.reports, WorkflowKind.STREAMING_AVAILABILITY)
    assert failed.status is WorkflowStatus.PARTIAL
    assert failed_library.status is WorkflowStatus.FAILED
    assert failed_availability.status is WorkflowStatus.PARTIAL
    assert failed_availability.counts.failed == 1

    after_failure = await orchestrator.recommend(criteria)
    assert tuple(item.media.title for item in after_failure.recommendations) == ("Local Short", "Netflix Long")
    assert any(item.media.title == "Netflix Long" and item.availability for item in after_failure.recommendations)
    await engine.dispose()


def test_phase2_workflow_is_offline_repeatable_and_failure_isolated(tmp_path: Path) -> None:
    """Verify the complete Phase 2 flow from a Phase 1 migration to recommendations."""
    database_path = tmp_path / "phase2.db"
    config = _alembic_config(database_path)
    command.upgrade(config, PHASE_ONE_REVISION)
    command.upgrade(config, "head")
    asyncio.run(_exercise_phase2_workflow(database_path, tmp_path))


class _RecordingNetflixWorkflow:
    """Record Netflix-only facade calls and return a synthetic result."""

    def __init__(self, result: PersonalImportResult) -> None:
        """Store the synthetic import result.

        :param result: Result returned by the viewing-activity import.
        """
        self._result = result
        self.calls: list[tuple[Path, str]] = []

    async def import_viewing_activity(self, path: Path, *, external_profile_id: str) -> PersonalImportResult:
        """Record one viewing-activity import call.

        :param path: Staged private CSV path.
        :param external_profile_id: Requested Netflix profile label.
        :return: Configured synthetic result.
        """
        self.calls.append((path, external_profile_id))
        return self._result

    async def import_ratings(self, path: Path, *, external_profile_id: str) -> PersonalImportResult:
        """Fail if the ratings import is invoked.

        :param path: Staged private CSV path.
        :param external_profile_id: Requested Netflix profile label.
        :raises AssertionError: Always, because ratings are out of scope.
        """
        del path, external_profile_id
        raise AssertionError


class _ForbiddenProfiles:
    """Fail if profile persistence is invoked."""

    async def get_or_create_default(self) -> Profile:
        """Fail if the default profile is requested.

        :raises AssertionError: Always, because the Netflix-only facade must not touch profiles.
        """
        raise AssertionError

    async def get_profile(self, profile_id: ProfileId) -> Profile | None:
        """Fail if a profile is requested.

        :param profile_id: Requested internal profile identity.
        :raises AssertionError: Always, because the Netflix-only facade must not touch profiles.
        """
        del profile_id
        raise AssertionError

    async def save_profile(self, profile: Profile) -> None:
        """Fail if a profile is saved.

        :param profile: Profile that would be persisted.
        :raises AssertionError: Always, because the Netflix-only facade must not touch profiles.
        """
        del profile
        raise AssertionError


class _ForbiddenLibrary:
    """Fail if library synchronization is invoked."""

    async def synchronize(self) -> LibrarySynchronizationResult:
        """Fail if library synchronization is invoked.

        :raises AssertionError: Always, because the Netflix-only facade must not synchronize.
        """
        raise AssertionError


class _ForbiddenAvailability:
    """Fail if availability refresh is invoked."""

    async def refresh(self, candidate: MediaIdentityCandidate, region: str) -> AvailabilityRefreshResult:
        """Fail if availability refresh is invoked.

        :param candidate: Identity evidence that would be refreshed.
        :param region: Region that would be refreshed.
        :raises AssertionError: Always, because the Netflix-only facade must not refresh availability.
        """
        del candidate, region
        raise AssertionError


class _ForbiddenRecommendations:
    """Fail if recommendation is invoked."""

    async def recommend(self, profile_id: ProfileId, criteria: RecommendationCriteria) -> RecommendationResult:
        """Fail if recommendation is invoked.

        :param profile_id: Profile that would be recommended for.
        :param criteria: Criteria that would be applied.
        :raises AssertionError: Always, because the Netflix-only facade must not recommend.
        """
        del profile_id, criteria
        raise AssertionError


def test_netflix_only_facade_imports_viewing_without_other_sources(tmp_path: Path) -> None:
    """Verify the narrow facade runs only the Netflix viewing import."""
    source = tmp_path / "synthetic-viewing.csv"
    source.write_text("Title,Date\nSynthetic Title,9/14/26\n", encoding="utf-8")
    netflix = _RecordingNetflixWorkflow(
        PersonalImportResult(records=(ImportRecordResult(2, ImportRecordStatus.IMPORTED),))
    )
    orchestrator = Phase2Orchestrator(
        _ForbiddenProfiles(),
        netflix,
        _ForbiddenLibrary(),
        _ForbiddenAvailability(),
        _ForbiddenRecommendations(),
    )

    report = asyncio.run(orchestrator.import_netflix_viewing(source, external_profile_id="Synthetic profile"))

    assert report.kind is WorkflowKind.NETFLIX_VIEWING
    assert report.status is WorkflowStatus.SUCCESS
    assert report.counts.succeeded == 1
    assert netflix.calls == [(source, "Synthetic profile")]


class _RecordingLibrary:
    """Record Jellyfin-only facade calls and return a synthetic result."""

    def __init__(self, result: LibrarySynchronizationResult) -> None:
        """Store the synthetic library result.

        :param result: Result returned by the library synchronization.
        """
        self._result = result
        self.calls = 0

    async def synchronize(self) -> LibrarySynchronizationResult:
        """Record one library synchronization call.

        :return: Configured synthetic result.
        """
        self.calls += 1
        return self._result


class _FailingLibrary:
    """Raise one configured safe source failure."""

    def __init__(self, error: SourceWorkflowError) -> None:
        """Store the synthetic failure.

        :param error: Exception raised by the library synchronization.
        """
        self._error = error

    async def synchronize(self) -> LibrarySynchronizationResult:
        """Raise the configured synthetic failure.

        :raises SourceWorkflowError: The configured synthetic error.
        """
        raise self._error


def _jellyfin_orchestrator(
    library: _RecordingLibrary | _FailingLibrary,
) -> Phase2Orchestrator:
    """Build an orchestrator with only the library workflow enabled.

    :param library: Synthetic library workflow used by the Jellyfin facade.
    :return: Orchestrator whose non-Jellyfin sources fail if invoked.
    """
    return Phase2Orchestrator(
        _ForbiddenProfiles(),
        _RecordingNetflixWorkflow(PersonalImportResult(records=())),
        library,
        _ForbiddenAvailability(),
        _ForbiddenRecommendations(),
    )


def test_jellyfin_only_facade_synchronizes_library_without_other_sources() -> None:
    """Verify the narrow facade runs only the Jellyfin library synchronization."""
    library = _RecordingLibrary(
        LibrarySynchronizationResult(
            records=(
                LibraryItemResult(1, LibraryItemStatus.SYNCHRONIZED),
                LibraryItemResult(2, LibraryItemStatus.UNRESOLVED),
            ),
            removed=1,
        )
    )
    orchestrator = _jellyfin_orchestrator(library)

    report = asyncio.run(orchestrator.synchronize_jellyfin_library())

    assert report.kind is WorkflowKind.JELLYFIN_LIBRARY
    assert report.status is WorkflowStatus.PARTIAL
    assert library.calls == 1
    assert (report.counts.succeeded, report.counts.unresolved, report.counts.removed) == (1, 1, 1)


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (ProviderAuthenticationError(), WorkflowFailureReason.AUTHENTICATION_FAILURE),
        (ProviderUnavailableError(), WorkflowFailureReason.TRANSIENT_FAILURE),
        (SourceWorkflowError("synthetic provider failure"), WorkflowFailureReason.PROVIDER_FAILURE),
    ],
)
def test_jellyfin_only_facade_classifies_safe_provider_failures(
    error: SourceWorkflowError,
    expected_reason: WorkflowFailureReason,
) -> None:
    """Map typed provider failures to distinct safe reasons without raw text."""
    orchestrator = _jellyfin_orchestrator(_FailingLibrary(error))

    report = asyncio.run(orchestrator.synchronize_jellyfin_library())

    assert report.status is WorkflowStatus.FAILED
    assert report.counts.failed == 1
    assert report.items[0].reason == expected_reason.value


class _FakeCatalogReader:
    """Return configured synthetic media by type in a stable order."""

    def __init__(self, media_by_type: dict[MediaType, list[Media]]) -> None:
        """Store synthetic media keyed by media type.

        :param media_by_type: Synthetic catalog items per media type.
        """
        self._media_by_type = media_by_type

    async def get(self, media_id: MediaId) -> Media | None:
        """Return no item because the availability facade never reads by identity.

        :param media_id: Requested internal identity.
        :return: Always ``None``.
        """
        del media_id
        return None

    async def find_by_external_id(self, external_id: ExternalId) -> Media | None:
        """Return no item because the availability facade never reads by external identity.

        :param external_id: Requested external identity.
        :return: Always ``None``.
        """
        del external_id
        return None

    async def iter_by_type(self, media_type: MediaType) -> AsyncIterator[Media]:
        """Yield configured synthetic media for one type.

        :param media_type: Requested kind of media.
        :yield: Configured synthetic media items.
        """
        for media in self._media_by_type.get(media_type, []):
            yield media


class _RecordingAvailability:
    """Record availability refresh calls and return configured results."""

    def __init__(self, results: Sequence[AvailabilityRefreshResult] | None = None) -> None:
        """Store the ordered synthetic results.

        :param results: Results returned in call order; a refreshed result is used when exhausted.
        """
        self._results = list(results or [])
        self.calls: list[tuple[MediaIdentityCandidate, str]] = []

    async def refresh(self, candidate: MediaIdentityCandidate, region: str) -> AvailabilityRefreshResult:
        """Record one refresh call and return the next configured result.

        :param candidate: Identity evidence being refreshed.
        :param region: Requested region.
        :return: Configured synthetic refresh result.
        """
        self.calls.append((candidate, region))
        if self._results:
            return self._results.pop(0)
        return AvailabilityRefreshResult(AvailabilityRefreshStatus.REFRESHED, available=1)


class _SelectiveAvailability:
    """Fail configured titles and refresh the rest."""

    def __init__(self, *, failing_titles: set[str], error: SourceWorkflowError) -> None:
        """Store the failing titles and the failure to raise.

        :param failing_titles: Titles that raise the configured failure.
        :param error: Safe source failure raised for failing titles.
        """
        self._failing_titles = failing_titles
        self._error = error
        self.calls: list[str] = []

    async def refresh(self, candidate: MediaIdentityCandidate, region: str) -> AvailabilityRefreshResult:
        """Record one refresh call and fail or succeed by title.

        :param candidate: Identity evidence being refreshed.
        :param region: Requested region.
        :return: Refreshed result for non-failing titles.
        :raises SourceWorkflowError: The configured failure for failing titles.
        """
        del region
        self.calls.append(candidate.title)
        if candidate.title in self._failing_titles:
            raise self._error
        return AvailabilityRefreshResult(AvailabilityRefreshStatus.REFRESHED, available=1, removed=1)


class _FailingAvailability:
    """Raise one configured safe source failure for every refresh."""

    def __init__(self, error: SourceWorkflowError) -> None:
        """Store the synthetic failure.

        :param error: Exception raised by every refresh.
        """
        self._error = error
        self.calls = 0

    async def refresh(self, candidate: MediaIdentityCandidate, region: str) -> AvailabilityRefreshResult:
        """Raise the configured synthetic failure.

        :param candidate: Identity evidence that would be refreshed.
        :param region: Region that would be refreshed.
        :raises SourceWorkflowError: The configured synthetic error.
        """
        del candidate, region
        self.calls += 1
        raise self._error


def _tv_show(title: str, tmdb_id: str, *, year: int, runtime: int) -> TVShow:
    """Build one fully normalized synthetic catalog TV show.

    :param title: Synthetic show title.
    :param tmdb_id: Synthetic TMDB identity value.
    :param year: Synthetic first-air year.
    :param runtime: Synthetic episode runtime in minutes.
    :return: Synthetic catalog TV show.
    """
    return TVShow(
        id=MediaId.new(),
        title=title,
        first_aired_on=date(year, 1, 1),
        episode_runtime=Runtime(runtime),
        external_ids=frozenset({ExternalId("tmdb", tmdb_id)}),
    )


def _availability_orchestrator(
    availability: _RecordingAvailability | _SelectiveAvailability | _FailingAvailability,
    catalog: _FakeCatalogReader,
) -> Phase2Orchestrator:
    """Build an orchestrator with only the availability workflow enabled.

    :param availability: Synthetic availability workflow used by the facade.
    :param catalog: Synthetic shared catalog reader.
    :return: Orchestrator whose non-availability sources fail if invoked.
    """
    return Phase2Orchestrator(
        _ForbiddenProfiles(),
        _RecordingNetflixWorkflow(PersonalImportResult(records=())),
        _ForbiddenLibrary(),
        availability,
        _ForbiddenRecommendations(),
        catalog=catalog,
    )


def test_availability_facade_enumerates_catalog_targets_in_stable_order() -> None:
    """Enumerate movies before TV shows and refresh each with the configured region."""
    catalog = _FakeCatalogReader(
        {
            MediaType.MOVIE: [
                _movie("Alpha", "1", year=2020, runtime=90),
                _movie("Beta", "2", year=2021, runtime=100),
            ],
            MediaType.TV_SHOW: [_tv_show("Gamma", "3", year=2019, runtime=45)],
        }
    )
    availability = _RecordingAvailability()
    orchestrator = _availability_orchestrator(availability, catalog)

    report = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))

    assert report.kind is WorkflowKind.STREAMING_AVAILABILITY
    assert report.status is WorkflowStatus.SUCCESS
    assert [candidate.title for candidate, _ in availability.calls] == ["Alpha", "Beta", "Gamma"]
    assert [region for _, region in availability.calls] == ["CZ", "CZ", "CZ"]
    assert report.counts.succeeded == EXPECTED_AVAILABILITY_TARGETS


def test_availability_facade_includes_items_without_tmdb_identity() -> None:
    """Include catalog items without TMDB identity and report them as unresolved."""
    movie = Movie(id=MediaId.new(), title="No Identity", released_on=date(2020, 1, 1), runtime=Runtime(90))
    catalog = _FakeCatalogReader({MediaType.MOVIE: [movie]})
    availability = _RecordingAvailability(
        [AvailabilityRefreshResult(AvailabilityRefreshStatus.SOURCE_ID_MISSING, reason="Missing tmdb media identity")]
    )
    orchestrator = _availability_orchestrator(availability, catalog)

    report = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))

    assert len(availability.calls) == 1
    assert report.counts.unresolved == 1
    assert report.status is WorkflowStatus.PARTIAL


def test_availability_facade_reports_invalid_identity_evidence_as_unresolved() -> None:
    """Report a catalog item whose evidence cannot form a candidate as unresolved."""
    movie = Movie(id=MediaId.new(), title="...", released_on=date(2020, 1, 1), runtime=Runtime(90))
    catalog = _FakeCatalogReader({MediaType.MOVIE: [movie]})
    availability = _RecordingAvailability()
    orchestrator = _availability_orchestrator(availability, catalog)

    report = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))

    assert availability.calls == []
    assert report.counts.unresolved == 1
    assert report.items[0].status is WorkflowItemStatus.UNRESOLVED
    assert report.status is WorkflowStatus.PARTIAL


def test_availability_facade_reports_partial_when_some_items_fail() -> None:
    """Report partial status and only successful removed facts when one item fails."""
    catalog = _FakeCatalogReader(
        {
            MediaType.MOVIE: [
                _movie("Alpha", "1", year=2020, runtime=90),
                _movie("Beta", "2", year=2021, runtime=100),
            ]
        }
    )
    availability = _SelectiveAvailability(failing_titles={"Beta"}, error=ProviderUnavailableError())
    orchestrator = _availability_orchestrator(availability, catalog)

    report = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))

    assert report.status is WorkflowStatus.PARTIAL
    assert (report.counts.succeeded, report.counts.failed, report.counts.removed) == (1, 1, 1)
    failed_items = [item for item in report.items if item.status is WorkflowItemStatus.FAILED]
    assert failed_items[0].reason == WorkflowFailureReason.TRANSIENT_FAILURE.value


@pytest.mark.parametrize(
    ("error", "expected_reason"),
    [
        (ProviderAuthenticationError(), WorkflowFailureReason.AUTHENTICATION_FAILURE),
        (ProviderUnavailableError(), WorkflowFailureReason.TRANSIENT_FAILURE),
        (SourceWorkflowError("synthetic provider failure"), WorkflowFailureReason.PROVIDER_FAILURE),
    ],
)
def test_availability_facade_classifies_safe_provider_failures(
    error: SourceWorkflowError,
    expected_reason: WorkflowFailureReason,
) -> None:
    """Map typed provider failures to distinct safe reasons without raw text."""
    catalog = _FakeCatalogReader({MediaType.MOVIE: [_movie("Alpha", "1", year=2020, runtime=90)]})
    availability = _FailingAvailability(error)
    orchestrator = _availability_orchestrator(availability, catalog)

    report = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))

    assert report.status is WorkflowStatus.FAILED
    assert report.counts.failed == 1
    assert report.items[0].reason == expected_reason.value


def test_availability_facade_repeat_is_a_new_snapshot() -> None:
    """Treat each explicit facade call as a new refresh rather than an automatic retry."""
    catalog = _FakeCatalogReader({MediaType.MOVIE: [_movie("Alpha", "1", year=2020, runtime=90)]})
    availability = _RecordingAvailability()
    orchestrator = _availability_orchestrator(availability, catalog)

    first = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))
    second = asyncio.run(orchestrator.refresh_streaming_availability("CZ"))

    assert first.counts.succeeded == 1
    assert second.counts.succeeded == 1
    assert len(availability.calls) == EXPECTED_REPEAT_REFRESHES


def test_availability_facade_requires_catalog_reader() -> None:
    """Reject an availability refresh when no shared catalog reader is configured."""
    orchestrator = Phase2Orchestrator(
        _ForbiddenProfiles(),
        _RecordingNetflixWorkflow(PersonalImportResult(records=())),
        _ForbiddenLibrary(),
        _ForbiddenAvailability(),
        _ForbiddenRecommendations(),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(orchestrator.refresh_streaming_availability("CZ"))
