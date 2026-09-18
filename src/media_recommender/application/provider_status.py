"""Provider-independent, privacy-safe provider status read model."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError
from pydantic_settings import SettingsError

from media_recommender.config import JellyfinSettings, TmdbSettings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime


_TMDB_ENV_PREFIX = "MEDIA_RECOMMENDER_TMDB_"
_JELLYFIN_ENV_PREFIX = "MEDIA_RECOMMENDER_JELLYFIN_"


class ProviderConfigurationState(StrEnum):
    """Safe configuration state of one provider."""

    CONFIGURED = "configured"
    MISCONFIGURED = "misconfigured"
    NOT_CONFIGURED = "not_configured"


class ProviderOperationalState(StrEnum):
    """Latest known operational state of one provider."""

    NO_RECORDED_OPERATION = "no_recorded_operation"
    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    CONFIGURATION_ERROR = "configuration_error"


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """Safe status for one provider without secrets or external identities."""

    provider_id: str
    display_name: str
    configuration: ProviderConfigurationState
    operation: ProviderOperationalState
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate provider labels, timestamp awareness, and state consistency.

        :raises ValueError: If a label is blank, the timestamp has no UTC offset,
            an absent operation carries an observation time, or a malformed
            configuration is not reported as a configuration error.
        """
        provider_id = self.provider_id.strip()
        display_name = self.display_name.strip()
        if not provider_id or not display_name:
            msg = "Provider identifier and display name must not be empty"
            raise ValueError(msg)
        if self.observed_at is not None:
            if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
                msg = "Provider status timestamp must be timezone-aware"
                raise ValueError(msg)
            if self.operation is ProviderOperationalState.NO_RECORDED_OPERATION:
                msg = "No recorded operation must not carry a timestamp"
                raise ValueError(msg)
        if (
            self.configuration is ProviderConfigurationState.MISCONFIGURED
            and self.operation is not ProviderOperationalState.CONFIGURATION_ERROR
        ):
            msg = "Malformed provider configuration must be reported as a configuration error"
            raise ValueError(msg)
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "display_name", display_name)


class ProviderStatusReader(Protocol):
    """Read safe provider status without contacting providers or persistence."""

    def read_statuses(self) -> Sequence[ProviderStatus]:
        """Return safe status for each supported provider.

        :return: Provider-independent status entries in display order.
        """
        ...


def is_tmdb_configured() -> bool:
    """Return whether the current TMDB settings pass validation.

    :return: Whether the TMDB provider is configured.
    """
    try:
        TmdbSettings()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError, ValidationError:
        return False
    return True


def is_jellyfin_configured() -> bool:
    """Return whether the current Jellyfin settings pass validation.

    :return: Whether the Jellyfin provider is configured.
    """
    try:
        JellyfinSettings()  # pyright: ignore[reportCallIssue] - BaseSettings supplies required values from env.
    except SettingsError, ValidationError:
        return False
    return True


def _has_supplied_setting(prefix: str) -> bool:
    """Return whether any environment setting with the prefix was supplied.

    A setting counts as supplied when its name is present in the environment,
    even when its value is blank. Only names are inspected; configured values
    are never read into the status model.

    :param prefix: Environment variable name prefix identifying provider settings.
    :return: Whether at least one matching setting was supplied.
    """
    return any(name.startswith(prefix) for name in os.environ)


def _tmdb_configuration_state() -> ProviderConfigurationState:
    """Return the safe configuration state of the TMDB provider.

    :return: Configured, misconfigured, or not-configured state.
    """
    if is_tmdb_configured():
        return ProviderConfigurationState.CONFIGURED
    if _has_supplied_setting(_TMDB_ENV_PREFIX):
        return ProviderConfigurationState.MISCONFIGURED
    return ProviderConfigurationState.NOT_CONFIGURED


def _jellyfin_configuration_state() -> ProviderConfigurationState:
    """Return the safe configuration state of the Jellyfin provider.

    :return: Configured, misconfigured, or not-configured state.
    """
    if is_jellyfin_configured():
        return ProviderConfigurationState.CONFIGURED
    if _has_supplied_setting(_JELLYFIN_ENV_PREFIX):
        return ProviderConfigurationState.MISCONFIGURED
    return ProviderConfigurationState.NOT_CONFIGURED


def _operation_state(configuration: ProviderConfigurationState) -> ProviderOperationalState:
    """Return the safe operational state implied by a configuration state.

    :param configuration: Safe configuration state derived by the reader.
    :return: Configuration error for malformed configuration, otherwise no recorded operation.
    """
    if configuration is ProviderConfigurationState.MISCONFIGURED:
        return ProviderOperationalState.CONFIGURATION_ERROR
    return ProviderOperationalState.NO_RECORDED_OPERATION


def _build_status(provider_id: str, display_name: str, configuration: ProviderConfigurationState) -> ProviderStatus:
    """Build one safe provider status from a derived configuration state.

    :param provider_id: Stable internal provider identifier.
    :param display_name: Human-readable provider name.
    :param configuration: Safe configuration state derived without contacting the provider.
    :return: Safe provider status entry.
    """
    return ProviderStatus(
        provider_id=provider_id,
        display_name=display_name,
        configuration=configuration,
        operation=_operation_state(configuration),
    )


class DefaultProviderStatusReader:
    """Derive safe configuration state without contacting any provider."""

    def read_statuses(self) -> tuple[ProviderStatus, ...]:
        """Return configuration-derived status with no recorded operation.

        :return: Safe TMDB and Jellyfin status entries.
        """
        return (
            _build_status("tmdb", "TMDB", _tmdb_configuration_state()),
            _build_status("jellyfin", "Jellyfin", _jellyfin_configuration_state()),
        )
