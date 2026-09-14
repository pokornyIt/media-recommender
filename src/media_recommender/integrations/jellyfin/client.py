"""HTTP client for supported Jellyfin library and user endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from media_recommender.integrations.errors import ProviderInvalidPayloadError
from media_recommender.integrations.http import ProviderHttpClient
from media_recommender.integrations.jellyfin.models import JellyfinItemsResponse, JellyfinUser

if TYPE_CHECKING:
    from types import TracebackType

    import httpx

    from media_recommender.config import JellyfinSettings


class JellyfinClient:
    """Retrieve the configured Jellyfin user's accessible movie and TV library."""

    def __init__(
        self,
        settings: JellyfinSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a Jellyfin client with one shared HTTP connection pool.

        :param settings: Validated Jellyfin server, token, and user configuration.
        :param transport: Optional transport used by offline tests.
        """
        self._settings = settings
        self._http = ProviderHttpClient(settings, transport=transport)

    async def __aenter__(self) -> Self:
        """Return this client as an asynchronous context manager.

        :return: This Jellyfin client.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the underlying connection pool.

        :param exc_type: Exception type raised in the managed block, if any.
        :param exc_value: Exception raised in the managed block, if any.
        :param traceback: Exception traceback, if any.
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._http.aclose()

    async def validate_user(self) -> JellyfinUser:
        """Validate credentials and return exactly the configured Jellyfin user.

        :return: Authenticated configured user.
        :raises ProviderInvalidPayloadError: If the endpoint returns another user identity.
        """
        user = await self._http.request_json(
            "GET",
            f"Users/{self._settings.user_id}",
            JellyfinUser,
            headers=self._headers,
        )
        if user.id != self._settings.user_id:
            raise ProviderInvalidPayloadError
        return user

    async def get_library_items(self) -> JellyfinItemsResponse:
        """Return the configured user's complete movie and series collection.

        :return: Raw item envelope whose entries can be validated independently.
        """
        return await self._http.request_json(
            "GET",
            f"Users/{self._settings.user_id}/Items",
            JellyfinItemsResponse,
            params={
                "Recursive": True,
                "IncludeItemTypes": "Movie,Series",
                "Fields": "ProviderIds,UserData,RunTimeTicks",
                "EnableUserData": True,
            },
            headers=self._headers,
        )

    @property
    def configured_user_id(self) -> str:
        """Return the selected external user identity.

        :return: Configured Jellyfin user ID.
        """
        return self._settings.user_id

    @property
    def _headers(self) -> dict[str, str]:
        """Return Jellyfin authentication and response headers.

        :return: Headers containing the API token outside URLs and diagnostics.
        """
        return {
            "X-Emby-Token": self._settings.api_token.get_secret_value(),
            "Accept": "application/json",
        }
