"""Asynchronous SQLAlchemy engine and session configuration."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sqlalchemy.engine.interfaces import DBAPIConnection
    from sqlalchemy.pool import ConnectionPoolEntry

    from media_recommender.config import Settings

type SessionFactory = async_sessionmaker[AsyncSession]


def _enable_sqlite_foreign_keys(
    dbapi_connection: DBAPIConnection,
    _connection_record: ConnectionPoolEntry,
) -> None:
    """Enable SQLite foreign-key enforcement for a pooled connection.

    :param dbapi_connection: Newly opened SQLite DB-API connection.
    :param _connection_record: SQLAlchemy pool record associated with the connection.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_engine(settings: Settings, *, echo: bool = False) -> AsyncEngine:
    """Create an asynchronous SQLite engine without modifying the schema.

    :param settings: Validated application settings.
    :param echo: Whether SQLAlchemy should log emitted SQL.
    :return: Configured asynchronous engine.
    """
    engine = create_async_engine(settings.database_url, echo=echo)
    event.listen(engine.sync_engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create an asynchronous session factory for an engine.

    :param engine: Configured asynchronous database engine.
    :return: Session factory with non-expiring committed objects.
    """
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(session_factory: SessionFactory) -> AsyncGenerator[AsyncSession]:
    """Provide a transactional session that commits or rolls back atomically.

    :param session_factory: Factory used to open the session.
    :yield: Open transactional session.
    """
    async with session_factory() as session, session.begin():
        yield session
