"""Offline tests for the Jellyfin library synchronization page."""

from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi.testclient import TestClient

from media_recommender.application import (
    WorkflowCounts,
    WorkflowItem,
    WorkflowItemStatus,
    WorkflowKind,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.web import create_app
from media_recommender.web.routes.jellyfin import get_jellyfin_configuration_state, get_jellyfin_sync_service

if TYPE_CHECKING:
    from httpx import Client, Response

_SYNTHETIC_PROVIDER_URL = "http://synthetic-jellyfin.invalid"
_SYNTHETIC_TOKEN = "synthetic-jellyfin-api-token"  # noqa: S105 - synthetic test value, not a real secret.
_SYNTHETIC_USER_ID = "synthetic-jellyfin-user-id"
_SYNTHETIC_REASON = "synthetic-private-reason"


class FakeJellyfinSyncService:
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


def _client(
    service: FakeJellyfinSyncService,
    *,
    configured: bool = True,
    blocking_outcome: str | None = None,
) -> Client:
    """Build a test client with the Jellyfin facade and configuration overridden.

    :param service: Synthetic Jellyfin facade.
    :param configured: Whether the synthetic configuration state validates.
    :param blocking_outcome: Optional safe blocking outcome value.
    :return: Offline HTTPX-compatible test client.
    """
    app = create_app()
    app.dependency_overrides[get_jellyfin_sync_service] = lambda: service
    app.dependency_overrides[get_jellyfin_configuration_state] = lambda: (
        configured,
        blocking_outcome,
    )
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


def test_get_renders_accessible_form_and_session_csrf_token() -> None:
    """Render the synchronization form with navigation and a CSRF token."""
    client = _client(FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())))

    response = client.get("/synchronizations/jellyfin")

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/synchronizations/jellyfin">Jellyfin sync</a>' in response.text
    assert 'method="post"' in response.text
    assert 'name="csrf_token"' in response.text
    assert "Synchronize Jellyfin library" in response.text
    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_get_without_configuration_disables_the_control() -> None:
    """Render the page without an enabled action when Jellyfin is not configured."""
    client = _client(
        FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())),
        configured=False,
        blocking_outcome="unavailable",
    )

    response = client.get("/synchronizations/jellyfin")

    assert response.status_code == HTTPStatus.OK
    assert "not configured" in response.text
    assert "disabled" in response.text


def test_get_with_invalid_configuration_disables_the_control() -> None:
    """Render the page without an enabled action when Jellyfin configuration is invalid."""
    client = _client(
        FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())),
        configured=False,
        blocking_outcome="invalid_configuration",
    )

    response = client.get("/synchronizations/jellyfin")

    assert response.status_code == HTTPStatus.OK
    assert "invalid" in response.text
    assert "disabled" in response.text


