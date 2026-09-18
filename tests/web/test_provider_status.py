"""Offline route and template tests for the provider status page."""

from __future__ import annotations

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
    monkeypatch.delenv("MEDIA_RECOMMENDER_TMDB_API_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_BASE_URL", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN", raising=False)
    monkeypatch.delenv("MEDIA_RECOMMENDER_JELLYFIN_USER_ID", raising=False)

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
            configuration=ProviderConfigurationState.NOT_CONFIGURED,
            operation=ProviderOperationalState.CONFIGURATION_ERROR,
        ),
    )

    response = _client(_app_with(statuses)).get("/providers/status")

    assert response.status_code == HTTPStatus.OK
    assert "Last operation failed temporarily" in response.text
    assert "Configuration error" in response.text


def test_provider_status_does_not_leak_synthetic_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep synthetic secrets, URLs, and identifiers out of the rendered page."""
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
    for value in (tmdb_value, jellyfin_url, jellyfin_value, jellyfin_user_id):
        assert value not in response.text


def test_provider_status_route_is_read_only() -> None:
    """Expose the provider status page only through a safe GET route."""
    route = next(
        route for route in create_app().routes if isinstance(route, APIRoute) and route.path == "/providers/status"
    )

    assert route.methods == {"GET"}
