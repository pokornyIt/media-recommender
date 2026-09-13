"""SQLAlchemy implementation of the media catalog contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from media_recommender.persistence.database import session_scope
from media_recommender.persistence.mapping import (
    assign_shared_metadata,
    media_to_record,
    record_to_media,
    update_record,
)
from media_recommender.persistence.models import CountryRecord, ExternalIdRecord, GenreRecord, MediaRecord

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

    from media_recommender.domain import ExternalId, Media, MediaId, MediaType
    from media_recommender.persistence.database import SessionFactory

MEDIA_LOAD_OPTIONS = (
    selectinload(MediaRecord.genres),
    selectinload(MediaRecord.countries),
    selectinload(MediaRecord.artwork),
    selectinload(MediaRecord.external_ids),
)


class SqlAlchemyMediaCatalog:
    """Persist normalized media through asynchronous SQLAlchemy sessions."""

    def __init__(self, session_factory: SessionFactory) -> None:
        """Initialize the catalog repository.

        :param session_factory: Factory providing asynchronous sessions.
        """
        self._session_factory = session_factory

    async def get(self, media_id: MediaId) -> Media | None:
        """Return a media item by its internal identity.

        :param media_id: Internal catalog identity.
        :return: Matching domain item, or ``None`` when absent.
        """
        statement = select(MediaRecord).where(MediaRecord.id == str(media_id.value)).options(*MEDIA_LOAD_OPTIONS)
        async with self._session_factory() as session:
            record = await session.scalar(statement)
        return record_to_media(record) if record is not None else None

    async def find_by_external_id(self, external_id: ExternalId) -> Media | None:
        """Return a media item by a provider-namespaced external identity.

        :param external_id: External identity to find.
        :return: Matching domain item, or ``None`` when absent.
        """
        statement = (
            select(MediaRecord)
            .join(MediaRecord.external_ids)
            .where(
                ExternalIdRecord.namespace == external_id.namespace,
                ExternalIdRecord.value == external_id.value,
            )
            .options(*MEDIA_LOAD_OPTIONS)
        )
        async with self._session_factory() as session:
            record = await session.scalar(statement)
        return record_to_media(record) if record is not None else None

    async def iter_by_type(self, media_type: MediaType) -> AsyncIterator[Media]:
        """Iterate over media items of one type ordered by title and identity.

        :param media_type: Kind of media to retrieve.
        :yield: Matching domain items.
        """
        statement = (
            select(MediaRecord)
            .where(MediaRecord.media_type == media_type.value)
            .order_by(MediaRecord.title, MediaRecord.id)
            .options(*MEDIA_LOAD_OPTIONS)
        )
        async with self._session_factory() as session:
            records = await session.stream_scalars(statement)
            async for record in records:
                yield record_to_media(record)

    async def save(self, media: Media) -> None:
        """Insert or update a normalized media item atomically.

        :param media: Domain media item to persist.
        """
        async with session_scope(self._session_factory) as session:
            statement = select(MediaRecord).where(MediaRecord.id == str(media.id.value)).options(*MEDIA_LOAD_OPTIONS)
            record = await session.scalar(statement)
            if record is None:
                record = media_to_record(media)
                session.add(record)
            else:
                update_record(record, media)

            genres = [await self._resolve_genre(session, item.name) for item in media.genres]
            countries = [
                await self._resolve_country(session, item.code, item.name) for item in media.production_countries
            ]
            assign_shared_metadata(record, genres, countries)

    @staticmethod
    async def _resolve_genre(session: AsyncSession, name: str) -> GenreRecord:
        """Return an existing genre row or create a new one.

        :param session: Active transaction session.
        :param name: Normalized genre name.
        :return: Persistent or pending genre row.
        """
        record = await session.get(GenreRecord, name)
        if record is None:
            record = GenreRecord(name=name)
            session.add(record)
        return record

    @staticmethod
    async def _resolve_country(session: AsyncSession, code: str, name: str) -> CountryRecord:
        """Return an existing country row or create a new one.

        :param session: Active transaction session.
        :param code: ISO production-country code.
        :param name: Production-country display name.
        :return: Persistent or pending country row.
        """
        record = await session.get(CountryRecord, code)
        if record is None:
            record = CountryRecord(code=code, name=name)
            session.add(record)
        else:
            record.name = name
        return record
