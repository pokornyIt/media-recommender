"""Tests for validated application settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from media_recommender.config import Settings

if TYPE_CHECKING:
    from pathlib import Path


def test_database_path_can_be_loaded_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify the SQLite location is explicit and environment-configurable."""
    database_path = tmp_path / "synthetic-catalog.db"
    monkeypatch.setenv("MEDIA_RECOMMENDER_DATABASE_PATH", str(database_path))

    settings = Settings()

    assert settings.database_path == database_path
    assert settings.database_url.database == str(database_path)


def test_database_path_rejects_empty_environment_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify an empty database path cannot silently target the working directory."""
    monkeypatch.setenv("MEDIA_RECOMMENDER_DATABASE_PATH", " ")

    with pytest.raises(ValidationError, match="Database path must not be empty"):
        Settings()
