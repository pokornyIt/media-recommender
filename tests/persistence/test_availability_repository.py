"""Tests for regional streaming availability persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config

from media_recommender.config import Settings
from media_recommender.domain import (
    AvailabilityProvenance,
    AvailabilityType,
    MediaId,
    Movie,
    StreamingAvailability,
    StreamingService,
)
from media_recommender.persistence import (
    SqlAlchemyAvailabilityRepository,
    SqlAlchemyMediaCatalog,
    create_engine,
    create_session_factory,
)

EXPECTED_REMOVED_OFFERS = 2
EXPECTED_REMAINING_OFFERS = 2


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary database."""
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _availability(
    media_id: MediaId,
    service: StreamingService,
    region: str,
    availability_type: AvailabilityType,
    observed_at: datetime,
) -> StreamingAvailability:
    """Build one synthetic normalized availability fact."""
    return StreamingAvailability(
        media_id=media_id,
        service=service,
        region=region,
        availability_type=availability_type,
        provenance=AvailabilityProvenance("tmdb", observed_at),
    )


async def _exercise_snapshot_replacement(database_path: Path) -> None:
    """Exercise isolation, upserts, removals, and freshness updates."""
    engine = create_engine(Settings(database_path=database_path))
    session_factory = create_session_factory(engine)
    catalog = SqlAlchemyMediaCatalog(session_factory)
    repository = SqlAlchemyAvailabilityRepository(session_factory)
    movie = Movie(id=MediaId.new(), title="Synthetic Movie")
    await catalog.save(movie)
    old = datetime(2026, 9, 14, 18, tzinfo=UTC)
    new = datetime(2026, 9, 14, 19, tzinfo=UTC)

    cz_snapshot = (
        _availability(movie.id, StreamingService("8", "Netflix"), "CZ", AvailabilityType.SUBSCRIPTION, old),
        _availability(movie.id, StreamingService("8", "Netflix"), "CZ", AvailabilityType.RENT, old),
        _availability(movie.id, StreamingService("1899", "Max"), "CZ", AvailabilityType.SUBSCRIPTION, old),
    )
    sk_snapshot = (_availability(movie.id, StreamingService("8", "Netflix"), "SK", AvailabilityType.SUBSCRIPTION, old),)
    assert await repository.replace_snapshot(movie.id, "CZ", "tmdb", cz_snapshot) == 0
    assert await repository.replace_snapshot(movie.id, "SK", "tmdb", sk_snapshot) == 0

    refreshed = (_availability(movie.id, StreamingService("8", "Netflix"), "CZ", AvailabilityType.SUBSCRIPTION, new),)
    assert await repository.replace_snapshot(movie.id, "CZ", "tmdb", refreshed) == EXPECTED_REMOVED_OFFERS
    assert await repository.replace_snapshot(movie.id, "CZ", "tmdb", refreshed) == 0

    cz_stored = await repository.list_for_media(movie.id, region="CZ")
    all_stored = await repository.list_for_media(movie.id)
    assert cz_stored == refreshed
    assert len(all_stored) == EXPECTED_REMAINING_OFFERS
    assert {item.region for item in all_stored} == {"CZ", "SK"}
    assert cz_stored[0].provenance.observed_at == new
    await engine.dispose()


def test_repository_replaces_only_one_regional_source_snapshot(tmp_path: Path) -> None:
    """Verify refreshes remove disappeared offers without duplicates or cross-region changes."""
    database_path = tmp_path / "availability.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_snapshot_replacement(database_path))
