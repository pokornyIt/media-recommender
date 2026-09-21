"""Server-rendered regional streaming-availability refresh page."""

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
from media_recommender.config import Settings
from media_recommender.web.csrf import CsrfValidationError, get_csrf_token, require_csrf_token
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.routes.jellyfin import csrf_rejection_handler as jellyfin_csrf_rejection_handler
from media_recommender.web.routes.pages import get_provider_statuses, get_templates

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ExceptionHandler

router = APIRouter()

AVAILABILITY_REFRESH_PATH = "/availability/refresh"


class AvailabilityConfigurationState(StrEnum):
    """Safe TMDB configuration state controlling availability refresh availability."""

    CONFIGURED = "configured"
    ABSENT = "absent"
    INVALID = "invalid"


class AvailabilityRefreshOutcome(StrEnum):
    """Privacy-safe outcome rendered for one availability refresh request."""

    ABSENT_CONFIGURATION = "absent_configuration"
    INVALID_CONFIGURATION = "invalid_configuration"
    REJECTED = "rejected"
    SUCCESS = "success"
    PARTIAL = "partial"
    AUTHENTICATION_FAILURE = "authentication_failure"
    TRANSIENT_FAILURE = "transient_failure"
    FAILED = "failed"
    UNEXPECTED_FAILURE = "unexpected_failure"


class AvailabilityFailureKind(StrEnum):
    """Privacy-safe aggregate failure classification for a refresh result."""

    AUTHENTICATION = "authentication"
    TRANSIENT = "transient"
    PROVIDER = "provider"


@dataclass(frozen=True, slots=True)
class AvailabilityRefreshView:
    """Aggregate-only availability refresh result safe for rendering."""

    outcome: AvailabilityRefreshOutcome
    failure: AvailabilityFailureKind | None = None
    refreshed: int = 0
    unresolved: int = 0
    ambiguous: int = 0
    failed: int = 0
    removed: int = 0


def get_availability_configuration_state(
    statuses: Annotated[Sequence[ProviderStatus], Depends(get_provider_statuses)],
) -> AvailabilityConfigurationState:
    """Resolve the safe TMDB configuration state from provider status snapshots.

    The existing provider-status contract already distinguishes absent, malformed,
    and valid configuration without reading or revealing any secret value.

    :param statuses: Safe provider status snapshots in a stable order.
    :return: Configuration state used to enable the refresh control.
    """
    return availability_configuration_state(statuses)


def availability_configuration_state(statuses: Sequence[ProviderStatus]) -> AvailabilityConfigurationState:
    """Map safe provider status snapshots to the TMDB configuration state.

    :param statuses: Safe provider status snapshots in a stable order.
    :return: Configured, absent, or invalid TMDB configuration state.
    """
    status = next((item for item in statuses if item.provider is ProviderKind.TMDB), None)
    if status is None:
        return AvailabilityConfigurationState.ABSENT
    if status.configuration is ConfigurationState.CONFIGURED:
        return AvailabilityConfigurationState.CONFIGURED
    if status.operational is OperationalState.CONFIGURATION_ERROR:
        return AvailabilityConfigurationState.INVALID
    return AvailabilityConfigurationState.ABSENT


def get_default_region() -> str:
    """Return the configured default availability region.

    :return: Configured ISO 3166-1 alpha-2 region code.
    """
    return Settings().default_region


def get_availability_refresh_service(
    request: Request,
    configuration: Annotated[AvailabilityConfigurationState, Depends(get_availability_configuration_state)],
) -> Phase2Orchestrator | None:
    """Return the availability refresh facade only for a valid configuration.

    Facade resolution is deliberately gated by the safe configuration state so
    an absent or malformed TMDB configuration never requires an installed
    service and cannot surface as a generic server error.

    :param request: Incoming request carrying the current application instance.
    :param configuration: Safe TMDB configuration state.
    :return: Phase 2 application facade, or ``None`` when TMDB is not configured.
    :raises RuntimeError: If TMDB is configured but no refresh service is installed.
    """
    if configuration is not AvailabilityConfigurationState.CONFIGURED:
        return None
    try:
        return cast("Phase2Orchestrator", request.app.state.availability_refresh_service)
    except AttributeError as error:
        msg = "Availability refresh service is not configured"
        raise RuntimeError(msg) from error


@router.get(AVAILABILITY_REFRESH_PATH, response_class=HTMLResponse)
async def availability_refresh_page(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    configuration: Annotated[AvailabilityConfigurationState, Depends(get_availability_configuration_state)],
) -> HTMLResponse:
    """Render the read-only availability refresh form.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param configuration: Safe TMDB configuration state.
    :return: Shared-layout refresh page with a session-bound CSRF token.
    """
    return _render(templates, request, configuration, None)


