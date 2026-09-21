"""Offline tests for the Jellyfin library synchronization page."""

from __future__ import annotations

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import pytest
from fastapi.testclient import TestClient

from media_recommender.application import (
    ConfigurationState,
    OperationalState,
    ProviderKind,
    ProviderStatus,
    WorkflowCounts,
    WorkflowItem,
    WorkflowItemStatus,
    WorkflowKind,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.domain import MediaId
from media_recommender.web import create_app
from media_recommender.web.routes.jellyfin import get_jellyfin_provider_status, get_jellyfin_sync_service

if TYPE_CHECKING:
    import pytest
    from httpx import Client, Response

_REPEATED_SYNC_CALLS = 2

_JELLYFIN_BASE_URL_ENV = "MEDIA_RECOMMENDER_JELLYFIN_BASE_URL"
_JELLYFIN_TOKEN_ENV = "MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN"  # noqa: S105 - environment variable name, not a secret.
_JELLYFIN_USER_ID_ENV = "MEDIA_RECOMMENDER_JELLYFIN_USER_ID"

_SYNTHETIC_SECRETS = (
    "https://jellyfin.synthetic.invalid/",
    "synthetic-jellyfin-token",
    "synthetic-user-id",
)
_SYNTHETIC_REASON = "synthetic-private-reason"


class FakeJellyfinService:
    """Record Jellyfin-only facade calls and return a configured report."""

    def __init__(self, report: WorkflowReport | None = None, *, error: Exception | None = None) -> None:
        """Store the synthetic report or error.

        :param report: Report returned by a successful call.
        :param error: Exception raised instead of returning a report.
        """
        self._report = report
        self._error = error
        self.calls = 0

    async def synchronize_jellyfin_library(self) -> WorkflowReport:
        """Record one facade call and return the configured outcome.

        :return: Configured synthetic report.
        :raises Exception: The configured synthetic error, when present.
        """
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._report is not None
        return self._report


def _report(
    status: WorkflowStatus,
    counts: WorkflowCounts,
    items: tuple[WorkflowItem, ...] = (),
) -> WorkflowReport:
    """Build a synthetic Jellyfin library report.

    :param status: Aggregate report status.
    :param counts: Aggregate report counts.
    :param items: Optional normalized item results.
    :return: Synthetic workflow report.
    """
    return WorkflowReport(kind=WorkflowKind.JELLYFIN_LIBRARY, status=status, counts=counts, items=items)


def _configured_status() -> ProviderStatus:
    """Return a synthetic configured Jellyfin status snapshot.

    :return: Configured provider status snapshot.
    """
    return ProviderStatus(provider=ProviderKind.JELLYFIN, configuration=ConfigurationState.CONFIGURED)


def _client(
    service: FakeJellyfinService,
    status: ProviderStatus,
) -> Client:
    """Build a test client with the Jellyfin facade and status overridden.

    :param service: Synthetic Jellyfin facade.
    :param status: Synthetic Jellyfin configuration snapshot.
    :return: Offline HTTPX-compatible test client.
    """
    app = create_app()
    app.dependency_overrides[get_jellyfin_sync_service] = lambda: service
    app.dependency_overrides[get_jellyfin_provider_status] = lambda: status
    return cast("Client", TestClient(app))


def _csrf_token(client: Client) -> str:
    """Return the CSRF token rendered by the synchronization page.

    :param client: Test client carrying a session cookie.
    :return: Hidden form token value.
    """
    response = client.get("/synchronizations/jellyfin")
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _post(client: Client, *, token: str | None, origin: str | None = None) -> Response:
    """Submit one synthetic synchronization request.

    :param client: Test client carrying a session cookie.
    :param token: CSRF token to submit, or ``None`` to omit it.
    :param origin: Optional ``Origin`` header value.
    :return: HTTP response.
    """
    data: dict[str, str] = {}
    if token is not None:
        data["csrf_token"] = token
    headers = {"Origin": origin} if origin is not None else None
    return client.post("/synchronizations/jellyfin", data=data, headers=headers)


def test_get_renders_form_navigation_and_csrf_token_when_configured() -> None:
    """Render the synchronization form with navigation and a CSRF token."""
    client = _client(FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())), _configured_status())

    response = client.get("/synchronizations/jellyfin")

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/synchronizations/jellyfin">Jellyfin synchronization</a>' in response.text
    assert 'method="post"' in response.text
    assert 'name="csrf_token"' in response.text
    assert "Synchronize Jellyfin library" in response.text
    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_get_renders_unavailable_control_when_not_configured() -> None:
    """Render a clear unavailable state without a form when Jellyfin is unconfigured."""
    status = ProviderStatus(provider=ProviderKind.JELLYFIN, configuration=ConfigurationState.NOT_CONFIGURED)
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, status)

    response = client.get("/synchronizations/jellyfin")

    assert response.status_code == HTTPStatus.OK
    assert "not configured" in response.text
    assert "unavailable" in response.text
    assert '<button type="submit" disabled>' in response.text


