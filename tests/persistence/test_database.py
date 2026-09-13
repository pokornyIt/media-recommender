"""Tests for asynchronous engine and session setup."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sqlalchemy import text

from media_recommender.config import Settings
from media_recommender.persistence import create_engine

if TYPE_CHECKING:
    from pathlib import Path


async def _inspect_unmigrated_database(database_path: Path) -> None:
    """Open an engine and verify that connecting creates no schema objects."""
    engine = create_engine(Settings(database_path=database_path))
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        )
    await engine.dispose()

    assert result.scalars().all() == []


def test_engine_connection_does_not_create_schema_implicitly(tmp_path: Path) -> None:
    """Verify normal engine setup leaves schema creation to Alembic."""
    asyncio.run(_inspect_unmigrated_database(tmp_path / "unmigrated.db"))
