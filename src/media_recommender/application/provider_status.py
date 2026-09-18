"""Provider-independent, privacy-safe provider status contracts.

The status reader exposes only safe configuration facts and known operational
outcomes. It never contacts providers, triggers workflows, mutates state, or
reveals credentials, URLs, external identities, or raw error details.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - required at runtime by the Pydantic model field.
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, final

if TYPE_CHECKING:
    from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from pydantic_settings import SettingsError

from media_recommender.config import JellyfinSettings, TmdbSettings


class ProviderKind(StrEnum):
    """Identifiers of the providers whose status can be reported."""

    TMDB = "tmdb"
    JELLYFIN = "jellyfin"


class ConfigurationState(StrEnum):
    """Whether provider configuration values validate successfully."""

    CONFIGURED = "configured"
    NOT_CONFIGURED = "not_configured"


class OperationalState(StrEnum):
    """Known operational outcome of the latest recorded provider operation."""

    NO_RECORDED_OPERATION = "no_recorded_operation"
    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    CONFIGURATION_ERROR = "configuration_error"


@final
class ProviderStatus(BaseModel):
    """Privacy-safe status snapshot for a single provider.

    The model intentionally contains no credentials, provider URLs, external
    user identifiers, raw exception messages, or record-level details.
    """

    model_config = ConfigDict(frozen=True)

    provider: ProviderKind
    configuration: ConfigurationState
    operational: OperationalState = OperationalState.NO_RECORDED_OPERATION
    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def _require_timezone_aware_timestamp(cls, value: datetime | None) -> datetime | None:
        """Reject naive timestamps to keep rendered times unambiguous.

        :param value: Optional observation timestamp.
        :return: Validated timestamp.
        :raises ValueError: If the timestamp is naive (without UTC offset).
        """
        if value is not None and value.tzinfo is None:
            message = "observed_at must be timezone-aware"
            raise ValueError(message)
        return value

    def model_post_init(self, __context: object, /) -> None:
        """Reject timestamps paired with an absent recorded operation.

        :param __context: Pydantic model context (unused).
        :raises ValueError: If a timestamp is supplied without a recorded operation.
        """
        if self.operational is OperationalState.NO_RECORDED_OPERATION and self.observed_at is not None:
            message = "observed_at must be None when no operation is recorded"
            raise ValueError(message)


class ProviderStatusReader(Protocol):
    """Read-only contract returning safe provider status snapshots.

    Implementations must not contact providers, start workflows, or mutate
    application state while resolving status.
    """

    def read_provider_statuses(self) -> Sequence[ProviderStatus]:
        """Return the safe status snapshot for every known provider.

        :return: One status per supported provider, in a stable order.
        """
        ...


class DefaultProviderStatusReader:
    """Default reader deriving configuration state without provider activity.

    Absent provider configuration is reported as ``no_recorded_operation``,
    while present but malformed configuration surfaces as
    ``configuration_error``. No validation details, credentials, or URLs are
    exposed, and no future workflow outcome can be recorded until one is
    safely supplied through this contract.
    """

    def read_provider_statuses(self) -> Sequence[ProviderStatus]:
        """Derive safe configuration state for each provider without side effects.

        :return: Status snapshots in a stable provider order.
        """
        return (
            _provider_status(ProviderKind.TMDB, TmdbSettings),
            _provider_status(ProviderKind.JELLYFIN, JellyfinSettings),
        )


def _provider_status(provider: ProviderKind, settings_type: type[TmdbSettings | JellyfinSettings]) -> ProviderStatus:
    """Validate a provider settings model and map the outcome to a safe snapshot.

    Missing required configuration values are a neutral absence; values that
    are present but invalid surface as ``configuration_error`` without any
    validation details.

    :param provider: Provider the snapshot describes.
    :param settings_type: Provider settings model to validate from the environment.
    :return: Safe configuration and operational state snapshot.
    """
    try:
        settings_type()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError:
        return _malformed_status(provider)
    except ValidationError as errors:
        if all(error["type"] == "missing" for error in errors.errors()):
            return ProviderStatus(provider=provider, configuration=ConfigurationState.NOT_CONFIGURED)
        return _malformed_status(provider)
    return ProviderStatus(provider=provider, configuration=ConfigurationState.CONFIGURED)


def _malformed_status(provider: ProviderKind) -> ProviderStatus:
    """Build the snapshot for present but malformed provider configuration.

    :param provider: Provider the snapshot describes.
    :return: Snapshot with ``configuration_error`` and no extra details.
    """
    return ProviderStatus(
        provider=provider,
        configuration=ConfigurationState.NOT_CONFIGURED,
        operational=OperationalState.CONFIGURATION_ERROR,
    )
