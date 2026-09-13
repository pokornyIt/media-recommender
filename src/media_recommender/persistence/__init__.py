"""SQLite persistence implementation for the shared media catalog."""

from media_recommender.persistence.database import create_engine, create_session_factory, session_scope
from media_recommender.persistence.repository import SqlAlchemyMediaCatalog

__all__ = ["SqlAlchemyMediaCatalog", "create_engine", "create_session_factory", "session_scope"]
