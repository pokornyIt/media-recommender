"""Offline tests for the provider-independent provider status read model."""

from __future__ import annotations

from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta, tzinfo

import pytest

from media_recommender.application.provider_status import (
    DefaultProviderStatusReader,
    ProviderConfigurationState,
    ProviderOperationalState,
    ProviderStatus,
)


class _NaiveTzInfo(tzinfo):
    """Timezone implementation that reports no UTC offset."""

    def utcoffset(self, _dt: datetime | None) -> timedelta | None:
        """Return no UTC offset to emulate a timezone-naive timestamp.

        :return: Always ``None``.
        """
        return None

    def dst(self, _dt: datetime | None) -> timedelta | None:
        """Return no daylight saving offset.

        :return: Always ``None``.
        """
        return None

    def tzname(self, _dt: datetime | None) -> str | None:
        """Return no timezone name.

        :return: Always ``None``.
        """
        return None


def test_status_accepts_timezone_aware_timestamp() -> None:
    """Keep a timezone-aware observation timestamp unchanged."""
    observed_at = datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)

    status = ProviderStatus(
        provider_id="synthetic",
        display_name="Synthetic",
        configuration=ProviderConfigurationState.CONFIGURED,
        operation=ProviderOperationalState.SUCCESS,
        observed_at=observed_at,
    )

    assert status.observed_at == observed_at


def test_status_rejects_naive_timestamp() -> None:
    """Reject an observation timestamp without timezone information."""
    with pytest.raises(ValueError, match="timezone-aware"):
        ProviderStatus(
            provider_id="synthetic",
            display_name="Synthetic",
            configuration=ProviderConfigurationState.CONFIGURED,
            operation=ProviderOperationalState.SUCCESS,
            observed_at=datetime(2024, 1, 2, 3, 4, 5),  # noqa: DTZ001 - Naive timestamp is the invalid input under test.
        )


def test_status_rejects_timestamp_without_utc_offset() -> None:
    """Reject a timestamp whose timezone reports no UTC offset."""
    with pytest.raises(ValueError, match="timezone-aware"):
        ProviderStatus(
            provider_id="synthetic",
            display_name="Synthetic",
            configuration=ProviderConfigurationState.CONFIGURED,
            operation=ProviderOperationalState.SUCCESS,
            observed_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=_NaiveTzInfo()),
        )


def test_status_rejects_timestamp_for_no_recorded_operation() -> None:
    """Reject an observation timestamp when no operation was recorded."""
    with pytest.raises(ValueError, match="must not carry a timestamp"):
        ProviderStatus(
            provider_id="synthetic",
            display_name="Synthetic",
            configuration=ProviderConfigurationState.NOT_CONFIGURED,
            operation=ProviderOperationalState.NO_RECORDED_OPERATION,
            observed_at=datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        )


def test_status_rejects_malformed_configuration_without_configuration_error() -> None:
    """Reject a malformed configuration that is not reported as a configuration error."""
    with pytest.raises(ValueError, match="configuration error"):
        ProviderStatus(
            provider_id="synthetic",
            display_name="Synthetic",
            configuration=ProviderConfigurationState.MISCONFIGURED,
            operation=ProviderOperationalState.SUCCESS,
        )


@pytest.mark.parametrize(
    ("provider_id", "display_name"),
    [
        (" ", "Synthetic"),
        ("synthetic", " "),
    ],
)
def test_status_rejects_blank_labels(provider_id: str, display_name: str) -> None:
    """Reject blank provider identifiers and display names."""
    with pytest.raises(ValueError, match="must not be empty"):
        ProviderStatus(
            provider_id=provider_id,
            display_name=display_name,
            configuration=ProviderConfigurationState.CONFIGURED,
            operation=ProviderOperationalState.NO_RECORDED_OPERATION,
        )


def test_status_normalizes_labels() -> None:
    """Strip surrounding whitespace from provider labels."""
    status = ProviderStatus(
        provider_id="  synthetic  ",
        display_name="  Synthetic  ",
        configuration=ProviderConfigurationState.CONFIGURED,
        operation=ProviderOperationalState.NO_RECORDED_OPERATION,
    )

    assert status.provider_id == "synthetic"
    assert status.display_name == "Synthetic"


def test_status_model_exposes_only_declared_safe_fields() -> None:
    """Expose only the declared safe fields on the provider status model."""
    assert {field.name for field in fields(ProviderStatus)} == {
        "provider_id",
        "display_name",
        "configuration",
        "operation",
        "observed_at",
    }


