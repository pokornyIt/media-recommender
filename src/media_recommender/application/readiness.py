"""Operational readiness contracts shared by interfaces and composition roots.

Readiness describes database accessibility and migration state only. It never
contacts external providers, so a temporary provider outage cannot make the
application report itself as not ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

type DatabaseState = Literal["ok", "unavailable"]
type MigrationState = Literal["current", "pending", "unknown"]


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Safe database readiness facts for operational health endpoints."""

    ready: bool
    database: DatabaseState
    migrations: MigrationState


class ReadinessProbe(Protocol):
    """Report database accessibility and migration state without provider calls."""

    async def check(self) -> ReadinessReport:
        """Return current readiness facts.

        :return: Safe readiness facts.
        """
        ...
