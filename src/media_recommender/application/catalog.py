"""Persistence-independent application contracts for normalized media."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from media_recommender.domain import ExternalId, Media, MediaId, MediaType


class MediaCatalogReader(Protocol):
    """Read normalized media without exposing persistence details."""

    async def get(self, media_id: MediaId) -> Media | None:
        """Return a media item by its internal identity.

        :param media_id: Internal catalog identity.
        :return: Matching media item, or ``None`` when it does not exist.
        """
        ...

    async def find_by_external_id(self, external_id: ExternalId) -> Media | None:
        """Return a media item carrying an external identifier.

        :param external_id: Provider-namespaced identifier to find.
        :return: Matching media item, or ``None`` when it does not exist.
        """
        ...

    def iter_by_type(self, media_type: MediaType) -> AsyncIterator[Media]:
        """Iterate over media items of one type.

        :param media_type: Kind of media to retrieve.
        :return: Asynchronous iterator of matching media items.
        """
        ...


class MediaCatalogWriter(Protocol):
    """Store normalized media without exposing persistence details."""

    async def save(self, media: Media) -> None:
        """Insert or replace a media item using its internal identity.

        :param media: Normalized media item to store.
        """
        ...
