"""Tests for local Netflix CSV parsing."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

from media_recommender.domain import LikeState
from media_recommender.integrations.netflix import NetflixCsvParser

if TYPE_CHECKING:
    from pathlib import Path

SHA256_HEX_LENGTH: Final = 64
EXPECTED_RATING_ROWS: Final = 2
EXPECTED_NUMERIC_RATING: Final = 8


def test_viewing_activity_accepts_extra_columns_and_utf8_bom(tmp_path: Path) -> None:
    """Verify normal exports tolerate harmless extra columns and Unicode titles."""
    path = tmp_path / "NetflixViewingHistory.csv"
    path.write_bytes("Title,Date,Ignored\nŽluťoučký film,9/14/26,value\n".encode("utf-8-sig"))

    batch = NetflixCsvParser().parse_viewing_activity(path)

    assert batch.invalid_rows == ()
    assert len(batch.source_digest) == SHA256_HEX_LENGTH
    assert batch.rows[0].title == "Žluťoučký film"
    assert batch.rows[0].watched_on == date(2026, 9, 14)


def test_viewing_activity_supports_explicit_locale_format_and_utf16(tmp_path: Path) -> None:
    """Verify callers can select the unambiguous date convention of their export."""
    path = tmp_path / "history.csv"
    path.write_bytes("Title,Date\nSynthetic title,14.09.26\n".encode("utf-16"))

    batch = NetflixCsvParser(date_format="%d.%m.%y").parse_viewing_activity(path)

    assert batch.invalid_rows == ()
    assert batch.rows[0].watched_on == date(2026, 9, 14)


def test_viewing_activity_reports_invalid_rows_and_required_headers(tmp_path: Path) -> None:
    """Verify malformed source data is reported without echoing private values."""
    rows_path = tmp_path / "rows.csv"
    rows_path.write_text("Title,Date\n,9/14/26\nPrivate title,not-a-date\n", encoding="utf-8")
    header_path = tmp_path / "header.csv"
    header_path.write_text("Name,When\nPrivate title,9/14/26\n", encoding="utf-8")

    rows = NetflixCsvParser().parse_viewing_activity(rows_path)
    header = NetflixCsvParser().parse_viewing_activity(header_path)

    assert [item.reason for item in rows.invalid_rows] == ["missing title", "invalid date"]
    assert "Private title" not in repr(rows.invalid_rows)
    assert header.rows == ()
    assert header.invalid_rows[0].reason == "missing required columns: Date, Title"


def test_viewing_activity_reports_malformed_csv_without_source_values(tmp_path: Path) -> None:
    """Verify structurally malformed CSV becomes a privacy-safe diagnostic."""
    path = tmp_path / "malformed.csv"
    path.write_text('Title,Date\n"Private unterminated title,9/14/26\n', encoding="utf-8")

    batch = NetflixCsvParser().parse_viewing_activity(path)

    assert batch.rows == ()
    assert batch.invalid_rows[0].reason == "malformed CSV"
    assert "Private unterminated title" not in repr(batch.invalid_rows)


def test_ratings_parse_numeric_reactions_optional_type_and_dates(tmp_path: Path) -> None:
    """Verify the supported richer ratings format normalizes known interactions."""
    path = tmp_path / "Ratings.csv"
    path.write_text(
        "Profile Name,Title Name,Rating Type,Star Value,Thumbs Value,Device Model,Event Utc Ts,Region View Date\n"
        "Default,Synthetic movie,Stars,4,0,Synthetic device,2026-09-14T12:30:00Z,09/14/26\n"
        "Default,Synthetic show,Thumbs,0,2,Synthetic device,2026-09-13 10:00:00,09/13/26\n"
        "Default,Rejected,Thumbs,0,0,Synthetic device,2026-09-12T10:00:00Z,09/12/26\n",
        encoding="utf-8",
    )

    batch = NetflixCsvParser().parse_ratings(path)

    assert len(batch.rows) == EXPECTED_RATING_ROWS
    assert batch.rows[0].value == EXPECTED_NUMERIC_RATING / 2
    assert batch.rows[0].rated_at == datetime(2026, 9, 14, 12, 30, tzinfo=UTC)
    assert batch.rows[1].like_state is LikeState.LIKED
    assert batch.rows[1].rated_at == datetime(2026, 9, 13, 10, tzinfo=UTC)
    assert batch.invalid_rows[0].reason == "unsupported rating value"
