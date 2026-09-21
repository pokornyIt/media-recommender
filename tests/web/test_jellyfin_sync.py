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
    WorkflowFailureReason,
    WorkflowItem,
    WorkflowItemStatus,
    WorkflowKind,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.domain import MediaId
from media_recommender.web import create_app
from media_recommender.web.routes.jellyfin import (
    JELLYFIN_SYNC_PATH,
    JellyfinConfigurationState,
    get_jellyfin_configuration_state,
    get_jellyfin_sync_service,
    jellyfin_configuration_state,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from httpx import Client, Response

_SESSION_SECRET_ENV = "MEDIA_RECOMMENDER_WEB_SESSION_SECRET"  # noqa: S105 - environment variable name, not a secret.
_JELLYFIN_BASE_URL_ENV = "MEDIA_RECOMMENDER_JELLYFIN_BASE_URL"
_JELLYFIN_TOKEN_ENV = "MEDIA_RECOMMENDER_JELLYFIN_API_TOKEN"  # noqa: S105 - environment variable name, not a secret.
_JELLYFIN_USER_ID_ENV = "MEDIA_RECOMMENDER_JELLYFIN_USER_ID"

_SYNTHETIC_SECRET = "synthetic-session-secret-value"  # noqa: S105 - synthetic test value, not a real secret.
_SYNTHETIC_JELLYFIN_URL = "https://jellyfin.synthetic.invalid/"
_SYNTHETIC_JELLYFIN_TOKEN = "synthetic-jellyfin-value"  # noqa: S105 - synthetic test value, not a real secret.
_SYNTHETIC_JELLYFIN_USER = "synthetic-user"
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


def _failure_report(reason: WorkflowFailureReason) -> WorkflowReport:
    """Build a synthetic failed Jellyfin report with one safe reason.

    :param reason: Sanitized failure classification.
    :return: Failed synthetic workflow report.
    """
    return _report(
        WorkflowStatus.FAILED,
        WorkflowCounts(failed=1),
        (WorkflowItem(1, WorkflowItemStatus.FAILED, reason.value),),
    )


def _client(
    service: FakeJellyfinService,
    *,
    configuration: JellyfinConfigurationState = JellyfinConfigurationState.CONFIGURED,
) -> Client:
    """Build a test client with the Jellyfin facade and configuration overridden.

    :param service: Synthetic Jellyfin facade.
    :param configuration: Synthetic Jellyfin configuration state.
    :return: Offline HTTPX-compatible test client.
    """
    app = create_app()
    app.dependency_overrides[get_jellyfin_sync_service] = lambda: service
    app.dependency_overrides[get_jellyfin_configuration_state] = lambda: configuration
    return cast("Client", TestClient(app))


def _client_without_service(
    *,
    configuration: JellyfinConfigurationState | None = None,
) -> Client:
    """Build a test client without an installed Jellyfin synchronization service.

    :param configuration: Optional synthetic Jellyfin configuration state.
    :return: Offline HTTPX-compatible test client with no facade installed.
    """
    app = create_app()
    if configuration is not None:
        app.dependency_overrides[get_jellyfin_configuration_state] = lambda: configuration
    return cast("Client", TestClient(app))


def _csrf_token(client: Client) -> str:
    """Return the CSRF token rendered by the synchronization page.

    :param client: Test client carrying a session cookie.
    :return: Hidden form token value.
    """
    response = client.get(JELLYFIN_SYNC_PATH)
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _post(client: Client, *, token: str | None = None, origin: str | None = None) -> Response:
    """Submit one synthetic synchronization request.

    :param client: Test client carrying a session cookie.
    :param token: CSRF token to submit, or ``None`` to omit it.
    :param origin: Optional ``Origin`` header value.
    :return: HTTP response.
    """
    data = {} if token is None else {"csrf_token": token}
    headers = {"Origin": origin} if origin is not None else None
    return client.post(JELLYFIN_SYNC_PATH, data=data, headers=headers)


def test_get_renders_accessible_form_with_navigation_and_csrf_token() -> None:
    """Render the enabled form with navigation, a CSRF token, and a submit action."""
    client = _client(FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())))

    response = client.get(JELLYFIN_SYNC_PATH)

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/synchronizations/jellyfin">Jellyfin synchronization</a>' in response.text
    assert 'method="post"' in response.text
    assert 'name="csrf_token"' in response.text
    assert "<button" in response.text
    assert "disabled" not in response.text
    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_absent_configuration_disables_control_without_invoking_facade() -> None:
    """Render a non-secret absent-configuration message and a disabled control."""
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, configuration=JellyfinConfigurationState.ABSENT)
    token = _csrf_token(client)

    page = client.get(JELLYFIN_SYNC_PATH)
    response = _post(client, token=token)

    assert page.status_code == HTTPStatus.OK
    assert "disabled" in page.text
    assert "not configured" in page.text.lower()
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == 0


