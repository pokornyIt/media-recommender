"""Tests for provider-independent streaming availability models."""

from datetime import UTC, datetime

import pytest

from media_recommender.domain import (
    AvailabilityProvenance,
    AvailabilityType,
    MediaId,
    StreamingAvailability,
    StreamingService,
)


def test_availability_normalizes_region_service_and_provenance() -> None:
    """Verify regional availability remains a shared timestamped source fact."""
    availability = StreamingAvailability(
        media_id=MediaId.new(),
        service=StreamingService(" 8 ", " Netflix "),
        region=" cz ",
        availability_type=AvailabilityType.SUBSCRIPTION,
        provenance=AvailabilityProvenance(" TMDB ", datetime(2026, 9, 14, 18, tzinfo=UTC), " JustWatch "),
    )

    assert availability.region == "CZ"
    assert availability.service == StreamingService("8", "Netflix")
    assert availability.provenance.provider == "tmdb"
    assert availability.provenance.attribution == "JustWatch"


@pytest.mark.parametrize("region", ["", "CZE", "1Z"])
def test_availability_rejects_invalid_regions(region: str) -> None:
    """Verify availability cannot silently lose its regional semantics."""
    with pytest.raises(ValueError, match="region"):
        StreamingAvailability(
            media_id=MediaId.new(),
            service=StreamingService("8", "Netflix"),
            region=region,
            availability_type=AvailabilityType.SUBSCRIPTION,
            provenance=AvailabilityProvenance("tmdb", datetime(2026, 9, 14, 18, tzinfo=UTC)),
        )


def test_availability_requires_aware_observation_timestamp() -> None:
    """Verify freshness cannot be persisted with an ambiguous timezone."""
    with pytest.raises(ValueError, match="timezone-aware"):
        AvailabilityProvenance("tmdb", datetime(2026, 9, 14, 18))  # noqa: DTZ001
