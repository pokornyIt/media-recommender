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
)

_TMDB_TOKEN_ENV = "MEDIA_RECOMMENDER_TMDB_API_TOKEN"  # noqa: S105 - environment variable name, not a secret.
_TMDB_HTTP_TIMEOUT_ENV = "MEDIA_RECOMMENDER_TMDB_HTTP__CONNECT_TIMEOUT_SECONDS"
_JELLYFIN_BASE_URL_ENV = "MEDIA_RECOMMENDER_JELLYFIN_BASE_URL"
_JELLYFIN_TOKEN_ENV = "MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN"  # noqa: S105 - environment variable name, not a secret.
_JELLYFIN_USER_ID_ENV = "MEDIA_RECOMMENDER_JELLYFIN_USER_ID"


def _clear_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every provider configuration value from the environment.

    :param monkeypatch: Pytest environment patching fixture.
    """
    for env_name in (
        _TMDB_TOKEN_ENV,
        _TMDB_HTTP_TIMEOUT_ENV,
        _JELLYFIN_BASE_URL_ENV,
        _JELLYFIN_TOKEN_ENV,
        _JELLYFIN_USER_ID_ENV,
    ):
        monkeypatch.delenv(env_name, raising=False)


def test_provider_status_defaults_to_no_recorded_operation_without_timestamp() -> None:
    """Construct a status snapshot with safe defaults."""
    status = ProviderStatus(provider=ProviderKind.TMDB, configuration=ConfigurationState.CONFIGURED)

    assert status.operational is OperationalState.NO_RECORDED_OPERATION
    assert status.observed_at is None


def test_provider_status_accepts_operational_state_with_timezone_aware_timestamp() -> None:
    """Attach a timezone-aware timestamp to a recorded operational outcome."""
    observed_at = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)

    status = ProviderStatus(
        provider=ProviderKind.JELLYFIN,
        configuration=ConfigurationState.CONFIGURED,
        operational=OperationalState.SUCCESS,
        observed_at=observed_at,
    )

    assert status.observed_at == observed_at


def test_provider_status_rejects_naive_timestamp() -> None:
    """Reject naive timestamps so rendered times are never ambiguous."""
    with pytest.raises(PydanticValidationError, match="timezone-aware"):
        ProviderStatus(
            provider=ProviderKind.TMDB,
            configuration=ConfigurationState.CONFIGURED,
            operational=OperationalState.SUCCESS,
            observed_at=datetime(2026, 9, 18, 12, 0),  # noqa: DTZ001 - naive on purpose for the regression test.
        )


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


def test_default_reader_reports_absent_configuration_as_no_recorded_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat missing provider configuration as neutral absence, not failure."""
    _clear_provider_environment(monkeypatch)

    statuses = DefaultProviderStatusReader().read_provider_statuses()

    assert [status.provider for status in statuses] == [ProviderKind.TMDB, ProviderKind.JELLYFIN]
    for status in statuses:
        assert status.configuration is ConfigurationState.NOT_CONFIGURED
        assert status.operational is OperationalState.NO_RECORDED_OPERATION
        assert status.observed_at is None


def test_default_reader_reports_malformed_tmdb_configuration_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface present but unparseable TMDB configuration as configuration_error."""
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(_TMDB_TOKEN_ENV, "synthetic-tmdb-value")
    monkeypatch.setenv(_TMDB_HTTP_TIMEOUT_ENV, "not-a-number")

    statuses = {status.provider: status for status in DefaultProviderStatusReader().read_provider_statuses()}

    assert statuses[ProviderKind.TMDB].operational is OperationalState.CONFIGURATION_ERROR
    assert statuses[ProviderKind.TMDB].configuration is ConfigurationState.NOT_CONFIGURED
    assert statuses[ProviderKind.TMDB].observed_at is None


def test_default_reader_reports_malformed_jellyfin_configuration_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface present but invalid Jellyfin configuration as configuration_error."""
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(_JELLYFIN_BASE_URL_ENV, "not-a-url")
    monkeypatch.setenv(_JELLYFIN_TOKEN_ENV, "synthetic-jellyfin-value")
    monkeypatch.setenv(_JELLYFIN_USER_ID_ENV, "synthetic-user")

    statuses = {status.provider: status for status in DefaultProviderStatusReader().read_provider_statuses()}

    assert statuses[ProviderKind.JELLYFIN].operational is OperationalState.CONFIGURATION_ERROR
    assert statuses[ProviderKind.JELLYFIN].configuration is ConfigurationState.NOT_CONFIGURED
    assert statuses[ProviderKind.JELLYFIN].observed_at is None


def test_default_reader_reports_valid_configuration_as_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report providers with valid configuration as configured without provider activity."""
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(_TMDB_TOKEN_ENV, "synthetic-tmdb-value")
    monkeypatch.setenv(_JELLYFIN_BASE_URL_ENV, "https://jellyfin.synthetic.invalid/")
    monkeypatch.setenv(_JELLYFIN_TOKEN_ENV, "synthetic-jellyfin-value")
    monkeypatch.setenv(_JELLYFIN_USER_ID_ENV, "synthetic-user")

    statuses = {status.provider: status for status in DefaultProviderStatusReader().read_provider_statuses()}

    for status in statuses.values():
        assert status.configuration is ConfigurationState.CONFIGURED
        assert status.operational is OperationalState.NO_RECORDED_OPERATION
        assert status.observed_at is None


def test_default_reader_result_is_immutable_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return frozen status models from the default reader."""
    _clear_provider_environment(monkeypatch)
    statuses = DefaultProviderStatusReader().read_provider_statuses()

    for status in statuses:
        with pytest.raises(PydanticValidationError):
            status.provider = ProviderKind.JELLYFIN  # type: ignore[misc]
