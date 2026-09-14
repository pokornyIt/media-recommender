"""Provider-independent regional streaming availability models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from media_recommender.domain.media import MediaId

REGION_CODE_LENGTH = 2


def _normalized_text(value: str, field_name: str, *, lowercase: bool = False) -> str:
    """Return normalized non-empty text.

    :param value: Text to normalize.
    :param field_name: Human-readable field name for validation errors.
    :param lowercase: Whether to lowercase the normalized value.
    :return: Normalized text.
    :raises ValueError: If the value is empty.
    """
    normalized = value.strip()
    if not normalized:
        msg = f"{field_name} must not be empty"
        raise ValueError(msg)
    return normalized.lower() if lowercase else normalized


class AvailabilityType(StrEnum):
    """Commercial access model reported by an availability source."""

    SUBSCRIPTION = "subscription"
    RENT = "rent"
    BUY = "buy"
    FREE = "free"
    ADS = "ads"


@dataclass(frozen=True, slots=True)
class StreamingService:
    """Streaming service identity within one availability source."""

    source_id: str
    name: str

    def __post_init__(self) -> None:
        """Normalize and validate service identity fields."""
        object.__setattr__(self, "source_id", _normalized_text(self.source_id, "Service source ID"))
        object.__setattr__(self, "name", _normalized_text(self.name, "Service name"))


@dataclass(frozen=True, slots=True)
class AvailabilityProvenance:
    """Origin and freshness metadata for a streaming availability fact."""

    provider: str
    observed_at: datetime
    attribution: str | None = None

    def __post_init__(self) -> None:
        """Normalize the provider and require an unambiguous timestamp.

        :raises ValueError: If the timestamp is not timezone-aware.
        """
        object.__setattr__(self, "provider", _normalized_text(self.provider, "Provider", lowercase=True))
        if self.attribution is not None:
            object.__setattr__(self, "attribution", _normalized_text(self.attribution, "Attribution"))
        if self.observed_at.utcoffset() is None:
            msg = "Availability observation timestamp must be timezone-aware"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class StreamingAvailability:
    """Current regional streaming access for one shared catalog item."""

    media_id: MediaId
    service: StreamingService
    region: str
    availability_type: AvailabilityType
    provenance: AvailabilityProvenance

    def __post_init__(self) -> None:
        """Normalize and validate the ISO 3166-1 alpha-2 region code.

        :raises ValueError: If the region is not a two-letter country code.
        """
        region = self.region.strip().upper()
        if len(region) != REGION_CODE_LENGTH or not region.isascii() or not region.isalpha():
            msg = "Availability region must be a two-letter ISO 3166-1 alpha-2 code"
            raise ValueError(msg)
        object.__setattr__(self, "region", region)
