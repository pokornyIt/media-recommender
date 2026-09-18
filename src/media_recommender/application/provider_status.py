"""Provider-independent, privacy-safe provider status read model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError
from pydantic_settings import SettingsError

from media_recommender.config import JellyfinSettings, TmdbSettings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime


class ProviderConfigurationState(StrEnum):
    """Safe configuration state of one provider."""

    CONFIGURED = "configured"
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
        """Validate provider labels and the optional observation timestamp.

        :raises ValueError: If a label is blank, the timestamp is timezone-naive,
            or an absent operation carries an observation time.
        """
        provider_id = self.provider_id.strip()
        display_name = self.display_name.strip()
        if not provider_id or not display_name:
            msg = "Provider identifier and display name must not be empty"
            raise ValueError(msg)
        if self.observed_at is not None:
            if self.observed_at.tzinfo is None:
                msg = "Provider status timestamp must be timezone-aware"
                raise ValueError(msg)
            if self.operation is ProviderOperationalState.NO_RECORDED_OPERATION:
                msg = "No recorded operation must not carry a timestamp"
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


class DefaultProviderStatusReader:
    """Derive safe configuration state without contacting any provider."""

    def read_statuses(self) -> tuple[ProviderStatus, ...]:
        """Return configuration-derived status with no recorded operation.

        :return: Safe TMDB and Jellyfin status entries.
        """
        return (
            ProviderStatus(
                provider_id="tmdb",
                display_name="TMDB",
                configuration=(
                    ProviderConfigurationState.CONFIGURED
                    if is_tmdb_configured()
                    else ProviderConfigurationState.NOT_CONFIGURED
                ),
                operation=ProviderOperationalState.NO_RECORDED_OPERATION,
            ),
            ProviderStatus(
                provider_id="jellyfin",
                display_name="Jellyfin",
                configuration=(
                    ProviderConfigurationState.CONFIGURED
                    if is_jellyfin_configured()
                    else ProviderConfigurationState.NOT_CONFIGURED
                ),
                operation=ProviderOperationalState.NO_RECORDED_OPERATION,
            ),
        )
