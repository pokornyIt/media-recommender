"""Offline tests for the read-only provider status page."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi.testclient import TestClient

from media_recommender.application import (
    ConfigurationState,
    OperationalState,
    ProviderKind,
    ProviderStatus,
)
from media_recommender.web import create_app
from media_recommender.web.routes.pages import get_provider_statuses

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest
    from httpx import Client

_TMDB_TOKEN_ENV = "MEDIA_RECOMMENDER_TMDB_API_TOKEN"  # noqa: S105 - environment variable name, not a secret.
_TMDB_HTTP_TIMEOUT_ENV = "MEDIA_RECOMMENDER_TMDB_HTTP__CONNECT_TIMEOUT_SECONDS"
_JELLYFIN_BASE_URL_ENV = "MEDIA_RECOMMENDER_JELLYFIN_BASE_URL"
_JELLYFIN_TOKEN_ENV = "MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN"  # noqa: S105 - environment variable name, not a secret.
_JELLYFIN_USER_ID_ENV = "MEDIA_RECOMMENDER_JELLYFIN_USER_ID"

_PROVIDER_COUNT = 2

_SYNTHETIC_SECRETS = (
    "synthetic-tmdb-value",
    "https://jellyfin.synthetic.invalid/",
    "synthetic-jellyfin-value",
    "synthetic-user",
)


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


def _status(
    provider: ProviderKind,
    configuration: ConfigurationState,
    operational: OperationalState,
    observed_at: datetime | None = None,
) -> ProviderStatus:
    """Build a synthetic status snapshot.

    :param provider: Provider the snapshot describes.
    :param configuration: Synthetic configuration state.
    :param operational: Synthetic operational state.
    :param observed_at: Optional synthetic observation timestamp.
    :return: Synthetic provider status snapshot.
    """
    return ProviderStatus(
        provider=provider,
        configuration=configuration,
        operational=operational,
        observed_at=observed_at,
    )


def _client_with(statuses: Sequence[ProviderStatus]) -> Client:
    """Build a statically typed test client with the status dependency overridden.

    :param statuses: Synthetic statuses returned by the override.
    :return: Offline HTTPX-compatible test client.
    """
    app = create_app()
    app.dependency_overrides[get_provider_statuses] = lambda: statuses
    return cast("Client", TestClient(app))


def test_provider_status_page_renders_no_recorded_operation_and_navigation() -> None:
    """Render neutral operational state and keep the page reachable from navigation."""
    statuses = [
        _status(ProviderKind.TMDB, ConfigurationState.CONFIGURED, OperationalState.NO_RECORDED_OPERATION),
        _status(ProviderKind.JELLYFIN, ConfigurationState.NOT_CONFIGURED, OperationalState.NO_RECORDED_OPERATION),
    ]

    response = _client_with(statuses).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/providers/status">Provider status</a>' in response.text
    assert response.text.count("No recorded operation") == _PROVIDER_COUNT
    assert "Configured" in response.text
    assert "Not configured" in response.text
    assert "Success" not in response.text
    assert "Transient failure" not in response.text
    assert "Configuration error" not in response.text


def test_provider_status_page_renders_success_with_timestamp() -> None:
    """Render a successful known operation with its timezone-aware timestamp."""
    observed_at = datetime(2026, 9, 18, 12, 30, tzinfo=UTC)
    statuses = [
        _status(ProviderKind.TMDB, ConfigurationState.CONFIGURED, OperationalState.SUCCESS, observed_at),
        _status(ProviderKind.JELLYFIN, ConfigurationState.CONFIGURED, OperationalState.NO_RECORDED_OPERATION),
    ]

    response = _client_with(statuses).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Last known operation: Success (2026-09-18T12:30:00+00:00)" in response.text


def test_provider_status_page_renders_failure_states_distinctly() -> None:
    """Render transient failure and configuration error as distinct states."""
    statuses = [
        _status(ProviderKind.TMDB, ConfigurationState.CONFIGURED, OperationalState.TRANSIENT_FAILURE),
        _status(ProviderKind.JELLYFIN, ConfigurationState.CONFIGURED, OperationalState.CONFIGURATION_ERROR),
    ]

    response = _client_with(statuses).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Last known operation: Transient failure" in response.text
    assert "Last known operation: Configuration error" in response.text
    assert "Success" not in response.text


def test_default_request_path_does_not_leak_synthetic_configuration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Serve the default page with synthetic secrets configured and leak none of them."""
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(_TMDB_TOKEN_ENV, "synthetic-tmdb-value")
    monkeypatch.setenv(_JELLYFIN_BASE_URL_ENV, "https://jellyfin.synthetic.invalid/")
    monkeypatch.setenv(_JELLYFIN_TOKEN_ENV, "synthetic-jellyfin-value")
    monkeypatch.setenv(_JELLYFIN_USER_ID_ENV, "synthetic-user")

    with caplog.at_level(logging.DEBUG):
        response = cast("Client", TestClient(create_app())).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert response.text.count("Configured") == _PROVIDER_COUNT
    for value in _SYNTHETIC_SECRETS:
        assert value not in response.text
        assert value not in caplog.text


def test_default_request_path_renders_malformed_configuration_without_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Surface malformed configuration as configuration_error without leaking values."""
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(_JELLYFIN_BASE_URL_ENV, "https://jellyfin.synthetic.invalid/")
    monkeypatch.setenv(_JELLYFIN_TOKEN_ENV, "synthetic-jellyfin-value")
    monkeypatch.setenv(_JELLYFIN_USER_ID_ENV, "synthetic-user")
    monkeypatch.setenv(_TMDB_TOKEN_ENV, "synthetic-tmdb-value")
    monkeypatch.setenv(_TMDB_HTTP_TIMEOUT_ENV, "not-a-number")

    with caplog.at_level(logging.DEBUG):
        response = cast("Client", TestClient(create_app())).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Last known operation: Configuration error" in response.text
    for value in (*_SYNTHETIC_SECRETS, "not-a-number", "synthetic-tmdb-value"):
        assert value not in response.text
        assert value not in caplog.text


def test_default_request_path_reports_partial_configuration_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render incomplete but partially present configuration as the error state."""
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv(_JELLYFIN_BASE_URL_ENV, "https://jellyfin.synthetic.invalid/")

    response = cast("Client", TestClient(create_app())).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Last known operation: Configuration error" in response.text
    assert "https://jellyfin.synthetic.invalid/" not in response.text
    assert "No recorded operation" in response.text


def test_default_request_path_reports_absent_configuration_as_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve the page with no provider configuration as a neutral, non-error state."""
    _clear_provider_environment(monkeypatch)

    response = cast("Client", TestClient(create_app())).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert response.text.count("Not configured") == _PROVIDER_COUNT
    assert response.text.count("No recorded operation") == _PROVIDER_COUNT
    assert "Configuration error" not in response.text
