"""Pydantic DTOs for the Jellyfin API subset used by library synchronization."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator

JellyfinText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class JellyfinDto(BaseModel):
    """Base model for tolerant, immutable Jellyfin response validation."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class JellyfinUser(JellyfinDto):
    """Selected Jellyfin user returned by the supported Users endpoint."""

    id: JellyfinText = Field(alias="Id")
    name: JellyfinText = Field(alias="Name")


class JellyfinUserData(JellyfinDto):
    """Per-user playback fields embedded in a Jellyfin library item."""

    played: bool | None = Field(default=None, alias="Played", strict=True)
    play_count: int | None = Field(default=None, alias="PlayCount", strict=True, ge=0)
    last_played_date: datetime | None = Field(default=None, alias="LastPlayedDate")

    @field_validator("last_played_date")
    @classmethod
    def last_played_date_must_be_aware(cls, value: datetime | None) -> datetime | None:
        """Reject ambiguous provider timestamps without an offset.

        :param value: Parsed optional provider timestamp.
        :return: Valid timezone-aware timestamp or ``None``.
        :raises ValueError: If the timestamp has no UTC offset.
        """
        if value is not None and value.utcoffset() is None:
            msg = "Last-played date must be timezone-aware"
            raise ValueError(msg)
        return value


class JellyfinLibraryItem(JellyfinDto):
    """Validated Jellyfin movie or series used by synchronization."""

    id: JellyfinText = Field(alias="Id")
    name: JellyfinText = Field(alias="Name")
    original_title: str | None = Field(default=None, alias="OriginalTitle")
    item_type: Literal["Movie", "Series"] = Field(alias="Type")
    production_year: int | None = Field(default=None, alias="ProductionYear", strict=True, ge=1870)
    runtime_ticks: int | None = Field(default=None, alias="RunTimeTicks", strict=True, gt=0)
    provider_ids: dict[str, str] = Field(default_factory=dict, alias="ProviderIds")
    user_data: JellyfinUserData | None = Field(default=None, alias="UserData")


class JellyfinItemsResponse(JellyfinDto):
    """Raw item collection allowing malformed entries to be reported individually."""

    items: list[dict[str, JsonValue]] = Field(alias="Items")
