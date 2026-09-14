"""Tests for Jellyfin library-item normalization."""

from datetime import UTC, datetime

from media_recommender.application import LibraryItemStatus
from media_recommender.domain import ExternalId, MediaType, WatchStatus
from media_recommender.integrations.jellyfin import map_library_items

EXPECTED_RUNTIME_MINUTES = 120
EXPECTED_PLAY_COUNT = 2


def test_mapper_extracts_identity_and_distinct_personal_state() -> None:
    """Verify stable IDs, metadata evidence, and explicit user state are normalized."""
    mapped = map_library_items(
        [
            {
                "Id": "movie-1",
                "Name": "Synthetic Movie",
                "OriginalTitle": "Original Synthetic Movie",
                "Type": "Movie",
                "ProductionYear": 2024,
                "RunTimeTicks": 72_000_000_000,
                "ProviderIds": {"Imdb": "tt0000042", "Tmdb": "42", "Unsupported": "ignored"},
                "UserData": {
                    "Played": True,
                    "PlayCount": 2,
                    "LastPlayedDate": "2026-09-13T18:00:00Z",
                },
            },
            {
                "Id": "series-1",
                "Name": "Synthetic Series",
                "Type": "Series",
                "UserData": {"Played": False, "PlayCount": 0},
            },
            {"Id": "partial-1", "Name": "Missing type"},
        ]
    )

    movie, series = mapped.items
    assert movie.candidate.media_type is MediaType.MOVIE
    assert movie.candidate.runtime_minutes == EXPECTED_RUNTIME_MINUTES
    assert movie.candidate.external_ids == frozenset(
        {
            ExternalId("jellyfin", "movie-1"),
            ExternalId("imdb", "tt0000042"),
            ExternalId("tmdb", "42"),
        }
    )
    assert movie.watch_status is WatchStatus.WATCHED
    assert movie.play_count == EXPECTED_PLAY_COUNT
    assert movie.last_played_at == datetime(2026, 9, 13, 18, tzinfo=UTC)
    assert series.candidate.media_type is MediaType.TV_SHOW
    assert series.watch_status is WatchStatus.UNWATCHED
    assert mapped.invalid_items[0].source_record_id == "partial-1"
    assert LibraryItemStatus.INVALID.value == "invalid"


def test_mapper_preserves_unknown_watch_state_and_rejects_partial_playback_data() -> None:
    """Verify absent user data stays unknown and malformed items remain isolated."""
    mapped = map_library_items(
        [
            {"Id": "unknown-1", "Name": "Unknown State", "Type": "Movie"},
            {
                "Id": "invalid-play-count",
                "Name": "Invalid Playback",
                "Type": "Movie",
                "UserData": {"Played": True, "PlayCount": -1},
            },
        ]
    )

    assert mapped.items[0].watch_status is WatchStatus.UNKNOWN
    assert mapped.invalid_items[0].source_record_id == "invalid-play-count"
