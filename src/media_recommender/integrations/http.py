"""Centralized asynchronous HTTP transport for provider integrations."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Self, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from media_recommender.integrations.errors import (
    ProviderAuthenticationError,
    ProviderInvalidPayloadError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from types import TracebackType

    from media_recommender.config import ProviderSettings

ProviderDtoT = TypeVar("ProviderDtoT", bound=BaseModel)


class ProviderHttpClient:
    """Own one explicitly closable ``httpx.AsyncClient`` instance."""

    def __init__(
        self,
        settings: ProviderSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create the shared client from validated provider settings.

        :param settings: Provider endpoint, credentials, and timeout settings.
        :param transport: Optional transport used by offline tests.
        """
        timeout = httpx.Timeout(
            connect=settings.http.connect_timeout_seconds,
            read=settings.http.read_timeout_seconds,
            write=settings.http.write_timeout_seconds,
            pool=settings.http.pool_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=str(settings.base_url),
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> Self:
        """Return this client for use as an asynchronous context manager.

        :return: This provider HTTP client.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the underlying HTTP client.

        :param exc_type: Exception type raised in the managed block, if any.
        :param exc_value: Exception raised in the managed block, if any.
        :param traceback: Exception traceback, if any.
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()

    async def request_json(
        self,
        method: str,
        url: str,
        model: type[ProviderDtoT],
        *,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ProviderDtoT:
        """Request JSON and validate it as a provider boundary DTO.

        The shared transport deliberately does not retry. A concrete provider may
        add a bounded, endpoint-aware retry policy only for idempotent requests.

        :param method: HTTP request method.
        :param url: Provider-relative URL.
        :param model: Pydantic model used to validate the response payload.
        :param params: Provider query parameters.
        :param headers: Provider request headers, including authorization.
        :return: Validated provider DTO.
        :raises ProviderAuthenticationError: If credentials are rejected.
        :raises ProviderRateLimitError: If the provider rate limit is reached.
        :raises ProviderTimeoutError: If any configured timeout is exceeded.
        :raises ProviderUnavailableError: If transport or upstream service fails.
        :raises ProviderResponseError: If another non-success response is returned.
        :raises ProviderInvalidPayloadError: If JSON parsing or validation fails.
        """
        try:
            response = await self._client.request(method, url, params=params, headers=headers)
        except httpx.TimeoutException:
            raise ProviderTimeoutError from None
        except httpx.TransportError:
            raise ProviderUnavailableError from None

        if response.status_code in {httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN}:
            raise ProviderAuthenticationError
        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            raise ProviderRateLimitError(retry_after_seconds=_parse_retry_after(response.headers.get("Retry-After")))
        if response.is_server_error:
            raise ProviderUnavailableError
        if not response.is_success:
            raise ProviderResponseError(response.status_code)
        try:
            return model.model_validate(response.json())
        except ValueError, ValidationError:
            raise ProviderInvalidPayloadError from None


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a non-negative delta-seconds ``Retry-After`` value.

    HTTP-date values are intentionally ignored because converting them requires
    clock-dependent behavior that belongs in a provider-specific retry policy.

    :param value: Raw ``Retry-After`` header value.
    :return: Non-negative delay in seconds, or ``None`` when unsupported.
    """
    if value is None:
        return None
    try:
        delay = int(value)
    except ValueError:
        return None
    return delay if delay >= 0 else None
