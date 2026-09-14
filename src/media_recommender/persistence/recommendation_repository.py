"""Efficient SQLite data source for deterministic recommendation filtering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from media_recommender.application import RecommendationCandidate
from media_recommender.domain import WatchStatus
from media_recommender.persistence.availability_mapping import record_to_availability
from media_recommender.persistence.mapping import record_to_media
from media_recommender.persistence.models import (
    LibraryPresenceRecord,
    MediaRecord,
    PreferenceRecord,
    RatingRecord,
    StreamingAvailabilityRecord,
    ViewingEventRecord,
    WatchStateRecord,
)
from media_recommender.persistence.personal_mapping import (
    record_to_library_presence,
    record_to_preference,
    record_to_rating,
)
from media_recommender.persistence.repository import MEDIA_LOAD_OPTIONS

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from media_recommender.domain import MediaType, Preference, ProfileId
    from media_recommender.persistence.database import SessionFactory


class SqlAlchemyRecommendationDataSource:
    """Load catalog and profile facts in a constant number of SQLite queries."""

    def __init__(self, session_factory: SessionFactory) -> None:
        """Initialize the recommendation data source.

        :param session_factory: Factory providing asynchronous sessions.
        """
        self._session_factory = session_factory

    async def list_candidates(
        self,
        profile_id: ProfileId,
        media_types: frozenset[MediaType],
    ) -> Sequence[RecommendationCandidate]:
        """Return normalized candidate snapshots without per-item queries.

        :param profile_id: Personal-state owner.
        :param media_types: Optional media-type restriction.
        :return: Candidate snapshots ordered by title and identity.
        """
        media_statement = select(MediaRecord).options(*MEDIA_LOAD_OPTIONS).order_by(MediaRecord.title, MediaRecord.id)
        if media_types:
            media_statement = media_statement.where(
                MediaRecord.media_type.in_(sorted(media_type.value for media_type in media_types))
            )

        profile_value = str(profile_id.value)
        async with self._session_factory() as session:
            media_records = list((await session.scalars(media_statement)).all())
            if not media_records:
                return ()
            media_values = [record.id for record in media_records]
            viewing_events = list(
                (
                    await session.scalars(
                        select(ViewingEventRecord).where(
                            ViewingEventRecord.profile_id == profile_value,
                            ViewingEventRecord.media_id.in_(media_values),
                        )
                    )
                ).all()
            )
            watch_states = list(
                (
                    await session.scalars(
                        select(WatchStateRecord).where(
                            WatchStateRecord.profile_id == profile_value,
                            WatchStateRecord.media_id.in_(media_values),
                        )
                    )
                ).all()
            )
            ratings = list(
                (
                    await session.scalars(
                        select(RatingRecord)
                        .where(RatingRecord.profile_id == profile_value, RatingRecord.media_id.in_(media_values))
                        .order_by(RatingRecord.id)
                    )
                ).all()
            )
            library_presence = list(
                (
                    await session.scalars(
                        select(LibraryPresenceRecord)
                        .where(
                            LibraryPresenceRecord.profile_id == profile_value,
                            LibraryPresenceRecord.media_id.in_(media_values),
                        )
                        .order_by(LibraryPresenceRecord.source_provider, LibraryPresenceRecord.source_record_id)
                    )
                ).all()
            )
            availability = list(
                (
                    await session.scalars(
                        select(StreamingAvailabilityRecord)
                        .where(StreamingAvailabilityRecord.media_id.in_(media_values))
                        .order_by(
                            StreamingAvailabilityRecord.region,
                            StreamingAvailabilityRecord.service_name,
                            StreamingAvailabilityRecord.availability_type,
                        )
                    )
                ).all()
            )

        watched_media = {record.media_id for record in viewing_events}
        states_by_media: dict[str, set[str]] = {}
        for record in watch_states:
            states_by_media.setdefault(record.media_id, set()).add(record.status)
        ratings_by_media = _group_by_media(ratings, lambda record: record.media_id)
        libraries_by_media = _group_by_media(library_presence, lambda record: record.media_id)
        availability_by_media = _group_by_media(availability, lambda record: record.media_id)

        return tuple(
            RecommendationCandidate(
                media=record_to_media(record),
                watch_status=_watch_status(record.id, watched_media, states_by_media),
                ratings=tuple(record_to_rating(item) for item in ratings_by_media.get(record.id, ())),
                library_presence=tuple(
                    record_to_library_presence(item) for item in libraries_by_media.get(record.id, ())
                ),
                streaming_availability=tuple(
                    record_to_availability(item) for item in availability_by_media.get(record.id, ())
                ),
            )
            for record in media_records
        )

    async def list_preferences(self, profile_id: ProfileId) -> Sequence[Preference]:
        """Return persisted profile criteria in deterministic identity order.

        :param profile_id: Personal-state owner.
        :return: Persisted preference records.
        """
        statement = (
            select(PreferenceRecord)
            .where(PreferenceRecord.profile_id == str(profile_id.value))
            .order_by(PreferenceRecord.id)
        )
        async with self._session_factory() as session:
            records = (await session.scalars(statement)).all()
        return tuple(record_to_preference(record) for record in records)


def _group_by_media[RecordT](
    records: Sequence[RecordT],
    media_id: Callable[[RecordT], str],
) -> dict[str, list[RecordT]]:
    """Group ORM records carrying a media ID without changing their order.

    :param records: Ordered records with a string ``media_id`` attribute.
    :param media_id: Function returning a record's shared catalog identity.
    :return: Records grouped by shared catalog identity.
    """
    grouped: dict[str, list[RecordT]] = {}
    for record in records:
        grouped.setdefault(media_id(record), []).append(record)
    return grouped


def _watch_status(media_id: str, watched_media: set[str], states_by_media: dict[str, set[str]]) -> WatchStatus:
    """Derive three-state watch status with viewing events taking precedence.

    :param media_id: Shared catalog identity.
    :param watched_media: Identities with definitive viewing events.
    :param states_by_media: Explicit provider states grouped by media identity.
    :return: Derived watched, unwatched, or unknown state.
    """
    if media_id in watched_media or WatchStatus.WATCHED.value in states_by_media.get(media_id, set()):
        return WatchStatus.WATCHED
    if WatchStatus.UNWATCHED.value in states_by_media.get(media_id, set()):
        return WatchStatus.UNWATCHED
    return WatchStatus.UNKNOWN
