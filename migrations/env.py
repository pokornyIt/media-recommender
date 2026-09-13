"""Alembic migration environment for the asynchronous SQLite database."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from media_recommender.config import Settings
from media_recommender.persistence.models import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url(*, prepare_directory: bool) -> str:
    """Return an explicit Alembic URL or the URL from application settings.

    :param prepare_directory: Whether to create the configured database parent directory.
    :return: Async SQLite SQLAlchemy URL.
    """
    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return configured_url

    settings = Settings()
    if prepare_directory:
        settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    return settings.database_url.render_as_string(hide_password=False)


def run_migrations_offline() -> None:
    """Render migrations without opening a database connection."""
    context.configure(
        url=_database_url(prepare_directory=False),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    """Run configured migrations through a synchronous connection adapter."""
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    """Create an async engine and apply migrations through its sync adapter."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url(prepare_directory=True)
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Apply migrations to a live database using the async driver."""
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
