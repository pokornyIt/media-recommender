"""Tests for explicit Alembic schema migrations."""

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

EXPECTED_TABLES = {
    "alembic_version",
    "artwork",
    "countries",
    "external_ids",
    "genres",
    "media_countries",
    "media_genres",
    "media_items",
    "preferences",
    "profiles",
    "provider_profile_mappings",
    "ratings",
    "viewing_events",
    "watch_states",
}
REPOSITORY_ROOT = Path(__file__).parents[2]


def alembic_config(database_path: Path) -> Config:
    """Return Alembic configuration targeting a temporary SQLite database."""
    config = Config(REPOSITORY_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_migration_upgrades_an_empty_database_to_current_schema(tmp_path: Path) -> None:
    """Verify a clean temporary database is created only through Alembic."""
    database_path = tmp_path / "catalog.db"
    config = alembic_config(database_path)

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert tables == EXPECTED_TABLES
    assert revision == ("5b643bc941d8",)
    command.check(config)


def test_migration_upgrades_phase_one_data_without_duplication(tmp_path: Path) -> None:
    """Verify the personal schema upgrades an existing Phase 1 catalog in place."""
    database_path = tmp_path / "phase-one.db"
    config = alembic_config(database_path)
    command.upgrade(config, "14fde284fd0d")

    media_id = "078fa456-ced1-484f-a0f4-e81816a55467"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO media_items (id, media_type, title) VALUES (?, ?, ?)",
            (media_id, "movie", "Existing Synthetic Movie"),
        )
        connection.commit()

    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        stored_media = connection.execute("SELECT id, title FROM media_items").fetchall()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()

    assert stored_media == [(media_id, "Existing Synthetic Movie")]
    assert revision == ("5b643bc941d8",)
