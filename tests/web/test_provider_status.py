"""Offline route and template tests for the provider status page."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from media_recommender.application.provider_status import (
    ProviderConfigurationState,
    ProviderOperationalState,
    ProviderStatus,
    ProviderStatusReader,
)
from media_recommender.web import create_app
from media_recommender.web.routes.pages import get_provider_status_reader

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest
    from fastapi import FastAPI
    from httpx import Client


_PROVIDER_COUNT = 2
_PROVIDER_ENV_PREFIXES = ("MEDIA_RECOMMENDER_TMDB_", "MEDIA_RECOMMENDER_JELLYFIN_")


class FakeProviderStatusReader:
    """Synthetic status reader returning fixed safe statuses."""

    def __init__(self, statuses: tuple[ProviderStatus, ...]) -> None:
        """Initialize the fake with synthetic safe statuses.

        :param statuses: Statuses returned for every read.
        """
        self._statuses = statuses

    def read_statuses(self) -> Sequence[ProviderStatus]:
        """Return the synthetic statuses without contacting providers.

        :return: Fixed synthetic statuses.
        """
        return self._statuses


def _client(app: FastAPI) -> Client:
    """Return a statically typed client for a synthetic HTTP application.

    :param app: Application to exercise.
    :return: HTTPX-compatible test client.
    """
    return cast("Client", TestClient(app))


def _clear_provider_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every synthetic provider setting from the environment.

    :param monkeypatch: Pytest environment patcher.
    """
    for name in list(os.environ):
        if name.startswith(_PROVIDER_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)


def _app_with(statuses: tuple[ProviderStatus, ...]) -> FastAPI:
    """Return an application whose status reader returns synthetic statuses.

    :param statuses: Synthetic statuses exposed by the overridden reader.
    :return: Application with the provider status dependency overridden.
    """
    app = create_app()

    def override_reader() -> ProviderStatusReader:
        """Return a synthetic status reader for the overridden dependency."""
        return FakeProviderStatusReader(statuses)

    app.dependency_overrides[get_provider_status_reader] = override_reader
    return app


def test_provider_status_is_reachable_from_shared_navigation() -> None:
    """Link the provider status page from the shared primary navigation."""
    response = _client(create_app()).get("/")

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/providers/status">Provider status</a>' in response.text


def test_provider_status_shows_unconfigured_without_recorded_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render unconfigured providers and an explicit absent-operation state."""
    _clear_provider_environment(monkeypatch)

    response = _client(create_app()).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert response.text.count("Not configured") == _PROVIDER_COUNT
    assert response.text.count("No recorded operation") == _PROVIDER_COUNT
    assert "Not recorded" in response.text


def test_provider_status_shows_success_with_timestamp() -> None:
    """Render a configured provider with a successful operation and timestamp."""
    observed_at = datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC)
    statuses = (
        ProviderStatus(
            provider_id="tmdb",
            display_name="TMDB",
            configuration=ProviderConfigurationState.CONFIGURED,
            operation=ProviderOperationalState.SUCCESS,
            observed_at=observed_at,
        ),
    )

    response = _client(_app_with(statuses)).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Configured" in response.text
    assert "Last operation succeeded" in response.text
    assert f'<time datetime="{observed_at.isoformat()}">{observed_at.isoformat()}</time>' in response.text


def test_provider_status_distinguishes_transient_failure_from_configuration_error() -> None:
    """Render distinct text for a transient failure and a configuration error."""
    statuses = (
        ProviderStatus(
            provider_id="tmdb",
            display_name="TMDB",
            configuration=ProviderConfigurationState.CONFIGURED,
            operation=ProviderOperationalState.TRANSIENT_FAILURE,
            observed_at=datetime(2024, 5, 6, 7, 8, 9, tzinfo=UTC),
        ),
        ProviderStatus(
            provider_id="jellyfin",
            display_name="Jellyfin",
            configuration=ProviderConfigurationState.MISCONFIGURED,
            operation=ProviderOperationalState.CONFIGURATION_ERROR,
        ),
    )

    response = _client(_app_with(statuses)).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Last operation failed temporarily" in response.text
    assert "Configuration error" in response.text


def test_provider_status_does_not_leak_valid_synthetic_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep valid synthetic secrets, URLs, and identifiers out of the response."""
    _clear_provider_environment(monkeypatch)
    tmdb_value = "synthetic-tmdb-value"
    jellyfin_url = "https://jellyfin.synthetic.invalid/"
    jellyfin_value = "synthetic-jellyfin-value"
    jellyfin_user_id = "synthetic-user"
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", tmdb_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", jellyfin_url)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", jellyfin_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", jellyfin_user_id)

    response = _client(create_app()).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Not configured" not in response.text
    assert "Misconfigured" not in response.text
    assert "Configuration error" not in response.text
    for value in (tmdb_value, jellyfin_url, jellyfin_value, jellyfin_user_id):
        assert value not in response.text


def test_provider_status_does_not_leak_malformed_synthetic_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep malformed synthetic values out of the response while reporting a configuration error."""
    _clear_provider_environment(monkeypatch)
    tmdb_value = "synthetic-tmdb-value"
    malformed_value = "not-a-number"
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", tmdb_value)
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_HTTP__CONNECT_TIMEOUT_SECONDS", malformed_value)

    response = _client(create_app()).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Misconfigured" in response.text
    assert "Configuration error" in response.text
    assert "Not configured" in response.text
    assert "No recorded operation" in response.text
    for value in (tmdb_value, malformed_value):
        assert value not in response.text


def test_provider_status_shows_blank_required_value_as_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render an explicitly supplied blank required value as a configuration error."""
    _clear_provider_environment(monkeypatch)
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", "")

    response = _client(create_app()).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Misconfigured" in response.text
    assert "Configuration error" in response.text
    assert "Not configured" in response.text


def test_provider_status_shows_malformed_optional_setting_as_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Render a malformed optional setting without required values as a configuration error."""
    _clear_provider_environment(monkeypatch)
    malformed_value = "not-a-number"
    monkeypatch.setenv("MEDIA_RECOMMENDER_TMDB_HTTP__CONNECT_TIMEOUT_SECONDS", malformed_value)

    response = _client(create_app()).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Misconfigured" in response.text
    assert "Configuration error" in response.text
    assert malformed_value not in response.text


def test_provider_status_route_is_read_only() -> None:
    """Expose the provider status page only through a safe GET route."""
    route = next(
        route for route in create_app().routes if isinstance(route, APIRoute) and route.path == "/providers/status"
    )

    assert route.methods == {"GET"}
