"""Tests for validated application settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import SecretStr, ValidationError

from media_recommender.config import ProviderHttpSettings, ProviderSettings, Settings

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


def test_provider_settings_mask_api_token() -> None:
    """Verify provider credentials do not appear in normal representations."""
    settings = ProviderSettings.model_validate(
        {"base_url": "https://provider.invalid", "api_token": SecretStr("synthetic-secret")}
    )

    assert "synthetic-secret" not in repr(settings)
    assert "synthetic-secret" not in str(settings)
    assert settings.api_token.get_secret_value() == "synthetic-secret"


def test_provider_http_timeouts_must_be_positive() -> None:
    """Verify invalid timeout configuration is rejected before client creation."""
    with pytest.raises(ValidationError):
        ProviderHttpSettings(read_timeout_seconds=0)


def test_provider_api_token_must_not_be_empty() -> None:
    """Verify empty provider credentials are rejected during configuration."""
    with pytest.raises(ValidationError):
        ProviderSettings.model_validate({"base_url": "https://provider.invalid", "api_token": ""})
