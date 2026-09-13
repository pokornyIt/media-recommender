"""Offline tests for the shared provider HTTP client."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from media_recommender.config import ProviderSettings
from media_recommender.integrations import (
    ProviderAuthenticationError,
    ProviderHttpClient,
    ProviderInvalidPayloadError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Callable


class SyntheticDto(BaseModel):
    """Synthetic provider payload used at the validation boundary."""

    identifier: int


def _settings() -> ProviderSettings:
    """Return synthetic provider settings.

    :return: Settings containing no real credentials.
    """
    return ProviderSettings.model_validate(
        {"base_url": "https://provider.invalid", "api_token": SecretStr("synthetic-token")}
    )


def _request(handler: Callable[[httpx.Request], httpx.Response]) -> SyntheticDto:
    """Run one request through an offline mock transport.

    :param handler: Mock transport handler.
    :return: Validated synthetic DTO.
    """

    async def run() -> SyntheticDto:
        """Own and close the async client during the request."""
        async with ProviderHttpClient(_settings(), transport=httpx.MockTransport(handler)) as client:
            return await client.request_json("GET", "/item", SyntheticDto)

    return asyncio.run(run())


def test_success_payload_is_validated() -> None:
    """Verify successful JSON is converted to the requested Pydantic DTO."""
    result = _request(lambda _request: httpx.Response(200, json={"identifier": 42}))

    assert result == SyntheticDto(identifier=42)


@pytest.mark.parametrize("status_code", [401, 403])
def test_authentication_errors_are_sanitized(status_code: int) -> None:
    """Verify authentication responses cannot expose response or request secrets."""
    with pytest.raises(ProviderAuthenticationError, match="Provider authentication failed") as caught:
        _request(lambda _request: httpx.Response(status_code, text="synthetic-token"))

    assert "synthetic-token" not in str(caught.value)


def test_timeout_is_translated_without_request_details() -> None:
    """Verify timeout details are replaced by a stable application error."""

    def timeout(request: httpx.Request) -> httpx.Response:
        """Raise a synthetic read timeout."""
        message = "secret-url"
        raise httpx.ReadTimeout(message, request=request)

    with pytest.raises(ProviderTimeoutError, match="Provider request timed out") as caught:
        _request(timeout)

    assert "secret-url" not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json={"identifier": "not-an-integer"}),
    ],
)
def test_invalid_payload_is_translated(response: httpx.Response) -> None:
    """Verify malformed or schema-invalid JSON is rejected at the boundary."""
    with pytest.raises(ProviderInvalidPayloadError, match="Provider returned an invalid payload"):
        _request(lambda _request: response)


def test_rate_limit_preserves_safe_retry_delay() -> None:
    """Verify rate limits expose only a parseable retry delay."""
    retry_delay = 30
    with pytest.raises(ProviderRateLimitError) as caught:
        _request(lambda _request: httpx.Response(429, headers={"Retry-After": str(retry_delay)}))

    assert caught.value.retry_after_seconds == retry_delay


def test_upstream_error_is_translated() -> None:
    """Verify server failures are exposed as temporary provider failures."""
    with pytest.raises(ProviderUnavailableError, match="Provider is temporarily unavailable"):
        _request(lambda _request: httpx.Response(503, text="internal provider details"))


def test_other_http_error_retains_only_status_code() -> None:
    """Verify other HTTP failures retain safe diagnostic information only."""
    status_code = 302
    with pytest.raises(ProviderResponseError, match=f"HTTP status {status_code}") as caught:
        _request(lambda _request: httpx.Response(status_code, text="sensitive body"))

    assert caught.value.status_code == status_code
    assert "sensitive body" not in str(caught.value)
