"""Typed mapping between streaming availability and ORM records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from media_recommender.domain import (
    AvailabilityProvenance,
    AvailabilityType,
    MediaId,
    StreamingAvailability,
    StreamingService,
)
from media_recommender.persistence.models import StreamingAvailabilityRecord


def availability_to_record(availability: StreamingAvailability) -> StreamingAvailabilityRecord:
    """Map a streaming availability fact to a new ORM record.

    :param availability: Provider-independent availability fact.
    :return: New persistence record.
    """
    return StreamingAvailabilityRecord(
        media_id=str(availability.media_id.value),
        region=availability.region,
        source_provider=availability.provenance.provider,
        source_service_id=availability.service.source_id,
        service_name=availability.service.name,
        availability_type=availability.availability_type.value,
        observed_at=availability.provenance.observed_at.astimezone(UTC).isoformat(),
        attribution=availability.provenance.attribution,
    )


def update_availability_record(
    record: StreamingAvailabilityRecord,
    availability: StreamingAvailability,
) -> None:
    """Update mutable availability metadata while preserving row identity.

    :param record: Existing persistence record.
    :param availability: Latest normalized availability fact.
    """
    record.service_name = availability.service.name
    record.observed_at = availability.provenance.observed_at.astimezone(UTC).isoformat()
    record.attribution = availability.provenance.attribution


def record_to_availability(record: StreamingAvailabilityRecord) -> StreamingAvailability:
    """Map an ORM record to provider-independent streaming availability.

    :param record: Streaming availability persistence record.
    :return: Provider-independent availability fact.
    """
    return StreamingAvailability(
        media_id=MediaId(UUID(record.media_id)),
        service=StreamingService(record.source_service_id, record.service_name),
        region=record.region,
        availability_type=AvailabilityType(record.availability_type),
        provenance=AvailabilityProvenance(
            provider=record.source_provider,
            observed_at=datetime.fromisoformat(record.observed_at),
            attribution=record.attribution,
        ),
    )
