"""Validated application configuration."""

from pathlib import Path
from typing import Self

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables when present."""

    model_config = SettingsConfigDict(env_prefix="MEDIA_RECOMMENDER_", frozen=True)

    database_path: Path = Path("data/media-recommender.db")

    @field_validator("database_path", mode="before")
    @classmethod
    def validate_database_path(cls, value: object) -> object:
        """Reject an empty database path before ``Path`` coercion.

        :param value: Raw configured path value.
        :return: Unmodified non-empty path value.
        :raises ValueError: If the configured path is an empty string.
        """
        if isinstance(value, str) and not value.strip():
            msg = "Database path must not be empty"
            raise ValueError(msg)
        return value

    @property
    def database_url(self: Self) -> URL:
        """Return the asynchronous SQLAlchemy URL for the SQLite database.

        :return: SQLite URL using the ``aiosqlite`` driver.
        """
        return URL.create("sqlite+aiosqlite", database=str(self.database_path))
