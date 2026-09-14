"""SQLAlchemy repository for current regional streaming availability."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from media_recommender.persistence.availability_mapping import (
    availability_to_record,
    record_to_availability,
    update_availability_record,
)
from media_recommender.persistence.database import session_scope
from media_recommender.persistence.models import StreamingAvailabilityRecord

if TYPE_CHECKING:
    from collections.abc import Sequence

    from media_recommender.domain import MediaId, StreamingAvailability
    from media_recommender.persistence.database import SessionFactory

type AvailabilityKey = tuple[str, str]
REGION_CODE_LENGTH = 2


class SqlAlchemyAvailabilityRepository:
    """Persist complete source snapshots independently from personal state."""

    def __init__(self, session_factory: SessionFactory) -> None:
        """Initialize the availability repository.

        :param session_factory: Factory providing asynchronous sessions.
        """
        self._session_factory = session_factory

    async def replace_snapshot(
        self,
        media_id: MediaId,
        region: str,
        source_provider: str,
        availability: Sequence[StreamingAvailability],
    ) -> int:
        """Atomically upsert current facts and remove disappeared facts.

        :param media_id: Shared catalog identity.
        :param region: Region covered by the complete snapshot.
        :param source_provider: Provider that supplied the snapshot.
        :param availability: Current normalized availability facts.
        :return: Number of disappeared facts removed from persistence.
        :raises ValueError: If facts do not belong to the declared snapshot or contain duplicates.
        """
        normalized_region = region.strip().upper()
        normalized_source = source_provider.strip().lower()
        if (
            len(normalized_region) != REGION_CODE_LENGTH
            or not normalized_region.isascii()
            or not normalized_region.isalpha()
        ):
            msg = "Availability snapshot region must be a two-letter ISO 3166-1 alpha-2 code"
            raise ValueError(msg)
        if not normalized_source:
            msg = "Availability snapshot source provider must not be empty"
            raise ValueError(msg)
        incoming: dict[AvailabilityKey, StreamingAvailability] = {}
        for item in availability:
            if (
                item.media_id != media_id
                or item.region != normalized_region
                or item.provenance.provider != normalized_source
            ):
                msg = "Availability facts must belong to the replaced media, region, and source snapshot"
                raise ValueError(msg)
            key = (item.service.source_id, item.availability_type.value)
            if key in incoming:
                msg = "Availability snapshot must not contain duplicate facts"
                raise ValueError(msg)
            incoming[key] = item

        statement = select(StreamingAvailabilityRecord).where(
            StreamingAvailabilityRecord.media_id == str(media_id.value),
            StreamingAvailabilityRecord.region == normalized_region,
            StreamingAvailabilityRecord.source_provider == normalized_source,
        )
        async with session_scope(self._session_factory) as session:
            records = (await session.scalars(statement)).all()
            existing = {(record.source_service_id, record.availability_type): record for record in records}
            for key, item in incoming.items():
                record = existing.get(key)
                if record is None:
                    session.add(availability_to_record(item))
                else:
                    update_availability_record(record, item)
            removed = [record for key, record in existing.items() if key not in incoming]
            for record in removed:
                await session.delete(record)
        return len(removed)

    async def list_for_media(
        self,
        media_id: MediaId,
        *,
        region: str | None = None,
    ) -> tuple[StreamingAvailability, ...]:
        """Return current availability in deterministic region and service order.

        :param media_id: Shared catalog identity.
        :param region: Optional region restriction.
        :return: Matching current availability facts.
        """
        statement = select(StreamingAvailabilityRecord).where(
            StreamingAvailabilityRecord.media_id == str(media_id.value)
        )
        if region is not None:
            statement = statement.where(StreamingAvailabilityRecord.region == region.strip().upper())
        statement = statement.order_by(
            StreamingAvailabilityRecord.region,
            StreamingAvailabilityRecord.service_name,
            StreamingAvailabilityRecord.availability_type,
            StreamingAvailabilityRecord.source_provider,
            StreamingAvailabilityRecord.source_service_id,
        )
        async with self._session_factory() as session:
            records = (await session.scalars(statement)).all()
        return tuple(record_to_availability(record) for record in records)
