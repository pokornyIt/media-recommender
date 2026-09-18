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

from pydantic import BaseModel, ConfigDict, ValidationError
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

    Operational state is always reported as ``no_recorded_operation`` until a
    future workflow safely supplies a real outcome through this contract.
    """

    def read_provider_statuses(self) -> Sequence[ProviderStatus]:
        """Derive safe configuration state for each provider without side effects.

        :return: Status snapshots in a stable provider order.
        """
        return (
            ProviderStatus(
                provider=ProviderKind.TMDB,
                configuration=_tmdb_configuration_state(),
            ),
            ProviderStatus(
                provider=ProviderKind.JELLYFIN,
                configuration=_jellyfin_configuration_state(),
            ),
        )


def _configuration_state(settings_type: type[TmdbSettings | JellyfinSettings]) -> ConfigurationState:
    """Validate a provider settings model and map the outcome to a safe state.

    :param settings_type: Provider settings model to validate from the environment.
    :return: Configuration state derived from settings validation only.
    """
    try:
        settings_type()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError, ValidationError:
        return ConfigurationState.NOT_CONFIGURED
    return ConfigurationState.CONFIGURED


def _tmdb_configuration_state() -> ConfigurationState:
    """Validate TMDB settings and map the outcome to a safe state.

    :return: Configuration state derived from settings validation only.
    """
    return _configuration_state(TmdbSettings)


def _jellyfin_configuration_state() -> ConfigurationState:
    """Validate Jellyfin settings and map the outcome to a safe state.

    :return: Configuration state derived from settings validation only.
    """
    return _configuration_state(JellyfinSettings)