def test_get_renders_invalid_configuration_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render present but malformed configuration as a configuration error."""
    monkeypatch.setenv(_JELLYFIN_BASE_URL_ENV, "https://jellyfin.synthetic.invalid/")
    monkeypatch.setenv(_JELLYFIN_TOKEN_ENV, "synthetic-jellyfin-token")
    monkeypatch.setenv(_JELLYFIN_USER_ID_ENV, "synthetic-user-id")
    monkeypatch.setenv("MEDIA_RECOMMENDER_JELLYFIN_HTTP__CONNECT_TIMEOUT_SECONDS", "not-a-number")
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    app = create_app()
    app.dependency_overrides[get_jellyfin_sync_service] = lambda: service
    client = cast("Client", TestClient(app))

    response = client.get("/synchronizations/jellyfin")

    assert response.status_code == HTTPStatus.OK
    assert "invalid" in response.text
    assert "unavailable" in response.text
    assert '<button type="submit" disabled>' in response.text
    for value in (*_SYNTHETIC_SECRETS, "not-a-number"):
        assert value not in response.text


def test_successful_synchronization_calls_facade_once_and_renders_counts() -> None:
    """Synchronize once and render aggregate counts without item details."""
    service = FakeJellyfinService(
        _report(
            WorkflowStatus.SUCCESS,
            WorkflowCounts(succeeded=3, unresolved=1, ambiguous=2, invalid=1, removed=2),
        )
    )
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert service.calls == 1
    assert "Synchronization completed." in response.text
    for count in (3, 1, 2, 1, 2):
        assert f"<dd>{count}</dd>" in response.text
    assert "No longer in library" in response.text


def test_partial_result_does_not_claim_success_or_render_items() -> None:
    """Render aggregate partial counts without exposing item details."""
    item = WorkflowItem(2, WorkflowItemStatus.UNRESOLVED, _SYNTHETIC_REASON, (MediaId.new(),))
    service = FakeJellyfinService(
        _report(
            WorkflowStatus.PARTIAL,
            WorkflowCounts(succeeded=1, unresolved=2, ambiguous=1, invalid=1),
            (item,),
        )
    )
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "not silently attached" in response.text
    for count in (1, 2, 1, 1):
        assert f"<dd>{count}</dd>" in response.text
    assert _SYNTHETIC_REASON not in response.text
    assert str(item.candidate_ids[0]) not in response.text


def test_authentication_failure_is_distinct_and_retryable() -> None:
    """Render an authentication failure distinctly without leaking values."""
    service = FakeJellyfinService(
        _report(
            WorkflowStatus.FAILED,
            WorkflowCounts(failed=1),
            (WorkflowItem(1, WorkflowItemStatus.FAILED, "provider_authentication_failure"),),
        )
    )
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "rejected the configured credentials" in response.text
    assert "provider_authentication_failure" not in response.text
    assert "<dl>" not in response.text


def test_transient_failure_is_distinct_and_retryable() -> None:
    """Render a transient provider failure distinctly without leaking values."""
    service = FakeJellyfinService(
        _report(
            WorkflowStatus.FAILED,
            WorkflowCounts(failed=1),
            (WorkflowItem(1, WorkflowItemStatus.FAILED, "provider_transient_failure"),),
        )
    )
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "temporarily unavailable" in response.text
    assert "provider_transient_failure" not in response.text
    assert "<dl>" not in response.text


def test_generic_failure_renders_retryable_message() -> None:
    """Render a generic failure without raw failure reasons."""
    service = FakeJellyfinService(
        _report(
            WorkflowStatus.FAILED,
            WorkflowCounts(failed=1),
            (WorkflowItem(1, WorkflowItemStatus.FAILED, "provider_failure"),),
        )
    )
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "could not be completed" in response.text
    assert "provider_failure" not in response.text


def test_unexpected_exception_is_translated_without_propagation_or_leakage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Translate an unexpected facade exception into a sanitized response."""
    monkeypatch.setenv(_JELLYFIN_TOKEN_ENV, "synthetic-jellyfin-token")
    service = FakeJellyfinService(
        error=RuntimeError(f"{_SYNTHETIC_REASON} synthetic-jellyfin-token https://jellyfin.synthetic.invalid/")
    )
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    with caplog.at_level(logging.DEBUG):
        response = _post(client, token=token)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "could not be completed" in response.text
    for value in (*_SYNTHETIC_SECRETS, _SYNTHETIC_REASON):
        assert value not in response.text
        assert value not in caplog.text


