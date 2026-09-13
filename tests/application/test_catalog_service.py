"""End-to-end tests for catalog application-service workflows."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import httpx
import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from media_recommender.application import (
    CatalogService,
    InvalidProviderResultError,
    MediaDetailsNotFoundError,
    MediaSearchResult,
    MetadataProvider,
    MetadataProviderNotConfiguredError,
)
from media_recommender.config import Settings, TmdbSettings
from media_recommender.domain import Country, ExternalId, Genre, MediaId, MediaType, Movie, Runtime, TVShow
from media_recommender.integrations.errors import ProviderUnavailableError
from media_recommender.integrations.tmdb import TmdbMetadataProvider
from media_recommender.persistence import SqlAlchemyMediaCatalog, create_engine, create_session_factory
from media_recommender.persistence.models import MediaRecord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncEngine

    from media_recommender.domain import Media


class FakeMetadataProvider:
    """Controllable offline metadata provider for service tests."""

    def __init__(self, details: Media | None) -> None:
        """Initialize the fake with a detail response.

        :param details: Normalized details to return, or ``None`` for not found.
        """
        self.details = details
        self.error: Exception | None = None
        self.search_results: Sequence[MediaSearchResult] = ()

    async def search(
        self,
        query: str,
        *,
        media_type: MediaType | None = None,
    ) -> Sequence[MediaSearchResult]:
        """Return configured search results or raise the configured error."""
        del query, media_type
        if self.error is not None:
            raise self.error
        return self.search_results

    async def get_details(self, external_id: ExternalId, media_type: MediaType) -> Media | None:
        """Return configured normalized details or raise the configured error."""
        del external_id, media_type
        if self.error is not None:
            raise self.error
        return self.details


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary database."""
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


async def _create_service(
    database_path: Path,
    provider: MetadataProvider,
) -> tuple[CatalogService, SqlAlchemyMediaCatalog, AsyncEngine]:
    """Create a service backed by a migrated temporary SQLite catalog."""
    engine = create_engine(Settings(database_path=database_path))
    catalog = SqlAlchemyMediaCatalog(create_session_factory(engine))
    return CatalogService(catalog, {"tmdb": provider}), catalog, engine


def _movie(*, media_id: str, title: str, runtime: int | None = 118) -> Movie:
    """Return synthetic normalized movie details."""
    return Movie(
        id=MediaId(UUID(media_id)),
        title=title,
        released_on=date(2025, 4, 12),
        runtime=Runtime(runtime) if runtime is not None else None,
        genres=(Genre("Adventure"),),
        production_countries=(Country("CZ", "Czechia"),) if runtime is not None else (),
        external_ids=frozenset({ExternalId("tmdb", "101"), ExternalId("imdb", "tt0000101")}),
    )


async def _exercise_movie_workflow(database_path: Path) -> None:
    """Exercise search, import, refresh, and both lookup identities."""
    tmdb_id = ExternalId("tmdb", "101")
    initial = _movie(media_id="1b3c793b-1a39-4fcb-9620-8dc15180bf4d", title="Initial Synthetic Movie")
    provider = FakeMetadataProvider(initial)
    provider.search_results = (
        MediaSearchResult(
            external_id=tmdb_id,
            media_type=MediaType.MOVIE,
            title=initial.title,
            release_year=initial.release_year,
        ),
    )
    service, _, engine = await _create_service(database_path, provider)

    results = await service.search("TMDB", "Synthetic", media_type=MediaType.MOVIE)
    imported = await service.sync(tmdb_id, MediaType.MOVIE)

    assert isinstance(imported, Movie)
    assert results == provider.search_results
    assert await service.get(imported.id) == imported
    assert await service.find_by_external_id(ExternalId("imdb", "tt0000101")) == imported

    provider.details = _movie(
        media_id="1165f8d5-15fb-499d-a894-0474b71f7f5f",
        title="Refreshed Synthetic Movie",
        runtime=None,
    )
    refreshed = await service.sync(tmdb_id, MediaType.MOVIE)

    assert isinstance(refreshed, Movie)
    assert refreshed.id == imported.id
    assert refreshed.title == "Refreshed Synthetic Movie"
    assert refreshed.runtime is None
    assert refreshed.production_countries == ()
    assert await service.get(imported.id) == refreshed
    async with create_session_factory(engine)() as session:
        count = await session.scalar(select(func.count()).select_from(MediaRecord))
    assert count == 1
    await engine.dispose()


