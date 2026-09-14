"""Provider-independent personal media domain models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from datetime import datetime

    from media_recommender.domain.media import MediaId

DEFAULT_PROFILE_ID = UUID("00000000-0000-0000-0000-000000000001")
MAX_RATING: Final = 10


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


def _require_aware(value: datetime, field_name: str) -> None:
    """Require a timezone-aware timestamp.

    :param value: Timestamp to validate.
    :param field_name: Human-readable field name for validation errors.
    :raises ValueError: If the timestamp has no UTC offset.
    """
    if value.utcoffset() is None:
        msg = f"{field_name} must be timezone-aware"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ProfileId:
    """Stable internal owner identity independent of external providers."""

    value: UUID

    @classmethod
    def new(cls) -> ProfileId:
        """Create a new random internal profile identity.

        :return: Newly generated profile identity.
        """
        return cls(uuid4())


@dataclass(frozen=True, slots=True)
class Profile:
    """Internal owner of personal media state."""

    id: ProfileId
    name: str
    is_default: bool = False

    def __post_init__(self) -> None:
        """Normalize the profile display name."""
        object.__setattr__(self, "name", _normalized_text(self.name, "Profile name"))


def default_profile() -> Profile:
    """Return the deterministic implicit profile for single-user operation.

    :return: Default internal profile.
    """
    return Profile(id=ProfileId(DEFAULT_PROFILE_ID), name="Default", is_default=True)


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Origin and ingest metadata for a personal-media record."""

    provider: str
    imported_at: datetime
    source_record_id: str | None = None
    synchronization_id: str | None = None

    def __post_init__(self) -> None:
        """Normalize source identifiers and validate the ingest timestamp."""
        object.__setattr__(self, "provider", _normalized_text(self.provider, "Provider", lowercase=True))
        _require_aware(self.imported_at, "Imported timestamp")
        for field_name, label in (
            ("source_record_id", "Source record ID"),
            ("synchronization_id", "Synchronization ID"),
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _normalized_text(value, label))


@dataclass(frozen=True, slots=True)
class ViewingEventId:
    """Stable identity of one viewing-history event."""

    value: UUID

    @classmethod
    def new(cls) -> ViewingEventId:
        """Create a new viewing-event identity.

        :return: Newly generated viewing-event identity.
        """
        return cls(uuid4())


class WatchStatus(StrEnum):
    """Explicit or derived knowledge about whether an item was watched."""

    UNKNOWN = "unknown"
    UNWATCHED = "unwatched"
    WATCHED = "watched"


@dataclass(frozen=True, slots=True)
class ViewingEvent:
    """One explicit watch of a shared catalog item by an internal profile."""

    id: ViewingEventId
    profile_id: ProfileId
    media_id: MediaId
    watched_at: datetime
    provenance: SourceProvenance

    def __post_init__(self) -> None:
        """Validate the watch timestamp."""
        _require_aware(self.watched_at, "Watched timestamp")


@dataclass(frozen=True, slots=True)
class WatchStateId:
    """Stable identity of one provider-supplied watch state."""

    value: UUID

    @classmethod
    def new(cls) -> WatchStateId:
        """Create a new watch-state identity.

        :return: Newly generated watch-state identity.
        """
        return cls(uuid4())


@dataclass(frozen=True, slots=True)
class WatchState:
    """Explicit provider-independent watched or unwatched state."""

    id: WatchStateId
    profile_id: ProfileId
    media_id: MediaId
    status: WatchStatus
    provenance: SourceProvenance

    def __post_init__(self) -> None:
        """Reject persistence of unknown state, which is represented by absence.

        :raises ValueError: If the supplied status is unknown.
        """
        if self.status is WatchStatus.UNKNOWN:
            msg = "Unknown watch state must be represented by absence of explicit state"
            raise ValueError(msg)


class LikeState(StrEnum):
    """Explicit provider-independent reaction when one is known."""

    LIKED = "liked"
    DISLIKED = "disliked"


@dataclass(frozen=True, slots=True)
class RatingId:
    """Stable identity of one personal rating record."""

    value: UUID

    @classmethod
    def new(cls) -> RatingId:
        """Create a new rating identity.

        :return: Newly generated rating identity.
        """
        return cls(uuid4())


