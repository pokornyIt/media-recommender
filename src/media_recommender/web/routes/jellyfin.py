"""Server-rendered Jellyfin library synchronization page."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.

from media_recommender.application import (
    ConfigurationState,
    Phase2Orchestrator,
    ProviderKind,
    ProviderStatus,
    ProviderStatusReader,
    WorkflowFailureReason,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.application.provider_status import DefaultProviderStatusReader
from media_recommender.web.csrf import CsrfValidationError, get_csrf_token, require_csrf_token
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.pages import get_templates

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ExceptionHandler

router = APIRouter()

JELLYFIN_SYNC_PATH = "/synchronizations/jellyfin"


class JellyfinSyncOutcome(StrEnum):
    """Privacy-safe outcome rendered for one Jellyfin synchronization request."""

    NOT_CONFIGURED = "not_configured"
    CONFIGURATION_ERROR = "configuration_error"
    SUCCESS = "success"
    PARTIAL = "partial"
    AUTHENTICATION_FAILURE = "authentication_failure"
    TRANSIENT_FAILURE = "transient_failure"
    FAILED = "failed"
    UNEXPECTED_FAILURE = "unexpected_failure"


@dataclass(frozen=True, slots=True)
class JellyfinSyncView:
    """Aggregate-only synchronization result safe for rendering."""

    outcome: JellyfinSyncOutcome
    synchronized: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    invalid: int = 0
    failed: int = 0
    removed: int = 0


def get_jellyfin_sync_service(request: Request) -> Phase2Orchestrator:
    """Return the Jellyfin synchronization facade configured by the composition root.

    :param request: Incoming request carrying the current application instance.
    :return: Phase 2 application facade.
    :raises RuntimeError: If no Jellyfin synchronization service is configured.
    """
    try:
        return cast("Phase2Orchestrator", request.app.state.jellyfin_sync_service)
    except AttributeError as error:
        msg = "Jellyfin synchronization service is not configured"
        raise RuntimeError(msg) from error


def get_jellyfin_provider_status() -> ProviderStatus:
    """Return the safe Jellyfin configuration snapshot without contacting providers.

    :return: Safe provider status snapshot for Jellyfin.
    """
    reader: ProviderStatusReader = DefaultProviderStatusReader()
    return next(status for status in reader.read_provider_statuses() if status.provider is ProviderKind.JELLYFIN)


@router.get(JELLYFIN_SYNC_PATH, response_class=HTMLResponse)
async def jellyfin_sync_page(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    status: Annotated[ProviderStatus, Depends(get_jellyfin_provider_status)],
) -> HTMLResponse:
    """Render the Jellyfin synchronization form with the safe configuration state.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param status: Safe Jellyfin configuration snapshot.
    :return: Shared-layout synchronization page with a session-bound CSRF token.
    """
    return _render(templates, request, _view_from_configuration(status))


@router.post(JELLYFIN_SYNC_PATH, response_class=HTMLResponse)
async def submit_jellyfin_sync(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    status: Annotated[ProviderStatus, Depends(get_jellyfin_provider_status)],
    service: Annotated[Phase2Orchestrator, Depends(get_jellyfin_sync_service)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
) -> HTMLResponse:
    """Run one Jellyfin library synchronization and render aggregate results.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param status: Safe Jellyfin configuration snapshot.
    :param service: Injected Jellyfin-only application facade.
    :param _csrf: CSRF validation dependency.
    :return: Shared-layout synchronization page with a privacy-safe result.
    """
    if status.configuration is not ConfigurationState.CONFIGURED:
        return _render(
            templates,
            request,
            _view_from_configuration(status),
            status_code=HTTPStatus.BAD_REQUEST,
        )
    try:
        report = await service.synchronize_jellyfin_library()
    except Exception:  # noqa: BLE001 - translate unexpected application failures into a sanitized result.
        return _render(templates, request, _unexpected_failure_view(), status_code=HTTPStatus.INTERNAL_SERVER_ERROR)
    return _render(templates, request, _view_from_report(report))


async def csrf_rejection_handler(request: Request, _: Exception) -> HTMLResponse:
    """Render a safe rejection page for an invalid CSRF or origin check.

    :param request: Incoming rejected request.
    :return: Shared-layout rejection page.
    """
    templates: Jinja2Templates = request.app.state.templates
    return _render(templates, request, None, status_code=HTTPStatus.FORBIDDEN)


def register_jellyfin_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced by the Jellyfin synchronization page.

    :param app: FastAPI application receiving the Jellyfin-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {CsrfValidationError: csrf_rejection_handler}
    register_exception_handlers(app, handlers)


def _view_from_configuration(status: ProviderStatus) -> JellyfinSyncView | None:
    """Map a safe configuration snapshot to a page result without counts.

    :param status: Safe Jellyfin configuration snapshot.
    :return: ``None`` for valid configuration; otherwise a configuration-only result.
    """
    if status.configuration is ConfigurationState.CONFIGURED:
        return None
    if status.operational.value == "configuration_error":
        return JellyfinSyncView(JellyfinSyncOutcome.CONFIGURATION_ERROR)
    return JellyfinSyncView(JellyfinSyncOutcome.NOT_CONFIGURED)


def _view_from_report(report: WorkflowReport) -> JellyfinSyncView:
    """Map a normalized workflow report to a privacy-safe page result.

    :param report: Jellyfin library workflow report.
    :return: Aggregate-only synchronization result.
    """
    counts = report.counts
    if report.status is WorkflowStatus.FAILED:
        outcome = _failure_outcome(report)
    elif report.status is WorkflowStatus.PARTIAL:
        outcome = JellyfinSyncOutcome.PARTIAL
    else:
        outcome = JellyfinSyncOutcome.SUCCESS
    return JellyfinSyncView(
        outcome,
        synchronized=counts.succeeded,
        unresolved=counts.unresolved,
        ambiguous=counts.ambiguous,
        invalid=counts.invalid,
        failed=counts.failed,
        removed=counts.removed,
    )


def _failure_outcome(report: WorkflowReport) -> JellyfinSyncOutcome:
    """Return the safe failure outcome for a failed report.

    :param report: Failed Jellyfin library workflow report.
    :return: Distinct safe failure outcome.
    """
    reasons = {item.reason for item in report.items}
    if WorkflowFailureReason.PROVIDER_AUTHENTICATION_FAILURE.value in reasons:
        return JellyfinSyncOutcome.AUTHENTICATION_FAILURE
    if WorkflowFailureReason.PROVIDER_TRANSIENT_FAILURE.value in reasons:
        return JellyfinSyncOutcome.TRANSIENT_FAILURE
    return JellyfinSyncOutcome.FAILED


def _unexpected_failure_view() -> JellyfinSyncView:
    """Return the safe result for an unexpected application failure.

    :return: Failure result without exception data or report counts.
    """
    return JellyfinSyncView(JellyfinSyncOutcome.UNEXPECTED_FAILURE)


def _render(
    templates: Jinja2Templates,
    request: Request,
    result: JellyfinSyncView | None,
    *,
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the synchronization page with a fresh CSRF token and safe result.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :param result: Optional aggregate-only synchronization result.
    :param status_code: HTTP status for the rendered response.
    :return: Shared-layout synchronization page.
    """
    return templates.TemplateResponse(
        request,
        "jellyfin_sync.html",
        {"csrf_token": get_csrf_token(request), "result": result},
        status_code=status_code,
    )
