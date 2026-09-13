"""Shared infrastructure for external integrations."""

from media_recommender.integrations.errors import (
    ProviderAuthenticationError,
    ProviderError,
    ProviderInvalidPayloadError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from media_recommender.integrations.http import ProviderHttpClient

__all__ = [
    "ProviderAuthenticationError",
    "ProviderError",
    "ProviderHttpClient",
    "ProviderInvalidPayloadError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
]
