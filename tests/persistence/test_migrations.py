"""Tests for explicit Alembic schema migrations."""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from media_recommender.config import Settings
from media_recommender.persistence.database import create_engine
from media_recommender.persistence.migrations import (
    MigrationError,
    check_readiness,
    restore_database,
    upgrade_to_head,
    verify_database,
)

EXPECTED_TABLES = {
    "alembic_version",
    "artwork",
    "countries",
    "external_ids",
    "genres",
    "library_presence",
    "media_countries",
    "media_genres",
    "media_items",
    "preferences",
    "profiles",
    "provider_profile_mappings",
    "ratings",
    "streaming_availability",
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
    assert revision == ("c91e2fa30d47",)
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
    assert revision == ("c91e2fa30d47",)


def test_upgrade_to_head_creates_and_migrates_a_fresh_database(tmp_path: Path) -> None:
    """Create a fresh database through the explicit startup migration path."""
    database_path = tmp_path / "fresh.db"
    settings = Settings(database_path=database_path)

    outcome = upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")

    assert outcome.applied is True
    assert outcome.previous_revision is None
    assert outcome.current_revision == "c91e2fa30d47"
    assert outcome.backup_path is None
    verify_database(database_path)


def test_upgrade_to_head_is_idempotent(tmp_path: Path) -> None:
    """Skip migration work when the database is already current."""
    database_path = tmp_path / "current.db"
    settings = Settings(database_path=database_path)
    upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")

    outcome = upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")

    assert outcome.applied is False
    assert outcome.backup_path is None


def test_upgrade_to_head_backs_up_existing_data_before_upgrade(tmp_path: Path) -> None:
    """Snapshot and verify existing data before applying pending migrations."""
    database_path = tmp_path / "existing.db"
    settings = Settings(database_path=database_path)
    command.upgrade(alembic_config(database_path), "14fde284fd0d")
    media_id = "078fa456-ced1-484f-a0f4-e81816a55467"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO media_items (id, media_type, title) VALUES (?, ?, ?)",
            (media_id, "movie", "Existing Synthetic Movie"),
        )
        connection.commit()

    outcome = upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")

    assert outcome.applied is True
    assert outcome.backup_path is not None
    assert outcome.backup_path.is_file()
    verify_database(outcome.backup_path)
    with sqlite3.connect(outcome.backup_path) as connection:
        stored = connection.execute("SELECT id, title FROM media_items").fetchall()
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert stored == [(media_id, "Existing Synthetic Movie")]
    assert revision == ("14fde284fd0d",)
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT id FROM media_items").fetchall() == [(media_id,)]


def test_upgrade_to_head_refuses_an_unmanaged_schema(tmp_path: Path) -> None:
    """Refuse to migrate a database that has tables but no Alembic revision."""
    database_path = tmp_path / "unmanaged.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        connection.commit()
    settings = Settings(database_path=database_path)

    with pytest.raises(MigrationError):
        upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")


def test_upgrade_to_head_does_not_migrate_when_backup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Abort before migrating when the pre-upgrade backup cannot be created."""
    database_path = tmp_path / "backup-failure.db"
    settings = Settings(database_path=database_path)
    command.upgrade(alembic_config(database_path), "14fde284fd0d")

    def fail_copy(_source: Path, _destination: Path) -> None:
        """Simulate a backup copy failure."""
        raise OSError

    monkeypatch.setattr("media_recommender.persistence.migrations._copy_database", fail_copy)

    with pytest.raises(MigrationError):
        upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")

    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision == ("14fde284fd0d",)


def test_upgrade_to_head_preserves_backup_when_migration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the verified pre-upgrade backup when the migration itself fails."""
    database_path = tmp_path / "failed-migration.db"
    settings = Settings(database_path=database_path)
    command.upgrade(alembic_config(database_path), "14fde284fd0d")

    def fail_upgrade(_config: Config, _revision: str) -> None:
        """Simulate a failing Alembic upgrade."""
        raise RuntimeError

    monkeypatch.setattr("media_recommender.persistence.migrations.command.upgrade", fail_upgrade)

    with pytest.raises(MigrationError):
        upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")

    backups = list((tmp_path / "backups").glob("*.pre-*.db"))
    assert len(backups) == 1
    verify_database(backups[0])
    with sqlite3.connect(database_path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision == ("14fde284fd0d",)


def test_restore_database_restores_a_verified_backup(tmp_path: Path) -> None:
    """Restore a verified backup over a modified database."""
    database_path = tmp_path / "restore.db"
    backup_path = tmp_path / "restore-backup.db"
    command.upgrade(alembic_config(database_path), "head")
    media_id = "078fa456-ced1-484f-a0f4-e81816a55467"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO media_items (id, media_type, title) VALUES (?, ?, ?)",
            (media_id, "movie", "Synthetic Backup Movie"),
        )
        connection.commit()
    with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)
    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM media_items")
        connection.commit()

    restore_database(backup_path, database_path)

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute("SELECT id, title FROM media_items").fetchall()
    assert stored == [(media_id, "Synthetic Backup Movie")]
    verify_database(database_path)


def _create_synthetic_media_database(database_path: Path) -> None:
    """Create a synthetic media table with one row.

    :param database_path: Database file to create.
    """
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE media_items (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
        connection.execute("INSERT INTO media_items (id, title) VALUES (?, ?)", ("synthetic-id", "Synthetic Movie"))
        connection.commit()


def test_verify_database_rejects_a_missing_file_without_creating_it(tmp_path: Path) -> None:
    """Reject a missing database file without creating it."""
    database_path = tmp_path / "missing.db"

    with pytest.raises(MigrationError):
        verify_database(database_path)

    assert not database_path.exists()


def test_restore_database_rejects_missing_backup_without_touching_destination(tmp_path: Path) -> None:
    """Reject a missing backup without creating it or changing the destination."""
    database_path = tmp_path / "destination.db"
    backup_path = tmp_path / "missing-backup.db"
    _create_synthetic_media_database(database_path)

    with pytest.raises(MigrationError):
        restore_database(backup_path, database_path)

    assert not backup_path.exists()
    with sqlite3.connect(database_path) as connection:
        stored = connection.execute("SELECT id, title FROM media_items").fetchall()
    assert stored == [("synthetic-id", "Synthetic Movie")]


def test_restore_database_rejects_a_non_regular_backup(tmp_path: Path) -> None:
    """Reject a directory backup without changing the destination."""
    database_path = tmp_path / "destination.db"
    backup_path = tmp_path / "backup-directory"
    backup_path.mkdir()
    _create_synthetic_media_database(database_path)

    with pytest.raises(MigrationError):
        restore_database(backup_path, database_path)

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute("SELECT id, title FROM media_items").fetchall()
    assert stored == [("synthetic-id", "Synthetic Movie")]


def test_check_readiness_reports_pending_then_current_migrations(tmp_path: Path) -> None:
    """Report pending migrations before upgrade and current state afterwards."""
    database_path = tmp_path / "readiness.db"
    settings = Settings(database_path=database_path)
    engine = create_engine(settings)
    try:
        pending = asyncio.run(check_readiness(settings, engine))
    finally:
        asyncio.run(engine.dispose())

    assert pending.ready is False
    assert pending.database == "ok"
    assert pending.migrations == "pending"

    upgrade_to_head(settings, config_path=REPOSITORY_ROOT / "alembic.ini")
    engine = create_engine(settings)
    try:
        current = asyncio.run(check_readiness(settings, engine))
    finally:
        asyncio.run(engine.dispose())

    assert current.ready is True
    assert current.database == "ok"
    assert current.migrations == "current"
