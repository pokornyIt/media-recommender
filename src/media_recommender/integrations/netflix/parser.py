"""Strict, privacy-conscious parsers for supported Netflix CSV exports."""

from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime
from io import StringIO
from typing import TYPE_CHECKING, Final

from media_recommender.domain import LikeState
from media_recommender.integrations.netflix.models import (
    NetflixInvalidRow,
    NetflixRatingBatch,
    NetflixRatingRow,
    NetflixViewingBatch,
    NetflixViewingRow,
)

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

DEFAULT_DATE_FORMAT: Final = "%m/%d/%y"
MAX_RATING: Final = 10
THUMBS_DOWN_VALUE: Final = 1
THUMBS_UP_VALUE: Final = 2


class NetflixCsvParser:
    """Parse supported Netflix exports without retaining or logging raw files."""

    def __init__(self, *, date_format: str = DEFAULT_DATE_FORMAT) -> None:
        """Configure the explicit locale-specific date format.

        :param date_format: ``datetime.strptime`` format used for source dates.
        """
        self._date_format = date_format

    def parse_viewing_activity(self, path: Path) -> NetflixViewingBatch:
        """Parse the normal two-column Netflix Viewing Activity export.

        Extra columns are ignored. Invalid data rows are reported without including
        their private values.

        :param path: Local path to the private CSV export.
        :return: Parsed provider DTOs and row-level diagnostics.
        """
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        reader, header_error = self._reader(payload, required_columns={"Title", "Date"})
        if header_error is not None:
            return NetflixViewingBatch(digest, (), (header_error,))

        rows: list[NetflixViewingRow] = []
        invalid: list[NetflixInvalidRow] = []
        try:
            for row_number, row in enumerate(reader, start=2):
                title = (row.get("Title") or "").strip()
                watched_on = self._parse_date(row.get("Date"))
                if not title:
                    invalid.append(NetflixInvalidRow(row_number, "missing title"))
                elif watched_on is None:
                    invalid.append(NetflixInvalidRow(row_number, "invalid date"))
                elif None in row:
                    invalid.append(NetflixInvalidRow(row_number, "unexpected field count"))
                else:
                    rows.append(NetflixViewingRow(row_number, title, watched_on))
        except csv.Error:
            invalid.append(NetflixInvalidRow(reader.line_num, "malformed CSV"))
        return NetflixViewingBatch(digest, tuple(rows), tuple(invalid))

    def parse_ratings(self, path: Path) -> NetflixRatingBatch:
        """Parse the supported richer Netflix account-data ratings export.

        The supported columns match the ``CONTENT_INTERACTION/Ratings.csv``
        account-data export. Device metadata is safely ignored.

        :param path: Local path to the private ratings CSV.
        :return: Parsed provider DTOs and row-level diagnostics.
        """
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        required = {"Profile Name", "Rating Type", "Star Value", "Thumbs Value", "Title Name"}
        reader, header_error = self._reader(payload, required_columns=required)
        if header_error is not None:
            return NetflixRatingBatch(digest, (), (header_error,))

        rows: list[NetflixRatingRow] = []
        invalid: list[NetflixInvalidRow] = []
        try:
            for row_number, row in enumerate(reader, start=2):
                parsed = self._parse_rating_row(row_number, row)
                if isinstance(parsed, NetflixInvalidRow):
                    invalid.append(parsed)
                else:
                    rows.append(parsed)
        except csv.Error:
            invalid.append(NetflixInvalidRow(reader.line_num, "malformed CSV"))
        return NetflixRatingBatch(digest, tuple(rows), tuple(invalid))

    def _reader(
        self,
        payload: bytes,
        *,
        required_columns: set[str],
    ) -> tuple[csv.DictReader[str], NetflixInvalidRow | None]:
        """Decode bytes and create a strict dictionary reader.

        :param payload: Raw private CSV bytes.
        :param required_columns: Exact columns required by the supported format.
        :return: Reader and an optional header-level diagnostic.
        """
        text = _decode_csv(payload)
        reader = csv.DictReader(StringIO(text, newline=""), strict=True)
        actual = set(reader.fieldnames or ())
        missing = sorted(required_columns - actual)
        error = NetflixInvalidRow(1, f"missing required columns: {', '.join(missing)}") if missing else None
        return reader, error

    def _parse_date(self, value: str | None) -> date | None:
        """Parse a source date using the explicitly configured locale format.

        :param value: Optional raw date value.
        :return: Parsed calendar date, or ``None`` for an absent or invalid value.
        """
        if value is None:
            return None
        try:
            return datetime.strptime(value.strip(), self._date_format).date()  # noqa: DTZ007
        except ValueError:
            return None

    def _parse_rating_row(
        self,
        row_number: int,
        row: dict[str | None, str | list[str] | None],
    ) -> NetflixRatingRow | NetflixInvalidRow:
        """Validate and parse one supported ratings row.

        :param row_number: One-based physical CSV row number.
        :param row: Dictionary row produced by the CSV parser.
        :return: Parsed rating DTO or a privacy-safe diagnostic.
        """
        if None in row:
            return NetflixInvalidRow(row_number, "unexpected field count")
        profile_name = str(row.get("Profile Name") or "").strip()
        title = str(row.get("Title Name") or "").strip()
        rating_type = str(row.get("Rating Type") or "").strip()
        if not profile_name or not title or not rating_type:
            return NetflixInvalidRow(row_number, "missing required value")
        value, like_state = _parse_rating(
            rating_type,
            str(row.get("Star Value") or "").strip(),
            str(row.get("Thumbs Value") or "").strip(),
        )
        if value is None and like_state is None:
            return NetflixInvalidRow(row_number, "unsupported rating value")
        event_timestamp = str(row.get("Event Utc Ts") or "").strip()
        region_date = str(row.get("Region View Date") or "").strip()
        rated_at = _parse_utc_timestamp(event_timestamp)
        if event_timestamp and rated_at is None:
            return NetflixInvalidRow(row_number, "invalid event timestamp")
        if rated_at is None and region_date:
            parsed_date = self._parse_date(region_date)
            if parsed_date is None:
                return NetflixInvalidRow(row_number, "invalid regional date")
            rated_at = datetime.combine(parsed_date, datetime.min.time(), UTC)
        return NetflixRatingRow(row_number, profile_name, title, rated_at, value, like_state)


