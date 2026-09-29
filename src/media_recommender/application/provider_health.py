"""In-memory record of the latest provider operation outcome.

The record is process-local and is not persisted. After a restart every provider
reports ``no_recorded_operation`` again until a new workflow completes. The
recorder is fed by completed workflow reports, so it never performs its own
network probes and never stores provider payloads or personal data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from media_recommender.application.orchestration import (
    WorkflowFailureReason,
    WorkflowItemStatus,
    WorkflowKind,
)
from media_recommender.application.provider_status import OperationalState, ProviderKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from media_recommender.application.orchestration import WorkflowReport

_REPORT_PROVIDERS = {
    WorkflowKind.JELLYFIN_LIBRARY: ProviderKind.JELLYFIN,
    WorkflowKind.STREAMING_AVAILABILITY: ProviderKind.TMDB,
}
_CONFIGURATION_REASONS = frozenset(
    {
        WorkflowFailureReason.AUTHENTICATION_FAILURE.value,
        WorkflowFailureReason.NOT_CONFIGURED.value,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderOperationOutcome:
    """Safe recorded outcome of the latest provider operation."""

    operational: OperationalState
    observed_at: datetime


class ProviderOperationRecorder:
    """Record the latest safe operational outcome for each provider in memory."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        """Initialize an empty recorder.

        :param clock: Optional timezone-aware observation timestamp source.
        """
        self._clock = clock or (lambda: datetime.now(UTC))
        self._outcomes: dict[ProviderKind, ProviderOperationOutcome] = {}

    def record(self, report: WorkflowReport) -> None:
        """Record the safe outcome of one completed provider workflow.

        Reports for operations that are not provider-backed are ignored.

        :param report: Normalized workflow report.
        """
        provider = _REPORT_PROVIDERS.get(report.kind)
        if provider is None:
            return
        self._outcomes[provider] = ProviderOperationOutcome(_operational_state(report), self._clock())

    def outcome(self, provider: ProviderKind) -> ProviderOperationOutcome | None:
        """Return the latest recorded outcome for a provider.

        :param provider: Provider to look up.
        :return: Recorded outcome, or ``None`` when no operation was recorded.
        """
        return self._outcomes.get(provider)


def _operational_state(report: WorkflowReport) -> OperationalState:
    """Map a workflow report to a safe operational state.

    :param report: Normalized workflow report.
    :return: Success, configuration error, or transient failure state.
    """
    reasons = {item.reason for item in report.items if item.status is WorkflowItemStatus.FAILED}
    if not reasons:
        return OperationalState.SUCCESS
    if reasons & _CONFIGURATION_REASONS:
        return OperationalState.CONFIGURATION_ERROR
    return OperationalState.TRANSIENT_FAILURE
