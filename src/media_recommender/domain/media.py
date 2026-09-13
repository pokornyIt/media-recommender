"""Domain types describing normalized movie and TV-show metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from datetime import date

COUNTRY_CODE_LENGTH = 2


def _empty_external_ids() -> frozenset[ExternalId]:
    """Return an explicitly typed empty external-ID collection."""
    return frozenset()


class MediaType(StrEnum):
    """Kind of title stored in the shared media catalog."""

    MOVIE = "movie"
    TV_SHOW = "tv_show"


class ArtworkType(StrEnum):
    """Purpose of an artwork image."""

    POSTER = "poster"
    BACKDROP = "backdrop"


@dataclass(frozen=True, slots=True)
class MediaId:
    """Stable internal identity independent of external providers."""

    value: UUID

    @classmethod
    def new(cls) -> MediaId:
        """Create a new random internal media identity.

        :return: Newly generated media identity.
        """
        return cls(uuid4())


@dataclass(frozen=True, slots=True)
class ExternalId:
    """Identifier assigned to a media item by an external namespace."""

    namespace: str
    value: str

    def __post_init__(self) -> None:
        """Validate and normalize the external identifier."""
        namespace = self.namespace.strip().lower()
        value = self.value.strip()
        if not namespace:
            msg = "External ID namespace must not be empty"
            raise ValueError(msg)
        if not value:
            msg = "External ID value must not be empty"
            raise ValueError(msg)
        object.__setattr__(self, "namespace", namespace)
        object.__setattr__(self, "value", value)


@dataclass(frozen=True, slots=True)
class Genre:
    """Provider-independent genre name."""

    name: str

    def __post_init__(self) -> None:
        """Validate and normalize the genre name."""
        name = self.name.strip()
        if not name:
            msg = "Genre name must not be empty"
            raise ValueError(msg)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class Country:
    """Production country represented by an ISO 3166-1 alpha-2 code."""

    code: str
    name: str

    def __post_init__(self) -> None:
        """Validate and normalize the country code and display name."""
        code = self.code.strip().upper()
        name = self.name.strip()
        if len(code) != COUNTRY_CODE_LENGTH or not code.isascii() or not code.isalpha():
            msg = "Country code must be a two-letter ISO 3166-1 alpha-2 code"
            raise ValueError(msg)
        if not name:
            msg = "Country name must not be empty"
            raise ValueError(msg)
        object.__setattr__(self, "code", code)
        object.__setattr__(self, "name", name)


@dataclass(frozen=True, slots=True)
class Runtime:
    """Positive media runtime expressed in whole minutes."""

    minutes: int

    def __post_init__(self) -> None:
        """Reject non-positive or non-integer runtime values."""
        if isinstance(self.minutes, bool) or self.minutes <= 0:
            msg = "Runtime must be a positive whole number of minutes"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Artwork:
    """Absolute reference to normalized media artwork."""

    type: ArtworkType
    url: str
    language: str | None = None

    def __post_init__(self) -> None:
        """Validate the artwork URL and normalize its optional language."""
        url = self.url.strip()
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            msg = "Artwork URL must be an absolute HTTP or HTTPS URL"
            raise ValueError(msg)

        language = self.language.strip().lower() if self.language is not None else None
        if language == "":
            msg = "Artwork language must not be empty"
            raise ValueError(msg)
        object.__setattr__(self, "url", url)
        object.__setattr__(self, "language", language)


@dataclass(frozen=True, slots=True, kw_only=True)
class _MediaMetadata:
    """Metadata shared by movies and TV shows."""

    id: MediaId
    title: str
    original_title: str | None = None
    genres: tuple[Genre, ...] = ()
    production_countries: tuple[Country, ...] = ()
    artwork: tuple[Artwork, ...] = ()
    external_ids: frozenset[ExternalId] = field(default_factory=_empty_external_ids)

    def __post_init__(self) -> None:
        """Validate common metadata invariants and normalize titles."""
        title = self.title.strip()
        original_title = self.original_title.strip() if self.original_title is not None else None
        if not title:
            msg = "Media title must not be empty"
            raise ValueError(msg)
        if original_title == "":
            msg = "Original title must not be empty"
            raise ValueError(msg)

        namespaces = [external_id.namespace for external_id in self.external_ids]
        if len(namespaces) != len(set(namespaces)):
            msg = "A media item must not have multiple external IDs in one namespace"
            raise ValueError(msg)

        object.__setattr__(self, "title", title)
        object.__setattr__(self, "original_title", original_title)

    @property
    def release_year(self) -> int | None:
        """Return the year of the title's applicable release date.

        :return: Release year, or ``None`` when the release date is unknown.
        """
        release_date = self.release_date
        return release_date.year if release_date is not None else None

    @property
    def release_date(self) -> date | None:
        """Return the title's applicable release date.

        :return: Movie release date or TV-show first air date, when known.
        :raises NotImplementedError: If a concrete media type does not define its release date.
        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True, kw_only=True)
class Movie(_MediaMetadata):
    """Normalized metadata for a movie in the shared catalog."""

    released_on: date | None = None
    runtime: Runtime | None = None
    media_type: MediaType = field(default=MediaType.MOVIE, init=False)

    @property
    def release_date(self) -> date | None:
        """Return the movie's theatrical or primary release date.

        :return: Known movie release date.
        """
        return self.released_on


@dataclass(frozen=True, slots=True, kw_only=True)
class TVShow(_MediaMetadata):
    """Normalized metadata for a TV show in the shared catalog."""

    first_aired_on: date | None = None
    episode_runtime: Runtime | None = None
    media_type: MediaType = field(default=MediaType.TV_SHOW, init=False)

    @property
    def release_date(self) -> date | None:
        """Return the TV show's first air date.

        :return: Known first air date.
        """
        return self.first_aired_on


type Media = Movie | TVShow
