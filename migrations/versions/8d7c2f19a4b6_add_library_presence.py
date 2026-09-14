"""Add provider library-presence persistence.

Revision ID: 8d7c2f19a4b6
Revises: ca0c3ba9fe91
Create Date: 2026-09-14 17:00:00.000000
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "8d7c2f19a4b6"
down_revision: str | Sequence[str] | None = "ca0c3ba9fe91"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add profile-visible provider library presence."""
    op.create_table(
        "library_presence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("play_count", sa.Integer(), nullable=True),
        sa.Column("last_played_at", sa.String(length=40), nullable=True),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("source_record_id", sa.String(length=500), nullable=False),
        sa.Column("synchronization_id", sa.String(length=255), nullable=True),
        sa.Column("imported_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint("play_count IS NULL OR play_count >= 0", name="ck_library_presence_play_count"),
        sa.ForeignKeyConstraint(["media_id"], ["media_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "source_provider",
            "source_record_id",
            name="uq_library_presence_source_record",
        ),
    )
    op.create_index(op.f("ix_library_presence_media_id"), "library_presence", ["media_id"], unique=False)
    op.create_index(op.f("ix_library_presence_profile_id"), "library_presence", ["profile_id"], unique=False)


def downgrade() -> None:
    """Remove provider library-presence persistence."""
    op.drop_index(op.f("ix_library_presence_profile_id"), table_name="library_presence")
    op.drop_index(op.f("ix_library_presence_media_id"), table_name="library_presence")
    op.drop_table("library_presence")