def test_movie_catalog_workflow_is_provider_independent_and_deduplicated(tmp_path: Path) -> None:
    """Verify repeated movie synchronization updates one stable catalog identity."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_movie_workflow(database_path))


async def _exercise_tv_workflow(database_path: Path) -> None:
    """Exercise TV-show synchronization and external-ID lookup."""
    tmdb_id = ExternalId("tmdb", "202")
    show = TVShow(
        id=MediaId(UUID("f55e7b29-d9eb-4ce2-a43c-fd72ff96d13d")),
        title="Synthetic Series",
        first_aired_on=date(2024, 8, 3),
        episode_runtime=Runtime(48),
        external_ids=frozenset({tmdb_id, ExternalId("tvdb", "2202")}),
    )
    service, _, engine = await _create_service(database_path, FakeMetadataProvider(show))

    synchronized = await service.sync(tmdb_id, MediaType.TV_SHOW)

    assert synchronized == show
    assert await service.get(show.id) == show
    assert await service.find_by_external_id(ExternalId("tvdb", "2202")) == show
    await engine.dispose()


def test_tv_show_flows_through_provider_service_and_sqlite(tmp_path: Path) -> None:
    """Verify TV details cross the complete offline application data path."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_tv_workflow(database_path))


async def _exercise_tmdb_to_sqlite_workflow(database_path: Path) -> None:
    """Exercise real TMDB normalization through the service into SQLite."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return synthetic TMDB details for the requested movie."""
        assert request.url.path == "/3/movie/505"
        return httpx.Response(
            200,
            json={
                "id": 505,
                "title": "Normalized TMDB Movie",
                "original_title": "Original TMDB Movie",
                "release_date": "2026-06-07",
                "runtime": 103,
                "genres": [{"id": 12, "name": "Adventure"}],
                "production_countries": [{"iso_3166_1": "CZ", "name": "Czechia"}],
                "external_ids": {"imdb_id": "tt0000505"},
            },
        )

    engine = create_engine(Settings(database_path=database_path))
    catalog = SqlAlchemyMediaCatalog(create_session_factory(engine))
    settings = TmdbSettings.model_validate({"api_token": SecretStr("synthetic-token")})
    async with TmdbMetadataProvider(settings, transport=httpx.MockTransport(handler)) as provider:
        service = CatalogService(catalog, {"tmdb": provider})
        synchronized = await service.sync(ExternalId("tmdb", "505"), MediaType.MOVIE)

    assert isinstance(synchronized, Movie)
    assert synchronized.title == "Normalized TMDB Movie"
    assert synchronized.runtime == Runtime(103)
    assert synchronized.external_ids == frozenset({ExternalId("tmdb", "505"), ExternalId("imdb", "tt0000505")})
    assert await catalog.get(synchronized.id) == synchronized
    await engine.dispose()


def test_tmdb_normalization_flows_through_service_into_sqlite(tmp_path: Path) -> None:
    """Verify the complete TMDB-to-catalog data path stays offline."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_tmdb_to_sqlite_workflow(database_path))


async def _exercise_service_errors(database_path: Path) -> None:
    """Verify provider configuration, not-found, and failure behavior."""
    tmdb_id = ExternalId("tmdb", "303")
    provider = FakeMetadataProvider(None)
    service, _, engine = await _create_service(database_path, provider)

    with pytest.raises(MetadataProviderNotConfiguredError):
        await service.search("missing", "Synthetic")
    with pytest.raises(MediaDetailsNotFoundError):
        await service.sync(tmdb_id, MediaType.MOVIE)

    provider.details = TVShow(id=MediaId.new(), title="Wrong Type", external_ids=frozenset({tmdb_id}))
    with pytest.raises(InvalidProviderResultError):
        await service.sync(tmdb_id, MediaType.MOVIE)

    provider.error = ProviderUnavailableError()
    with pytest.raises(ProviderUnavailableError):
        await service.search("tmdb", "Synthetic")
    with pytest.raises(ProviderUnavailableError):
        await service.sync(tmdb_id, MediaType.MOVIE)
    await engine.dispose()


def test_service_errors_are_explicit_and_provider_failures_propagate(tmp_path: Path) -> None:
    """Verify application errors remain distinct from provider failures."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_service_errors(database_path))


async def _exercise_persistence_rollback(database_path: Path) -> None:
    """Verify a conflicting provider result leaves the catalog unchanged."""
    conflicting_id = ExternalId("imdb", "tt-conflict")
    tmdb_id = ExternalId("tmdb", "404")
    details = Movie(
        id=MediaId(UUID("f9ac51b9-9c42-48a9-863b-d57737c54bfd")),
        title="Conflicting Provider Movie",
        external_ids=frozenset({tmdb_id, conflicting_id}),
    )
    provider = FakeMetadataProvider(details)
    service, catalog, engine = await _create_service(database_path, provider)
    existing = Movie(
        id=MediaId(UUID("37d3cbbe-b469-4506-b235-8051a81c84d1")),
        title="Existing Catalog Movie",
        external_ids=frozenset({conflicting_id}),
    )
    await catalog.save(existing)

    with pytest.raises(IntegrityError):
        await service.sync(tmdb_id, MediaType.MOVIE)

    assert await service.find_by_external_id(tmdb_id) is None
    assert await service.find_by_external_id(conflicting_id) == existing
    async with create_session_factory(engine)() as session:
        count = await session.scalar(select(func.count()).select_from(MediaRecord))
    assert count == 1
    await engine.dispose()


def test_persistence_failure_rolls_back_complete_service_write(tmp_path: Path) -> None:
    """Verify an ambiguous external-ID collision is propagated and rolled back."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_persistence_rollback(database_path))
