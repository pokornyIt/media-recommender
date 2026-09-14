"""Integration tests for SQLite-backed recommendation candidate loading."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from sqlalchemy import event

from media_recommender.application import (
    AvailabilityCriterion,
    AvailabilitySourceKind,
    RecommendationCriteria,
    RecommendationFilterService,
    WatchRequirement,
)
from media_recommender.config import Settings
from media_recommender.domain import (
    AvailabilityProvenance,
    AvailabilityType,
    Genre,
    LibraryPresence,
    LibraryPresenceId,
    MediaId,
    Movie,
    Profile,
    ProfileId,
    SourceProvenance,
    StreamingAvailability,
    StreamingService,
    ViewingEvent,
    ViewingEventId,
    WatchState,
    WatchStateId,
    WatchStatus,
)
from media_recommender.persistence import (
    SqlAlchemyAvailabilityRepository,
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    SqlAlchemyRecommendationDataSource,
    create_engine,
    create_session_factory,
)

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from sqlalchemy.engine.interfaces import DBAPICursor
    from sqlalchemy.sql.compiler import Compiled

MAXIMUM_CANDIDATE_QUERIES = 10
SYNTHETIC_NOW = datetime(2026, 9, 14, 20, tzinfo=UTC)


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary database."""
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


async def _exercise_recommendation_data_source(database_path: Path) -> None:
    """Verify bulk candidate loading preserves shared and profile-owned state."""
    engine = create_engine(Settings(database_path=database_path))
    session_factory = create_session_factory(engine)
    catalog = SqlAlchemyMediaCatalog(session_factory)
    personal = SqlAlchemyPersonalMediaRepository(session_factory)
    availability = SqlAlchemyAvailabilityRepository(session_factory)
    data_source = SqlAlchemyRecommendationDataSource(session_factory)
    first_profile = Profile(id=ProfileId.new(), name="First")
    second_profile = Profile(id=ProfileId.new(), name="Second")
    await personal.save_profile(first_profile)
    await personal.save_profile(second_profile)

    local_movie = Movie(
        id=MediaId.new(),
        title="Local Movie",
        genres=(Genre("Science Fiction"),),
        released_on=date(2025, 1, 1),
    )
    streaming_movie = Movie(
        id=MediaId.new(),
        title="Streaming Movie",
        genres=(Genre("Science Fiction"),),
        released_on=date(2024, 1, 1),
    )
    await catalog.save(local_movie)
    await catalog.save(streaming_movie)

    await personal.save_viewing_event(
        ViewingEvent(
            id=ViewingEventId.new(),
            profile_id=first_profile.id,
            media_id=local_movie.id,
            watched_at=SYNTHETIC_NOW,
            provenance=SourceProvenance(provider="netflix", imported_at=SYNTHETIC_NOW),
        )
    )
    await personal.save_watch_state(
        WatchState(
            id=WatchStateId.new(),
            profile_id=second_profile.id,
            media_id=local_movie.id,
            status=WatchStatus.UNWATCHED,
            provenance=SourceProvenance(provider="jellyfin", imported_at=SYNTHETIC_NOW),
        )
    )
    await personal.save_library_presence(
        LibraryPresence(
            id=LibraryPresenceId.new(),
            profile_id=second_profile.id,
            media_id=local_movie.id,
            available=True,
            provenance=SourceProvenance(
                provider="jellyfin",
                source_record_id="local-movie",
                imported_at=SYNTHETIC_NOW,
            ),
        )
    )
    netflix = StreamingAvailability(
        media_id=streaming_movie.id,
        service=StreamingService("8", "Netflix"),
        region="CZ",
        availability_type=AvailabilityType.SUBSCRIPTION,
        provenance=AvailabilityProvenance("tmdb", SYNTHETIC_NOW, "JustWatch"),
    )
    await availability.replace_snapshot(streaming_movie.id, "CZ", "tmdb", (netflix,))

    query_count = 0

    def count_query(
        _connection: Connection,
        _cursor: DBAPICursor,
        _statement: str,
        _parameters: object,
        _context: Compiled | None,
        _executemany: object,
    ) -> None:
        """Count SQL statements used to assemble any number of candidates."""
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    first_candidates = await data_source.list_candidates(first_profile.id, frozenset())
    first_query_count = query_count
    query_count = 0
    second_candidates = await data_source.list_candidates(second_profile.id, frozenset())
    event.remove(engine.sync_engine, "before_cursor_execute", count_query)

    first_by_title = {candidate.media.title: candidate for candidate in first_candidates}
    second_by_title = {candidate.media.title: candidate for candidate in second_candidates}
    assert first_by_title["Local Movie"].watch_status is WatchStatus.WATCHED
    assert second_by_title["Local Movie"].watch_status is WatchStatus.UNWATCHED
    assert first_by_title["Local Movie"].library_presence == ()
    assert second_by_title["Local Movie"].library_presence != ()
    assert first_by_title["Streaming Movie"].streaming_availability == (netflix,)
    assert second_by_title["Streaming Movie"].streaming_availability == (netflix,)
    assert first_query_count <= MAXIMUM_CANDIDATE_QUERIES
    assert query_count <= MAXIMUM_CANDIDATE_QUERIES

    criteria = RecommendationCriteria(
        watch=WatchRequirement.NOT_WATCHED,
        availability_any_of=(
            AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider="jellyfin"),
            AvailabilityCriterion(kind=AvailabilitySourceKind.STREAMING, provider="Netflix", region="CZ"),
        ),
    )
    first_result = await RecommendationFilterService(data_source).filter(first_profile.id, criteria)
    second_result = await RecommendationFilterService(data_source).filter(second_profile.id, criteria)
    assert tuple(media.title for media in first_result.accepted) == ("Streaming Movie",)
    assert tuple(media.title for media in second_result.accepted) == ("Local Movie", "Streaming Movie")
    await engine.dispose()


def test_sqlite_data_source_bulk_loads_profile_specific_recommendation_facts(tmp_path: Path) -> None:
    """Verify recommendation filtering works over persisted normalized state."""
    database_path = tmp_path / "recommendations.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_recommendation_data_source(database_path))
