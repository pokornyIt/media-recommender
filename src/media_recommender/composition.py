"""Production composition root wiring application services for the Web interface.

The composition root constructs the database engine, repositories, application
services, and the Phase 2 orchestration facade once per process. Provider-backed
workflows are constructed only when their provider configuration validates, so
an unconfigured provider never prevents startup and never surfaces as an
unexpected server error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError
from pydantic_settings import SettingsError

from media_recommender.application import (
    AvailabilityRefreshService,
    CatalogService,
    LibrarySynchronizationService,
    MediaDetailService,
    MediaIdentityResolver,
    PersonalMediaImportService,
    Phase2Orchestrator,
    ProviderOperationRecorder,
    RecommendationService,
)
from media_recommender.config import JellyfinSettings, Settings, TmdbSettings
from media_recommender.integrations.jellyfin.client import JellyfinClient
from media_recommender.integrations.jellyfin.synchronizer import JellyfinLibrarySynchronizer
from media_recommender.integrations.netflix.importer import NetflixFileImporter
from media_recommender.integrations.tmdb.client import TmdbMetadataProvider
from media_recommender.persistence.availability_repository import SqlAlchemyAvailabilityRepository
from media_recommender.persistence.database import create_engine, create_session_factory
from media_recommender.persistence.migrations import check_readiness, upgrade_to_head
from media_recommender.persistence.personal_repository import SqlAlchemyPersonalMediaRepository
from media_recommender.persistence.recommendation_repository import SqlAlchemyRecommendationDataSource
from media_recommender.persistence.repository import SqlAlchemyMediaCatalog

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine

    from media_recommender.application import (
        AvailabilityRefreshWorkflow,
        LibrarySynchronizationWorkflow,
        MetadataProvider,
    )
    from media_recommender.application.readiness import ReadinessProbe, ReadinessReport
    from media_recommender.persistence.database import SessionFactory


class AsyncClosable(Protocol):
    """Resource that releases its connections asynchronously."""

    async def aclose(self) -> None:
        """Release the resource's connections."""
        ...


@dataclass(frozen=True, slots=True)
class ApplicationServices:
    """Constructed application services and the resources they own."""

    engine: AsyncEngine
    session_factory: SessionFactory
    catalog_service: CatalogService
    media_detail_service: MediaDetailService
    orchestrator: Phase2Orchestrator
    readiness_probe: ReadinessProbe
    provider_operations: ProviderOperationRecorder
    closables: tuple[AsyncClosable, ...]

    async def aclose(self) -> None:
        """Release provider connections and dispose the database engine."""
        try:
            for closable in self.closables:
                await closable.aclose()
        finally:
            await self.engine.dispose()


class DatabaseReadinessProbe:
    """Readiness probe backed by the application database engine."""

    def __init__(self, settings: Settings, engine: AsyncEngine) -> None:
        """Initialize the probe with validated settings and the database engine.

        :param settings: Validated application settings.
        :param engine: Application database engine.
        """
        self._settings = settings
        self._engine = engine

    async def check(self) -> ReadinessReport:
        """Return database accessibility and migration state.

        :return: Safe readiness facts.
        """
        return await check_readiness(self._settings, self._engine)


def run_startup_migrations(settings: Settings) -> None:
    """Apply pending migrations before the application starts serving.

    :param settings: Validated application settings.
    """
    upgrade_to_head(settings)


def build_services(settings: Settings) -> ApplicationServices:
    """Build the application services used by the Web interface.

    :param settings: Validated application settings.
    :return: Constructed services and owned resources.
    """
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    catalog = SqlAlchemyMediaCatalog(session_factory)
    personal = SqlAlchemyPersonalMediaRepository(session_factory)
    availability_repository = SqlAlchemyAvailabilityRepository(session_factory)
    resolver = MediaIdentityResolver(catalog)

    closables: list[AsyncClosable] = []
    metadata_providers: dict[str, MetadataProvider] = {}
    library_workflow: LibrarySynchronizationWorkflow | None = None
    availability_workflow: AvailabilityRefreshWorkflow | None = None

    tmdb_settings = _tmdb_settings()
    if tmdb_settings is not None:
        tmdb = TmdbMetadataProvider(tmdb_settings)
        closables.append(tmdb)
        metadata_providers["tmdb"] = tmdb
        availability_workflow = AvailabilityRefreshService(resolver, tmdb, availability_repository)

    jellyfin_settings = _jellyfin_settings()
    if jellyfin_settings is not None:
        jellyfin_client = JellyfinClient(jellyfin_settings)
        closables.append(jellyfin_client)
        library_service = LibrarySynchronizationService(personal, personal, resolver)
        library_workflow = JellyfinLibrarySynchronizer(jellyfin_client, library_service)

    catalog_service = CatalogService(catalog, metadata_providers)
    media_detail_service = MediaDetailService(catalog, personal, personal, availability_repository)
    netflix = NetflixFileImporter(PersonalMediaImportService(personal, personal, resolver))
    recommendations = RecommendationService(SqlAlchemyRecommendationDataSource(session_factory))
    orchestrator = Phase2Orchestrator(
        personal,
        netflix,
        library_workflow,
        availability_workflow,
        recommendations,
        catalog=catalog,
    )
    return ApplicationServices(
        engine=engine,
        session_factory=session_factory,
        catalog_service=catalog_service,
        media_detail_service=media_detail_service,
        orchestrator=orchestrator,
        readiness_probe=DatabaseReadinessProbe(settings, engine),
        provider_operations=ProviderOperationRecorder(),
        closables=tuple(closables),
    )


def attach_services(app: FastAPI, services: ApplicationServices) -> None:
    """Expose constructed services to feature routes through application state.

    :param app: FastAPI application receiving the services.
    :param services: Constructed application services.
    """
    app.state.engine = services.engine
    app.state.readiness_probe = services.readiness_probe
    app.state.provider_operations = services.provider_operations
    app.state.catalog_service = services.catalog_service
    app.state.media_detail_service = services.media_detail_service
    app.state.recommendation_service = services.orchestrator
    app.state.netflix_import_service = services.orchestrator
    app.state.jellyfin_sync_service = services.orchestrator
    app.state.availability_refresh_service = services.orchestrator


def _tmdb_settings() -> TmdbSettings | None:
    """Return validated TMDB settings, or ``None`` when absent or malformed.

    :return: Validated TMDB settings, or ``None``.
    """
    try:
        return TmdbSettings()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError, ValidationError:
        return None


def _jellyfin_settings() -> JellyfinSettings | None:
    """Return validated Jellyfin settings, or ``None`` when absent or malformed.

    :return: Validated Jellyfin settings, or ``None``.
    """
    try:
        return JellyfinSettings()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError, ValidationError:
        return None
