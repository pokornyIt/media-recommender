"""Offline tests for the Netflix viewing-activity import page."""

from __future__ import annotations

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from media_recommender.application import (
    WorkflowCounts,
    WorkflowItem,
    WorkflowItemStatus,
    WorkflowKind,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.domain import MediaId
from media_recommender.web import create_app
from media_recommender.web.middleware import RequestBodyLimitMiddleware
from media_recommender.web.routes.imports import get_netflix_import_service, get_netflix_upload_limit

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Client, Response

_SESSION_SECRET_ENV = "MEDIA_RECOMMENDER_WEB_SESSION_SECRET"  # noqa: S105 - environment variable name, not a secret.
_SYNTHETIC_SECRET = "synthetic-session-secret-value"  # noqa: S105 - synthetic test value, not a real secret.
_SYNTHETIC_CSV = b"Title,Date\nSynthetic Title,9/14/26\n"
_SYNTHETIC_LABEL = "Synthetic profile"
_SYNTHETIC_FILENAME = "synthetic-viewing.csv"
_SYNTHETIC_REASON = "synthetic-private-reason"


class FakeNetflixService:
    """Record Netflix-only facade calls and return a configured report."""

    def __init__(self, report: WorkflowReport | None = None, *, error: Exception | None = None) -> None:
        """Store the synthetic report or error.

        :param report: Report returned by a successful call.
        :param error: Exception raised instead of returning a report.
        """
        self._report = report
        self._error = error
        self.calls: list[tuple[Path, str]] = []
        self.path_existed: list[bool] = []

    async def import_netflix_viewing(self, path: Path, *, external_profile_id: str) -> WorkflowReport:
        """Record one facade call and return the configured outcome.

        :param path: Staged private CSV path.
        :param external_profile_id: Requested Netflix profile label.
        :return: Configured synthetic report.
        :raises Exception: The configured synthetic error, when present.
        """
        self.calls.append((path, external_profile_id))
        self.path_existed.append(path.exists())  # noqa: ASYNC240 - synthetic test assertion on a small file.
        if self._error is not None:
            raise self._error
        assert self._report is not None
        return self._report


def _report(
    status: WorkflowStatus,
    counts: WorkflowCounts,
    items: tuple[WorkflowItem, ...] = (),
) -> WorkflowReport:
    """Build a synthetic Netflix viewing-activity report.

    :param status: Aggregate report status.
    :param counts: Aggregate report counts.
    :param items: Optional normalized item results.
    :return: Synthetic workflow report.
    """
    return WorkflowReport(kind=WorkflowKind.NETFLIX_VIEWING, status=status, counts=counts, items=items)


def _client(service: FakeNetflixService, *, max_bytes: int = 1024) -> Client:
    """Build a test client with the Netflix facade and upload limit overridden.

    :param service: Synthetic Netflix facade.
    :param max_bytes: Synthetic maximum accepted upload size.
    :return: Offline HTTPX-compatible test client.
    """
    app = create_app()
    app.dependency_overrides[get_netflix_import_service] = lambda: service
    app.dependency_overrides[get_netflix_upload_limit] = lambda: max_bytes
    return cast("Client", TestClient(app))


def _csrf_token(client: Client) -> str:
    """Return the CSRF token rendered by the import page.

    :param client: Test client carrying a session cookie.
    :return: Hidden form token value.
    """
    response = client.get("/imports/netflix")
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def _post(  # noqa: PLR0913 - mirrors the multipart fields varied by each scenario.
    client: Client,
    *,
    token: str | None,
    label: str = _SYNTHETIC_LABEL,
    filename: str = _SYNTHETIC_FILENAME,
    content: bytes = _SYNTHETIC_CSV,
    content_type: str = "text/csv",
    origin: str | None = None,
) -> Response:
    """Submit one synthetic multipart import request.

    :param client: Test client carrying a session cookie.
    :param token: CSRF token to submit, or ``None`` to omit it.
    :param label: Profile label to submit.
    :param filename: Upload filename.
    :param content: Upload bytes.
    :param content_type: Upload content type.
    :param origin: Optional ``Origin`` header value.
    :return: HTTP response.
    """
    data = {"profile_label": label}
    if token is not None:
        data["csrf_token"] = token
    headers = {"Origin": origin} if origin is not None else None
    return client.post(
        "/imports/netflix",
        data=data,
        files={"upload": (filename, content, content_type)},
        headers=headers,
    )


def test_get_renders_accessible_form_and_session_csrf_token() -> None:
    """Render the import form with navigation, multipart encoding, and a CSRF token."""
    client = _client(FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())))

    response = client.get("/imports/netflix")

    assert response.status_code == HTTPStatus.OK
    assert 'href="http://testserver/imports/netflix">Netflix import</a>' in response.text
    assert 'method="post"' in response.text
    assert 'enctype="multipart/form-data"' in response.text
    assert 'name="csrf_token"' in response.text
    assert 'type="file"' in response.text
    assert "required" in response.text
    cookie = response.headers["set-cookie"]
    assert "httponly" in cookie.lower()
    assert "samesite=lax" in cookie.lower()


