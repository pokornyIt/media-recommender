"""SQLite persistence implementation for the shared media catalog."""

from media_recommender.persistence.availability_repository import SqlAlchemyAvailabilityRepository
from media_recommender.persistence.database import create_engine, create_session_factory, session_scope
from media_recommender.persistence.personal_repository import SqlAlchemyPersonalMediaRepository
from media_recommender.persistence.repository import SqlAlchemyMediaCatalog

__all__ = [
    "SqlAlchemyAvailabilityRepository",
    "SqlAlchemyMediaCatalog",
    "SqlAlchemyPersonalMediaRepository",
    "create_engine",
    "create_session_factory",
    "session_scope",
]
