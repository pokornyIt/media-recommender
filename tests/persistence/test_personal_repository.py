"""Tests for profile-owned personal media persistence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from media_recommender.config import Settings
from media_recommender.domain import (
    LikeState,
    MediaId,
    Movie,
    Preference,
    PreferenceEffect,
    PreferenceId,
    PreferenceKind,
    Profile,
    ProfileId,
    ProviderProfileMapping,
    ProviderProfileMappingId,
    Rating,
    RatingId,
    SourceProvenance,
    ViewingEvent,
    ViewingEventId,
    WatchState,
    WatchStateId,
    WatchStatus,
)
from media_recommender.persistence import (
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    create_engine,
    create_session_factory,
)
from media_recommender.persistence.models import MediaRecord, ProfileRecord, ViewingEventRecord

EXPECTED_PROFILE_COUNT = 2
EXPECTED_WATCH_COUNT = 2


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary SQLite database."""
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _source(record_id: str, *, synchronization_id: str = "sync-1") -> SourceProvenance:
    """Return deterministic synthetic source provenance for a record."""
    return SourceProvenance(
        provider="synthetic-provider",
        source_record_id=record_id,
        synchronization_id=synchronization_id,
        imported_at=datetime(2026, 9, 14, 10, tzinfo=UTC),
    )


async def _exercise_personal_repository(database_path: Path) -> None:
    """Exercise ownership, personal state semantics, and persistence round trips."""
    engine = create_engine(Settings(database_path=database_path))
    session_factory = create_session_factory(engine)
    repository = SqlAlchemyPersonalMediaRepository(session_factory)
    catalog = SqlAlchemyMediaCatalog(session_factory)

    default = await repository.get_or_create_default()
    assert await repository.get_or_create_default() == default
    second = Profile(id=ProfileId.new(), name="Second synthetic profile")
    await repository.save_profile(second)

    movie = Movie(id=MediaId.new(), title="Shared Synthetic Movie")
    await catalog.save(movie)

    first_watch = ViewingEvent(
        id=ViewingEventId.new(),
        profile_id=default.id,
        media_id=movie.id,
        watched_at=datetime(2026, 9, 10, 18, tzinfo=UTC),
        provenance=_source("watch-1"),
    )
    second_watch = ViewingEvent(
        id=ViewingEventId.new(),
        profile_id=default.id,
        media_id=movie.id,
        watched_at=datetime(2026, 9, 12, 20, tzinfo=UTC),
        provenance=_source("watch-2"),
    )
    await repository.save_viewing_event(first_watch)
    await repository.save_viewing_event(second_watch)

    repeated_import = ViewingEvent(
        id=ViewingEventId.new(),
        profile_id=default.id,
        media_id=movie.id,
        watched_at=first_watch.watched_at,
        provenance=_source("watch-1", synchronization_id="sync-2"),
    )
    persisted_import = await repository.save_viewing_event(repeated_import)

    assert persisted_import.id == first_watch.id
    assert persisted_import.provenance.synchronization_id == "sync-2"
    stored_watches = await repository.list_viewing_events(default.id, movie.id)
    assert tuple(event.id for event in stored_watches) == (first_watch.id, second_watch.id)
    assert await repository.get_watch_status(default.id, movie.id) is WatchStatus.WATCHED
    assert await repository.get_watch_status(second.id, movie.id) is WatchStatus.UNKNOWN

    unwatched_state = WatchState(
        id=WatchStateId.new(),
        profile_id=second.id,
        media_id=movie.id,
        status=WatchStatus.UNWATCHED,
        provenance=_source("library-state-1"),
    )
    await repository.save_watch_state(unwatched_state)
    assert await repository.get_watch_status(second.id, movie.id) is WatchStatus.UNWATCHED

    rating = Rating(
        id=RatingId.new(),
        profile_id=second.id,
        media_id=movie.id,
        value=8.5,
        like_state=LikeState.LIKED,
        provenance=_source("rating-1"),
    )
    await repository.save_rating(rating)

    assert await repository.list_ratings(second.id, movie.id) == (rating,)
    assert await repository.list_ratings(default.id, movie.id) == ()
    assert await repository.get_watch_status(second.id, movie.id) is WatchStatus.UNWATCHED

    preferences = (
        Preference(
            id=PreferenceId.new(),
            profile_id=default.id,
            kind=PreferenceKind.GENRE,
            effect=PreferenceEffect.EXCLUDE,
            value="Horror",
        ),
        Preference(
            id=PreferenceId.new(),
            profile_id=default.id,
            kind=PreferenceKind.RUNTIME_MINUTES,
            effect=PreferenceEffect.PREFER,
            maximum=150,
        ),
    )
    for preference in preferences:
        await repository.save_preference(preference)

    assert set(await repository.list_preferences(default.id)) == set(preferences)
    assert await repository.list_preferences(second.id) == ()

    mapping = ProviderProfileMapping(
        id=ProviderProfileMappingId.new(),
        profile_id=default.id,
        provider="Jellyfin",
        external_profile_id="synthetic-user-1",
        synchronized_at=datetime(2026, 9, 14, 11, tzinfo=UTC),
    )
    await repository.save_provider_mapping(mapping)

    assert await repository.find_provider_mapping("jellyfin", "synthetic-user-1") == mapping
    assert await repository.list_provider_mappings(default.id) == (mapping,)
    assert await repository.list_provider_mappings(second.id) == ()

    async with session_factory() as session:
        profile_count = await session.scalar(select(func.count()).select_from(ProfileRecord))
        media_count = await session.scalar(select(func.count()).select_from(MediaRecord))
        watch_count = await session.scalar(select(func.count()).select_from(ViewingEventRecord))

    assert profile_count == EXPECTED_PROFILE_COUNT
    assert media_count == 1
    assert watch_count == EXPECTED_WATCH_COUNT
    await engine.dispose()


def test_personal_repository_preserves_ownership_and_distinct_state(tmp_path: Path) -> None:
    """Verify personal records stay isolated while catalog metadata remains shared."""
    database_path = tmp_path / "personal.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_personal_repository(database_path))