def test_get_page_does_not_render_session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render the form without exposing the configured session secret."""
    monkeypatch.setenv(_SESSION_SECRET_ENV, _SYNTHETIC_SECRET)
    client = _client(FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts())))

    response = client.get("/imports/netflix")

    assert response.status_code == HTTPStatus.OK
    assert _SYNTHETIC_SECRET not in response.text


def test_valid_upload_calls_facade_once_with_transient_path() -> None:
    """Import one synthetic CSV through a temporary path that is removed afterwards."""
    service = FakeNetflixService(
        _report(
            WorkflowStatus.SUCCESS,
            WorkflowCounts(succeeded=3, skipped=1, unresolved=2, ambiguous=4, invalid=5),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert len(service.calls) == 1
    path, label = service.calls[0]
    assert label == _SYNTHETIC_LABEL
    assert service.path_existed == [True]
    assert not path.exists()
    for count in (3, 1, 2, 4, 5):
        assert f"<dd>{count}</dd>" in response.text
    assert _SYNTHETIC_FILENAME not in response.text
    assert _SYNTHETIC_LABEL not in response.text


@pytest.mark.parametrize(
    ("label", "filename", "content", "content_type"),
    [
        (_SYNTHETIC_LABEL, _SYNTHETIC_FILENAME, b"", "text/csv"),
        ("   ", _SYNTHETIC_FILENAME, _SYNTHETIC_CSV, "text/csv"),
        (_SYNTHETIC_LABEL, "synthetic-viewing.txt", _SYNTHETIC_CSV, "text/plain"),
        (_SYNTHETIC_LABEL, _SYNTHETIC_FILENAME, b"Name,When\nSynthetic,9/14/26\n", "text/csv"),
        (_SYNTHETIC_LABEL, _SYNTHETIC_FILENAME, _SYNTHETIC_CSV, "application/pdf"),
    ],
)
def test_invalid_uploads_are_rejected_before_import(
    label: str,
    filename: str,
    content: bytes,
    content_type: str,
) -> None:
    """Reject empty, blank-label, unsupported, and malformed uploads without importing."""
    service = FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, label=label, filename=filename, content=content, content_type=content_type)

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == []
    assert "Request rejected" in response.text


def test_missing_upload_is_rejected_before_import() -> None:
    """Reject a submission without a file before the facade is called."""
    service = FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service)
    token = _csrf_token(client)

    response = client.post("/imports/netflix", data={"csrf_token": token, "profile_label": _SYNTHETIC_LABEL})

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == []


def test_oversized_upload_is_rejected_before_import() -> None:
    """Reject an upload whose streamed bytes exceed the configured route limit."""
    service = FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service, max_bytes=16)
    token = _csrf_token(client)

    response = _post(client, token=token, content=b"Title,Date\n" + b"x" * 64 + b"\n")

    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == []


def test_oversized_upload_is_rejected_at_the_receiving_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject an oversized body before multipart parsing can spool it."""
    monkeypatch.setenv("MEDIA_RECOMMENDER_NETFLIX_UPLOAD_MAX_BYTES", "16")
    service = FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    app = create_app()
    app.dependency_overrides[get_netflix_import_service] = lambda: service
    client = cast("Client", TestClient(app))
    token = _csrf_token(client)

    response = _post(client, token=token, content=b"Title,Date\n" + b"x" * 64 + b"\n")

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    assert service.calls == []


