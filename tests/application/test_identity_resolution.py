"""Tests for deterministic cross-provider media identity resolution."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config

from media_recommender.application import (
    MatchKind,
    MatchReason,
    MediaIdentityCandidate,
    MediaIdentityResolver,
    MediaMatch,
    normalize_title,
)
from media_recommender.config import Settings
from media_recommender.domain import ExternalId, MediaId, MediaType, Movie, Runtime, TVShow
from media_recommender.persistence import SqlAlchemyMediaCatalog, create_engine, create_session_factory

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from media_recommender.domain import Media


class InMemoryCatalog:
    """Small catalog fake preserving identities learned by the resolver."""

    def __init__(self, *items: Media) -> None:
        """Initialize the catalog with synthetic normalized media."""
        self.items = {item.id: item for item in items}
        self.save_count = 0

    async def get(self, media_id: MediaId) -> Media | None:
        """Return media by internal identity."""
        return self.items.get(media_id)

    async def find_by_external_id(self, external_id: ExternalId) -> Media | None:
        """Return media carrying an exact external identity."""
        return next((item for item in self.items.values() if external_id in item.external_ids), None)

    async def iter_by_type(self, media_type: MediaType) -> AsyncIterator[Media]:
        """Iterate deterministically over media of one type."""
        for item in sorted(self.items.values(), key=lambda media: str(media.id.value)):
            if item.media_type is media_type:
                yield item

    async def save(self, media: Media) -> None:
        """Persist normalized media by internal identity."""
        self.items[media.id] = media
        self.save_count += 1


class StaticEnricher:
    """Return configured normalized evidence without provider transport DTOs."""

    def __init__(self, enriched: MediaIdentityCandidate) -> None:
        """Store the evidence returned by enrichment."""
        self.enriched = enriched

    async def enrich(self, candidate: MediaIdentityCandidate) -> MediaIdentityCandidate:
        """Return configured enrichment."""
        del candidate
        return self.enriched


def _movie(  # noqa: PLR0913
    identity: str,
    title: str,
    year: int | None,
    *,
    original_title: str | None = None,
    runtime: int | None = 100,
    external_ids: frozenset[ExternalId] = frozenset(),
) -> Movie:
    """Return synthetic shared movie metadata."""
    return Movie(
        id=MediaId(UUID(identity)),
        title=title,
        original_title=original_title,
        released_on=date(year, 1, 1) if year is not None else None,
        runtime=Runtime(runtime) if runtime is not None else None,
        external_ids=external_ids,
    )


def _show(identity: str, title: str, year: int, *, runtime: int = 45) -> TVShow:
    """Return synthetic shared TV-show metadata."""
    return TVShow(
        id=MediaId(UUID(identity)),
        title=title,
        first_aired_on=date(year, 1, 1),
        episode_runtime=Runtime(runtime),
    )


def _alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary SQLite database."""
    config = Config(Path(__file__).parents[2] / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


async def _resolve_exact_id_before_metadata() -> None:
    """Verify a stable provider identifier outranks heuristic candidates."""
    exact_id = ExternalId("tmdb", "100")
    exact = _movie(
        "00000000-0000-0000-0000-000000000001",
        "Different Stored Title",
        1999,
        external_ids=frozenset({exact_id}),
    )
    heuristic = _movie("00000000-0000-0000-0000-000000000002", "Source Title", 2020)
    resolver = MediaIdentityResolver(InMemoryCatalog(exact, heuristic))

    result = await resolver.resolve(
        MediaIdentityCandidate(
            media_type=MediaType.MOVIE,
            title="Source Title",
            release_year=2020,
            external_ids=frozenset({exact_id}),
        )
    )
    wrong_type = await resolver.resolve(
        MediaIdentityCandidate(
            media_type=MediaType.TV_SHOW,
            title="Different Stored Title",
            external_ids=frozenset({exact_id}),
        )
    )

    assert result.kind is MatchKind.EXACT
    assert result.reason is MatchReason.EXTERNAL_ID
    assert result.media == exact
    assert wrong_type.kind is MatchKind.AMBIGUOUS
    assert wrong_type.reason is MatchReason.MEDIA_TYPE_CONFLICT
    assert wrong_type.candidate_ids == (exact.id,)


def test_exact_external_id_is_preferred_over_metadata() -> None:
    """Verify exact provider identity is the strongest deterministic evidence."""
    asyncio.run(_resolve_exact_id_before_metadata())


async def _resolve_cross_provider_id_and_strengthen_mapping() -> None:
    """Verify authoritative cross-provider identity preserves a new source ID."""
    imdb_id = ExternalId("imdb", "tt0000100")
    source_id = ExternalId("jellyfin", "source-item-1")
    movie = _movie(
        "00000000-0000-0000-0000-000000000003",
        "Cross Provider",
        2021,
        external_ids=frozenset({ExternalId("tmdb", "100"), imdb_id}),
    )
    catalog = InMemoryCatalog(movie)
    resolver = MediaIdentityResolver(catalog)
    candidate = MediaIdentityCandidate(
        media_type=MediaType.MOVIE,
        title="Provider Title Is Irrelevant",
        external_ids=frozenset({imdb_id, source_id}),
    )

    first = await resolver.resolve(candidate)
    second = await resolver.resolve(
        MediaIdentityCandidate(
            media_type=MediaType.MOVIE,
            title="Minimal Later Sync",
            external_ids=frozenset({source_id}),
        )
    )

    assert first.kind is MatchKind.EXACT
    assert first.media is not None
    assert source_id in first.media.external_ids
    assert second.kind is MatchKind.EXACT
    assert second.media == first.media
    assert catalog.save_count == 1


def test_cross_provider_identifier_strengthens_subsequent_resolution() -> None:
    """Verify learned provider identity makes repeated synchronization exact."""
    asyncio.run(_resolve_cross_provider_id_and_strengthen_mapping())


async def _persist_learned_identity(database_path: Path) -> None:
    """Verify a heuristically resolved provider identity survives in SQLite."""
    source_id = ExternalId("netflix", "catalog-entry-1")
    engine = create_engine(Settings(database_path=database_path))
    catalog = SqlAlchemyMediaCatalog(create_session_factory(engine))
    movie = _movie("00000000-0000-0000-0000-000000000013", "Persistent Match", 2025)
    await catalog.save(movie)
    resolver = MediaIdentityResolver(catalog)

    resolved = await resolver.resolve(
        MediaIdentityCandidate(
            media_type=MediaType.MOVIE,
            title="Persistent Match",
            release_year=2025,
            external_ids=frozenset({source_id}),
        )
    )
    exact = await resolver.resolve(
        MediaIdentityCandidate(
            media_type=MediaType.MOVIE,
            title="Sparse Later Import",
            external_ids=frozenset({source_id}),
        )
    )

    assert resolved.kind is MatchKind.RESOLVED
    assert exact.kind is MatchKind.EXACT
    assert await catalog.find_by_external_id(source_id) == exact.media
    await engine.dispose()


def test_resolved_provider_identity_is_persisted_for_later_imports(tmp_path: Path) -> None:
    """Verify learned source mappings use the real catalog persistence boundary."""
    database_path = tmp_path / "identity.db"
    command.upgrade(_alembic_config(database_path), "head")
    asyncio.run(_persist_learned_identity(database_path))


async def _resolve_title_with_corroborating_metadata() -> None:
    """Verify year, type, title variants, and runtime constrain heuristics."""
    wrong_year = _movie("00000000-0000-0000-0000-000000000004", "Shared Title", 2019)
    right_year = _movie("00000000-0000-0000-0000-000000000005", "Shared Title", 2020)
    localized = _movie(
        "00000000-0000-0000-0000-000000000006",
        "Lokalizovaný název",
        2022,
        original_title="Original Title",
        runtime=123,
    )
    show = _show("00000000-0000-0000-0000-000000000007", "Shared Title", 2020)
    resolver = MediaIdentityResolver(InMemoryCatalog(wrong_year, right_year, localized, show))

    by_year = await resolver.resolve(
        MediaIdentityCandidate(media_type=MediaType.MOVIE, title="shared title", release_year=2020)
    )
    by_type = await resolver.resolve(
        MediaIdentityCandidate(media_type=MediaType.TV_SHOW, title="Shared Title", release_year=2020)
    )
    by_original_title = await resolver.resolve(
        MediaIdentityCandidate(
            media_type=MediaType.MOVIE,
            title="Original—Title",
            release_year=2022,
            runtime_minutes=125,
        )
    )

    assert by_year.media == right_year
    assert by_year.kind is MatchKind.RESOLVED
    assert by_type.media == show
    assert by_original_title.media == localized


def test_metadata_resolution_respects_year_type_and_title_variants() -> None:
    """Verify heuristic matching requires consistent independent evidence."""
    asyncio.run(_resolve_title_with_corroborating_metadata())


async def _surface_ambiguous_and_unresolved_results() -> None:
    """Verify unsafe metadata never selects an arbitrary catalog item."""
    first = _movie("00000000-0000-0000-0000-000000000009", "Duplicate", 2024)
    second = _movie("00000000-0000-0000-0000-000000000008", "Duplicate", 2024)
    resolver = MediaIdentityResolver(InMemoryCatalog(first, second))

    ambiguous = await resolver.resolve(
        MediaIdentityCandidate(media_type=MediaType.MOVIE, title="Duplicate", release_year=2024)
    )
    insufficient = await resolver.resolve(MediaIdentityCandidate(media_type=MediaType.MOVIE, title="Duplicate"))
    missing = await resolver.resolve(
        MediaIdentityCandidate(media_type=MediaType.MOVIE, title="Absent", release_year=2024)
    )
    runtime_only = await MediaIdentityResolver(
        InMemoryCatalog(_movie("00000000-0000-0000-0000-000000000014", "Runtime Evidence", None, runtime=98))
    ).resolve(MediaIdentityCandidate(media_type=MediaType.MOVIE, title="Runtime Evidence", runtime_minutes=100))

    assert ambiguous.kind is MatchKind.AMBIGUOUS
    assert ambiguous.reason is MatchReason.MULTIPLE_CANDIDATES
    assert ambiguous.candidate_ids == (second.id, first.id)
    assert insufficient == MediaMatch(MatchKind.NOT_FOUND, MatchReason.INSUFFICIENT_EVIDENCE)
    assert missing == MediaMatch(MatchKind.NOT_FOUND, MatchReason.NO_CANDIDATE)
    assert runtime_only.kind is MatchKind.RESOLVED


def test_ambiguity_missing_evidence_and_no_match_are_explicit() -> None:
    """Verify all unresolved outcomes remain typed and deterministic."""
    asyncio.run(_surface_ambiguous_and_unresolved_results())


async def _surface_identifier_conflicts() -> None:
    """Verify contradictory strong identifiers are never merged."""
    tmdb_id = ExternalId("tmdb", "100")
    imdb_id = ExternalId("imdb", "tt200")
    first = _movie(
        "00000000-0000-0000-0000-000000000010",
        "First",
        2020,
        external_ids=frozenset({tmdb_id}),
    )
    second = _movie(
        "00000000-0000-0000-0000-000000000011",
        "Second",
        2021,
        external_ids=frozenset({imdb_id}),
    )
    resolver = MediaIdentityResolver(InMemoryCatalog(second, first))

    conflict = await resolver.resolve(
        MediaIdentityCandidate(
            media_type=MediaType.MOVIE,
            title="Conflicting Source",
            external_ids=frozenset({tmdb_id, imdb_id}),
        )
    )

    assert conflict.kind is MatchKind.AMBIGUOUS
    assert conflict.reason is MatchReason.IDENTIFIER_CONFLICT
    assert conflict.candidate_ids == (first.id, second.id)


def test_contradictory_provider_identifiers_are_deterministic() -> None:
    """Verify conflicting identifiers surface all candidates in stable order."""
    asyncio.run(_surface_identifier_conflicts())


async def _enrich_without_transport_coupling() -> None:
    """Verify normalized enrichment can supply authoritative cross-provider evidence."""
    imdb_id = ExternalId("imdb", "tt-enriched")
    source_id = ExternalId("netflix", "source-1")
    movie = _movie(
        "00000000-0000-0000-0000-000000000012",
        "Catalog Title",
        2023,
        external_ids=frozenset({imdb_id}),
    )
    candidate = MediaIdentityCandidate(
        media_type=MediaType.MOVIE,
        title="Sparse Export Title",
        external_ids=frozenset({source_id}),
    )
    enriched = MediaIdentityCandidate(
        media_type=MediaType.MOVIE,
        title="Catalog Title",
        release_year=2023,
        external_ids=frozenset({imdb_id}),
    )
    resolver = MediaIdentityResolver(InMemoryCatalog(movie), enricher=StaticEnricher(enriched))

    result = await resolver.resolve(candidate)

    assert result.kind is MatchKind.EXACT
    assert result.media is not None
    assert result.media.external_ids == frozenset({imdb_id, source_id})


def test_normalized_enrichment_can_resolve_sparse_provider_data() -> None:
    """Verify enrichment uses only the provider-independent candidate contract."""
    asyncio.run(_enrich_without_transport_coupling())


def test_title_normalization_is_explicit_and_punctuation_insensitive() -> None:
    """Verify Unicode width, case, punctuation, and whitespace normalization."""
    assert normalize_title("  \uff37\uff21\uff2c\uff2c·\uff25  ") == "wall e"


def test_identity_candidate_rejects_nonsemantic_title() -> None:
    """Verify punctuation-only titles cannot become an empty normalized match key."""
    with pytest.raises(ValueError, match="letters or numbers"):
        MediaIdentityCandidate(media_type=MediaType.MOVIE, title="---", release_year=2020)
