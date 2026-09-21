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
    Phase2Orchestrator,
    WorkflowFailureReason,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.application.provider_status import (
    ConfigurationState,
    OperationalState,
    ProviderKind,
)
from media_recommender.web.csrf import CsrfValidationError, get_csrf_token, require_csrf_token
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.pages import get_provider_statuses, get_templates

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ExceptionHandler

router = APIRouter()

JELLYFIN_SYNC_PATH = "/synchronizations/jellyfin"


class JellyfinSyncOutcome(StrEnum):
    """Privacy-safe outcome rendered for one Jellyfin synchronization request."""

    UNAVAILABLE = "unavailable"
    INVALID_CONFIGURATION = "invalid_configuration"
    SUCCESS = "success"
    PARTIAL = "partial"
    AUTHENTICATION_FAILURE = "authentication_failure"
    TRANSIENT_FAILURE = "transient_failure"
    FAILED = "failed"
    UNEXPECTED_FAILURE = "unexpected_failure"


@dataclass(frozen=True, slots=True)
class JellyfinSyncView:
    """Aggregate-only synchronization result safe for rendering."""

    configured: bool
    outcome: JellyfinSyncOutcome | None = None
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


def get_jellyfin_configuration_state() -> tuple[bool, JellyfinSyncOutcome | None]:
    """Resolve the safe Jellyfin configuration state without contacting providers.

    The existing read-only provider-status contract distinguishes absent from
    malformed configuration without revealing any configuration values.

    :return: Whether synchronization may run and the safe blocking outcome.
    """
    for status in get_provider_statuses():
        if status.provider is ProviderKind.JELLYFIN:
            if status.configuration is ConfigurationState.CONFIGURED:
                return True, None
            if status.operational is OperationalState.CONFIGURATION_ERROR:
                return False, JellyfinSyncOutcome.INVALID_CONFIGURATION
            return False, JellyfinSyncOutcome.UNAVAILABLE
    return False, JellyfinSyncOutcome.UNAVAILABLE


@router.get(JELLYFIN_SYNC_PATH, response_class=HTMLResponse)
async def jellyfin_sync_page(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    configuration: Annotated[tuple[bool, JellyfinSyncOutcome | None], Depends(get_jellyfin_configuration_state)],
) -> HTMLResponse:
    """Render the Jellyfin synchronization form and its configuration state.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param configuration: Safe configuration state and blocking outcome.
    :return: Shared-layout synchronization page with a session-bound CSRF token.
    """
    configured, blocking_outcome = configuration
    return _render(templates, request, JellyfinSyncView(configured, _outcome(blocking_outcome)))


@router.post(JELLYFIN_SYNC_PATH, response_class=HTMLResponse)
async def submit_jellyfin_sync(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    service: Annotated[Phase2Orchestrator, Depends(get_jellyfin_sync_service)],
    configuration: Annotated[tuple[bool, JellyfinSyncOutcome | None], Depends(get_jellyfin_configuration_state)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
) -> HTMLResponse:
    """Run one Jellyfin library synchronization and render aggregate results.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param service: Injected Jellyfin-only application facade.
    :param configuration: Safe configuration state and blocking outcome.
    :param _csrf: CSRF validation dependency.
    :return: Shared-layout synchronization page with a privacy-safe result.
    """
    configured, blocking_outcome = configuration
    if not configured:
        return _render(
            templates,
            request,
            JellyfinSyncView(configured, _outcome(blocking_outcome)),
            status_code=HTTPStatus.BAD_REQUEST,
        )
    try:
        report = await service.synchronize_jellyfin_library()
    except Exception:  # noqa: BLE001 - translate unexpected application failures into a sanitized result.
        return _render(
            templates,
            request,
            JellyfinSyncView(configured, JellyfinSyncOutcome.UNEXPECTED_FAILURE),
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return _render(templates, request, _view_from_report(report, configured=configured))


async def csrf_rejection_handler(request: Request, _: Exception) -> HTMLResponse:
    """Render a safe rejection page for an invalid CSRF or origin check.

    :param request: Incoming rejected request.
    :return: Shared-layout rejection page.
    """
    templates: Jinja2Templates = request.app.state.templates
    return _render(
        templates,
        request,
        JellyfinSyncView(configured=False, outcome=JellyfinSyncOutcome.FAILED),
        status_code=HTTPStatus.FORBIDDEN,
    )


def register_jellyfin_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced by the Jellyfin synchronization page.

    :param app: FastAPI application receiving the synchronization-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {CsrfValidationError: csrf_rejection_handler}
    register_exception_handlers(app, handlers)


def _outcome(blocking_outcome: str | None) -> JellyfinSyncOutcome | None:
    """Coerce a safe blocking outcome value into the page outcome enum.

    :param blocking_outcome: Safe blocking outcome value, if any.
    :return: Matching page outcome, or ``None`` when synchronization may run.
    """
    return JellyfinSyncOutcome(blocking_outcome) if blocking_outcome is not None else None


def _view_from_report(report: WorkflowReport, *, configured: bool) -> JellyfinSyncView:
    """Map a normalized workflow report to a privacy-safe page result.

    :param report: Jellyfin library workflow report.
    :param configured: Whether Jellyfin configuration currently validates.
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
        configured,
        outcome,
        synchronized=counts.succeeded,
        unresolved=counts.unresolved,
        ambiguous=counts.ambiguous,
        invalid=counts.invalid,
        failed=counts.failed,
        removed=counts.removed,
    )


def _failure_outcome(report: WorkflowReport) -> JellyfinSyncOutcome:
    """Map a failed report's sanitized reason to a safe page outcome.

    :param report: Failed Jellyfin library workflow report.
    :return: Specific safe failure outcome when classified, otherwise generic.
    """
    reasons = {item.reason for item in report.items}
    if WorkflowFailureReason.PROVIDER_AUTHENTICATION_FAILURE.value in reasons:
        return JellyfinSyncOutcome.AUTHENTICATION_FAILURE
    if WorkflowFailureReason.PROVIDER_TRANSIENT_FAILURE.value in reasons:
        return JellyfinSyncOutcome.TRANSIENT_FAILURE
    return JellyfinSyncOutcome.FAILED


def _render(
    templates: Jinja2Templates,
    request: Request,
    result: JellyfinSyncView,
    *,
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the synchronization page with a fresh CSRF token and safe result.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :param result: Aggregate-only synchronization result.
    :param status_code: HTTP status for the rendered response.
    :return: Shared-layout synchronization page.
    """
    return templates.TemplateResponse(
        request,
        "jellyfin_sync.html",
        {"csrf_token": get_csrf_token(request), "result": result},
        status_code=status_code,
    )