def test_invalid_configuration_is_distinct_and_does_not_invoke_facade() -> None:
    """Render an invalid-configuration message without disclosing the invalid value."""
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, configuration=JellyfinConfigurationState.INVALID)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert "configuration is invalid" in response.text
    assert service.calls == 0


def test_success_renders_aggregate_counts_and_removed_entries() -> None:
    """Render supported aggregate counts only for a completed synchronization."""
    report = _report(
        WorkflowStatus.SUCCESS,
        WorkflowCounts(succeeded=3, unresolved=2, ambiguous=4, invalid=5, removed=2),
    )
    service = FakeJellyfinService(report)
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert service.calls == 1
    assert "Synchronization completed." in response.text
    assert "<dt>Removed</dt>" in response.text
    for count in (3, 2, 4, 5):
        assert f"<dd>{count}</dd>" in response.text


def test_partial_result_does_not_claim_success_or_render_items() -> None:
    """Render aggregate partial counts without exposing item-level details."""
    item = WorkflowItem(2, WorkflowItemStatus.UNRESOLVED, _SYNTHETIC_REASON, (MediaId.new(),))
    report = _report(
        WorkflowStatus.PARTIAL,
        WorkflowCounts(succeeded=1, unresolved=2, ambiguous=3, invalid=4, failed=5),
        (item,),
    )
    service = FakeJellyfinService(report)
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "not synchronized" in response.text
    assert "Synchronization completed." not in response.text
    assert "<dt>Removed</dt>" in response.text
    for count in (1, 2, 3, 4, 5):
        assert f"<dd>{count}</dd>" in response.text
    assert _SYNTHETIC_REASON not in response.text
    assert str(item.candidate_ids[0]) not in response.text


def test_authentication_failure_is_distinct_and_free_of_raw_reasons() -> None:
    """Render an authentication failure distinctly without exposing the reason token."""
    service = FakeJellyfinService(_failure_report(WorkflowFailureReason.AUTHENTICATION_FAILURE))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "credentials" in response.text
    assert "not configured" not in response.text.lower()
    assert "authentication_failure" not in response.text
    assert "<dt>Failed</dt>" in response.text


def test_transient_failure_offers_manual_retry_without_raw_reasons() -> None:
    """Render a transient outage distinctly and support a manual retry."""
    service = FakeJellyfinService(_failure_report(WorkflowFailureReason.TRANSIENT_FAILURE))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "could not be reached" in response.text
    assert "Try again later" in response.text
    assert "transient_failure" not in response.text
    assert 'method="post"' in response.text


def test_generic_failure_is_sanitized() -> None:
    """Render a generic retryable failure without exposing the safe reason token."""
    service = FakeJellyfinService(_failure_report(WorkflowFailureReason.PROVIDER_FAILURE))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "could not be completed" in response.text
    assert "provider_failure" not in response.text


