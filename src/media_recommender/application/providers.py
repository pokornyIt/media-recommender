"""Application contracts for external metadata providers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from media_recommender.domain import ExternalId, Media, MediaType


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaSearchResult:
    """Provider-independent summary returned by metadata search."""

    external_id: ExternalId
    media_type: MediaType
    title: str
    release_year: int | None = None

    def __post_init__(self) -> None:
        """Normalize and validate the result title.

        :raises ValueError: If the title is empty.
        """
        title = self.title.strip()
        if not title:
            msg = "Media search result title must not be empty"
            raise ValueError(msg)
        object.__setattr__(self, "title", title)


class MetadataProvider(Protocol):
    """Retrieve normalized metadata without exposing provider DTOs."""

    async def search(
        self,
        query: str,
        *,
        media_type: MediaType | None = None,
    ) -> Sequence[MediaSearchResult]:
        """Search for media summaries.

        :param query: Provider search text.
        :param media_type: Optional kind of media to search for.
        :return: Provider-independent matching summaries.
        """
        ...

    async def get_details(self, external_id: ExternalId, media_type: MediaType) -> Media | None:
        """Return normalized details for one provider identity.

        :param external_id: Provider-namespaced identity to retrieve.
        :param media_type: Expected kind of media.
        :return: Normalized media, or ``None`` when the provider has no match.
        """
        ...
