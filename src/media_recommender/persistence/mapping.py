"""Typed mapping between domain objects and persistence records."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from media_recommender.domain import (
    Artwork,
    ArtworkType,
    Country,
    ExternalId,
    Genre,
    Media,
    MediaId,
    MediaType,
    Movie,
    Runtime,
    TVShow,
)
from media_recommender.persistence.models import (
    ArtworkRecord,
    CountryRecord,
    ExternalIdRecord,
    GenreRecord,
    MediaRecord,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def media_to_record(media: Media) -> MediaRecord:
    """Map a domain media item to a new detached ORM graph.

    :param media: Provider-independent domain media item.
    :return: New ORM record graph containing the normalized metadata.
    """
    record = MediaRecord()
    update_record(record, media)
    record.genres = [GenreRecord(name=genre.name) for genre in media.genres]
    record.countries = [CountryRecord(code=country.code, name=country.name) for country in media.production_countries]
    return record


def update_record(record: MediaRecord, media: Media) -> None:
    """Copy mutable scalar and owned collection data onto an ORM record.

    Genre and country references are resolved separately by the repository so
    shared rows can be reused.

    :param record: ORM record to update.
    :param media: Source domain media item.
    """
    record.id = str(media.id.value)
    record.media_type = media.media_type.value
    record.title = media.title
    record.original_title = media.original_title
    record.release_date = media.release_date
    record.runtime_minutes = _runtime_minutes(media)
    record.artwork = [
        ArtworkRecord(artwork_type=item.type.value, url=item.url, language=item.language) for item in media.artwork
    ]
    _sync_external_ids(record, media.external_ids)


def record_to_media(record: MediaRecord) -> Media:
    """Map a fully loaded ORM record to a provider-independent domain item.

    :param record: ORM record with catalog relationships eagerly loaded.
    :return: Matching movie or TV-show domain object.
    :raises ValueError: If the stored media type is unsupported.
    """
    media_type = MediaType(record.media_type)
    media_id = MediaId(UUID(record.id))
    genres = tuple(Genre(item.name) for item in record.genres)
    countries = tuple(Country(item.code, item.name) for item in record.countries)
    artwork = tuple(Artwork(ArtworkType(item.artwork_type), item.url, item.language) for item in record.artwork)
    external_ids = frozenset(ExternalId(item.namespace, item.value) for item in record.external_ids)
    runtime = Runtime(record.runtime_minutes) if record.runtime_minutes is not None else None

    if media_type is MediaType.MOVIE:
        return Movie(
            id=media_id,
            title=record.title,
            original_title=record.original_title,
            released_on=record.release_date,
            runtime=runtime,
            genres=genres,
            production_countries=countries,
            artwork=artwork,
            external_ids=external_ids,
        )
    if media_type is MediaType.TV_SHOW:
        return TVShow(
            id=media_id,
            title=record.title,
            original_title=record.original_title,
            first_aired_on=record.release_date,
            episode_runtime=runtime,
            genres=genres,
            production_countries=countries,
            artwork=artwork,
            external_ids=external_ids,
        )
    msg = f"Unsupported stored media type: {record.media_type}"
    raise ValueError(msg)


def assign_shared_metadata(
    record: MediaRecord,
    genres: Sequence[GenreRecord],
    countries: Sequence[CountryRecord],
) -> None:
    """Assign repository-resolved genre and country rows to a record.

    :param record: ORM media record to update.
    :param genres: Reusable genre rows.
    :param countries: Reusable country rows.
    """
    record.genres = list(genres)
    record.countries = list(countries)


def _runtime_minutes(media: Media) -> int | None:
    """Return the applicable runtime value for a domain media item.

    :param media: Movie or TV show whose runtime should be extracted.
    :return: Runtime in minutes, or ``None`` when it is unknown.
    """
    runtime = media.runtime if isinstance(media, Movie) else media.episode_runtime
    return runtime.minutes if runtime is not None else None


def _sync_external_ids(record: MediaRecord, external_ids: frozenset[ExternalId]) -> None:
    """Update owned external-ID rows without replacing unchanged identities.

    :param record: ORM media record whose external IDs should be synchronized.
    :param external_ids: Complete desired set of domain external IDs.
    """
    existing_by_namespace = {item.namespace: item for item in record.external_ids}
    synchronized: list[ExternalIdRecord] = []
    for external_id in sorted(external_ids, key=lambda item: item.namespace):
        external_id_record = existing_by_namespace.get(external_id.namespace)
        if external_id_record is None:
            external_id_record = ExternalIdRecord(namespace=external_id.namespace, value=external_id.value)
        else:
            external_id_record.value = external_id.value
        synchronized.append(external_id_record)
    record.external_ids = synchronized
