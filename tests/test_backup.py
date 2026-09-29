"""Offline smoke tests for the documented SQLite backup and restore procedure."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from media_recommender.persistence.migrations import restore_database, verify_database

if TYPE_CHECKING:
    from pathlib import Path


def _backup(source: Path, destination: Path) -> None:
    """Create a consistent backup through the SQLite online backup API.

    :param source: Source database file.
    :param destination: Destination backup file.
    """
    with sqlite3.connect(source) as source_connection, sqlite3.connect(destination) as destination_connection:
        source_connection.backup(destination_connection)


def test_backup_and_restore_preserve_synthetic_data(tmp_path: Path) -> None:
    """Back up, modify, restore, and verify synthetic data and integrity."""
    database_path = tmp_path / "app.db"
    backup_path = tmp_path / "backup.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE media_items (id TEXT PRIMARY KEY, title TEXT NOT NULL)")
        connection.execute("INSERT INTO media_items (id, title) VALUES (?, ?)", ("synthetic-id", "Synthetic Movie"))
        connection.commit()

    _backup(database_path, backup_path)
    verify_database(backup_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute("DELETE FROM media_items")
        connection.commit()

    restore_database(backup_path, database_path)

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute("SELECT id, title FROM media_items").fetchall()
    assert stored == [("synthetic-id", "Synthetic Movie")]
    verify_database(database_path)
