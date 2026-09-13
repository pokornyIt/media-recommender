"""Tests for asynchronous SQLite catalog persistence."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from media_recommender.config import Settings
from media_recommender.domain import Country, ExternalId, Genre, MediaId, MediaType, Movie, Runtime, TVShow
from media_recommender.persistence import SqlAlchemyMediaCatalog, create_engine, create_session_factory, session_scope
from media_recommender.persistence.models import MediaRecord

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from media_recommender.persistence.database import SessionFactory


class SyntheticTransactionError(Exception):
    """Expected error used to exercise transaction rollback."""


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary SQLite database."""
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


async def _create_catalog(database_path: Path) -> tuple[SqlAlchemyMediaCatalog, AsyncEngine]:
    """Return a repository and engine for an already migrated database."""
    engine = create_engine(Settings(database_path=database_path))
    return SqlAlchemyMediaCatalog(create_session_factory(engine)), engine


async def _exercise_repository(database_path: Path) -> None:
    """Store and retrieve movies and TV shows through the catalog contracts."""
    catalog, engine = await _create_catalog(database_path)
    movie = Movie(
        id=MediaId(UUID("02f116bf-08e9-46fa-8271-e8b82788eb45")),
        title="Synthetic Movie",
        released_on=date(2023, 8, 14),
        runtime=Runtime(121),
        genres=(Genre("Adventure"),),
        production_countries=(Country("US", "United States"),),
        external_ids=frozenset({ExternalId("tmdb", "100"), ExternalId("imdb", "tt0000100")}),
    )
    show = TVShow(
        id=MediaId(UUID("880220bc-6de9-4c16-84ba-ab6254c3cc66")),
        title="Synthetic Show",
        first_aired_on=date(2024, 2, 5),
        episode_runtime=Runtime(52),
        genres=(Genre("Drama"),),
        production_countries=(Country("GB", "United Kingdom"),),
        external_ids=frozenset({ExternalId("tmdb", "200")}),
    )

    await catalog.save(movie)
    await catalog.save(show)

    assert await catalog.get(movie.id) == movie
    assert await catalog.find_by_external_id(ExternalId("imdb", "tt0000100")) == movie
    assert await catalog.get(show.id) == show
    assert [item async for item in catalog.iter_by_type(MediaType.MOVIE)] == [movie]
    assert [item async for item in catalog.iter_by_type(MediaType.TV_SHOW)] == [show]

    updated_movie = Movie(
        id=movie.id,
        title="Updated Synthetic Movie",
        released_on=movie.released_on,
        runtime=movie.runtime,
        genres=movie.genres,
        production_countries=movie.production_countries,
        external_ids=movie.external_ids,
    )
    await catalog.save(updated_movie)

    assert await catalog.get(movie.id) == updated_movie
    await engine.dispose()


def test_repository_round_trips_movies_and_tv_shows(tmp_path: Path) -> None:
    """Verify async persistence covers both supported media types and external IDs."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_repository(database_path))


async def _exercise_external_id_uniqueness(database_path: Path) -> None:
    """Verify one namespaced external identity cannot belong to two media items."""
    catalog, engine = await _create_catalog(database_path)
    external_id = ExternalId("tmdb", "duplicate")
    await catalog.save(Movie(id=MediaId.new(), title="Synthetic First", external_ids=frozenset({external_id})))

    with pytest.raises(IntegrityError):
        await catalog.save(Movie(id=MediaId.new(), title="Synthetic Second", external_ids=frozenset({external_id})))

    await engine.dispose()


def test_repository_enforces_external_id_uniqueness(tmp_path: Path) -> None:
    """Verify database constraints protect cross-provider identity integrity."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_external_id_uniqueness(database_path))


async def _exercise_session_rollback(database_path: Path) -> None:
    """Verify the session lifecycle rolls back a failed transaction."""
    _, engine = await _create_catalog(database_path)
    session_factory = create_session_factory(engine)

    with pytest.raises(SyntheticTransactionError):
        await _fail_transaction(session_factory)

    async with session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(MediaRecord))

    assert count == 0
    await engine.dispose()


async def _fail_transaction(session_factory: SessionFactory) -> None:
    """Add a row and fail before the transaction can commit."""
    async with session_scope(session_factory) as session:
        session.add(
            MediaRecord(
                id="da8101b5-7f91-4bee-963d-446f16fc90b9",
                media_type="movie",
                title="Rolled Back Synthetic Movie",
            )
        )
        raise SyntheticTransactionError


def test_session_scope_rolls_back_and_closes_failed_transaction(tmp_path: Path) -> None:
    """Verify asynchronous sessions do not commit failed work."""
    database_path = tmp_path / "catalog.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_session_rollback(database_path))
