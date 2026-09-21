"""Server-rendered Jellyfin library synchronization page."""

from __future__ import annotations

from collections.abc import Sequence  # noqa: TC003 - resolved at runtime by FastAPI dependency wiring.
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.

from media_recommender.application import (
    ConfigurationState,
    OperationalState,
    Phase2Orchestrator,
    ProviderKind,
    ProviderStatus,
    WorkflowFailureReason,
    WorkflowItemStatus,
    WorkflowReport,
    WorkflowStatus,
)
from media_recommender.web.csrf import CsrfValidationError, get_csrf_token, require_csrf_token
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.imports import csrf_rejection_handler as netflix_csrf_rejection_handler
from media_recommender.web.routes.pages import get_provider_statuses, get_templates

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ExceptionHandler

router = APIRouter()

JELLYFIN_SYNC_PATH = "/synchronizations/jellyfin"


class JellyfinConfigurationState(StrEnum):
    """Safe Jellyfin configuration state controlling synchronization availability."""

    CONFIGURED = "configured"
    ABSENT = "absent"
    INVALID = "invalid"


class JellyfinSyncOutcome(StrEnum):
    """Privacy-safe outcome rendered for one Jellyfin synchronization request."""

    ABSENT_CONFIGURATION = "absent_configuration"
    INVALID_CONFIGURATION = "invalid_configuration"
    REJECTED = "rejected"
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


def get_jellyfin_configuration_state(
    statuses: Annotated[Sequence[ProviderStatus], Depends(get_provider_statuses)],
) -> JellyfinConfigurationState:
    """Resolve the safe Jellyfin configuration state from provider status snapshots.

    The existing provider-status contract already distinguishes absent, malformed,
    and valid configuration without reading or revealing any secret value.

    :param statuses: Safe provider status snapshots in a stable order.
    :return: Configuration state used to enable the synchronization control.
    """
    return jellyfin_configuration_state(statuses)


def jellyfin_configuration_state(statuses: Sequence[ProviderStatus]) -> JellyfinConfigurationState:
    """Map safe provider status snapshots to the Jellyfin configuration state.

    :param statuses: Safe provider status snapshots in a stable order.
    :return: Configured, absent, or invalid Jellyfin configuration state.
    """
    status = next((item for item in statuses if item.provider is ProviderKind.JELLYFIN), None)
    if status is None:
        return JellyfinConfigurationState.ABSENT
    if status.configuration is ConfigurationState.CONFIGURED:
        return JellyfinConfigurationState.CONFIGURED
    if status.operational is OperationalState.CONFIGURATION_ERROR:
        return JellyfinConfigurationState.INVALID
    return JellyfinConfigurationState.ABSENT


@router.get(JELLYFIN_SYNC_PATH, response_class=HTMLResponse)
async def jellyfin_sync_page(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    configuration: Annotated[JellyfinConfigurationState, Depends(get_jellyfin_configuration_state)],
) -> HTMLResponse:
    """Render the read-only Jellyfin synchronization form.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param configuration: Safe Jellyfin configuration state.
    :return: Shared-layout synchronization page with a session-bound CSRF token.
    """
    return _render(templates, request, configuration, None)