@router.post(AVAILABILITY_REFRESH_PATH, response_class=HTMLResponse)
async def submit_availability_refresh(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
    _csrf: Annotated[None, Depends(require_csrf_token)],
    service: Annotated[Phase2Orchestrator | None, Depends(get_availability_refresh_service)],
    configuration: Annotated[AvailabilityConfigurationState, Depends(get_availability_configuration_state)],
    region: Annotated[str, Depends(get_default_region)],
) -> HTMLResponse:
    """Run exactly one regional availability refresh and render aggregate results.

    The CSRF/origin gate is declared first so it is enforced before the facade
    is ever resolved, and facade resolution is gated by safe configuration.
    Absent or invalid configuration therefore returns its distinct safe outcome
    and an invalid token or origin receives the established rejection without
    requiring an installed service. Configuration is resolved only through the
    safe provider-status contract and the route never reads secrets,
    persistence, or provider DTOs directly.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :param _csrf: CSRF and origin validation dependency.
    :param service: Injected availability-only application facade, or ``None`` when unconfigured.
    :param configuration: Safe TMDB configuration state.
    :param region: Configured default availability region.
    :return: Shared-layout refresh page with a privacy-safe result.
    """
    if configuration is not AvailabilityConfigurationState.CONFIGURED:
        result = _configuration_view(configuration)
        return _render(templates, request, configuration, result, status_code=HTTPStatus.BAD_REQUEST)
    if service is None:
        return _render(
            templates,
            request,
            configuration,
            _unexpected_failure_view(),
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR,
        )
    try:
        report = await service.refresh_streaming_availability(region)
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
    if request.url.path != AVAILABILITY_REFRESH_PATH:
        return await jellyfin_csrf_rejection_handler(request, error)
    templates: Jinja2Templates = request.app.state.templates
    configuration = availability_configuration_state(get_provider_statuses())
    return _render(
        templates,
        request,
        configuration,
        AvailabilityRefreshView(AvailabilityRefreshOutcome.REJECTED),
        status_code=HTTPStatus.FORBIDDEN,
    )


def register_availability_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced by the availability refresh page.

    :param app: FastAPI application receiving the availability-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {CsrfValidationError: csrf_rejection_handler}
    register_exception_handlers(app, handlers)


def _view_from_report(report: WorkflowReport) -> AvailabilityRefreshView:
    """Map a normalized workflow report to a privacy-safe page result.

    A partial report keeps its partial completion while also exposing a safe
    aggregate failure classification, so a mixed success and provider failure
    remains visibly distinct from unresolved or ambiguous titles.

    :param report: Streaming-availability workflow report.
    :return: Aggregate-only refresh result without item data.
    """
    counts = report.counts
    if report.status is WorkflowStatus.FAILED:
        outcome = _failure_outcome(report)
        failure = None
    elif report.status is WorkflowStatus.PARTIAL:
        outcome = AvailabilityRefreshOutcome.PARTIAL
        failure = _failure_kind(report)
    else:
        outcome = AvailabilityRefreshOutcome.SUCCESS
        failure = None
    return AvailabilityRefreshView(
        outcome,
        failure=failure,
        refreshed=counts.succeeded,
        unresolved=counts.unresolved,
        ambiguous=counts.ambiguous,
        failed=counts.failed,
        removed=counts.removed,
    )


def _failure_kind(report: WorkflowReport) -> AvailabilityFailureKind | None:
    """Classify failed items without exposing provider values or raw errors.

    :param report: Availability workflow report.
    :return: Safe aggregate failure classification, or ``None`` when no item failed.
    """
    reasons = {item.reason for item in report.items if item.status is WorkflowItemStatus.FAILED}
    if WorkflowFailureReason.AUTHENTICATION_FAILURE.value in reasons:
        return AvailabilityFailureKind.AUTHENTICATION
    if WorkflowFailureReason.TRANSIENT_FAILURE.value in reasons:
        return AvailabilityFailureKind.TRANSIENT
    if reasons:
        return AvailabilityFailureKind.PROVIDER
    return None


def _failure_outcome(report: WorkflowReport) -> AvailabilityRefreshOutcome:
    """Classify a failed report without exposing provider values or raw errors.

    :param report: Failed availability workflow report.
    :return: Distinct safe outcome for authentication, transient, or generic failure.
    """
    kind = _failure_kind(report)
    if kind is AvailabilityFailureKind.AUTHENTICATION:
        return AvailabilityRefreshOutcome.AUTHENTICATION_FAILURE
    if kind is AvailabilityFailureKind.TRANSIENT:
        return AvailabilityRefreshOutcome.TRANSIENT_FAILURE
    return AvailabilityRefreshOutcome.FAILED


def _configuration_view(configuration: AvailabilityConfigurationState) -> AvailabilityRefreshView:
    """Return the safe result for absent or invalid TMDB configuration.

    :param configuration: Resolved non-configured TMDB state.
    :return: Configuration result without refresh counts.
    """
    if configuration is AvailabilityConfigurationState.INVALID:
        return AvailabilityRefreshView(AvailabilityRefreshOutcome.INVALID_CONFIGURATION)
    return AvailabilityRefreshView(AvailabilityRefreshOutcome.ABSENT_CONFIGURATION)


def _unexpected_failure_view() -> AvailabilityRefreshView:
    """Return the safe result for an unexpected application failure.

    :return: Failure result without exception data or report counts.
    """
    return AvailabilityRefreshView(AvailabilityRefreshOutcome.UNEXPECTED_FAILURE)


def _render(
    templates: Jinja2Templates,
    request: Request,
    configuration: AvailabilityConfigurationState,
    result: AvailabilityRefreshView | None,
    *,
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the refresh page with a fresh CSRF token and safe result.

    :param templates: Shared Jinja2 template renderer.
    :param request: Incoming browser request.
    :param configuration: Safe TMDB configuration state.
    :param result: Optional aggregate-only refresh result.
    :param status_code: HTTP status for the rendered response.
    :return: Shared-layout refresh page.
    """
    return templates.TemplateResponse(
        request,
        "availability_refresh.html",
        {
            "csrf_token": get_csrf_token(request),
            "configuration": configuration,
            "result": result,
        },
        status_code=status_code,
    )