def test_body_limit_middleware_counts_streamed_bytes_without_content_length() -> None:
    """Reject a chunked body that exceeds the limit even without Content-Length."""
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, path="/upload", max_bytes=16)

    @app.post("/upload")
    async def upload(request: Request) -> dict[str, int]:
        """Return the received body length."""
        body = await request.body()
        return {"length": len(body)}

    response = cast("Client", TestClient(app)).post("/upload", content=iter([b"x" * 32]))

    assert response.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


def test_repeat_import_reports_already_imported() -> None:
    """Render repeat-import semantics for an all-skipped report."""
    service = FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(skipped=2)))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "already imported" in response.text.lower()
    assert "<dd>2</dd>" in response.text


def test_partial_result_does_not_claim_success_or_render_items() -> None:
    """Render aggregate partial counts without exposing item details."""
    item = WorkflowItem(2, WorkflowItemStatus.UNRESOLVED, _SYNTHETIC_REASON, (MediaId.new(),))
    service = FakeNetflixService(
        _report(
            WorkflowStatus.PARTIAL,
            WorkflowCounts(succeeded=1, unresolved=2, ambiguous=3, invalid=4, failed=5),
            (item,),
        )
    )
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "not silently imported" in response.text
    for count in (1, 2, 3, 4, 5):
        assert f"<dd>{count}</dd>" in response.text
    assert _SYNTHETIC_REASON not in response.text
    assert str(item.candidate_ids[0]) not in response.text


def test_failed_report_renders_generic_retryable_failure_and_failed_count() -> None:
    """Render a generic failure and the aggregate failed count for a failed report."""
    service = FakeNetflixService(_report(WorkflowStatus.FAILED, WorkflowCounts(failed=1)))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token)

    assert response.status_code == HTTPStatus.OK
    assert "could not be completed" in response.text
    assert "<dt>Failed</dt>" in response.text
    assert "<dd>1</dd>" in response.text
    assert "local_source_failure" not in response.text


def test_unexpected_exception_is_translated_without_propagation_or_leakage(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Translate an unexpected facade exception into a sanitized response."""
    monkeypatch.setenv(_SESSION_SECRET_ENV, _SYNTHETIC_SECRET)
    service = FakeNetflixService(
        error=RuntimeError(f"{_SYNTHETIC_FILENAME} {_SYNTHETIC_LABEL} {_SYNTHETIC_SECRET} {_SYNTHETIC_REASON}")
    )
    client = _client(service)
    token = _csrf_token(client)

    with caplog.at_level(logging.DEBUG):
        response = _post(client, token=token)

    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert "could not be completed" in response.text
    for value in (_SYNTHETIC_FILENAME, _SYNTHETIC_LABEL, _SYNTHETIC_SECRET, _SYNTHETIC_REASON):
        assert value not in response.text
        assert value not in caplog.text


def test_missing_or_incorrect_csrf_token_is_rejected_before_import() -> None:
    """Reject submissions without a valid session CSRF token."""
    service = FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service)
    _csrf_token(client)

    missing = _post(client, token=None)
    incorrect = _post(client, token="synthetic-incorrect-token")  # noqa: S106 - synthetic test value, not a secret.

    assert missing.status_code == HTTPStatus.FORBIDDEN
    assert incorrect.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == []


def test_cross_origin_submission_is_rejected_before_import() -> None:
    """Reject a valid token submitted from a different origin."""
    service = FakeNetflixService(_report(WorkflowStatus.SUCCESS, WorkflowCounts(succeeded=1)))
    client = _client(service)
    token = _csrf_token(client)

    response = _post(client, token=token, origin="https://synthetic-evil.invalid")

    assert response.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == []
