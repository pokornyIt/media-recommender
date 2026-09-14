"""End-to-end offline tests for Jellyfin library synchronization."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from media_recommender.application import (
    LibraryItemStatus,
    LibrarySynchronizationService,
    MediaIdentityResolver,
)
from media_recommender.config import Settings
from media_recommender.domain import ExternalId, MediaId, Movie, Runtime, WatchStatus
from media_recommender.integrations import ProviderUnavailableError
from media_recommender.integrations.jellyfin import JellyfinLibrarySynchronizer
from media_recommender.integrations.jellyfin.models import JellyfinItemsResponse, JellyfinUser
from media_recommender.persistence import (
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    create_engine,
    create_session_factory,
)
from media_recommender.persistence.models import LibraryPresenceRecord, WatchStateRecord

EXPECTED_SYNCHRONIZED_COUNT = 2

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from media_recommender.persistence.database import SessionFactory


class FakeJellyfinClient:
    """Return configurable offline Jellyfin user and collection payloads."""

    def __init__(self, items: list[dict[str, object]]) -> None:
        """Initialize the fake for one selected synthetic user."""
        self.items = items
        self.fail = False

    async def validate_user(self) -> JellyfinUser:
        """Return the selected user or simulate a temporary server failure."""
        if self.fail:
            raise ProviderUnavailableError
        return JellyfinUser.model_validate({"Id": "selected-user", "Name": "Selected User"})

    async def get_library_items(self) -> JellyfinItemsResponse:
        """Return the current synthetic complete collection."""
        return JellyfinItemsResponse.model_validate({"Items": self.items})


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration for a temporary SQLite database."""
    config = Config(Path(__file__).parents[3] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _movie_payload(*, played: bool) -> dict[str, object]:
    """Return a Jellyfin item with an exact cross-provider identity."""
    return {
        "Id": "jellyfin-movie-1",
        "Name": "Provider Title Can Differ",
        "Type": "Movie",
        "ProviderIds": {"Imdb": "tt0000101"},
        "UserData": {
            "Played": played,
            "PlayCount": 3 if played else 0,
            "LastPlayedDate": "2026-09-12T20:00:00Z" if played else None,
        },
    }


def _fallback_payload() -> dict[str, object]:
    """Return a Jellyfin item requiring shared metadata fallback matching."""
    return {
        "Id": "jellyfin-movie-2",
        "Name": "Fallback Match",
        "Type": "Movie",
        "ProductionYear": 2025,
        "RunTimeTicks": 60_000_000_000,
    }


async def _build_services(
    database_path: Path,
) -> tuple[
    AsyncEngine,
    SessionFactory,
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    LibrarySynchronizationService,
]:
    """Create migrated persistence and seed the shared synthetic catalog."""
    engine = create_engine(Settings(database_path=database_path))
    session_factory = create_session_factory(engine)
    catalog = SqlAlchemyMediaCatalog(session_factory)
    personal = SqlAlchemyPersonalMediaRepository(session_factory)
    await catalog.save(
        Movie(
            id=MediaId.new(),
            title="Exact Match",
            released_on=date(2024, 1, 1),
            external_ids=frozenset({ExternalId("imdb", "tt0000101")}),
        )
    )
    for _ in range(2):
        await catalog.save(
            Movie(
                id=MediaId.new(),
                title="Ambiguous Match",
                released_on=date(2023, 1, 1),
            )
        )
    await catalog.save(
        Movie(
            id=MediaId.new(),
            title="Fallback Match",
            released_on=date(2025, 1, 1),
            runtime=Runtime(100),
        )
    )
    service = LibrarySynchronizationService(
        personal,
        personal,
        MediaIdentityResolver(catalog),
        clock=lambda: datetime(2026, 9, 14, 12, tzinfo=UTC),
    )
    return engine, session_factory, catalog, personal, service


async def _exercise_complete_synchronization(database_path: Path) -> None:
    """Verify identity resolution, state distinctions, idempotency, and removals."""
    engine, session_factory, catalog, personal, service = await _build_services(database_path)
    client = FakeJellyfinClient(
        [
            _movie_payload(played=True),
            _fallback_payload(),
            {"Id": "unresolved", "Name": "Unknown Catalog Item", "Type": "Series", "ProductionYear": 2022},
            {"Id": "ambiguous", "Name": "Ambiguous Match", "Type": "Movie", "ProductionYear": 2023},
            {"Id": "malformed", "Name": "Missing Type"},
        ]
    )
    synchronizer = JellyfinLibrarySynchronizer(client, service, synchronization_id_factory=lambda: "sync-1")

    first = await synchronizer.synchronize()
    profile = await personal.get_or_create_default()
    first_presence = await personal.list_library_presence(profile.id, "jellyfin")

    assert first.count(LibraryItemStatus.SYNCHRONIZED) == EXPECTED_SYNCHRONIZED_COUNT
    assert first.count(LibraryItemStatus.UNRESOLVED) == 1
    assert first.count(LibraryItemStatus.AMBIGUOUS) == 1
    assert first.count(LibraryItemStatus.INVALID) == 1
    assert first.removed == 0
    assert len(first_presence) == EXPECTED_SYNCHRONIZED_COUNT
    assert all(item.available for item in first_presence)
    assert await personal.get_watch_status(profile.id, first_presence[0].media_id) is WatchStatus.WATCHED
    assert await personal.get_watch_status(profile.id, first_presence[1].media_id) is WatchStatus.UNKNOWN
    assert len(await personal.list_provider_mappings(profile.id)) == 1
    assert await catalog.find_by_external_id(ExternalId("jellyfin", "jellyfin-movie-1")) is not None
    assert await catalog.find_by_external_id(ExternalId("jellyfin", "jellyfin-movie-2")) is not None

    second = await synchronizer.synchronize()
    second_presence = await personal.list_library_presence(profile.id, "jellyfin")
    assert second.removed == 0
    assert tuple(item.id for item in second_presence) == tuple(item.id for item in first_presence)

    client.items = [_movie_payload(played=False)]
    changed = await JellyfinLibrarySynchronizer(
        client,
        service,
        synchronization_id_factory=lambda: "sync-2",
    ).synchronize()
    final_presence = await personal.list_library_presence(profile.id, "jellyfin")
    assert changed.removed == 1
    assert final_presence[0].available
    assert not final_presence[1].available
    assert final_presence[0].play_count == 0
    assert final_presence[0].last_played_at is None
    assert await personal.get_watch_status(profile.id, final_presence[0].media_id) is WatchStatus.UNWATCHED

    async with session_factory() as session:
        presence_count = await session.scalar(select(func.count()).select_from(LibraryPresenceRecord))
        watch_state_count = await session.scalar(select(func.count()).select_from(WatchStateRecord))
    assert presence_count == EXPECTED_SYNCHRONIZED_COUNT
    assert watch_state_count == 1
    await engine.dispose()


def test_complete_jellyfin_synchronization_is_repeatable_and_tracks_removals(tmp_path: Path) -> None:
    """Verify complete offline Jellyfin snapshots update profile-owned state deterministically."""
    database_path = tmp_path / "jellyfin.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_exercise_complete_synchronization(database_path))


async def _provider_failure_preserves_state(database_path: Path) -> None:
    """Verify a failed fetch cannot mark previously valid local presence unavailable."""
    engine, _session_factory, _catalog, personal, service = await _build_services(database_path)
    client = FakeJellyfinClient([_movie_payload(played=True)])
    synchronizer = JellyfinLibrarySynchronizer(client, service, synchronization_id_factory=lambda: "sync-ok")
    await synchronizer.synchronize()
    profile = await personal.get_or_create_default()
    before = await personal.list_library_presence(profile.id, "jellyfin")

    client.fail = True
    with pytest.raises(ProviderUnavailableError):
        await synchronizer.synchronize()

    assert await personal.list_library_presence(profile.id, "jellyfin") == before
    await engine.dispose()


def test_provider_failure_does_not_corrupt_previous_library_state(tmp_path: Path) -> None:
    """Verify provider failures occur before the application service mutates local state."""
    database_path = tmp_path / "failed-jellyfin.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_provider_failure_preserves_state(database_path))
