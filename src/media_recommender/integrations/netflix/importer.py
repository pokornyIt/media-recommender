"""File-oriented Netflix import orchestration and provider normalization."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING, Final

from media_recommender.application.identity import MediaIdentityCandidate
from media_recommender.application.imports import (
    InvalidImportRecord,
    PersonalImportKind,
    PersonalImportRecord,
)
from media_recommender.domain import MediaType
from media_recommender.integrations.netflix.parser import NetflixCsvParser

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from media_recommender.application.imports import PersonalImportResult, PersonalMediaImportService
    from media_recommender.integrations.netflix.models import NetflixInvalidRow, NetflixRatingRow, NetflixViewingRow

NETFLIX_PROVIDER: Final = "netflix"
TV_TITLE_MINIMUM_PARTS: Final = 3


class NetflixFileImporter:
    """Import supported local Netflix CSV files through the application service."""

    def __init__(self, service: PersonalMediaImportService, *, parser: NetflixCsvParser | None = None) -> None:
        """Initialize the importer with application and parsing boundaries.

        :param service: Provider-independent personal import service.
        :param parser: Optional parser configured for the export's date locale.
        """
        self._service = service
        self._parser = parser or NetflixCsvParser()

    async def import_viewing_activity(self, path: Path, *, external_profile_id: str) -> PersonalImportResult:
        """Import a normal Netflix Viewing Activity CSV for one profile.

        :param path: Local private export path.
        :param external_profile_id: Stable caller-supplied Netflix profile label.
        :return: Complete privacy-safe import report.
        """
        batch = self._parser.parse_viewing_activity(path)
        records = _viewing_records(batch.rows, external_profile_id)
        return await self._service.import_records(
            provider=NETFLIX_PROVIDER,
            external_profile_id=external_profile_id,
            synchronization_id=batch.source_digest,
            records=records,
            invalid_records=_invalid_records(batch.invalid_rows),
        )

    async def import_ratings(self, path: Path, *, external_profile_id: str) -> PersonalImportResult:
        """Import a supported Netflix account-data ratings CSV for one profile.

        Rows belonging to a different external profile are reported as invalid and
        never merged into the requested internal owner.

        :param path: Local private ratings export path.
        :param external_profile_id: Netflix profile represented by this import.
        :return: Complete privacy-safe import report.
        """
        batch = self._parser.parse_ratings(path)
        matching = tuple(row for row in batch.rows if row.profile_name == external_profile_id)
        mismatched = tuple(
            InvalidImportRecord(row.row_number, "profile does not match requested import profile")
            for row in batch.rows
            if row.profile_name != external_profile_id
        )
        return await self._service.import_records(
            provider=NETFLIX_PROVIDER,
            external_profile_id=external_profile_id,
            synchronization_id=batch.source_digest,
            records=_rating_records(matching, external_profile_id),
            invalid_records=(*_invalid_records(batch.invalid_rows), *mismatched),
        )


def _viewing_records(
    rows: Iterable[NetflixViewingRow],
    external_profile_id: str,
) -> tuple[PersonalImportRecord, ...]:
    """Normalize viewing DTOs and assign stable duplicate-aware identities.

    :param rows: Parsed Netflix viewing rows.
    :param external_profile_id: Netflix profile represented by the rows.
    :return: Provider-independent viewing import records.
    """
    occurrences: defaultdict[str, int] = defaultdict(int)
    records: list[PersonalImportRecord] = []
    for row in rows:
        candidate, alternative = _candidates(row.title)
        fingerprint = f"{external_profile_id}\0{row.title}\0{row.watched_on.isoformat()}"
        occurrences[fingerprint] += 1
        records.append(
            PersonalImportRecord(
                row_number=row.row_number,
                kind=PersonalImportKind.VIEWING,
                source_record_id=_source_id("viewing", fingerprint, occurrences[fingerprint]),
                candidate=candidate,
                alternative_candidate=alternative,
                occurred_at=datetime.combine(row.watched_on, time.min, UTC),
            )
        )
    return tuple(records)


def _rating_records(
    rows: Iterable[NetflixRatingRow],
    external_profile_id: str,
) -> tuple[PersonalImportRecord, ...]:
    """Normalize rating DTOs and assign stable source identities.

    :param rows: Parsed Netflix rating rows.
    :param external_profile_id: Netflix profile represented by the rows.
    :return: Provider-independent rating import records.
    """
    occurrences: defaultdict[str, int] = defaultdict(int)
    records: list[PersonalImportRecord] = []
    for row in rows:
        candidate, alternative = _candidates(row.title)
        date_part = row.rated_at.isoformat() if row.rated_at is not None else ""
        fingerprint = f"{external_profile_id}\0{row.title}\0{date_part}\0{row.value}\0{row.like_state}"
        occurrences[fingerprint] += 1
        records.append(
            PersonalImportRecord(
                row_number=row.row_number,
                kind=PersonalImportKind.RATING,
                source_record_id=_source_id("rating", fingerprint, occurrences[fingerprint]),
                candidate=candidate,
                alternative_candidate=alternative,
                occurred_at=row.rated_at,
                rating_value=row.value,
                like_state=row.like_state,
            )
        )
    return tuple(records)


def _candidates(title: str) -> tuple[MediaIdentityCandidate, MediaIdentityCandidate]:
    """Return movie and TV evidence without silently guessing a sparse title's type.

    :param title: Provider title, including episode detail when present.
    :return: Primary and alternate media-type identity candidates.
    """
    parts = title.split(": ")
    movie = MediaIdentityCandidate(media_type=MediaType.MOVIE, title=title)
    show = MediaIdentityCandidate(media_type=MediaType.TV_SHOW, title=parts[0])
    return (show, movie) if len(parts) >= TV_TITLE_MINIMUM_PARTS else (movie, show)


def _source_id(kind: str, fingerprint: str, occurrence: int) -> str:
    """Return a stable opaque identity while keeping private titles out of storage.

    :param kind: Imported personal-record kind.
    :param fingerprint: Stable source evidence for one equivalent record.
    :param occurrence: One-based occurrence among duplicate source rows.
    :return: Opaque SHA-256 source identity.
    """
    payload = f"{kind}\0{fingerprint}\0{occurrence}".encode()
    return hashlib.sha256(payload).hexdigest()


def _invalid_records(rows: Iterable[NetflixInvalidRow]) -> tuple[InvalidImportRecord, ...]:
    """Convert provider diagnostics without copying private source values.

    :param rows: Provider parse diagnostics.
    :return: Provider-independent safe diagnostics.
    """
    return tuple(InvalidImportRecord(row.row_number, row.reason) for row in rows)
