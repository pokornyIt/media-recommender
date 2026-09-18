"""Offline tests for the provider-independent provider status read model."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from media_recommender.application.provider_status import (
    DefaultProviderStatusReader,
    ProviderConfigurationState,
    ProviderOperationalState,
    ProviderStatus,
)


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


def test_default_reader_does_not_expose_configured_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep configured secrets, URLs, and identifiers out of the safe status model."""
    tmdb_value = "synthetic-tmdb-value"
    jellyfin_url = "https://jellyfin.synthetic.invalid/"
    jellyfin_value = "synthetic-jellyfin-value"
    jellyfin_user_id = "synthetic-user"
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", tmdb_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", jellyfin_url)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", jellyfin_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", jellyfin_user_id)

    rendered = repr(DefaultProviderStatusReader().read_statuses())

    for value in (tmdb_value, jellyfin_url, jellyfin_value, jellyfin_user_id):
        assert value not in rendered
