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
    assert revision == ("14fde284fd0d",)
    command.check(config)