def test_successful_synchronization_renders_aggregate_counts() -> None:
    """Render success with only the aggregate counts supported by the report."""
    service = FakeJellyfinSyncService(
        _report(
            WorkflowStatus.SUCCESS,
            WorkflowCounts(succeeded=4, unresolved=1, ambiguous=2, invalid=3, removed=5),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert service.calls == 1
    assert "Synchronization completed." in response.text
    for count in (4, 1, 2, 3, 5):
        assert f"<dd>{count}</dd>" in response.text
    assert "Removed" in response.text


def test_partial_synchronization_states_unsynchronized_records() -> None:
    """Render a partial result without claiming every record synchronized."""
    service = FakeJellyfinSyncService(
        _report(
            WorkflowStatus.PARTIAL,
            WorkflowCounts(succeeded=2, unresolved=1, invalid=1),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "not synchronized" in response.text
    assert "not silently attached" in response.text


def test_authentication_failure_is_distinct_and_retryable() -> None:
    """Render a sanitized authentication failure without provider details."""
    service = FakeJellyfinSyncService(
        _report(
            WorkflowStatus.FAILED,
            WorkflowCounts(failed=1),
            (WorkflowItem(1, WorkflowItemStatus.FAILED, "provider_authentication_failure"),),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert service.calls == 1
    assert "rejected the configured credentials" in response.text
    assert "Synchronization completed." not in response.text
    assert 'method="post"' in response.text


def test_transient_failure_is_distinct_and_retryable() -> None:
    """Render a sanitized transient failure and keep the form usable."""
    service = FakeJellyfinSyncService(
        _report(
            WorkflowStatus.FAILED,
            WorkflowCounts(failed=1),
            (WorkflowItem(1, WorkflowItemStatus.FAILED, "provider_transient_failure"),),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "temporarily unavailable" in response.text
    assert 'method="post"' in response.text


def test_generic_failure_is_rendered_without_details() -> None:
    """Render a generic sanitized failure without raw reasons."""
    service = FakeJellyfinSyncService(
        _report(
            WorkflowStatus.FAILED,
            WorkflowCounts(failed=1),
            (WorkflowItem(1, WorkflowItemStatus.FAILED, _SYNTHETIC_REASON),),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "could not be completed" in response.text
    assert _SYNTHETIC_REASON not in response.text


def test_unexpected_failure_returns_sanitized_server_error() -> None:
    """Translate an unexpected facade failure into a generic 500 page."""
    service = FakeJellyfinSyncService(error=RuntimeError("synthetic unexpected failure"))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert service.calls == 1
    assert "synthetic unexpected failure" not in response.text
    assert "<dd>" not in response.text


def test_unconfigured_post_is_rejected_without_invoking_the_facade() -> None:
    """Reject a POST for absent configuration before the facade runs."""
    service = FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, configured=False, blocking_outcome="unavailable")
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == 0
    assert "not configured" in response.text


def test_invalid_configuration_post_is_rejected_without_invoking_the_facade() -> None:
    """Reject a POST for invalid configuration before the facade runs."""
    service = FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service, configured=False, blocking_outcome="invalid_configuration")
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == 0
    assert "invalid" in response.text


def test_missing_csrf_token_is_rejected() -> None:
    """Reject a state-changing submission without a CSRF token."""
    service = FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service)
    _csrf_token(client)

    response = _post(client, token=None)

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == 0


def test_invalid_csrf_token_is_rejected() -> None:
    """Reject a state-changing submission with an unexpected CSRF token."""
    service = FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service)
    _csrf_token(client)

    response = _post(client, token="synthetic-invalid-token")  # noqa: S106 - synthetic test value, not a real secret.

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == 0


def test_cross_origin_post_is_rejected() -> None:
    """Reject a state-changing submission from a foreign origin."""
    service = FakeJellyfinSyncService(_report(WorkflowStatus.SUCCESS, WorkflowCounts()))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, origin="http://synthetic-attacker.invalid")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == 0


_EXPECTED_RETRY_CALLS = 2


def test_retry_runs_a_fresh_synchronization() -> None:
    """A repeated POST invokes the facade again instead of reporting a repeat."""
    service = FakeJellyfinSyncService(
        _report(
            WorkflowStatus.SUCCESS,
            WorkflowCounts(succeeded=3, removed=1),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    first = _post(client, token=token)
    second = _post(client, token=token)

    assert first.status_code == HTTPStatus.OK
    assert second.status_code == HTTPStatus.OK
    assert service.calls == _EXPECTED_RETRY_CALLS
    assert "already" not in second.text.lower()


def test_responses_do_not_leak_secrets_or_provider_details() -> None:
    """Keep synthetic provider URL, token, and user id out of every response."""
    service = FakeJellyfinSyncService(
        _report(
            WorkflowStatus.SUCCESS,
            WorkflowCounts(succeeded=1),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    pages = [
        client.get("/synchronizations/jellyfin").text,
        _post(client, token=token).text,
        _post(client, token=None).text,
    ]

    for page in pages:
        assert _SYNTHETIC_PROVIDER_URL not in page
        assert _SYNTHETIC_TOKEN not in page
        assert _SYNTHETIC_USER_ID not in page