def test_configuration_states_are_limited_to_safe_values() -> None:
    """Expose only the safe configuration states."""
    assert {state.value for state in ProviderConfigurationState} == {
        "configured",
        "misconfigured",
        "not_configured",
    }


def test_operational_states_are_limited_to_safe_values() -> None:
    """Expose only the four safe operational states."""
    assert {state.value for state in ProviderOperationalState} == {
        "no_recorded_operation",
        "success",
        "transient_failure",
        "configuration_error",
    }


def test_default_reader_reports_unconfigured_without_recorded_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derive unconfigured state and no recorded operation from absent settings."""
    monkeypatch.delenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", raising=False)

    statuses = DefaultProviderStatusReader().read_statuses()

    assert [status.provider_id for status in statuses] == ["tmdb", "jellyfin"]
    assert all(status.configuration is ProviderConfigurationState.NOT_CONFIGURED for status in statuses)
    assert all(status.operation is ProviderOperationalState.NO_RECORDED_OPERATION for status in statuses)
    assert all(status.observed_at is None for status in statuses)


def test_default_reader_reports_configured_without_recorded_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Derive configured state from complete synthetic settings."""
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", "synthetic-tmdb-value")
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", "https://jellyfin.synthetic.invalid/")
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", "synthetic-jellyfin-value")
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", "synthetic-user")

    statuses = DefaultProviderStatusReader().read_statuses()

    assert all(status.configuration is ProviderConfigurationState.CONFIGURED for status in statuses)
    assert all(status.operation is ProviderOperationalState.NO_RECORDED_OPERATION for status in statuses)


def test_default_reader_reports_malformed_tmdb_configuration_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface present-but-invalid TMDB settings as a safe configuration error."""
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", "synthetic-tmdb-value")
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_HTTP__CONNECT_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", raising=False)

    statuses = {status.provider_id: status for status in DefaultProviderStatusReader().read_statuses()}

    assert statuses["tmdb"].configuration is ProviderConfigurationState.MISCONFIGURED
    assert statuses["tmdb"].operation is ProviderOperationalState.CONFIGURATION_ERROR
    assert statuses["tmdb"].observed_at is None
    assert statuses["jellyfin"].configuration is ProviderConfigurationState.NOT_CONFIGURED
    assert statuses["jellyfin"].operation is ProviderOperationalState.NO_RECORDED_OPERATION


def test_default_reader_reports_malformed_jellyfin_configuration_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface present-but-invalid Jellyfin settings as a safe configuration error."""
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", "https://jellyfin.synthetic.invalid/")
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", "synthetic-jellyfin-value")
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", "synthetic-user")
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_HTTP__CONNECT_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.delenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", raising=False)

    statuses = {status.provider_id: status for status in DefaultProviderStatusReader().read_statuses()}

    assert statuses["jellyfin"].configuration is ProviderConfigurationState.MISCONFIGURED
    assert statuses["jellyfin"].operation is ProviderOperationalState.CONFIGURATION_ERROR
    assert statuses["tmdb"].configuration is ProviderConfigurationState.NOT_CONFIGURED
    assert statuses["tmdb"].operation is ProviderOperationalState.NO_RECORDED_OPERATION


def test_default_reader_treats_partial_configuration_as_misconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat a partially supplied configuration as malformed rather than absent."""
    monkeypatch.delenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", raising=False)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", "synthetic-user")
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", raising=False)

    statuses = {status.provider_id: status for status in DefaultProviderStatusReader().read_statuses()}

    assert statuses["jellyfin"].configuration is ProviderConfigurationState.MISCONFIGURED
    assert statuses["jellyfin"].operation is ProviderOperationalState.CONFIGURATION_ERROR
    assert statuses["tmdb"].configuration is ProviderConfigurationState.NOT_CONFIGURED


def test_default_reader_does_not_expose_synthetic_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep configured secrets, URLs, and identifiers out of the safe status model."""
    tmdb_value = "synthetic-tmdb-value"
    jellyfin_url = "https://jellyfin.synthetic.invalid/"
    jellyfin_value = "synthetic-jellyfin-value"
    jellyfin_user_id = "synthetic-user"
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", tmdb_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", jellyfin_url)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", jellyfin_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", jellyfin_user_id)

    statuses = DefaultProviderStatusReader().read_statuses()
    rendered = repr([asdict(status) for status in statuses])

    assert all(status.configuration is ProviderConfigurationState.CONFIGURED for status in statuses)
    for value in (tmdb_value, jellyfin_url, jellyfin_value, jellyfin_user_id):
        assert value not in rendered
