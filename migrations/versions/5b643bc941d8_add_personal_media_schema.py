"""Add profile-owned personal media schema.

Revision ID: 5b643bc941d8
Revises: 14fde284fd0d
Create Date: 2026-09-14 13:00:00.000000
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "5b643bc941d8"
down_revision: str | Sequence[str] | None = "14fde284fd0d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add internal profiles and profile-owned personal media tables."""
    op.create_table(
        "profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_profiles_single_default",
        "profiles",
        ["is_default"],
        unique=True,
        sqlite_where=sa.text("is_default = 1"),
    )
    op.create_table(
        "preferences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("effect", sa.String(length=10), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=True),
        sa.Column("minimum", sa.Integer(), nullable=True),
        sa.Column("maximum", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('genre', 'production_country', 'production_region', "
            "'runtime_minutes', 'release_year', 'provider')",
            name="ck_preferences_kind",
        ),
        sa.CheckConstraint("effect IN ('prefer', 'exclude')", name="ck_preferences_effect"),
        sa.CheckConstraint("minimum IS NULL OR minimum >= 0", name="ck_preferences_minimum_nonnegative"),
        sa.CheckConstraint("maximum IS NULL OR maximum >= 0", name="ck_preferences_maximum_nonnegative"),
        sa.CheckConstraint(
            "minimum IS NULL OR maximum IS NULL OR minimum <= maximum",
            name="ck_preferences_bounds_order",
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_preferences_profile_id"), "preferences", ["profile_id"], unique=False)
    op.create_table(
        "provider_profile_mappings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("external_profile_id", sa.String(length=500), nullable=False),
        sa.Column("synchronized_at", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_profile_id", name="uq_provider_profile_mappings_external"),
    )
    op.create_index(
        op.f("ix_provider_profile_mappings_profile_id"),
        "provider_profile_mappings",
        ["profile_id"],
        unique=False,
    )
    op.create_table(
        "ratings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("like_state", sa.String(length=10), nullable=True),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("source_record_id", sa.String(length=500), nullable=True),
        sa.Column("synchronization_id", sa.String(length=255), nullable=True),
        sa.Column("imported_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint("value IS NULL OR (value >= 0 AND value <= 10)", name="ck_ratings_value_range"),
        sa.CheckConstraint("like_state IS NULL OR like_state IN ('liked', 'disliked')", name="ck_ratings_like_state"),
        sa.CheckConstraint("value IS NOT NULL OR like_state IS NOT NULL", name="ck_ratings_has_value"),
        sa.ForeignKeyConstraint(["media_id"], ["media_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("profile_id", "source_provider", "source_record_id", name="uq_ratings_source_record"),
    )
    op.create_index(op.f("ix_ratings_media_id"), "ratings", ["media_id"], unique=False)
    op.create_index(op.f("ix_ratings_profile_id"), "ratings", ["profile_id"], unique=False)
    op.create_table(
        "viewing_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("watched_at", sa.String(length=40), nullable=False),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("source_record_id", sa.String(length=500), nullable=True),
        sa.Column("synchronization_id", sa.String(length=255), nullable=True),
        sa.Column("imported_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "source_provider",
            "source_record_id",
            name="uq_viewing_events_source_record",
        ),
    )
    op.create_index(op.f("ix_viewing_events_media_id"), "viewing_events", ["media_id"], unique=False)
    op.create_index(op.f("ix_viewing_events_profile_id"), "viewing_events", ["profile_id"], unique=False)
    op.create_table(
        "watch_states",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("media_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("source_provider", sa.String(length=100), nullable=False),
        sa.Column("source_record_id", sa.String(length=500), nullable=True),
        sa.Column("synchronization_id", sa.String(length=255), nullable=True),
        sa.Column("imported_at", sa.String(length=40), nullable=False),
        sa.CheckConstraint("status IN ('watched', 'unwatched')", name="ck_watch_states_status"),
        sa.ForeignKeyConstraint(["media_id"], ["media_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "profile_id",
            "media_id",
            "source_provider",
            name="uq_watch_states_profile_media_provider",
        ),
    )
    op.create_index(op.f("ix_watch_states_media_id"), "watch_states", ["media_id"], unique=False)
    op.create_index(op.f("ix_watch_states_profile_id"), "watch_states", ["profile_id"], unique=False)


def downgrade() -> None:
    """Remove profile-owned personal media tables."""
    op.drop_index(op.f("ix_watch_states_profile_id"), table_name="watch_states")
    op.drop_index(op.f("ix_watch_states_media_id"), table_name="watch_states")
    op.drop_table("watch_states")
    op.drop_index(op.f("ix_viewing_events_profile_id"), table_name="viewing_events")
    op.drop_index(op.f("ix_viewing_events_media_id"), table_name="viewing_events")
    op.drop_table("viewing_events")
    op.drop_index(op.f("ix_ratings_profile_id"), table_name="ratings")
    op.drop_index(op.f("ix_ratings_media_id"), table_name="ratings")
    op.drop_table("ratings")
    op.drop_index(op.f("ix_provider_profile_mappings_profile_id"), table_name="provider_profile_mappings")
    op.drop_table("provider_profile_mappings")
    op.drop_index(op.f("ix_preferences_profile_id"), table_name="preferences")
    op.drop_table("preferences")
    op.drop_index("uq_profiles_single_default", table_name="profiles")
    op.drop_table("profiles")
