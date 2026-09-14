"""Provider DTOs for supported Netflix CSV exports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date, datetime

    from media_recommender.domain import LikeState


@dataclass(frozen=True, slots=True)
class NetflixInvalidRow:
    """Privacy-safe parse failure for one CSV row."""

    row_number: int
    reason: str


@dataclass(frozen=True, slots=True)
class NetflixViewingRow:
    """One row from a Netflix Viewing Activity CSV export."""

    row_number: int
    title: str
    watched_on: date


@dataclass(frozen=True, slots=True)
class NetflixRatingRow:
    """One row from a supported Netflix account-data ratings CSV."""

    row_number: int
    profile_name: str
    title: str
    rated_at: datetime | None
    value: float | None
    like_state: LikeState | None


@dataclass(frozen=True, slots=True)
class NetflixViewingBatch:
    """Parsed Viewing Activity records plus safe diagnostics."""

    source_digest: str
    rows: tuple[NetflixViewingRow, ...]
    invalid_rows: tuple[NetflixInvalidRow, ...]


@dataclass(frozen=True, slots=True)
class NetflixRatingBatch:
    """Parsed rating records plus safe diagnostics."""

    source_digest: str
    rows: tuple[NetflixRatingRow, ...]
    invalid_rows: tuple[NetflixInvalidRow, ...]
