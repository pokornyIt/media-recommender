"""Explicit, recoverable Alembic migration execution for the SQLite database.

Startup applies pending migrations through Alembic only. Before any pending
migration is applied, an existing database is snapshotted with the SQLite online
backup API and the snapshot is verified, so a failed upgrade always leaves a
recoverable copy of the previous state. A migration failure aborts startup
instead of serving an incompatible schema.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from media_recommender.application.readiness import ReadinessReport

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from media_recommender.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_ALEMBIC_CONFIG = Path("alembic.ini")
BACKUP_DIRECTORY_NAME = "backups"
BACKUP_RETENTION = 5
_BACKUP_GLOB = "*.pre-*.db"


class MigrationError(RuntimeError):
    """Raised when the database schema cannot be upgraded safely."""


@dataclass(frozen=True, slots=True)
class MigrationOutcome:
    """Result of one explicit migration attempt."""

    previous_revision: str | None
    current_revision: str
    applied: bool
    backup_path: Path | None


def upgrade_to_head(settings: Settings, *, config_path: Path | None = None) -> MigrationOutcome:
    """Apply all pending migrations, snapshotting existing data first.

    :param settings: Validated application settings.
    :param config_path: Optional explicit Alembic configuration path.
    :return: Applied migration outcome including any pre-upgrade backup.
    :raises MigrationError: If the schema is unmanaged, the backup cannot be verified, or the upgrade fails.
    """
    database_path = settings.database_path
    database_path.parent.mkdir(parents=True, exist_ok=True)
    config = _alembic_config(settings, config_path)
    head = _head_revision(config)
    previous = _current_revision(database_path)
    if previous == head:
        return MigrationOutcome(previous, head, applied=False, backup_path=None)
    if database_path.exists() and previous is None and _has_user_tables(database_path):
        msg = "Existing database has no Alembic revision; refusing to migrate an unmanaged schema"
        raise MigrationError(msg)
    backup_path = None
    if database_path.exists() and previous is not None:
        backup_path = _create_verified_backup(database_path, previous, head)
    try:
        command.upgrade(config, "head")
    except Exception as error:
        msg = "Database migration failed; startup aborted to avoid serving an incompatible schema"
        raise MigrationError(msg) from error
    if backup_path is not None:
        _prune_backups(backup_path.parent, protect=backup_path)
    logger.info("Applied database migrations from %s to %s", previous or "empty", head)
    return MigrationOutcome(previous, head, applied=True, backup_path=backup_path)


def verify_database(database_path: Path) -> None:
    """Verify SQLite structural integrity of an existing database file.

    The path is rejected before any connection is opened, so verification never
    creates a missing file.

    :param database_path: Database file to verify.
    :raises MigrationError: If the path is not an existing regular file or is not a structurally valid SQLite database.
    """
    if not database_path.is_file():
        msg = f"SQLite database file was not found at {database_path.name}"
        raise MigrationError(msg)
    with closing(_connect_read_only(database_path)) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    if row is None or row[0] != "ok":
        msg = f"SQLite integrity check failed for {database_path.name}"
        raise MigrationError(msg)


def restore_database(backup_path: Path, database_path: Path) -> None:
    """Restore a verified backup over the configured database path.

    The application must be stopped before restoring so no writer is active. A
    missing or non-regular backup is rejected before any database file is
    opened, so a failed restore never creates a source file or changes the
    destination database.

    :param backup_path: Verified backup to restore.
    :param database_path: Destination database path.
    :raises MigrationError: If the backup is missing or not a regular file, or if verification fails.
    """
    if not backup_path.is_file():
        msg = f"Backup file was not found at {backup_path.name}"
        raise MigrationError(msg)
    verify_database(backup_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    for sidecar in (
        database_path.with_name(f"{database_path.name}-wal"),
        database_path.with_name(f"{database_path.name}-shm"),
    ):
        sidecar.unlink(missing_ok=True)
    _copy_database(backup_path, database_path)
    verify_database(database_path)


async def check_readiness(settings: Settings, engine: AsyncEngine) -> ReadinessReport:
    """Check database accessibility and migration state without contacting providers.

    :param settings: Validated application settings.
    :param engine: Application database engine.
    :return: Safe readiness facts.
    """
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return ReadinessReport(ready=False, database="unavailable", migrations="unknown")
    current = await _read_applied_revision(engine)
    try:
        head = _head_revision(_alembic_config(settings, None))
    except MigrationError:
        return ReadinessReport(ready=False, database="ok", migrations="unknown")
    if current == head:
        return ReadinessReport(ready=True, database="ok", migrations="current")
    return ReadinessReport(ready=False, database="ok", migrations="pending")


async def _read_applied_revision(engine: AsyncEngine) -> str | None:
    """Return the applied Alembic revision, or ``None`` when it cannot be read.

    :param engine: Application database engine.
    :return: Applied revision identifier, or ``None``.
    """
    revision: str | None = None
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            revision = result.scalar_one_or_none()
    except SQLAlchemyError:
        return None
    return revision


def _alembic_config(settings: Settings, config_path: Path | None) -> Config:
    """Return Alembic configuration targeting the configured database.

    :param settings: Validated application settings.
    :param config_path: Optional explicit configuration path.
    :return: Alembic configuration with an explicit database URL.
    :raises MigrationError: If the configuration file is missing.
    """
    path = config_path or DEFAULT_ALEMBIC_CONFIG
    if not path.is_file():
        msg = f"Alembic configuration was not found at {path}"
        raise MigrationError(msg)
    config = Config(str(path))
    config.set_main_option("sqlalchemy.url", settings.database_url.render_as_string(hide_password=False))
    return config


def _head_revision(config: Config) -> str:
    """Return the single head revision declared by the migration scripts.

    :param config: Alembic configuration.
    :return: Head revision identifier.
    :raises MigrationError: If no head revision is declared.
    """
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        msg = "Alembic configuration declares no head revision"
        raise MigrationError(msg)
    return head


def _current_revision(database_path: Path) -> str | None:
    """Return the applied Alembic revision, or ``None`` when absent.

    :param database_path: Database file to inspect.
    :return: Applied revision identifier, or ``None``.
    """
    if not database_path.exists():
        return None
    with sqlite3.connect(database_path) as connection:
        if not _table_exists(connection, "alembic_version"):
            return None
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row is not None else None


def _has_user_tables(database_path: Path) -> bool:
    """Return whether a database file already contains application tables.

    :param database_path: Database file to inspect.
    :return: Whether any non-internal table exists.
    """
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
    return row is not None


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    """Return whether a table exists in an open SQLite connection.

    :param connection: Open SQLite connection.
    :param name: Table name to look up.
    :return: Whether the table exists.
    """
    row = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row is not None


def _create_verified_backup(database_path: Path, previous: str, head: str) -> Path:
    """Create and verify a consistent pre-upgrade snapshot.

    :param database_path: Existing database to snapshot.
    :param previous: Currently applied revision.
    :param head: Target revision.
    :return: Verified backup path.
    :raises MigrationError: If the snapshot cannot be created or verified.
    """
    backup_directory = database_path.parent / BACKUP_DIRECTORY_NAME
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_directory / f"{database_path.stem}.pre-{previous}-to-{head}.{timestamp}.db"
    try:
        _copy_database(database_path, backup_path)
        verify_database(backup_path)
    except (OSError, sqlite3.Error, MigrationError) as error:
        backup_path.unlink(missing_ok=True)
        msg = "Pre-upgrade database backup could not be created and verified; migration was not attempted"
        raise MigrationError(msg) from error
    return backup_path


def _connect_read_only(database_path: Path) -> sqlite3.Connection:
    """Open an existing SQLite database read-only.

    :param database_path: Existing database file.
    :return: Read-only SQLite connection.
    """
    return sqlite3.connect(f"{database_path.resolve().as_uri()}?mode=ro", uri=True)


def _copy_database(source: Path, destination: Path) -> None:
    """Copy a SQLite database consistently through the online backup API.

    :param source: Source database file.
    :param destination: Destination database file.
    """
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as destination_connection:
        source_connection.backup(destination_connection)


def _prune_backups(backup_directory: Path, *, protect: Path) -> None:
    """Remove older automatic backups after a successful migration.

    Retention runs only after a successful upgrade, and the newest backup is
    always protected, so a backup required after a failed upgrade is never
    removed by retention.

    :param backup_directory: Directory containing automatic backups.
    :param protect: Newest backup that must never be removed.
    """
    candidates = sorted(
        (path for path in backup_directory.glob(_BACKUP_GLOB) if path != protect),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in candidates[BACKUP_RETENTION - 1 :]:
        stale.unlink(missing_ok=True)
