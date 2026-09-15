"""HTTP response schemas and mappings for shared catalog media."""

from __future__ import annotations

from datetime import date  # noqa: TC003 - Pydantic resolves this response-field annotation at runtime.
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from media_recommender.domain import Media, Movie


class ExternalIdResponse(BaseModel):
    """A provider-namespaced catalog identifier exposed by the HTTP API."""

    namespace: str
    value: str


class GenreResponse(BaseModel):
    """A normalized genre exposed by the HTTP API."""

    name: str


class ProductionCountryResponse(BaseModel):
    """A normalized production country exposed by the HTTP API."""

    code: str
    name: str


class ArtworkResponse(BaseModel):
    """A normalized artwork reference exposed by the HTTP API."""

    type: Literal["poster", "backdrop"]
    url: str
    language: str | None


class MediaResponseBase(BaseModel):
    """Shared normalized catalog facts for every media response."""

    id: str
    title: str
    original_title: str | None
    release_date: date | None
    release_year: int | None
    genres: tuple[GenreResponse, ...]
    production_countries: tuple[ProductionCountryResponse, ...]
    artwork: tuple[ArtworkResponse, ...]
    external_ids: tuple[ExternalIdResponse, ...]


class MovieResponse(MediaResponseBase):
    """Shared catalog metadata for a movie."""

    media_type: Literal["movie"]
    runtime_minutes: int | None


class TVShowResponse(MediaResponseBase):
    """Shared catalog metadata for a TV show."""

    media_type: Literal["tv_show"]
    episode_runtime_minutes: int | None


MediaResponse = Annotated[MovieResponse | TVShowResponse, Field(discriminator="media_type")]


def media_to_response(media: Media) -> MovieResponse | TVShowResponse:
    """Map normalized domain media to its explicit HTTP representation.

    :param media: Shared catalog media retrieved by the application service.
    :return: Typed HTTP response model for the concrete media kind.
    """
    shared_fields = {
        "id": str(media.id.value),
        "title": media.title,
        "original_title": media.original_title,
        "release_date": media.release_date,
        "release_year": media.release_year,
        "genres": tuple(GenreResponse(name=genre.name) for genre in media.genres),
        "production_countries": tuple(
            ProductionCountryResponse(code=country.code, name=country.name) for country in media.production_countries
        ),
        "artwork": tuple(
            ArtworkResponse(type=artwork.type.value, url=artwork.url, language=artwork.language)
            for artwork in media.artwork
        ),
        "external_ids": tuple(
            ExternalIdResponse(namespace=external_id.namespace, value=external_id.value)
            for external_id in sorted(media.external_ids, key=lambda external_id: external_id.namespace)
        ),
    }
    if isinstance(media, Movie):
        return MovieResponse.model_validate(
            {
                **shared_fields,
                "media_type": "movie",
                "runtime_minutes": media.runtime.minutes if media.runtime is not None else None,
            }
        )
    return TVShowResponse.model_validate(
        {
            **shared_fields,
            "media_type": "tv_show",
            "episode_runtime_minutes": media.episode_runtime.minutes if media.episode_runtime is not None else None,
        }
    )
