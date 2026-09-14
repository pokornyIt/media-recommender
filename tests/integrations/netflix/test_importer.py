"""Integration tests for repeatable Netflix personal-data imports."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config

from media_recommender.application import (
    ImportRecordStatus,
    MatchKind,
    MatchReason,
    MediaMatch,
    PersonalMediaImportService,
)
from media_recommender.config import Settings
from media_recommender.domain import MediaId, Movie, TVShow
from media_recommender.integrations.netflix import NetflixFileImporter
from media_recommender.persistence import (
    SqlAlchemyMediaCatalog,
    SqlAlchemyPersonalMediaRepository,
    create_engine,
    create_session_factory,
)

if TYPE_CHECKING:
    from media_recommender.application import MediaIdentityCandidate
    from media_recommender.domain import Media

EXPECTED_MOVIE_WATCHES = 2


class StaticResolver:
    """Resolve synthetic titles while surfacing explicit unsafe outcomes."""

    def __init__(self, matches: dict[str, MediaMatch]) -> None:
        """Store deterministic matches keyed by normalized candidate title."""
        self._matches = matches
        self.candidates: list[MediaIdentityCandidate] = []

    async def resolve(self, candidate: MediaIdentityCandidate) -> MediaMatch:
        """Return a configured match or an explicit not-found result."""
        self.candidates.append(candidate)
        match = self._matches.get(candidate.title)
        if match is not None and match.media is not None and match.media.media_type is candidate.media_type:
            return match
        if match is not None and match.kind is MatchKind.AMBIGUOUS:
            return match
        return MediaMatch(MatchKind.NOT_FOUND, MatchReason.NO_CANDIDATE)


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary SQLite database."""
    config = Config(Path(__file__).parents[3] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _exact(media: Media) -> MediaMatch:
    """Return an exact synthetic identity match."""
    return MediaMatch(MatchKind.EXACT, MatchReason.EXTERNAL_ID, media, (media.id,))


async def _exercise_viewing_import(database_path: Path, source_path: Path) -> None:
    """Verify ownership, matching, duplicate watches, and repeatable imports."""
    engine = create_engine(Settings(database_path=database_path))
    sessions = create_session_factory(engine)
    personal = SqlAlchemyPersonalMediaRepository(sessions)
    catalog = SqlAlchemyMediaCatalog(sessions)
    movie = Movie(id=MediaId.new(), title="Resolved Movie", released_on=date(2025, 1, 1))
    show = TVShow(id=MediaId.new(), title="Resolved Show", first_aired_on=date(2024, 1, 1))
    ambiguous_id = MediaId.new()
    resolver = StaticResolver(
        {
            movie.title: _exact(movie),
            show.title: _exact(show),
            "Ambiguous": MediaMatch(
                MatchKind.AMBIGUOUS,
                MatchReason.MULTIPLE_CANDIDATES,
                candidate_ids=(ambiguous_id,),
            ),
        }
    )
    await catalog.save(movie)
    await catalog.save(show)
    service = PersonalMediaImportService(
        personal,
        personal,
        resolver,
        clock=lambda: datetime(2026, 9, 14, 12, tzinfo=UTC),
    )
    importer = NetflixFileImporter(service)

    first = await importer.import_viewing_activity(source_path, external_profile_id="Synthetic profile")
    second = await importer.import_viewing_activity(source_path, external_profile_id="Synthetic profile")
    profile = await personal.get_or_create_default()

    assert (first.imported, first.skipped, first.unresolved, first.ambiguous, first.invalid) == (3, 0, 1, 1, 1)
    assert (second.imported, second.skipped) == (0, 3)
    assert len(await personal.list_viewing_events(profile.id, movie.id)) == EXPECTED_MOVIE_WATCHES
    assert len(await personal.list_viewing_events(profile.id, show.id)) == 1
    mapping = await personal.find_provider_mapping("netflix", "Synthetic profile")
    assert mapping is not None
    assert mapping.profile_id == profile.id
    show_candidates = [candidate for candidate in resolver.candidates if candidate.title == "Resolved Show"]
    assert show_candidates[0].media_type.value == "tv_show"
    assert "Unknown Private Title" not in repr(first)
    assert first.records[-2].status is ImportRecordStatus.AMBIGUOUS
    await engine.dispose()


def test_viewing_import_is_profile_owned_duplicate_aware_and_idempotent(tmp_path: Path) -> None:
    """Verify a real repository import preserves repeated watches without re-import duplication."""
    database_path = tmp_path / "netflix.db"
    command.upgrade(_alembic_config(database_path), "head")
    source_path = tmp_path / "NetflixViewingHistory.csv"
    source_path.write_text(
        "Title,Date\n"
        "Resolved Movie,9/14/26\n"
        "Resolved Movie,9/14/26\n"
        "Resolved Show: Season 1: Episode 1,9/13/26\n"
        "Unknown Private Title,9/12/26\n"
        "Ambiguous,9/11/26\n"
        "Invalid Date,nope\n",
        encoding="utf-8",
    )

    asyncio.run(_exercise_viewing_import(database_path, source_path))


async def _exercise_rating_import(database_path: Path, source_path: Path) -> None:
    """Verify supported ratings and profile mismatches remain independent of history."""
    engine = create_engine(Settings(database_path=database_path))
    sessions = create_session_factory(engine)
    personal = SqlAlchemyPersonalMediaRepository(sessions)
    catalog = SqlAlchemyMediaCatalog(sessions)
    movie = Movie(id=MediaId.new(), title="Rated Movie")
    await catalog.save(movie)
    resolver = StaticResolver({movie.title: _exact(movie)})
    importer = NetflixFileImporter(
        PersonalMediaImportService(
            personal,
            personal,
            resolver,
            clock=lambda: datetime(2026, 9, 14, 12, tzinfo=UTC),
        )
    )

    first = await importer.import_ratings(source_path, external_profile_id="Synthetic profile")
    second = await importer.import_ratings(source_path, external_profile_id="Synthetic profile")
    profile = await personal.get_or_create_default()

    assert (first.imported, first.invalid) == (1, 2)
    assert second.skipped == 1
    ratings = await personal.list_ratings(profile.id, movie.id)
    assert len(ratings) == 1
    assert ratings[0].rated_at == datetime(2026, 9, 14, 12, tzinfo=UTC)
    assert await personal.list_viewing_events(profile.id, movie.id) == ()
    await engine.dispose()


def test_rating_import_handles_supported_interactions_and_profile_mapping(tmp_path: Path) -> None:
    """Verify ratings import rejects foreign-profile and invalid rows safely."""
    database_path = tmp_path / "ratings.db"
    command.upgrade(_alembic_config(database_path), "head")
    source_path = tmp_path / "Ratings.csv"
    source_path.write_text(
        "Profile Name,Title Name,Rating Type,Star Value,Thumbs Value,Event Utc Ts,Region View Date\n"
        "Synthetic profile,Rated Movie,Thumbs,0,2,2026-09-14T12:00:00Z,9/14/26\n"
        "Other profile,Rated Movie,Stars,4,0,2026-09-13T12:00:00Z,9/13/26\n"
        "Synthetic profile,Invalid,Thumbs,0,0,2026-09-12T12:00:00Z,9/12/26\n",
        encoding="utf-8",
    )

    asyncio.run(_exercise_rating_import(database_path, source_path))
