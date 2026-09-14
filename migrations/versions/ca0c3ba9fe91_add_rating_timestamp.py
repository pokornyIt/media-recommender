"""Add source rating timestamp.

Revision ID: ca0c3ba9fe91
Revises: 5b643bc941d8
Create Date: 2026-09-14 15:00:00.000000
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "ca0c3ba9fe91"
down_revision: str | Sequence[str] | None = "5b643bc941d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Preserve the provider-supplied timestamp of a rating interaction."""
    op.add_column("ratings", sa.Column("rated_at", sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Remove the provider-supplied rating timestamp."""
    op.drop_column("ratings", "rated_at")
