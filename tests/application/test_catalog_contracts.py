"""Static-shape tests for the media catalog application contracts."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from media_recommender.application import MediaCatalogReader, MediaCatalogWriter
from media_recommender.domain import ExternalId, MediaId, MediaType, Movie

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from media_recommender.domain import Media


class InMemoryCatalog(MediaCatalogReader, MediaCatalogWriter):
    """Minimal contract implementation used to prove persistence independence."""

    def __init__(self) -> None:
        """Initialize an empty catalog."""
        self.items: dict[MediaId, Media] = {}

    async def get(self, media_id: MediaId) -> Media | None:
        """Return an item by internal identity."""
        return self.items.get(media_id)

    async def find_by_external_id(self, external_id: ExternalId) -> Media | None:
        """Return an item by external identity."""
        return next((item for item in self.items.values() if external_id in item.external_ids), None)

    async def iter_by_type(self, media_type: MediaType) -> AsyncIterator[Media]:
        """Iterate over items matching the requested type."""
        for item in self.items.values():
            if item.media_type is media_type:
                yield item

    async def save(self, media: Media) -> None:
        """Store an item by internal identity."""
        self.items[media.id] = media


async def _exercise_catalog() -> None:
    """Exercise the read and write contracts with an in-memory implementation."""
    catalog = InMemoryCatalog()
    external_id = ExternalId("test-provider", "movie-1")
    movie = Movie(id=MediaId.new(), title="Synthetic Contract", external_ids=frozenset({external_id}))

    await catalog.save(movie)

    assert await catalog.get(movie.id) == movie
    assert await catalog.find_by_external_id(external_id) == movie
    assert [item async for item in catalog.iter_by_type(MediaType.MOVIE)] == [movie]
    assert [item async for item in catalog.iter_by_type(MediaType.TV_SHOW)] == []


def test_catalog_contracts_do_not_require_framework_types() -> None:
    """Verify contracts can be implemented with standard Python types."""
    asyncio.run(_exercise_catalog())
