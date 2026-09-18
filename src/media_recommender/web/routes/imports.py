"""Server-rendered Netflix viewing-history import page.

This module owns HTTP, form, temporary-file, and CSRF concerns only. It calls
the narrow Phase 2 Netflix viewing facade and never touches persistence,
parsers, or the full synchronization orchestrator entry point.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.

from media_recommender.application import (  # noqa: TC001 - FastAPI resolves this annotation at runtime.
    Phase2Orchestrator,
)
from media_recommender.web import csrf
from media_recommender.web.routes.pages import get_templates

if TYPE_CHECKING:
    from collections.abc import Iterator

router = APIRouter()

_CSV_SUFFIX = ".csv"
_REQUIRED_HEADER_COLUMNS = ("title", "date")
_CHUNK_SIZE = 64 * 1024
_TOO_LARGE_MESSAGE = "The uploaded file is too large."
_EMPTY_UPLOAD_MESSAGE = "The uploaded file is empty."
_UNEXPECTED_FAILURE_MESSAGE = "The import failed. Please try again."


class UploadRejected(Exception):  # noqa: N818 - rejection is a handled control-flow outcome, not an error.
    """Raised when an upload is rejected before the application facade runs."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        """Store a privacy-safe rejection message.

        :param message: Safe user-facing description without upload specifics.
        :param status: HTTP status for the rejection response.
        """
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class NetflixImportOutcome:
    """Privacy-safe aggregate view model for one import submission."""

    status: str
    imported: int
    already_imported: int
    unresolved: int
    ambiguous: int
    invalid: int

    @property
    def is_failed(self) -> bool:
        """Return whether the operation failed at the local source level.

        :return: Whether the outcome represents an operational failure.
        """
        return self.status == "failed"

    @property
    def is_partial(self) -> bool:
        """Return whether some rows were not imported.

        :return: Whether unresolved, ambiguous, or invalid rows exist.
        """
        return (self.unresolved + self.ambiguous + self.invalid) > 0

    @property
    def is_repeat(self) -> bool:
        """Return whether every row was already imported previously.

        :return: Whether the import was a fully idempotent repeat.
        """
        return self.imported == 0 and self.already_imported > 0


def get_session_secret(request: Request) -> bytes:
    """Return the runtime session-signing secret configured at startup.

    :param request: Incoming request carrying the current application.
    :return: Session-signing secret bytes.
    :raises RuntimeError: If no session secret is configured.
    """
    try:
        return cast("bytes", request.app.state.web_session_secret)
    except AttributeError as error:
        msg = "Web session secret is not configured"
        raise RuntimeError(msg) from error


def get_netflix_service(request: Request) -> Phase2Orchestrator:
    """Return the Netflix-capable application facade configured by the composition root.

    :param request: Incoming request carrying the current application instance.
    :return: Phase 2 orchestration facade exposing the Netflix viewing operation.
    :raises RuntimeError: If no Phase 2 service is configured.
    """
    try:
        return cast("Phase2Orchestrator", request.app.state.netflix_import_service)
    except AttributeError as error:
        msg = "Netflix import service is not configured"
        raise RuntimeError(msg) from error


def _same_origin(request: Request) -> bool:
    """Return whether an explicit browser ``Origin`` matches this server.

    Requests without an ``Origin`` header are accepted because the signed
    session token remains the primary CSRF defense.

    :param request: Incoming request.
    :return: Whether the request passes the same-origin check.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    host = request.headers.get("host", "")
    return origin in {f"https://{host}", f"http://{host}"}


def _validate_csrf(request: Request, submitted_token: str | None) -> None:
    """Reject requests without a valid session-bound CSRF token.

    :param request: Incoming request.
    :param submitted_token: Token from the hidden form field.
    :raises UploadRejected: If the token or origin is missing or incorrect.
    """
    if not _same_origin(request):
        msg = "This request was rejected for security reasons."
        raise UploadRejected(msg, HTTPStatus.FORBIDDEN)
    secret = get_session_secret(request)
    session_token_value = csrf.session_token(request.cookies.get(csrf.COOKIE_NAME), secret)
    if not csrf.tokens_match(submitted_token, session_token_value):
        msg = "This form submission was rejected for security reasons."
        raise UploadRejected(msg, HTTPStatus.FORBIDDEN)


def _header_line(path: Path) -> str:
    """Return the first text line of the staged file.

    :param path: Staged temporary file path.
    :return: Decoded first line of the staged content.
    """
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.readline()


def _upload_chunks(upload: UploadFile) -> Iterator[bytes]:
    """Yield raw upload chunks from the multipart stream.

    :param upload: Multipart upload stream.
    :yields: Raw content chunks from the upload stream.
    """
    while chunk := upload.file.read(_CHUNK_SIZE):
        yield chunk


def _stage_bounded_upload(upload: UploadFile, max_bytes: int) -> Path:
    """Stage the upload into a private temporary file with a hard byte limit.

    The file exists only for the duration of the request and is removed by the
    caller. The limit is enforced on the actual streamed bytes, not headers.

    :param upload: Multipart upload stream.
    :param max_bytes: Maximum accepted byte count.
    :return: Path to the staged private temporary file.
    :raises UploadRejected: For empty or oversized uploads.
    """
    handle = tempfile.NamedTemporaryFile(prefix="mr-netflix-", suffix=".csv", delete=False)  # noqa: SIM115
    path = Path(handle.name)
    try:
        with handle:
            total = 0
            for chunk in _upload_chunks(upload):
                total += len(chunk)
                if total > max_bytes:
                    raise UploadRejected(_TOO_LARGE_MESSAGE)  # noqa: TRY301 - staged cleanup happens in this scope.
                handle.write(chunk)
        if total == 0:
            raise UploadRejected(_EMPTY_UPLOAD_MESSAGE)  # noqa: TRY301 - staged cleanup happens in this scope.
    except UploadRejected:
        path.unlink(missing_ok=True)
        raise
    return path


