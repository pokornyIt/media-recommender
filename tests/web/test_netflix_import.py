"""Offline tests for the Netflix viewing-history import page."""

from __future__ import annotations

import logging
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import FastAPI  # noqa: TC002 - used in runtime-resolved test annotations.
from fastapi.testclient import TestClient

from media_recommender.application.orchestration import (
    WorkflowCounts,
    WorkflowKind,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.web import create_app

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from httpx import Client, Response

_SYNTHETIC_PROFILE = "Synthetic Profile"
_SYNTHETIC_FILENAME = "synthetic-viewing-history.csv"
_VALID_TOKEN = "valid-token"  # noqa: S105 - synthetic test value.
_SYNTHETIC_CSV = b"Title,Date\nSynthetic Title,9/14/26\n"
_SYNTHETIC_SECRET = "synthetic-session-signing-secret"  # noqa: S105 - synthetic test value, not a credential.
_TOKEN_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


class FakeNetflixService:
    """Record facade calls and return a canned normalized report."""

    def __init__(self, report: WorkflowReport, error: Exception | None = None) -> None:
        """Initialize with a canned report or exception.

        :param report: Report returned on each call.
        :param error: Optional exception raised on each call.
        """
        self.report = report
        self.error = error
        self.calls: list[tuple[Path, str, bool]] = []

    async def import_netflix_viewing(self, path: Path, *, external_profile_id: str) -> WorkflowReport:
        """Record one synthetic facade call with temporary-file visibility.

        :param path: Staged temporary CSV path provided by the Web route.
        :param external_profile_id: Submitted profile label.
        :return: Canned workflow report.
        :raises RuntimeError: If configured to fail.
        """
        self.calls.append((path, external_profile_id, path.exists()))  # noqa: ASYNC240 - synthetic fake.
        if self.error is not None:
            raise self.error
        return self.report


def _report(  # noqa: PLR0913 - mirrors the aggregate counts varied by the scenarios.
    *,
    imported: int = 0,
    skipped: int = 0,
    unresolved: int = 0,
    ambiguous: int = 0,
    invalid: int = 0,
    status: WorkflowStatus | None = None,
) -> WorkflowReport:
    """Build a synthetic Netflix viewing workflow report.

    :param imported: Synthetic imported count.
    :param skipped: Synthetic skipped count.
    :param unresolved: Synthetic unresolved count.
    :param ambiguous: Synthetic ambiguous count.
    :param invalid: Synthetic invalid count.
    :param status: Optional explicit status override.
    :return: Synthetic workflow report without item details.
    """
    if status is None:
        problems = unresolved + ambiguous + invalid
        status = WorkflowStatus.PARTIAL if problems else WorkflowStatus.SUCCESS
    return WorkflowReport(
        WorkflowKind.NETFLIX_VIEWING,
        status,
        WorkflowCounts(
            succeeded=imported,
            skipped=skipped,
            unresolved=unresolved,
            ambiguous=ambiguous,
            invalid=invalid,
        ),
        (),
    )


def _app(service: FakeNetflixService, *, max_upload_bytes: int | None = None) -> FastAPI:
    """Build a test application with synthetic state.

    :param service: Fake Netflix facade to expose.
    :param max_upload_bytes: Optional reduced upload limit.
    :return: Configured FastAPI test application.
    """
    app = create_app()
    app.state.web_session_secret = _SYNTHETIC_SECRET.encode()
    app.state.netflix_import_service = service
    if max_upload_bytes is not None:
        app.state.netflix_max_upload_bytes = max_upload_bytes
    return app


def _client(app: FastAPI, *, raise_server_exceptions: bool = True) -> Client:
    """Return a statically typed synchronous test client.

    :param app: Application to exercise.
    :param raise_server_exceptions: Whether endpoint exceptions escape the client.
    :return: HTTPX-compatible test client.
    """
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _form_token(client: Client) -> str:
    """Fetch the form page and extract the hidden CSRF token.

    :param client: Test client holding the issued session cookie.
    :return: Hidden CSRF token value.
    """
    response = client.get("/imports/netflix")
    match = _TOKEN_PATTERN.search(response.text)
    assert match is not None
    return match.group(1)


def _post(  # noqa: PLR0913 - mirrors the multipart form fields varied by the scenarios.
    client: Client,
    *,
    token: str | None,
    profile: str | None = _SYNTHETIC_PROFILE,
    filename: str = _SYNTHETIC_FILENAME,
    content: bytes = _SYNTHETIC_CSV,
    content_type: str = "text/csv",
    headers: dict[str, str] | None = None,
) -> Response:
    """Submit the import form with synthetic multipart data.

    :param client: Test client.
    :param token: CSRF token to submit, if any.
    :param profile: Profile label to submit, if any.
    :param filename: Synthetic upload filename.
    :param content: Synthetic upload bytes.
    :param content_type: Synthetic upload content type.
    :param headers: Optional extra request headers.
    :return: Response object.
    """
    data: dict[str, str] = {}
    if token is not None:
        data["csrf_token"] = token
    if profile is not None:
        data["profile_label"] = profile
    files = [("file", (filename, content, content_type))]
    return client.post("/imports/netflix", data=data, files=files, headers=headers)


def test_get_renders_accessible_form_with_csrf_and_no_secret() -> None:
    """Render navigation, multipart form, hidden token, and no secrets."""
    app = _app(FakeNetflixService(_report()))
    with _client(app) as client:
        response = client.get("/imports/netflix")
    assert response.status_code == HTTPStatus.OK
    assert 'method="post"' in response.text
    assert "multipart/form-data" in response.text
    assert 'name="csrf_token"' in response.text
    assert _TOKEN_PATTERN.search(response.text) is not None
    assert _SYNTHETIC_SECRET not in response.text
    assert "Netflix import" in response.text


def test_valid_upload_calls_facade_once_with_deleted_temporary_file() -> None:
    """Call the facade exactly once and leave no temporary artifact."""
    service = FakeNetflixService(_report(imported=2))
    app = _app(service)
    with _client(app) as client:
        token = _form_token(client)
        response = _post(client, token=token)
    assert response.status_code == HTTPStatus.OK
    assert len(service.calls) == 1
    path, profile, existed_during_call = service.calls[0]
    assert profile == _SYNTHETIC_PROFILE
    assert existed_during_call
    assert not path.exists()
    assert "synthetic-viewing-history" not in response.text
    assert path.name not in response.text
    assert "Imported: 2" in response.text


def test_all_skipped_result_reports_already_imported() -> None:
    """Report idempotent repeat imports without claiming new imports."""
    service = FakeNetflixService(_report(skipped=3))
    app = _app(service)
    with _client(app) as client:
        token = _form_token(client)
        response = _post(client, token=token)
    assert response.status_code == HTTPStatus.OK
    assert "already imported" in response.text.lower()
    assert "Nothing new was added" in response.text


def test_partial_result_reports_non_imported_rows_without_details() -> None:
    """Report partial outcomes without rendering item details."""
    service = FakeNetflixService(_report(imported=1, unresolved=2, ambiguous=1, invalid=1))
    app = _app(service)
    with _client(app) as client:
        token = _form_token(client)
        response = _post(client, token=token)
    assert response.status_code == HTTPStatus.OK
    assert "not silently imported" in response.text
    assert "unresolved 2" in response.text
    assert "reason" not in response.text.lower()
    assert "candidate" not in response.text.lower()


@pytest.mark.parametrize(
    ("token", "profile", "filename", "content", "expected_status"),
    [
        (_VALID_TOKEN, None, _SYNTHETIC_FILENAME, _SYNTHETIC_CSV, HTTPStatus.BAD_REQUEST),
        (_VALID_TOKEN, "   ", _SYNTHETIC_FILENAME, _SYNTHETIC_CSV, HTTPStatus.BAD_REQUEST),
        (_VALID_TOKEN, _SYNTHETIC_PROFILE, "history.txt", _SYNTHETIC_CSV, HTTPStatus.BAD_REQUEST),
        (_VALID_TOKEN, _SYNTHETIC_PROFILE, _SYNTHETIC_FILENAME, b"Not a CSV header\nrow\n", HTTPStatus.BAD_REQUEST),
        (None, _SYNTHETIC_PROFILE, _SYNTHETIC_FILENAME, _SYNTHETIC_CSV, HTTPStatus.FORBIDDEN),
        ("wrong-token", _SYNTHETIC_PROFILE, _SYNTHETIC_FILENAME, _SYNTHETIC_CSV, HTTPStatus.FORBIDDEN),
    ],
)
def test_rejections_perform_zero_facade_calls(
    token: str | None,
    profile: str | None,
    filename: str,
    content: bytes,
    expected_status: HTTPStatus,
) -> None:
    """Reject invalid submissions before any importer execution."""
    service = FakeNetflixService(_report(imported=1))
    app = _app(service)
    with _client(app) as client:
        resolved_token = _form_token(client) if token == _VALID_TOKEN else token
        response = _post(client, token=resolved_token, profile=profile, filename=filename, content=content)
    assert response.status_code == expected_status
    assert service.calls == []


def test_missing_file_part_is_rejected() -> None:
    """Reject submissions without any file part."""
    service = FakeNetflixService(_report())
    app = _app(service)
    with _client(app) as client:
        token = _form_token(client)
        response = client.post(
            "/imports/netflix",
            data={"csrf_token": token, "profile_label": _SYNTHETIC_PROFILE},
        )
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == []


def test_oversized_upload_is_rejected_on_streamed_bytes() -> None:
    """Enforce the byte limit on actual streamed content, not headers."""
    service = FakeNetflixService(_report())
    app = _app(service, max_upload_bytes=8)
    with _client(app) as client:
        token = _form_token(client)
        response = _post(client, token=token, content=b"Title,Date\n" + b"x" * 100)
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == []


def test_zero_byte_upload_is_rejected() -> None:
    """Reject zero-byte uploads before the facade runs."""
    service = FakeNetflixService(_report())
    app = _app(service)
    with _client(app) as client:
        token = _form_token(client)
        response = _post(client, token=token, content=b"")
    assert response.status_code == HTTPStatus.BAD_REQUEST
    assert service.calls == []


def test_cross_origin_submission_is_rejected() -> None:
    """Reject cross-origin POSTs before the facade runs."""
    service = FakeNetflixService(_report())
    app = _app(service)
    with _client(app) as client:
        token = _form_token(client)
        response = _post(client, token=token, headers={"Origin": "https://evil.example.invalid"})
    assert response.status_code == HTTPStatus.FORBIDDEN
    assert service.calls == []


def test_failed_report_and_exception_leak_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Keep filenames, labels, content, paths, and secrets out of failures."""
    caplog.set_level(logging.DEBUG)
    secrets_to_check = (
        _SYNTHETIC_FILENAME,
        _SYNTHETIC_PROFILE,
        "Synthetic Title",
        "synthetic-session-signing-secret",
    )
    failing = FakeNetflixService(_report(status=WorkflowStatus.FAILED))
    app = _app(failing)
    with _client(app) as client:
        token = _form_token(client)
        response = _post(client, token=token)
    assert response.status_code == HTTPStatus.OK
    assert "could not be completed" in response.text
    for secret_value in secrets_to_check:
        assert secret_value not in response.text

    raising = FakeNetflixService(_report(), error=RuntimeError("synthetic internal failure"))
    app = _app(raising)
    with _client(app, raise_server_exceptions=False) as client:
        token = _form_token(client)
        response = _post(client, token=token)
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    for secret_value in secrets_to_check:
        assert secret_value not in response.text
        assert secret_value not in caplog.text
    for call_path, _, _ in raising.calls:
        assert str(call_path) not in response.text
        assert not call_path.exists()


def test_no_temporary_files_remain_after_all_scenarios() -> None:
    """Ensure every handled path cleans up its staged temporary file."""
    scenarios: list[Callable[[Client], object]] = [
        lambda client: _post(client, token=_form_token(client), content=b""),
        lambda client: _post(client, token=_form_token(client), filename="history.txt"),
        lambda client: _post(client, token=_form_token(client), content=b"Wrong header\n"),
        lambda client: _post(client, token=None),
    ]
    for scenario in scenarios:
        service = FakeNetflixService(_report())
        app = _app(service)
        with _client(app) as client:
            response = cast("Response", scenario(client))
        assert response.status_code in {HTTPStatus.BAD_REQUEST, HTTPStatus.FORBIDDEN}
        assert service.calls == []
