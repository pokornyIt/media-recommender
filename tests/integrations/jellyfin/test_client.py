"""Offline HTTP contract tests for the Jellyfin client."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from media_recommender.config import JellyfinSettings
from media_recommender.integrations import (
    ProviderAuthenticationError,
    ProviderInvalidPayloadError,
    ProviderUnavailableError,
)
from media_recommender.integrations.jellyfin import JellyfinClient

EXPECTED_REQUEST_COUNT = 2


def _settings() -> JellyfinSettings:
    """Return synthetic Jellyfin settings."""
    return JellyfinSettings.model_validate(
        {
            "base_url": "https://jellyfin.invalid/api/",
            "api_token": SecretStr("synthetic-jellyfin-token"),
            "user_id": "selected-user",
        }
    )


def test_client_validates_selected_user_and_requests_only_their_library() -> None:
    """Verify supported endpoints, filters, and header-only authentication."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Validate requests and return synthetic Jellyfin payloads."""
        requests.append(request)
        assert request.headers["X-Emby-Token"] == "synthetic-jellyfin-token"
        assert "synthetic-jellyfin-token" not in str(request.url)
        if request.url.path == "/api/Users/selected-user":
            return httpx.Response(200, json={"Id": "selected-user", "Name": "Selected Synthetic User"})
        assert request.url.path == "/api/Users/selected-user/Items"
        assert request.url.params["Recursive"] == "true"
        assert request.url.params["IncludeItemTypes"] == "Movie,Series"
        assert request.url.params["EnableUserData"] == "true"
        return httpx.Response(200, json={"Items": [{"Id": "item-1"}]})

    async def run() -> None:
        """Run both client operations with one connection lifecycle."""
        async with JellyfinClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            user = await client.validate_user()
            response = await client.get_library_items()
        assert user.id == "selected-user"
        assert response.items == [{"Id": "item-1"}]

    asyncio.run(run())
    assert len(requests) == EXPECTED_REQUEST_COUNT


@pytest.mark.parametrize(
    ("response", "error_type"),
    [
        (httpx.Response(401, text="synthetic-jellyfin-token"), ProviderAuthenticationError),
        (httpx.Response(503, text="temporary"), ProviderUnavailableError),
        (httpx.Response(200, json={"Id": "other-user", "Name": "Other"}), ProviderInvalidPayloadError),
    ],
)
def test_user_validation_reports_sanitized_provider_failures(
    response: httpx.Response,
    error_type: type[Exception],
) -> None:
    """Verify authentication, availability, and mismatched-user failures are explicit and secret-safe."""

    async def run() -> None:
        """Validate the configured user through a synthetic failing response."""
        async with JellyfinClient(
            _settings(),
            transport=httpx.MockTransport(lambda _request: response),
        ) as client:
            await client.validate_user()

    with pytest.raises(error_type) as caught:
        asyncio.run(run())
    assert "synthetic-jellyfin-token" not in str(caught.value)


def test_invalid_collection_envelope_is_rejected() -> None:
    """Verify malformed collection responses fail at the provider boundary."""

    async def run() -> None:
        """Request one malformed synthetic collection."""
        async with JellyfinClient(
            _settings(),
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"Items": "invalid"})),
        ) as client:
            await client.get_library_items()

    with pytest.raises(ProviderInvalidPayloadError):
        asyncio.run(run())
