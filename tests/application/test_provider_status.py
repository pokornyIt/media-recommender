"""Tests for the privacy-safe provider status contracts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from media_recommender.application import (
    ConfigurationState,
    DefaultProviderStatusReader,
    OperationalState,
    ProviderKind,
    ProviderStatus,
    ProviderStatusReader,
)


def test_provider_status_defaults_to_no_recorded_operation_without_timestamp() -> None:
    """Construct a status snapshot with safe defaults."""
    status = ProviderStatus(provider=ProviderKind.TMDB, configuration=ConfigurationState.CONFIGURED)

    assert status.operational is OperationalState.NO_RECORDED_OPERATION
    assert status.observed_at is None


def test_provider_status_accepts_operational_state_with_timestamp() -> None:
    """Attach a timezone-aware timestamp to a recorded operational outcome."""
    observed_at = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

    status = ProviderStatus(
        provider=ProviderKind.JELLYFIN,
        configuration=ConfigurationState.CONFIGURED,
        operational=OperationalState.SUCCESS,
        observed_at=observed_at,
    )

    assert status.observed_at == observed_at


def test_provider_status_rejects_timestamp_without_recorded_operation() -> None:
    """Reject a timestamp when no operation is recorded."""
    with pytest.raises(PydanticValidationError, match="no operation is recorded"):
        ProviderStatus(
            provider=ProviderKind.TMDB,
            configuration=ConfigurationState.CONFIGURED,
            observed_at=datetime(2026, 9, 18, 12, 0, tzinfo=UTC),
        )


def test_provider_status_is_immutable() -> None:
    """Prevent mutation of published status snapshots."""
    status = ProviderStatus(provider=ProviderKind.TMDB, configuration=ConfigurationState.NOT_CONFIGURED)

    with pytest.raises(PydanticValidationError):
        status.configuration = ConfigurationState.CONFIGURED  # type: ignore[misc]


def test_operational_states_are_limited_to_safe_outcomes() -> None:
    """Keep operational states restricted to the documented safe set."""
    assert {state.value for state in OperationalState} == {
        "no_recorded_operation",
        "success",
        "transient_failure",
        "configuration_error",
    }


def test_default_reader_returns_no_recorded_operation_for_every_provider() -> None:
    """Derive configuration state without recording any operational outcome."""
    reader: ProviderStatusReader = DefaultProviderStatusReader()

    statuses = reader.read_provider_statuses()

    assert [status.provider for status in statuses] == [ProviderKind.TMDB, ProviderKind.JELLYFIN]
    for status in statuses:
        assert status.operational is OperationalState.NO_RECORDED_OPERATION
        assert status.observed_at is None
        assert status.configuration in {ConfigurationState.CONFIGURED, ConfigurationState.NOT_CONFIGURED}


def test_default_reader_result_is_immutable_snapshots() -> None:
    """Return frozen status models from the default reader."""
    statuses = DefaultProviderStatusReader().read_provider_statuses()

    for status in statuses:
        with pytest.raises(PydanticValidationError):
            status.provider = ProviderKind.JELLYFIN  # type: ignore[misc]