@router.post(JELLYFIN_SYNC_PATH, response_class=HTMLResponse)
async def submit_jellyfin_sync(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    service: Annotated[Phase2Orchestrator, Depends(get_jellyfin_sync_service)],
    configuration: Annotated[JellyfinConfigurationState, Depends(get_jellyfin_configuration_state)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
) -> HTMLResponse:
    """Run exactly one Jellyfin-only synchronization and render aggregate results.

    The CSRF/origin dependency is applied before configuration is re-validated.
    Configuration is resolved only through the safe provider-status contract and
    the route never reads secrets, persistence, or provider DTOs directly.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param service: Injected Jellyfin-only application facade.
    :param configuration: Safe Jellyfin configuration state.
    :param _csrf: CSRF and origin validation dependency.
    :return: Shared-layout synchronization page with a privacy-safe result.
    """
    if configuration is not JellyfinConfigurationState.CONFIGURED:
        result = _configuration_view(configuration)
        return _render(templates, request, configuration, result, status_code=HTTPStatus.BAD_REQUEST)
    try:
        report = await service.synchronize_jellyfin_library()
    except Exception:  # noqa: BLE001 - translate unexpected application failures into a sanitized result.
        return _render(
            templates,
            request,
            configuration,
            _unexpected_failure_view(),
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    return _render(templates, request, configuration, _view_from_report(report))


async def csrf_rejection_handler(request: Request, error: Exception) -> HTMLResponse:
    """Render a safe rejection page for an invalid CSRF or origin check.

    Submissions for other server-rendered forms are delegated to their own
    handler so this feature does not change existing pages.

    :param request: Incoming rejected request.
    :param error: Rejected CSRF validation error.
    :return: Shared-layout rejection page.
    """
    if request.url.path != JELLYFIN_SYNC_PATH:
        return await netflix_csrf_rejection_handler(request, error)
    templates: Jinja2Templates = request.app.state.templates
    configuration = jellyfin_configuration_state(get_provider_statuses())
    return _render(
        templates,
        request,
        configuration,
        JellyfinSyncView(JellyfinSyncOutcome.REJECTED),
        status_code=HTTPStatus.FORBIDDEN,
    )


def register_jellyfin_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced by the Jellyfin synchronization page.

    :param app: FastAPI application receiving the Jellyfin-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {CsrfValidationError: csrf_rejection_handler}
    register_exception_handlers(app, handlers)


def _view_from_report(report: WorkflowReport) -> JellyfinSyncView:
    """Map a normalized workflow report to a privacy-safe page result.

    :param report: Jellyfin library workflow report.
    :return: Aggregate-only synchronization result without item data.
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
    """Classify a failed report without exposing provider values or raw errors.

    :param report: Failed library workflow report.
    :return: Distinct safe outcome for authentication, transient, or generic failure.
    """
    reasons = {item.reason for item in report.items if item.status is WorkflowItemStatus.FAILED}
    if WorkflowFailureReason.AUTHENTICATION_FAILURE.value in reasons:
        return JellyfinSyncOutcome.AUTHENTICATION_FAILURE
    if WorkflowFailureReason.TRANSIENT_FAILURE.value in reasons:
        return JellyfinSyncOutcome.TRANSIENT_FAILURE
    return JellyfinSyncOutcome.FAILED


def _configuration_view(configuration: JellyfinConfigurationState) -> JellyfinSyncView:
    """Return the safe result for absent or invalid Jellyfin configuration.

    :param configuration: Resolved non-configured Jellyfin state.
    :return: Configuration result without synchronization counts.
    """
    if configuration is JellyfinConfigurationState.INVALID:
        return JellyfinSyncView(JellyfinSyncOutcome.INVALID_CONFIGURATION)
    return JellyfinSyncView(JellyfinSyncOutcome.ABSENT_CONFIGURATION)


def _unexpected_failure_view() -> JellyfinSyncView:
    """Return the safe result for an unexpected application failure.

    :return: Failure result without exception data or report counts.
    """
    return JellyfinSyncView(JellyfinSyncOutcome.UNEXPECTED_FAILURE)


def _render(
    templates: Jinja2Templates,
    request: Request,
    configuration: JellyfinConfigurationState,
    result: JellyfinSyncView | None,
    *,
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the synchronization page with a fresh CSRF token and safe result.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :param configuration: Safe Jellyfin configuration state.
    :param result: Optional aggregate-only synchronization result.
    :param status_code: HTTP status for the rendered response.
    :return: Shared-layout synchronization page.
    """
    return templates.TemplateResponse(
        request,
        "jellyfin_sync.html",
        {
            "csrf_token": get_csrf_token(request),
            "configuration": configuration,
            "result": result,
        },
        status_code=status_code,
    )