def _decode_csv(payload: bytes) -> str:
    """Decode common Netflix export encodings in deterministic order.

    :param payload: Raw CSV bytes.
    :return: Decoded CSV text.
    """
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("cp1252")


def _parse_rating(
    rating_type: str,
    star_value: str,
    thumbs_value: str,
) -> tuple[float | None, LikeState | None]:
    """Normalize documented Netflix star and thumb rating values.

    :param rating_type: Provider rating-system discriminator.
    :param star_value: Provider star value text.
    :param thumbs_value: Provider numeric thumb value text.
    :return: Numeric rating and explicit reaction dimensions.
    """
    normalized_type = rating_type.casefold()
    if "thumb" in normalized_type:
        return None, _parse_thumb_rating(thumbs_value)
    if "star" not in normalized_type:
        return None, None
    try:
        value = float(star_value)
    except ValueError:
        return None, None
    return (value, None) if 0 <= value <= MAX_RATING else (None, None)


def _parse_thumb_rating(value: str) -> LikeState | None:
    """Map documented Netflix thumb values to an explicit reaction.

    :param value: Provider numeric thumb value text.
    :return: Explicit like/dislike state, or ``None`` for no supported rating.
    """
    try:
        numeric_thumb = int(value)
    except ValueError:
        return None
    if numeric_thumb == THUMBS_DOWN_VALUE:
        return LikeState.DISLIKED
    if numeric_thumb == THUMBS_UP_VALUE:
        return LikeState.LIKED
    return None


def _parse_utc_timestamp(value: str) -> datetime | None:
    """Parse an optional ISO-like timestamp from the explicitly UTC source column.

    :param value: Provider UTC timestamp text.
    :return: UTC timestamp, or ``None`` for an absent or invalid value.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.utcoffset() is None else parsed.astimezone(UTC)
