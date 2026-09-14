"""Explicit mapping from TMDB DTOs into provider-independent models."""

from __future__ import annotations

from typing import TYPE_CHECKING

from media_recommender.application import AvailabilityOffer, MediaSearchResult
from media_recommender.domain import (
    Artwork,
    ArtworkType,
    AvailabilityType,
    Country,
    ExternalId,
    Genre,
    MediaId,
    MediaType,
    Movie,
    Runtime,
    StreamingService,
    TVShow,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from media_recommender.config import TmdbSettings
    from media_recommender.integrations.tmdb.models import (
        TmdbExternalIds,
        TmdbMovieDetails,
        TmdbMovieSearchItem,
        TmdbProductionCountry,
        TmdbTvDetails,
        TmdbTvSearchItem,
        TmdbWatchProviderResponse,
    )


def map_movie_search_result(item: TmdbMovieSearchItem) -> MediaSearchResult:
    """Map a TMDB movie-search item to an application summary.

    :param item: Validated TMDB movie-search item.
    :return: Provider-independent movie summary.
    """
    return MediaSearchResult(
        external_id=ExternalId("tmdb", str(item.id)),
        media_type=MediaType.MOVIE,
        title=item.title,
        release_year=item.release_date.year if item.release_date is not None else None,
    )


def map_tv_search_result(item: TmdbTvSearchItem) -> MediaSearchResult:
    """Map a TMDB TV-search item to an application summary.

    :param item: Validated TMDB TV-search item.
    :return: Provider-independent TV summary.
    """
    return MediaSearchResult(
        external_id=ExternalId("tmdb", str(item.id)),
        media_type=MediaType.TV_SHOW,
        title=item.name,
        release_year=item.first_air_date.year if item.first_air_date is not None else None,
    )


def map_movie_details(item: TmdbMovieDetails, settings: TmdbSettings) -> Movie:
    """Map TMDB movie details to a normalized domain movie.

    :param item: Validated TMDB movie details.
    :param settings: TMDB image configuration.
    :return: Provider-independent movie metadata.
    """
    return Movie(
        id=MediaId.new(),
        title=item.title,
        original_title=_optional_text(item.original_title),
        released_on=item.release_date,
        runtime=_runtime(item.runtime),
        genres=tuple(Genre(genre.name) for genre in item.genres),
        production_countries=_countries(item.production_countries),
        artwork=_artwork(item.poster_path, item.backdrop_path, settings),
        external_ids=_external_ids(item.id, item.external_ids),
    )


def map_tv_details(item: TmdbTvDetails, settings: TmdbSettings) -> TVShow:
    """Map TMDB TV details to a normalized domain TV show.

    :param item: Validated TMDB TV details.
    :param settings: TMDB image configuration.
    :return: Provider-independent TV-show metadata.
    """
    return TVShow(
        id=MediaId.new(),
        title=item.name,
        original_title=_optional_text(item.original_name),
        first_aired_on=item.first_air_date,
        episode_runtime=_runtime(next((runtime for runtime in item.episode_run_time if runtime > 0), None)),
        genres=tuple(Genre(genre.name) for genre in item.genres),
        production_countries=_countries(item.production_countries),
        artwork=_artwork(item.poster_path, item.backdrop_path, settings),
        external_ids=_external_ids(item.id, item.external_ids),
    )


def map_watch_provider_offers(
    response: TmdbWatchProviderResponse,
    region: str,
) -> tuple[AvailabilityOffer, ...]:
    """Map one TMDB region to deterministic provider-independent offers.

    :param response: Validated TMDB watch-provider response.
    :param region: Normalized region selected by the caller.
    :return: Available service and access-type combinations.
    """
    regional = response.results.get(region)
    if regional is None:
        return ()

    categories = (
        (AvailabilityType.SUBSCRIPTION, regional.flatrate),
        (AvailabilityType.RENT, regional.rent),
        (AvailabilityType.BUY, regional.buy),
        (AvailabilityType.FREE, regional.free),
        (AvailabilityType.ADS, regional.ads),
    )
    offers = {
        (str(provider.provider_id), availability_type): AvailabilityOffer(
            service=StreamingService(str(provider.provider_id), provider.provider_name),
            availability_type=availability_type,
        )
        for availability_type, providers in categories
        for provider in providers
    }
    return tuple(offers[key] for key in sorted(offers, key=lambda item: (item[1].value, item[0])))


def _optional_text(value: str | None) -> str | None:
    """Normalize missing and whitespace-only optional text.

    :param value: Optional provider text.
    :return: Stripped text, or ``None`` when absent or empty.
    """
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _runtime(value: int | None) -> Runtime | None:
    """Map a positive provider runtime when available.

    :param value: Runtime in minutes.
    :return: Normalized runtime, or ``None`` for missing/non-positive values.
    """
    return Runtime(value) if value is not None and value > 0 else None


def _countries(items: Iterable[TmdbProductionCountry]) -> tuple[Country, ...]:
    """Map production countries while preserving provider order.

    :param items: Validated TMDB production countries.
    :return: Normalized country values.
    """
    return tuple(Country(code=item.iso_3166_1, name=item.name) for item in items)


def _external_ids(tmdb_id: int, ids: TmdbExternalIds) -> frozenset[ExternalId]:
    """Map relevant external media identifiers.

    :param tmdb_id: TMDB media identity.
    :param ids: External identifiers appended by TMDB.
    :return: Namespaced provider-independent identities.
    """
    values = {
        ExternalId("tmdb", str(tmdb_id)),
        *(
            ExternalId(namespace, str(value))
            for namespace, value in (
                ("imdb", ids.imdb_id),
                ("tvdb", ids.tvdb_id),
                ("wikidata", ids.wikidata_id),
            )
            if value is not None and str(value).strip()
        ),
    }
    return frozenset(values)


def _artwork(
    poster_path: str | None,
    backdrop_path: str | None,
    settings: TmdbSettings,
) -> tuple[Artwork, ...]:
    """Build absolute artwork references from validated TMDB paths.

    :param poster_path: Optional TMDB poster path.
    :param backdrop_path: Optional TMDB backdrop path.
    :param settings: TMDB image URL and size settings.
    :return: Available normalized artwork references.
    """
    artwork: list[Artwork] = []
    if poster_path is not None:
        artwork.append(
            Artwork(
                type=ArtworkType.POSTER,
                url=_image_url(settings, settings.poster_size, poster_path),
            )
        )
    if backdrop_path is not None:
        artwork.append(
            Artwork(
                type=ArtworkType.BACKDROP,
                url=_image_url(settings, settings.backdrop_size, backdrop_path),
            )
        )
    return tuple(artwork)


def _image_url(settings: TmdbSettings, size: str, path: str) -> str:
    """Compose an absolute TMDB image URL.

    :param settings: TMDB image base URL.
    :param size: Configured TMDB image size.
    :param path: Validated TMDB image path.
    :return: Absolute artwork URL.
    """
    return f"{str(settings.image_base_url).rstrip('/')}/{size}/{path.lstrip('/')}"