def test_unconfigured_post_is_rejected_before_facade() -> None:
    """Reject a synchronization attempt without valid configuration."""
    status = ProviderStatus(
        provider=ProviderKind.JELLYFIN,
        configuration=ConfigurationState.NOT_CONFIGURED,
        operational=OperationalState.CONFIGURATION_ERROR,
    )
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, status)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == 0
    assert "invalid" in response.text


def test_missing_or_incorrect_csrf_token_is_rejected_before_sync() -> None:
    """Reject submissions without a valid session CSRF token."""
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, _configured_status())
    _csrf_token(client)

    missing = _post(client, token=None)
    incorrect = _post(client, token="synthetic-incorrect-token")  # noqa: S106 - synthetic test value, not a secret.

    assert missing.status_code == HTTPStatus.FORBIDDEN
    assert incorrect.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == 0


def test_cross_origin_submission_is_rejected_before_sync() -> None:
    """Reject a valid token submitted from a different origin."""
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    response = _post(client, token=token, origin="https://synthetic-evil.invalid")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == 0


def test_repeat_synchronization_invokes_fresh_snapshot() -> None:
    """Invoke the facade once per submission without claiming already-imported state."""
    service = FakeJellyfinService(
        _report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=2, removed=1)),
    )
    client = _client(service, _configured_status())
    token = _csrf_token(client)

    first = _post(client, token=token)
    second = _post(client, token=token)

    assert first.status_code == HTTPStatus.OK
    assert second.status_code == HTTPStatus.OK
    assert service.calls == _REPEATED_SYNC_CALLS
    assert "already imported" not in second.text.lower()


def test_settings_and_status_pages_remain_available_after_failure() -> None:
    """Keep read-only pages reachable with a failed synchronization facade configured."""
    service = FakeJellyfinService(error=RuntimeError("synthetic failure"))
    app = create_app()
    app.dependency_overrides[get_jellyfin_sync_service] = lambda: service
    app.dependency_overrides[get_jellyfin_provider_status] = _configured_status
    client = cast("Client", TestClient(app))

    settings_response = client.get("/settings")
    status_response = client.get("/providers/status")

    assert settings_response.status_code == HTTPStatus.OK
    assert status_response.status_code == HTTPStatus.OK


def test_page_does_not_render_session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render the form without exposing the configured session secret."""
    monkeypatch.setenv("MEDIA_RECOMMENDER_WEB_SESSION_SECRET", "synthetic-session-secret-value")
    client = _client(FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())), _configured_status())

    response = client.get("/synchronizations/jellyfin")

    assert response.status_code == HTTPStatus.OK
    assert "synthetic-session-secret-value" not in response.text
