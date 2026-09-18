"""Server-rendered Netflix viewing-activity import page."""

from __future__ import annotations

import csv
import os
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.

from media_recommender.application import Phase2Orchestrator, WorkflowReport, WorkflowStatus
from media_recommender.config import Settings
from media_recommender.web.csrf import CsrfValidationError, get_csrf_token, require_csrf_token
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.pages import get_templates

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ExceptionHandler

router = APIRouter()

NETFLIX_IMPORT_PATH = "/imports/netflix"

_UPLOAD_CHUNK_BYTES = 64 * 1024
_HEADER_READ_BYTES = 4096
_REQUIRED_VIEWING_COLUMNS = frozenset({"Title", "Date"})
_ALLOWED_CONTENT_TYPES = frozenset(
    {
        "",
        "application/csv",
        "application/octet-stream",
        "application/vnd.ms-excel",
        "text/csv",
        "text/plain",
    }
)


class NetflixImportOutcome(StrEnum):
    """Privacy-safe outcome rendered for one Netflix import request."""

    INVALID_REQUEST = "invalid_request"
    SUCCESS = "success"
    REPEAT = "repeat"
    PARTIAL = "partial"
    FAILED = "failed"
    UNEXPECTED_FAILURE = "unexpected_failure"


@dataclass(frozen=True, slots=True)
class NetflixImportView:
    """Aggregate-only import result safe for rendering."""

    outcome: NetflixImportOutcome
    imported: int = 0
    skipped: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    invalid: int = 0
    failed: int = 0


class _UploadRejectedError(Exception):
    """Raised when an upload cannot be staged safely."""


def get_netflix_import_service(request: Request) -> Phase2Orchestrator:
    """Return the Netflix import facade configured by the composition root.

    :param request: Incoming request carrying the current application instance.
    :return: Phase 2 application facade.
    :raises RuntimeError: If no Netflix import service is configured.
    """
    try:
        return cast("Phase2Orchestrator", request.app.state.netflix_import_service)
    except AttributeError as error:
        msg = "Netflix import service is not configured"
        raise RuntimeError(msg) from error


def get_netflix_upload_limit() -> int:
    """Return the configured maximum Netflix upload size in bytes.

    :return: Positive maximum accepted upload size.
    """
    return Settings().netflix_upload_max_bytes


