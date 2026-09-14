"""Add regional streaming availability persistence.

Revision ID: c91e2fa30d47
Revises: 8d7c2f19a4b6
Create Date: 2026-09-14 19:00:00.000000
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "c91e2fa30d47"
down_revision: str | Sequence[str] | None = "8d7c2f19a4b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add shared current regional streaming availability."""
    op.create_table(
        "streaming_availability",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("region", sa.String(length=2), nullable=False),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("source_service_id", sa.String(length=255), nullable=False),
        sa.Column("service_name", sa.String(length=200), nullable=False),
        sa.Column("availability_type", sa.String(length=20), nullable=False),
        sa.Column("observed_at", sa.String(length=40), nullable=False),
        sa.Column("attribution", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "availability_type IN ('subscription', 'rent', 'buy', 'free', 'ads')",
            name="ck_streaming_availability_type",
        ),
        sa.ForeignKeyConstraint(["media_id"], ["media_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id",
            "region",
            "source_provider",
            "source_service_id",
            "availability_type",
            name="uq_streaming_availability_fact",
        ),
    )
    op.create_index(
        op.f("ix_streaming_availability_media_id"),
        "streaming_availability",
        ["media_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_streaming_availability_region"),
        "streaming_availability",
        ["region"],
        unique=False,
    )


def downgrade() -> None:
    """Remove regional streaming availability persistence."""
    op.drop_index(op.f("ix_streaming_availability_region"), table_name="streaming_availability")
    op.drop_index(op.f("ix_streaming_availability_media_id"), table_name="streaming_availability")
    op.drop_table("streaming_availability")
