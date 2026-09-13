"""Pydantic DTOs for the subset of TMDB responses used by the application."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - Pydantic resolves this annotation at runtime.
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

TmdbId = Annotated[int, Field(strict=True, gt=0)]
TmdbRuntime = Annotated[int, Field(strict=True)]
TmdbText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
TmdbImagePath = Annotated[str, StringConstraints(pattern=r"^/[^/]+$")]


class TmdbDto(BaseModel):
    """Base model for tolerant, immutable TMDB response validation."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class TmdbMovieSearchItem(TmdbDto):
    """Movie fields returned by the TMDB movie-search endpoint."""

    id: TmdbId
    title: TmdbText
    release_date: date | None = None

    @field_validator("release_date", mode="before")
    @classmethod
    def empty_release_date_is_unknown(cls, value: object) -> object:
        """Treat TMDB's empty date representation as missing metadata.

        :param value: Raw release-date value.
        :return: Value suitable for Pydantic date validation.
        """
        return None if value == "" else value


class TmdbTvSearchItem(TmdbDto):
    """TV fields returned by the TMDB TV-search endpoint."""

    id: TmdbId
    name: TmdbText
    first_air_date: date | None = None

    @field_validator("first_air_date", mode="before")
    @classmethod
    def empty_first_air_date_is_unknown(cls, value: object) -> object:
        """Treat TMDB's empty date representation as missing metadata.

        :param value: Raw first-air-date value.
        :return: Value suitable for Pydantic date validation.
        """
        return None if value == "" else value


class TmdbMovieSearchResponse(TmdbDto):
    """Paginated movie-search response used by the TMDB client."""

    results: list[TmdbMovieSearchItem]


class TmdbTvSearchResponse(TmdbDto):
    """Paginated TV-search response used by the TMDB client."""

    results: list[TmdbTvSearchItem]


class TmdbGenre(TmdbDto):
    """Genre embedded in a TMDB detail response."""

    id: TmdbId
    name: TmdbText


class TmdbProductionCountry(TmdbDto):
    """Production country embedded in a TMDB detail response."""

    iso_3166_1: str = Field(pattern=r"^[A-Z]{2}$")
    name: TmdbText


def _empty_genres() -> list[TmdbGenre]:
    """Return an explicitly typed empty genre list.

    :return: Empty TMDB genre list.
    """
    return []


def _empty_countries() -> list[TmdbProductionCountry]:
    """Return an explicitly typed empty production-country list.

    :return: Empty TMDB production-country list.
    """
    return []


def _empty_runtimes() -> list[int]:
    """Return an explicitly typed empty episode-runtime list.

    :return: Empty runtime list.
    """
    return []


class TmdbExternalIds(TmdbDto):
    """Relevant identifiers appended to a TMDB detail response."""

    imdb_id: str | None = None
    tvdb_id: TmdbId | None = None
    wikidata_id: str | None = None


class TmdbMovieDetails(TmdbDto):
    """Movie detail payload normalized by the TMDB mapper."""

    id: TmdbId
    title: TmdbText
    original_title: str | None = None
    release_date: date | None = None
    runtime: TmdbRuntime | None = None
    genres: list[TmdbGenre] = Field(default_factory=_empty_genres)
    production_countries: list[TmdbProductionCountry] = Field(default_factory=_empty_countries)
    poster_path: TmdbImagePath | None = None
    backdrop_path: TmdbImagePath | None = None
    external_ids: TmdbExternalIds = Field(default_factory=TmdbExternalIds)

    @field_validator("release_date", mode="before")
    @classmethod
    def empty_release_date_is_unknown(cls, value: object) -> object:
        """Treat TMDB's empty date representation as missing metadata.

        :param value: Raw release-date value.
        :return: Value suitable for Pydantic date validation.
        """
        return None if value == "" else value


class TmdbTvDetails(TmdbDto):
    """TV detail payload normalized by the TMDB mapper."""

    id: TmdbId
    name: TmdbText
    original_name: str | None = None
    first_air_date: date | None = None
    episode_run_time: list[TmdbRuntime] = Field(default_factory=_empty_runtimes)
    genres: list[TmdbGenre] = Field(default_factory=_empty_genres)
    production_countries: list[TmdbProductionCountry] = Field(default_factory=_empty_countries)
    poster_path: TmdbImagePath | None = None
    backdrop_path: TmdbImagePath | None = None
    external_ids: TmdbExternalIds = Field(default_factory=TmdbExternalIds)

    @field_validator("first_air_date", mode="before")
    @classmethod
    def empty_first_air_date_is_unknown(cls, value: object) -> object:
        """Treat TMDB's empty date representation as missing metadata.

        :param value: Raw first-air-date value.
        :return: Value suitable for Pydantic date validation.
        """
        return None if value == "" else value
