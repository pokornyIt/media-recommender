"""Orchestrate Jellyfin retrieval through the shared library application service."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from media_recommender.integrations.jellyfin.mapper import map_library_items

if TYPE_CHECKING:
    from collections.abc import Callable

    from media_recommender.application import LibrarySynchronizationResult, LibrarySynchronizationService
    from media_recommender.integrations.jellyfin.models import JellyfinItemsResponse, JellyfinUser


class JellyfinLibrarySource(Protocol):
    """Retrieve one configured Jellyfin user's identity and library."""

    async def validate_user(self) -> JellyfinUser:
        """Return the authenticated configured Jellyfin user.

        :return: Selected provider user.
        """
        ...

    async def get_library_items(self) -> JellyfinItemsResponse:
        """Return the configured user's complete media collection.

        :return: Complete provider item response.
        """
        ...


class JellyfinLibrarySynchronizer:
    """Synchronize the selected Jellyfin user into profile-owned local state."""

    def __init__(
        self,
        client: JellyfinLibrarySource,
        service: LibrarySynchronizationService,
        *,
        synchronization_id_factory: Callable[[], str] | None = None,
    ) -> None:
        """Initialize Jellyfin transport and provider-independent application service.

        :param client: Configured Jellyfin API client.
        :param service: Shared library synchronization service.
        :param synchronization_id_factory: Optional deterministic ID source for tests.
        """
        self._client = client
        self._service = service
        self._synchronization_id_factory = synchronization_id_factory or (lambda: str(uuid4()))

    async def synchronize(self) -> LibrarySynchronizationResult:
        """Fetch and persist one complete Jellyfin library snapshot.

        Provider failures occur before local personal state is changed.

        :return: Provider-independent synchronization result.
        """
        user = await self._client.validate_user()
        response = await self._client.get_library_items()
        mapped = map_library_items(response.items)
        return await self._service.synchronize(
            provider="jellyfin",
            external_profile_id=user.id,
            synchronization_id=self._synchronization_id_factory(),
            items=mapped.items,
            invalid_items=mapped.invalid_items,
        )