@router.get(NETFLIX_IMPORT_PATH, response_class=HTMLResponse)
async def netflix_import_page(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the Netflix viewing-activity upload form.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :return: Shared-layout import page with a session-bound CSRF token.
    """
    return _render(templates, request, None)


@router.post(NETFLIX_IMPORT_PATH, response_class=HTMLResponse)
async def submit_netflix_import(  # noqa: PLR0913, PLR0917 - FastAPI dependency and multipart form parameters.
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    service: Annotated[Phase2Orchestrator, Depends(get_netflix_import_service)],
    max_bytes: Annotated[int, Depends(get_netflix_upload_limit)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
    profile_label: Annotated[str, Form()] = "",
    upload: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    """Validate one Netflix upload, import it, and render aggregate results.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param service: Injected Netflix-only application facade.
    :param max_bytes: Configured maximum accepted upload size.
    :param _csrf: CSRF validation dependency.
    :param profile_label: Caller-selected stable Netflix profile label.
    :param upload: Uploaded Viewing Activity CSV.
    :return: Shared-layout import page with a privacy-safe result.
    """
    label = profile_label.strip()
    if not label or upload is None or not _is_supported_upload(upload):
        return _render(templates, request, _invalid_request_view(), status_code=HTTPStatus.BAD_REQUEST)
    try:
        staged = await _stage_upload(upload, max_bytes)
    except _UploadRejectedError:
        return _render(templates, request, _invalid_request_view(), status_code=HTTPStatus.BAD_REQUEST)
    try:
        if not _has_viewing_header(staged):
            return _render(templates, request, _invalid_request_view(), status_code=HTTPStatus.BAD_REQUEST)
        report = await service.import_netflix_viewing(staged, external_profile_id=label)
    except Exception:  # noqa: BLE001 - translate unexpected application failures into a sanitized result.
        return _render(templates, request, _unexpected_failure_view(), status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
    finally:
        staged.unlink(missing_ok=True)
    return _render(templates, request, _view_from_report(report))


async def csrf_rejection_handler(request: Request, _: Exception) -> HTMLResponse:
    """Render a safe rejection page for an invalid CSRF or origin check.

    :param request: Incoming rejected request.
    :return: Shared-layout rejection page.
    """
    templates: Jinja2Templates = request.app.state.templates
    return _render(templates, request, _invalid_request_view(), status_code=HTTPStatus.FORBIDDEN)


def register_import_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced by the Netflix import page.

    :param app: FastAPI application receiving the import-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {CsrfValidationError: csrf_rejection_handler}
    register_exception_handlers(app, handlers)


def _is_supported_upload(upload: UploadFile) -> bool:
    """Return whether an upload carries a supported CSV filename and content type.

    The filename suffix is only a user-experience signal; the staged header is
    validated separately before the importer runs.

    :param upload: Uploaded file metadata.
    :return: Whether the upload signals a supported CSV file.
    """
    filename = (upload.filename or "").strip()
    if not filename.lower().endswith(".csv"):
        return False
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    return content_type in _ALLOWED_CONTENT_TYPES


async def _stage_upload(upload: UploadFile, max_bytes: int) -> Path:
    """Write a bounded upload to a private request-local temporary file.

    :param upload: Uploaded file stream.
    :param max_bytes: Maximum accepted byte count.
    :return: Path to the staged temporary file.
    :raises _UploadRejectedError: If the upload is empty or exceeds the limit.
    """
    descriptor, name = tempfile.mkstemp(prefix="media-recommender-netflix-", suffix=".csv")
    path = Path(name)
    total = 0
    keep = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise _UploadRejectedError
                handle.write(chunk)
        if total == 0:
            raise _UploadRejectedError
        keep = True
        return path
    finally:
        if not keep:
            path.unlink(missing_ok=True)  # noqa: ASYNC240 - request-local cleanup of a small temporary file.


def _has_viewing_header(path: Path) -> bool:
    """Return whether a staged file starts with the supported viewing-activity header.

    :param path: Staged private CSV path.
    :return: Whether the first row contains the required columns.
    """
    with path.open("rb") as handle:
        head = handle.read(_HEADER_READ_BYTES)
    lines = _decode_head(head).splitlines()
    if not lines:
        return False
    try:
        columns = next(csv.reader([lines[0]]))
    except csv.Error:
        return False
    return _REQUIRED_VIEWING_COLUMNS.issubset({column.strip() for column in columns})


def _decode_head(payload: bytes) -> str:
    """Decode the leading bytes of a supported Netflix export.

    :param payload: Leading raw file bytes.
    :return: Decoded text with replacement for undecodable bytes.
    """
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        return payload.decode("utf-16", errors="replace")
    try:
        return payload.decode("utf-8-sig")
    except UnicodeDecodeError:
        return payload.decode("cp1252", errors="replace")


def _view_from_report(report: WorkflowReport) -> NetflixImportView:
    """Map a normalized workflow report to a privacy-safe page result.

    :param report: Netflix viewing-activity workflow report.
    :return: Aggregate-only import result.
    """
    counts = report.counts
    if report.status is WorkflowStatus.FAILED:
        outcome = NetflixImportOutcome.FAILED
    elif report.status is WorkflowStatus.PARTIAL:
        outcome = NetflixImportOutcome.PARTIAL
    elif counts.succeeded == 0 and counts.skipped > 0:
        outcome = NetflixImportOutcome.REPEAT
    else:
        outcome = NetflixImportOutcome.SUCCESS
    return NetflixImportView(
        outcome,
        imported=counts.succeeded,
        skipped=counts.skipped,
        unresolved=counts.unresolved,
        ambiguous=counts.ambiguous,
        invalid=counts.invalid,
        failed=counts.failed,
    )


def _invalid_request_view() -> NetflixImportView:
    """Return the safe result for a rejected request.

    :return: Invalid-request result without import counts.
    """
    return NetflixImportView(NetflixImportOutcome.INVALID_REQUEST)


def _unexpected_failure_view() -> NetflixImportView:
    """Return the safe result for an unexpected application failure.

    :return: Failure result without exception data or report counts.
    """
    return NetflixImportView(NetflixImportOutcome.UNEXPECTED_FAILURE)


def _render(
    templates: Jinja2Templates,
    request: Request,
    result: NetflixImportView | None,
    *,
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the import page with a fresh CSRF token and safe result.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :param result: Optional aggregate-only import result.
    :param status_code: HTTP status for the rendered response.
    :return: Shared-layout import page.
    """
    return templates.TemplateResponse(
        request,
        "netflix_import.html",
        {"csrf_token": get_csrf_token(request), "result": result},
        status_code=status_code,
    )