def _reject_unsupported(upload: UploadFile, staged: Path) -> None:
    """Reject uploads that do not look like a supported viewing-activity CSV.

    The filename suffix is treated only as an early UX signal; the content
    header check is the truthful validation.

    :param upload: Original multipart upload.
    :param staged: Staged temporary file path.
    :raises UploadRejected: If the upload signals an unsupported format.
    """
    if upload.filename is not None and upload.filename.strip() and not upload.filename.lower().endswith(_CSV_SUFFIX):
        msg = "The uploaded file is not a supported Netflix viewing activity CSV."
        raise UploadRejected(msg)
    header = _header_line(staged).lower()
    if not all(column in header for column in _REQUIRED_HEADER_COLUMNS):
        msg = "The uploaded file is not a supported Netflix viewing activity CSV."
        raise UploadRejected(msg)


def _outcome_from_report(report_status: str, counts: object) -> NetflixImportOutcome:
    """Build the safe aggregate view model from a workflow report.

    :param report_status: Workflow report status value.
    :param counts: Workflow report counts.
    :return: Aggregate-only view model without item details.
    """
    return NetflixImportOutcome(
        status=report_status,
        imported=getattr(counts, "succeeded", 0),
        already_imported=getattr(counts, "skipped", 0),
        unresolved=getattr(counts, "unresolved", 0),
        ambiguous=getattr(counts, "ambiguous", 0),
        invalid=getattr(counts, "invalid", 0),
    )


def _upload_form(upload: UploadFile | None) -> UploadFile:
    """Require that the multipart form carried a file part with a filename.

    :param upload: File part from the submitted form, if any.
    :return: The validated file part.
    :raises UploadRejected: If the file part is missing or carries no filename.
    """
    if upload is None or not (upload.filename or "").strip():
        msg = "Select a Netflix viewing activity CSV file to import."
        raise UploadRejected(msg)
    return upload


def _reject_blank_label(profile_label: str | None) -> str:
    """Require a non-blank, user-selected Netflix profile label.

    :param profile_label: Submitted profile label.
    :return: The stripped, non-blank profile label.
    :raises UploadRejected: If the label is missing or blank.
    """
    if profile_label is None or not profile_label.strip():
        msg = "Enter the Netflix profile label the export belongs to."
        raise UploadRejected(msg)
    return profile_label.strip()


def _error_page(request: Request, templates: Jinja2Templates, message: str, status: HTTPStatus) -> HTMLResponse:
    """Render a privacy-safe rejection message on the import form.

    :param request: Incoming request.
    :param templates: Shared Jinja2 template renderer.
    :param message: Safe user-facing rejection description.
    :param status: HTTP status for the response.
    :return: Rejection page without upload specifics.
    """
    return templates.TemplateResponse(
        request,
        "netflix_import.html",
        {"error": message},
        status_code=status.value,
    )


@router.get("/imports/netflix", response_class=HTMLResponse)
async def netflix_import_form(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the accessible multipart Netflix import form.

    A fresh signed CSRF session is issued for every anonymous form visit.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :return: Import form page with a hidden CSRF token.
    """
    cookie_value = csrf.issue_signed_token(get_session_secret(request))
    token = csrf.session_token(cookie_value, get_session_secret(request))
    response = templates.TemplateResponse(request, "netflix_import.html", {"csrf_token": token})
    response.set_cookie(
        csrf.COOKIE_NAME,
        cookie_value,
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/imports/netflix", response_class=HTMLResponse)
async def netflix_import_submit(  # noqa: PLR0913, PLR0917 - FastAPI dependencies require explicit route parameters.
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    service: Annotated[Phase2Orchestrator, Depends(get_netflix_service)],
    profile_label: Annotated[str | None, Form()] = None,
    csrf_token: Annotated[str | None, Form(alias="csrf_token")] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    """Validate and process one Netflix viewing-history import submission.

    All validation happens before the application facade. Every rejection path
    performs zero facade calls and leaves no temporary artifact. The staged
    file is always removed after the attempt and is never logged or rendered.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param service: Application facade exposing the Netflix viewing operation.
    :param profile_label: Submitted Netflix profile label.
    :param csrf_token: Token from the hidden form field.
    :param file: Multipart file part carrying the CSV upload.
    :return: Aggregate result page or a privacy-safe rejection page.
    """
    try:
        _validate_csrf(request, csrf_token)
        validated_upload = _upload_form(file)
        validated_label = _reject_blank_label(profile_label)
    except UploadRejected as rejection:
        return _error_page(request, templates, rejection.message, rejection.status)
    try:
        staged = _stage_bounded_upload(validated_upload, int(request.app.state.netflix_max_upload_bytes))
    except UploadRejected as rejection:
        return _error_page(request, templates, rejection.message, rejection.status)
    try:
        _reject_unsupported(validated_upload, staged)
        report = await service.import_netflix_viewing(staged, external_profile_id=validated_label)
    except UploadRejected as rejection:
        return _error_page(request, templates, rejection.message, rejection.status)
    except Exception:  # noqa: BLE001 - unexpected failures must not leak internals into the response.
        staged.unlink(missing_ok=True)
        return _error_page(request, templates, _UNEXPECTED_FAILURE_MESSAGE, HTTPStatus.INTERNAL_SERVER_ERROR)
    finally:
        staged.unlink(missing_ok=True)
    return templates.TemplateResponse(
        request,
        "netflix_import.html",
        {"outcome": _outcome_from_report(str(report.status.value), report.counts)},
    )