def test_unexpected_exception_is_translated_without_leakage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Translate an unexpected facade exception into a sanitized response."""
    monkeypatch.setenv(_SESSION_SECRET_ENV, _SYNTHETIC_SECRET)
    service = FakeJellyfinService(
        error=RuntimeError(f"{_SYNTHETIC_JELLYFIN_URL} {_SYNTHETIC_JELLYFIN_TOKEN} {_SYNTHETIC_REASON}")
    )
    client = _client(service)
    token = _csrf_token(client)

    with caplog.at_level(logging.DEBUG):
        response = _post(client, token=token)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "could not be completed" in response.text
    assert "<dt>Synchronized</dt>" not in response.text
    for value in (_SYNTHETIC_JELLYFIN_URL, _SYNTHETIC_JELLYFIN_TOKEN, _SYNTHETIC_SECRET, _SYNTHETIC_REASON):
        assert value not in response.text
        assert value not in caplog.text


def test_missing_or_incorrect_csrf_token_is_rejected_before_synchronization() -> None:
    """Reject submissions without a valid session CSRF token."""
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service)
    _csrf_token(client)

    missing = _post(client)
    incorrect = _post(client, token="synthetic-incorrect-token")  # noqa: S106 - synthetic test value, not a secret.

    assert missing.status_code == HTTPStatus.FORBIDDEN
    assert incorrect.status_code == HTTPStatus.FORBIDDEN
    assert "Request rejected" in missing.text
    assert service.calls == 0


def test_cross_origin_submission_is_rejected_before_synchronization() -> None:
    """Reject a valid token submitted from a different origin."""
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, origin="https://synthetic-evil.invalid")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == 0


def test_configured_request_does_not_leak_synthetic_configuration(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Serve a successful synchronization with synthetic secrets configured and leak none of them."""
    monkeypatch.setenv(_SESSION_SECRET_ENV, _SYNTHETIC_SECRET)
    monkeypatch.setenv(_JELLYFIN_BASE_URL_ENV, _SYNTHETIC_JELLYFIN_URL)
    monkeypatch.setenv(_JELLYFIN_TOKEN_ENV, _SYNTHETIC_JELLYFIN_TOKEN)
    monkeypatch.setenv(_JELLYFIN_USER_ID_ENV, _SYNTHETIC_JELLYFIN_USER)
    service = FakeJellyfinService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service)
    token = _csrf_token(client)

    with caplog.at_level(logging.DEBUG):
        response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert service.calls == 1
    for value in (
        _SYNTHETIC_JELLYFIN_URL,
        _SYNTHETIC_JELLYFIN_TOKEN,
        _SYNTHETIC_JELLYFIN_USER,
        _SYNTHETIC_SECRET,
    ):
        assert value not in response.text
        assert value not in caplog.text


def test_failed_submission_does_not_affect_settings_or_provider_status() -> None:
    """Keep the read-only settings and provider-status pages available after a failure."""
    service = FakeJellyfinService(_failure_report(WorkflowFailureReason.TRANSIENT_FAILURE))
    client = _client(service)
    token = _csrf_token(client)

    failure = _post(client, token=token)
    settings = client.get("/settings")
    provider_status = client.get("/providers/status")

    assert failure.status_code == HTTPStatus.OK
    assert settings.status_code == HTTPStatus.OK
    assert provider_status.status_code == HTTPStatus.OK


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (
            [ProviderStatus(provider=ProviderKind.JELLYFIN, configuration=ConfigurationState.CONFIGURED)],
            JellyfinConfigurationState.CONFIGURED,
        ),
        (
            [
                ProviderStatus(
                    provider=ProviderKind.JELLYFIN,
                    configuration=ConfigurationState.NOT_CONFIGURED,
                    operational=OperationalState.CONFIGURATION_ERROR,
                )
            ],
            JellyfinConfigurationState.INVALID,
        ),
        (
            [ProviderStatus(provider=ProviderKind.JELLYFIN, configuration=ConfigurationState.NOT_CONFIGURED)],
            JellyfinConfigurationState.ABSENT,
        ),
        ([], JellyfinConfigurationState.ABSENT),
    ],
)
def test_configuration_state_mapping_uses_safe_provider_status(
    statuses: Sequence[ProviderStatus],
    expected: JellyfinConfigurationState,
) -> None:
    """Map safe provider status snapshots to the synchronization configuration state."""
    assert jellyfin_configuration_state(statuses) is expected


@pytest.mark.parametrize(
    ("configuration", "message"),
    [
        (JellyfinConfigurationState.ABSENT, "not configured"),
        (JellyfinConfigurationState.INVALID, "configuration is invalid"),
    ],
)
def test_configuration_gate_without_service_returns_safe_outcome(
    configuration: JellyfinConfigurationState,
    message: str,
) -> None:
    """Return the distinct configuration outcome when no synchronization service is installed."""
    client = _client_without_service(configuration=configuration)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert message in response.text.lower()


@pytest.mark.parametrize("configuration", [None, JellyfinConfigurationState.CONFIGURED])
def test_csrf_rejection_without_service_is_enforced(
    configuration: JellyfinConfigurationState | None,
) -> None:
    """Enforce the established CSRF/origin rejection when no synchronization service is installed."""
    client = _client_without_service(configuration=configuration)
    token = _csrf_token(client)

    missing = _post(client)
    incorrect = _post(client, token="synthetic-incorrect-token")  # noqa: S106 - synthetic test value, not a secret.
    cross_origin = _post(client, token=token, origin="https://synthetic-evil.invalid")

    assert missing.status_code == HTTPStatus.FORBIDDEN
    assert incorrect.status_code == HTTPStatus.FORBIDDEN
    assert cross_origin.status_code == HTTPStatus.FORBIDDEN
    assert "Request rejected" in missing.text
