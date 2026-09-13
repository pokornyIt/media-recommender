"""Provider errors exposed at the integration boundary."""

from __future__ import annotations


class ProviderError(Exception):
    """Base class for failures produced by a metadata provider."""


class ProviderAuthenticationError(ProviderError):
    """Provider rejected the configured credentials."""

    def __init__(self) -> None:
        """Initialize a sanitized authentication failure."""
        super().__init__("Provider authentication failed")


class ProviderTimeoutError(ProviderError):
    """Provider request exceeded a configured timeout."""

    def __init__(self) -> None:
        """Initialize a sanitized timeout failure."""
        super().__init__("Provider request timed out")


class ProviderUnavailableError(ProviderError):
    """Provider failed temporarily or could not be reached."""

    def __init__(self) -> None:
        """Initialize a sanitized temporary provider failure."""
        super().__init__("Provider is temporarily unavailable")


class ProviderRateLimitError(ProviderError):
    """Provider rejected a request because its rate limit was reached."""

    def __init__(self, *, retry_after_seconds: int | None = None) -> None:
        """Initialize a rate-limit failure without request details.

        :param retry_after_seconds: Optional delay supplied by the provider.
        """
        super().__init__("Provider rate limit exceeded")
        self.retry_after_seconds = retry_after_seconds


class ProviderResponseError(ProviderError):
    """Provider returned an unexpected non-success response."""

    def __init__(self, status_code: int) -> None:
        """Initialize a sanitized response failure.

        :param status_code: HTTP status returned by the provider.
        """
        super().__init__(f"Provider request failed with HTTP status {status_code}")
        self.status_code = status_code


class ProviderInvalidPayloadError(ProviderError):
    """Provider returned malformed JSON or data that failed validation."""

    def __init__(self) -> None:
        """Initialize a sanitized payload validation failure."""
        super().__init__("Provider returned an invalid payload")