@dataclass(frozen=True, slots=True)
class Rating:
    """Numeric and/or explicit reaction independent of watched state."""

    id: RatingId
    profile_id: ProfileId
    media_id: MediaId
    provenance: SourceProvenance
    value: float | None = None
    like_state: LikeState | None = None

    def __post_init__(self) -> None:
        """Validate that the rating carries a supported explicit value.

        :raises ValueError: If no rating information exists or a numeric value is out of range.
        """
        if self.value is None and self.like_state is None:
            msg = "A rating must contain a numeric value or explicit like state"
            raise ValueError(msg)
        if self.value is not None and not 0 <= self.value <= MAX_RATING:
            msg = "Numeric rating must be between 0 and 10"
            raise ValueError(msg)


class PreferenceKind(StrEnum):
    """Concrete criterion available to later deterministic recommendations."""

    GENRE = "genre"
    PRODUCTION_COUNTRY = "production_country"
    PRODUCTION_REGION = "production_region"
    RUNTIME_MINUTES = "runtime_minutes"
    RELEASE_YEAR = "release_year"
    PROVIDER = "provider"


class PreferenceEffect(StrEnum):
    """Whether a criterion is preferred or excluded."""

    PREFER = "prefer"
    EXCLUDE = "exclude"


@dataclass(frozen=True, slots=True)
class PreferenceId:
    """Stable identity of one preference or exclusion."""

    value: UUID

    @classmethod
    def new(cls) -> PreferenceId:
        """Create a new preference identity.

        :return: Newly generated preference identity.
        """
        return cls(uuid4())


@dataclass(frozen=True, slots=True)
class Preference:
    """Small typed preference or exclusion criterion owned by a profile."""

    id: PreferenceId
    profile_id: ProfileId
    kind: PreferenceKind
    effect: PreferenceEffect
    value: str | None = None
    minimum: int | None = None
    maximum: int | None = None

    def __post_init__(self) -> None:
        """Validate text and numeric criterion shapes.

        :raises ValueError: If values do not match the selected preference kind.
        """
        numeric_kind = self.kind in {PreferenceKind.RUNTIME_MINUTES, PreferenceKind.RELEASE_YEAR}
        if numeric_kind:
            if self.value is not None or (self.minimum is None and self.maximum is None):
                msg = "Numeric preferences require a minimum and/or maximum and no text value"
                raise ValueError(msg)
            if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
                msg = "Preference minimum must not exceed maximum"
                raise ValueError(msg)
            if (self.minimum is not None and self.minimum < 0) or (self.maximum is not None and self.maximum < 0):
                msg = "Preference bounds must not be negative"
                raise ValueError(msg)
        elif self.value is None or self.minimum is not None or self.maximum is not None:
            msg = "Text preferences require one value and no numeric bounds"
            raise ValueError(msg)
        else:
            object.__setattr__(self, "value", _normalized_text(self.value, "Preference value"))


@dataclass(frozen=True, slots=True)
class ProviderProfileMappingId:
    """Stable identity of one external-to-internal profile mapping."""

    value: UUID

    @classmethod
    def new(cls) -> ProviderProfileMappingId:
        """Create a new provider mapping identity.

        :return: Newly generated mapping identity.
        """
        return cls(uuid4())


@dataclass(frozen=True, slots=True)
class ProviderProfileMapping:
    """Map an external provider identity to an internal profile owner."""

    id: ProviderProfileMappingId
    profile_id: ProfileId
    provider: str
    external_profile_id: str
    synchronized_at: datetime | None = None

    def __post_init__(self) -> None:
        """Normalize identifiers and validate the optional synchronization timestamp."""
        object.__setattr__(self, "provider", _normalized_text(self.provider, "Provider", lowercase=True))
        object.__setattr__(
            self,
            "external_profile_id",
            _normalized_text(self.external_profile_id, "External profile ID"),
        )
        if self.synchronized_at is not None:
            _require_aware(self.synchronized_at, "Synchronized timestamp")
