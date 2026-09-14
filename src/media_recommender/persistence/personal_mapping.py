"""Typed mapping between personal-media domain objects and ORM records."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from media_recommender.domain import (
    LikeState,
    MediaId,
    Preference,
    PreferenceEffect,
    PreferenceId,
    PreferenceKind,
    Profile,
    ProfileId,
    ProviderProfileMapping,
    ProviderProfileMappingId,
    Rating,
    RatingId,
    SourceProvenance,
    ViewingEvent,
    ViewingEventId,
    WatchState,
    WatchStateId,
    WatchStatus,
)
from media_recommender.persistence.models import (
    PreferenceRecord,
    ProfileRecord,
    ProviderProfileMappingRecord,
    RatingRecord,
    ViewingEventRecord,
    WatchStateRecord,
)


def _timestamp_to_storage(value: datetime) -> str:
    """Serialize an aware timestamp without losing its UTC offset.

    :param value: Timestamp to serialize.
    :return: ISO 8601 representation.
    """
    return value.astimezone(UTC).isoformat()


def _provenance_from_record(record: ViewingEventRecord | WatchStateRecord | RatingRecord) -> SourceProvenance:
    """Restore source provenance from a personal ORM record.

    :param record: Viewing event or rating persistence record.
    :return: Provider-independent source provenance.
    """
    return SourceProvenance(
        provider=record.source_provider,
        source_record_id=record.source_record_id,
        synchronization_id=record.synchronization_id,
        imported_at=datetime.fromisoformat(record.imported_at),
    )


def profile_to_record(profile: Profile) -> ProfileRecord:
    """Map a profile domain object to an ORM record.

    :param profile: Internal profile to map.
    :return: New profile record.
    """
    return ProfileRecord(id=str(profile.id.value), name=profile.name, is_default=profile.is_default)


def record_to_profile(record: ProfileRecord) -> Profile:
    """Map a profile ORM record to the domain.

    :param record: Profile persistence record.
    :return: Internal profile domain object.
    """
    return Profile(id=ProfileId(UUID(record.id)), name=record.name, is_default=record.is_default)


def viewing_event_to_record(event: ViewingEvent) -> ViewingEventRecord:
    """Map a viewing event to a new ORM record.

    :param event: Viewing event to map.
    :return: New viewing-event record.
    """
    return ViewingEventRecord(
        id=str(event.id.value),
        profile_id=str(event.profile_id.value),
        media_id=str(event.media_id.value),
        watched_at=_timestamp_to_storage(event.watched_at),
        source_provider=event.provenance.provider,
        source_record_id=event.provenance.source_record_id,
        synchronization_id=event.provenance.synchronization_id,
        imported_at=_timestamp_to_storage(event.provenance.imported_at),
    )


def update_viewing_event_record(record: ViewingEventRecord, event: ViewingEvent) -> None:
    """Update an existing viewing-event record while preserving its identity.

    :param record: Existing persistence record.
    :param event: Latest normalized viewing event.
    """
    record.media_id = str(event.media_id.value)
    record.watched_at = _timestamp_to_storage(event.watched_at)
    record.synchronization_id = event.provenance.synchronization_id
    record.imported_at = _timestamp_to_storage(event.provenance.imported_at)


def record_to_viewing_event(record: ViewingEventRecord) -> ViewingEvent:
    """Map a viewing-event ORM record to the domain.

    :param record: Viewing-event persistence record.
    :return: Provider-independent viewing event.
    """
    return ViewingEvent(
        id=ViewingEventId(UUID(record.id)),
        profile_id=ProfileId(UUID(record.profile_id)),
        media_id=MediaId(UUID(record.media_id)),
        watched_at=datetime.fromisoformat(record.watched_at),
        provenance=_provenance_from_record(record),
    )


def watch_state_to_record(state: WatchState) -> WatchStateRecord:
    """Map explicit watch state to a new ORM record.

    :param state: Watch state to map.
    :return: New watch-state record.
    """
    return WatchStateRecord(
        id=str(state.id.value),
        profile_id=str(state.profile_id.value),
        media_id=str(state.media_id.value),
        status=state.status.value,
        source_provider=state.provenance.provider,
        source_record_id=state.provenance.source_record_id,
        synchronization_id=state.provenance.synchronization_id,
        imported_at=_timestamp_to_storage(state.provenance.imported_at),
    )


def update_watch_state_record(record: WatchStateRecord, state: WatchState) -> None:
    """Update explicit watch state while preserving its internal identity.

    :param record: Existing watch-state record.
    :param state: Latest normalized watch state.
    """
    record.status = state.status.value
    record.source_record_id = state.provenance.source_record_id
    record.synchronization_id = state.provenance.synchronization_id
    record.imported_at = _timestamp_to_storage(state.provenance.imported_at)


def record_to_watch_state(record: WatchStateRecord) -> WatchState:
    """Map a watch-state ORM record to the domain.

    :param record: Watch-state persistence record.
    :return: Provider-independent explicit watch state.
    """
    return WatchState(
        id=WatchStateId(UUID(record.id)),
        profile_id=ProfileId(UUID(record.profile_id)),
        media_id=MediaId(UUID(record.media_id)),
        status=WatchStatus(record.status),
        provenance=_provenance_from_record(record),
    )


def rating_to_record(rating: Rating) -> RatingRecord:
    """Map a rating to a new ORM record.

    :param rating: Rating to map.
    :return: New rating record.
    """
    return RatingRecord(
        id=str(rating.id.value),
        profile_id=str(rating.profile_id.value),
        media_id=str(rating.media_id.value),
        value=rating.value,
        like_state=rating.like_state.value if rating.like_state is not None else None,
        source_provider=rating.provenance.provider,
        source_record_id=rating.provenance.source_record_id,
        synchronization_id=rating.provenance.synchronization_id,
        imported_at=_timestamp_to_storage(rating.provenance.imported_at),
    )


def update_rating_record(record: RatingRecord, rating: Rating) -> None:
    """Update an existing rating record while preserving its identity.

    :param record: Existing persistence record.
    :param rating: Latest normalized rating.
    """
    record.media_id = str(rating.media_id.value)
    record.value = rating.value
    record.like_state = rating.like_state.value if rating.like_state is not None else None
    record.synchronization_id = rating.provenance.synchronization_id
    record.imported_at = _timestamp_to_storage(rating.provenance.imported_at)


def record_to_rating(record: RatingRecord) -> Rating:
    """Map a rating ORM record to the domain.

    :param record: Rating persistence record.
    :return: Provider-independent rating.
    """
    return Rating(
        id=RatingId(UUID(record.id)),
        profile_id=ProfileId(UUID(record.profile_id)),
        media_id=MediaId(UUID(record.media_id)),
        value=record.value,
        like_state=LikeState(record.like_state) if record.like_state is not None else None,
        provenance=_provenance_from_record(record),
    )


def preference_to_record(preference: Preference) -> PreferenceRecord:
    """Map a preference to a new ORM record.

    :param preference: Preference or exclusion to map.
    :return: New preference record.
    """
    return PreferenceRecord(
        id=str(preference.id.value),
        profile_id=str(preference.profile_id.value),
        kind=preference.kind.value,
        effect=preference.effect.value,
        value=preference.value,
        minimum=preference.minimum,
        maximum=preference.maximum,
    )


def record_to_preference(record: PreferenceRecord) -> Preference:
    """Map a preference ORM record to the domain.

    :param record: Preference persistence record.
    :return: Provider-independent preference or exclusion.
    """
    return Preference(
        id=PreferenceId(UUID(record.id)),
        profile_id=ProfileId(UUID(record.profile_id)),
        kind=PreferenceKind(record.kind),
        effect=PreferenceEffect(record.effect),
        value=record.value,
        minimum=record.minimum,
        maximum=record.maximum,
    )


def update_preference_record(record: PreferenceRecord, preference: Preference) -> None:
    """Copy mutable preference data onto an existing ORM record.

    :param record: Existing persistence record.
    :param preference: Latest preference domain object.
    """
    record.kind = preference.kind.value
    record.effect = preference.effect.value
    record.value = preference.value
    record.minimum = preference.minimum
    record.maximum = preference.maximum


def provider_mapping_to_record(mapping: ProviderProfileMapping) -> ProviderProfileMappingRecord:
    """Map a provider profile mapping to a new ORM record.

    :param mapping: Provider mapping to persist.
    :return: New provider-profile mapping record.
    """
    return ProviderProfileMappingRecord(
        id=str(mapping.id.value),
        profile_id=str(mapping.profile_id.value),
        provider=mapping.provider,
        external_profile_id=mapping.external_profile_id,
        synchronized_at=(
            _timestamp_to_storage(mapping.synchronized_at) if mapping.synchronized_at is not None else None
        ),
    )


def record_to_provider_mapping(record: ProviderProfileMappingRecord) -> ProviderProfileMapping:
    """Map a provider profile mapping ORM record to the domain.

    :param record: Provider mapping persistence record.
    :return: Provider-independent mapping.
    """
    return ProviderProfileMapping(
        id=ProviderProfileMappingId(UUID(record.id)),
        profile_id=ProfileId(UUID(record.profile_id)),
        provider=record.provider,
        external_profile_id=record.external_profile_id,
        synchronized_at=datetime.fromisoformat(record.synchronized_at) if record.synchronized_at is not None else None,
    )


def update_provider_mapping_record(
    record: ProviderProfileMappingRecord,
    mapping: ProviderProfileMapping,
) -> None:
    """Copy mutable provider mapping data onto an existing ORM record.

    :param record: Existing mapping record.
    :param mapping: Latest mapping domain object.
    """
    record.provider = mapping.provider
    record.external_profile_id = mapping.external_profile_id
    record.synchronized_at = (
        _timestamp_to_storage(mapping.synchronized_at) if mapping.synchronized_at is not None else None
    )
