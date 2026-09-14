"""SQLAlchemy repositories for profile-owned personal media data."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from media_recommender.domain import WatchStatus, default_profile
from media_recommender.persistence.database import session_scope
from media_recommender.persistence.models import (
    PreferenceRecord,
    ProfileRecord,
    ProviderProfileMappingRecord,
    RatingRecord,
    ViewingEventRecord,
    WatchStateRecord,
)
from media_recommender.persistence.personal_mapping import (
    preference_to_record,
    profile_to_record,
    provider_mapping_to_record,
    rating_to_record,
    record_to_preference,
    record_to_profile,
    record_to_provider_mapping,
    record_to_rating,
    record_to_viewing_event,
    record_to_watch_state,
    update_preference_record,
    update_provider_mapping_record,
    update_rating_record,
    update_viewing_event_record,
    update_watch_state_record,
    viewing_event_to_record,
    watch_state_to_record,
)

if TYPE_CHECKING:
    from media_recommender.domain import (
        MediaId,
        Preference,
        Profile,
        ProfileId,
        ProviderProfileMapping,
        Rating,
        ViewingEvent,
        WatchState,
    )
    from media_recommender.persistence.database import SessionFactory


class SqlAlchemyPersonalMediaRepository:
    """Persist internal profiles and their personal media state."""

    def __init__(self, session_factory: SessionFactory) -> None:
        """Initialize the personal-media repository.

        :param session_factory: Factory providing asynchronous sessions.
        """
        self._session_factory = session_factory

    async def get_or_create_default(self) -> Profile:
        """Return or create the deterministic implicit single-user profile.

        :return: Persisted default internal profile.
        """
        desired = default_profile()
        async with session_scope(self._session_factory) as session:
            statement = select(ProfileRecord).where(ProfileRecord.is_default.is_(True))
            record = await session.scalar(statement)
            if record is None:
                record = profile_to_record(desired)
                session.add(record)
        return record_to_profile(record)

    async def get_profile(self, profile_id: ProfileId) -> Profile | None:
        """Return a profile by its internal identity.

        :param profile_id: Internal profile identity.
        :return: Matching profile, or ``None`` when absent.
        """
        async with self._session_factory() as session:
            record = await session.get(ProfileRecord, str(profile_id.value))
        return record_to_profile(record) if record is not None else None

    async def save_profile(self, profile: Profile) -> None:
        """Insert or update an internal profile.

        :param profile: Profile to persist.
        """
        async with session_scope(self._session_factory) as session:
            record = await session.get(ProfileRecord, str(profile.id.value))
            if record is None:
                session.add(profile_to_record(profile))
            else:
                record.name = profile.name
                record.is_default = profile.is_default

    async def save_viewing_event(self, event: ViewingEvent) -> ViewingEvent:
        """Persist one watch idempotently when it has a provider record identity.

        :param event: Viewing event to persist.
        :return: Stored event, preserving the first internal identity on repeated ingest.
        """
        async with session_scope(self._session_factory) as session:
            record = await session.scalar(self._viewing_event_identity_statement(event))
            if record is None:
                record = viewing_event_to_record(event)
                session.add(record)
            else:
                self._require_owner(record.profile_id, event.profile_id, "Viewing event")
                update_viewing_event_record(record, event)
        return record_to_viewing_event(record)

    async def list_viewing_events(self, profile_id: ProfileId, media_id: MediaId) -> tuple[ViewingEvent, ...]:
        """Return a profile's watches of one shared catalog item.

        :param profile_id: Internal owner identity.
        :param media_id: Shared catalog identity.
        :return: Matching events ordered by timestamp and identity.
        """
        statement = (
            select(ViewingEventRecord)
            .where(
                ViewingEventRecord.profile_id == str(profile_id.value),
                ViewingEventRecord.media_id == str(media_id.value),
            )
            .order_by(ViewingEventRecord.watched_at, ViewingEventRecord.id)
        )
        async with self._session_factory() as session:
            records = (await session.scalars(statement)).all()
        return tuple(record_to_viewing_event(record) for record in records)

    async def save_watch_state(self, state: WatchState) -> WatchState:
        """Persist explicit provider state independently from viewing events.

        :param state: Explicit watched or unwatched state to persist.
        :return: Stored state, preserving its first internal identity on synchronization.
        """
        statement = select(WatchStateRecord).where(
            WatchStateRecord.profile_id == str(state.profile_id.value),
            WatchStateRecord.media_id == str(state.media_id.value),
            WatchStateRecord.source_provider == state.provenance.provider,
        )
        async with session_scope(self._session_factory) as session:
            record = await session.scalar(statement)
            if record is None:
                record = watch_state_to_record(state)
                session.add(record)
            else:
                update_watch_state_record(record, state)
        return record_to_watch_state(record)

    async def get_watch_status(self, profile_id: ProfileId, media_id: MediaId) -> WatchStatus:
        """Derive three-state watch knowledge from events and explicit states.

        Viewing events are definitive evidence of a watch. In their absence, any
        provider's explicit watched state wins over explicit unwatched state. No
        matching personal data remains unknown.

        :param profile_id: Internal owner identity.
        :param media_id: Shared catalog identity.
        :return: Derived watched, unwatched, or unknown status.
        """
        profile_value = str(profile_id.value)
        media_value = str(media_id.value)
        event_statement = select(ViewingEventRecord.id).where(
            ViewingEventRecord.profile_id == profile_value,
            ViewingEventRecord.media_id == media_value,
        )
        state_statement = select(WatchStateRecord.status).where(
            WatchStateRecord.profile_id == profile_value,
            WatchStateRecord.media_id == media_value,
        )
        async with self._session_factory() as session:
            if await session.scalar(event_statement) is not None:
                return WatchStatus.WATCHED
            statuses = set((await session.scalars(state_statement)).all())
        if WatchStatus.WATCHED.value in statuses:
            return WatchStatus.WATCHED
        if WatchStatus.UNWATCHED.value in statuses:
            return WatchStatus.UNWATCHED
        return WatchStatus.UNKNOWN

    async def save_rating(self, rating: Rating) -> Rating:
        """Persist a rating without creating viewing history.

        :param rating: Rating to persist.
        :return: Stored rating, preserving the first internal identity on repeated ingest.
        """
        async with session_scope(self._session_factory) as session:
            record = await session.scalar(self._rating_identity_statement(rating))
            if record is None:
                record = rating_to_record(rating)
                session.add(record)
            else:
                self._require_owner(record.profile_id, rating.profile_id, "Rating")
                update_rating_record(record, rating)
        return record_to_rating(record)

    async def list_ratings(self, profile_id: ProfileId, media_id: MediaId) -> tuple[Rating, ...]:
        """Return a profile's ratings for one shared catalog item.

        :param profile_id: Internal owner identity.
        :param media_id: Shared catalog identity.
        :return: Matching ratings ordered by identity.
        """
        statement = (
            select(RatingRecord)
            .where(
                RatingRecord.profile_id == str(profile_id.value),
                RatingRecord.media_id == str(media_id.value),
            )
            .order_by(RatingRecord.id)
        )
        async with self._session_factory() as session:
            records = (await session.scalars(statement)).all()
        return tuple(record_to_rating(record) for record in records)

    async def save_preference(self, preference: Preference) -> None:
        """Insert or update one profile-owned preference or exclusion.

        :param preference: Preference to persist.
        """
        async with session_scope(self._session_factory) as session:
            record = await session.get(PreferenceRecord, str(preference.id.value))
            if record is None:
                session.add(preference_to_record(preference))
            else:
                self._require_owner(record.profile_id, preference.profile_id, "Preference")
                update_preference_record(record, preference)

    async def list_preferences(self, profile_id: ProfileId) -> tuple[Preference, ...]:
        """Return all preferences and exclusions owned by a profile.

        :param profile_id: Internal owner identity.
        :return: Matching criteria ordered by identity.
        """
        statement = (
            select(PreferenceRecord)
            .where(PreferenceRecord.profile_id == str(profile_id.value))
            .order_by(PreferenceRecord.id)
        )
        async with self._session_factory() as session:
            records = (await session.scalars(statement)).all()
        return tuple(record_to_preference(record) for record in records)

    async def save_provider_mapping(self, mapping: ProviderProfileMapping) -> None:
        """Insert or update an external-to-internal profile mapping.

        :param mapping: Provider mapping to persist.
        """
        async with session_scope(self._session_factory) as session:
            record = await session.get(ProviderProfileMappingRecord, str(mapping.id.value))
            if record is None:
                session.add(provider_mapping_to_record(mapping))
            else:
                self._require_owner(record.profile_id, mapping.profile_id, "Provider mapping")
                update_provider_mapping_record(record, mapping)

    async def find_provider_mapping(
        self,
        provider: str,
        external_profile_id: str,
    ) -> ProviderProfileMapping | None:
        """Resolve an external owner identity to its internal mapping.

        :param provider: Provider namespace.
        :param external_profile_id: Provider-owned profile identifier.
        :return: Matching mapping, or ``None`` when absent.
        :raises ValueError: If either external identity component is empty.
        """
        normalized_provider = provider.strip().lower()
        normalized_external_id = external_profile_id.strip()
        if not normalized_provider or not normalized_external_id:
            msg = "Provider and external profile ID must not be empty"
            raise ValueError(msg)
        statement = select(ProviderProfileMappingRecord).where(
            ProviderProfileMappingRecord.provider == normalized_provider,
            ProviderProfileMappingRecord.external_profile_id == normalized_external_id,
        )
        async with self._session_factory() as session:
            record = await session.scalar(statement)
        return record_to_provider_mapping(record) if record is not None else None

    async def list_provider_mappings(self, profile_id: ProfileId) -> tuple[ProviderProfileMapping, ...]:
        """Return provider mappings owned by an internal profile.

        :param profile_id: Internal owner identity.
        :return: Matching mappings ordered by provider and external identity.
        """
        statement = (
            select(ProviderProfileMappingRecord)
            .where(ProviderProfileMappingRecord.profile_id == str(profile_id.value))
            .order_by(ProviderProfileMappingRecord.provider, ProviderProfileMappingRecord.external_profile_id)
        )
        async with self._session_factory() as session:
            records = (await session.scalars(statement)).all()
        return tuple(record_to_provider_mapping(record) for record in records)

    @staticmethod
    def _viewing_event_identity_statement(event: ViewingEvent):  # noqa: ANN205
        """Build the idempotency lookup for a viewing event.

        :param event: Viewing event whose stored identity should be found.
        :return: SQLAlchemy select statement for the event identity.
        """
        if event.provenance.source_record_id is None:
            return select(ViewingEventRecord).where(ViewingEventRecord.id == str(event.id.value))
        return select(ViewingEventRecord).where(
            ViewingEventRecord.profile_id == str(event.profile_id.value),
            ViewingEventRecord.source_provider == event.provenance.provider,
            ViewingEventRecord.source_record_id == event.provenance.source_record_id,
        )

    @staticmethod
    def _rating_identity_statement(rating: Rating):  # noqa: ANN205
        """Build the idempotency lookup for a rating.

        :param rating: Rating whose stored identity should be found.
        :return: SQLAlchemy select statement for the rating identity.
        """
        if rating.provenance.source_record_id is None:
            return select(RatingRecord).where(RatingRecord.id == str(rating.id.value))
        return select(RatingRecord).where(
            RatingRecord.profile_id == str(rating.profile_id.value),
            RatingRecord.source_provider == rating.provenance.provider,
            RatingRecord.source_record_id == rating.provenance.source_record_id,
        )

    @staticmethod
    def _require_owner(stored_profile_id: str, expected_profile_id: ProfileId, entity_name: str) -> None:
        """Prevent an existing personal record from changing owners.

        :param stored_profile_id: Persisted profile UUID string.
        :param expected_profile_id: Owner supplied by the domain object.
        :param entity_name: Entity name for a validation error.
        :raises ValueError: If the stored and supplied owner identities differ.
        """
        if stored_profile_id != str(expected_profile_id.value):
            msg = f"{entity_name} ownership cannot be changed"
            raise ValueError(msg)
