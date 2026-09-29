"""Integration tests for the media-detail application service."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from media_recommender.application import MediaDetailService
from media_recommender.config import Settings
from media_recommender.domain import (
    AvailabilityProvenance,
    AvailabilityType,
    Genre,
    LibraryPresence,
    LibraryPresenceId,
    LikeState,
    MediaId,
    Movie,
    SourceProvenance,
    StreamingAvailability,
    StreamingService,
    ViewingEvent,
    ViewingEventId,
    WatchStatus,
)
from media_recommender.persistence import (
    SqlAlchemyAvailabilityRepository,
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    create_engine,
    create_session_factory,
)

SYNTHETIC_NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)
INITIAL_RATING = 7.5
UPDATED_RATING = 9.0


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary database.

    :param database_path: Temporary SQLite database path.
    :return: Alembic configuration for the temporary database.
    """
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


async def _exercise_media_detail_service(database_path: Path) -> None:
    """Verify combined detail facts and deterministic profile-owned edits.

    :param database_path: Temporary SQLite database path.
    """
    engine = create_engine(Settings(database_path=database_path))
    session_factory = create_session_factory(engine)
    catalog = SqlAlchemyMediaCatalog(session_factory)
    personal = SqlAlchemyPersonalMediaRepository(session_factory)
    availability = SqlAlchemyAvailabilityRepository(session_factory)
    service = MediaDetailService(catalog, personal, personal, availability, clock=lambda: SYNTHETIC_NOW)

    assert await service.get_detail(MediaId.new()) is None

    movie = Movie(
        id=MediaId.new(),
        title="Synthetic Detail Movie",
        released_on=date(2024, 3, 1),
        genres=(Genre("Drama"),),
    )
    await catalog.save(movie)
    profile = await personal.get_or_create_default()

    event = ViewingEvent(
        id=ViewingEventId.new(),
        profile_id=profile.id,
        media_id=movie.id,
        watched_at=datetime(2026, 9, 10, 18, tzinfo=UTC),
        provenance=SourceProvenance(provider="netflix", imported_at=SYNTHETIC_NOW),
    )
    await personal.save_viewing_event(event)
    presence = LibraryPresence(
        id=LibraryPresenceId.new(),
        profile_id=profile.id,
        media_id=movie.id,
        available=True,
        play_count=2,
        last_played_at=datetime(2026, 9, 11, 20, tzinfo=UTC),
        provenance=SourceProvenance(provider="jellyfin", source_record_id="item-1", imported_at=SYNTHETIC_NOW),
    )
    await personal.save_library_presence(presence)
    await availability.replace_snapshot(
        movie.id,
        "CZ",
        "tmdb",
        (
            StreamingAvailability(
                media_id=movie.id,
                service=StreamingService("8", "Netflix"),
                region="CZ",
                availability_type=AvailabilityType.SUBSCRIPTION,
                provenance=AvailabilityProvenance("tmdb", SYNTHETIC_NOW, "JustWatch"),
            ),
        ),
    )

    detail = await service.get_detail(movie.id)

    assert detail is not None
    assert detail.media == movie
    assert detail.profile == profile
    assert detail.watch_status is WatchStatus.WATCHED
    assert detail.viewing_events == (event,)
    assert detail.library_presence == (presence,)
    assert len(detail.streaming_availability) == 1
    assert detail.streaming_availability[0].service.name == "Netflix"

    first = await service.set_rating(movie.id, value=INITIAL_RATING, like_state=LikeState.LIKED)
    second = await service.set_rating(movie.id, value=UPDATED_RATING, like_state=None)

    assert first.id == second.id
    assert second.value == UPDATED_RATING
    assert second.like_state is None
    assert second.provenance.provider == "web"
    stored = await personal.list_ratings(profile.id, movie.id)
    assert len(stored) == 1
    assert stored[0].value == UPDATED_RATING

    with pytest.raises(ValueError, match="numeric value or explicit like state"):
        await service.set_rating(movie.id, value=None, like_state=None)

    await engine.dispose()


def test_media_detail_service_combines_shared_personal_and_availability(tmp_path: Path) -> None:
    """Verify combined detail facts and deterministic profile-owned edits."""
    database_path = tmp_path / "media_detail.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_media_detail_service(database_path))
