"""Tests for provider-independent metadata contracts."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from media_recommender.application import MediaSearchResult, MetadataProvider
from media_recommender.domain import ExternalId, MediaId, MediaType, Movie

if TYPE_CHECKING:
    from collections.abc import Sequence

    from media_recommender.domain import Media


class FakeMetadataProvider(MetadataProvider):
    """Offline provider fake reusable by application service tests."""

    def __init__(self, media: Media) -> None:
        """Initialize the fake with one normalized media item.

        :param media: Synthetic item returned by the fake.
        """
        self.media = media

    async def search(
        self,
        query: str,
        *,
        media_type: MediaType | None = None,
    ) -> Sequence[MediaSearchResult]:
        """Return a matching summary without provider DTOs."""
        if query.casefold() not in self.media.title.casefold():
            return []
        if media_type is not None and media_type is not self.media.media_type:
            return []
        external_id = next(iter(self.media.external_ids))
        return [
            MediaSearchResult(
                external_id=external_id,
                media_type=self.media.media_type,
                title=self.media.title,
                release_year=self.media.release_year,
            )
        ]

    async def get_details(self, external_id: ExternalId, media_type: MediaType) -> Media | None:
        """Return details when identity and type match the synthetic item."""
        if external_id in self.media.external_ids and media_type is self.media.media_type:
            return self.media
        return None


async def _exercise_provider() -> None:
    """Exercise search and detail contracts through the protocol."""
    external_id = ExternalId("synthetic-provider", "42")
    movie = Movie(id=MediaId.new(), title="Synthetic Result", external_ids=frozenset({external_id}))
    provider: MetadataProvider = FakeMetadataProvider(movie)

    results = await provider.search("Synthetic", media_type=MediaType.MOVIE)

    assert results == [MediaSearchResult(external_id=external_id, media_type=MediaType.MOVIE, title="Synthetic Result")]
    assert await provider.get_details(external_id, MediaType.MOVIE) == movie


def test_metadata_provider_contract_uses_normalized_types() -> None:
    """Verify an offline provider can satisfy the application contract."""
    asyncio.run(_exercise_provider())
