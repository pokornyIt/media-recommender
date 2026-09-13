"""Tests for explicit TMDB-to-domain metadata mapping."""

from __future__ import annotations

from datetime import date

from pydantic import SecretStr

from media_recommender.config import TmdbSettings
from media_recommender.domain import ArtworkType, ExternalId, MediaType
from media_recommender.integrations.tmdb.mapper import (
    map_movie_details,
    map_movie_search_result,
    map_tv_details,
    map_tv_search_result,
)
from media_recommender.integrations.tmdb.models import (
    TmdbMovieDetails,
    TmdbMovieSearchItem,
    TmdbTvDetails,
    TmdbTvSearchItem,
)


def _settings() -> TmdbSettings:
    """Return synthetic TMDB settings.

    :return: Settings containing no real credentials.
    """
    return TmdbSettings.model_validate({"api_token": SecretStr("synthetic-token")})


def test_movie_search_result_is_provider_independent() -> None:
    """Verify movie search fields are normalized into the application summary."""
    release_year = 2024
    item = TmdbMovieSearchItem.model_validate(
        {
            "id": 101,
            "title": "Synthetic Movie",
            "release_date": f"{release_year}-03-02",
            "provider_only": "ignored",
        }
    )

    result = map_movie_search_result(item)

    assert result.external_id == ExternalId("tmdb", "101")
    assert result.media_type is MediaType.MOVIE
    assert result.title == "Synthetic Movie"
    assert result.release_year == release_year


def test_tv_search_result_handles_missing_air_date() -> None:
    """Verify TV search normalization keeps an unknown year explicit."""
    item = TmdbTvSearchItem.model_validate({"id": 202, "name": "Synthetic Series", "first_air_date": ""})

    result = map_tv_search_result(item)

    assert result.external_id == ExternalId("tmdb", "202")
    assert result.media_type is MediaType.TV_SHOW
    assert result.release_year is None


def test_movie_details_include_countries_artwork_and_external_ids() -> None:
    """Verify complete movie details map all Phase 1 metadata."""
    runtime_minutes = 126
    item = TmdbMovieDetails.model_validate(
        {
            "id": 303,
            "title": "Localized Movie",
            "original_title": "Original Movie",
            "release_date": "2023-05-06",
            "runtime": runtime_minutes,
            "genres": [{"id": 1, "name": "Drama"}, {"id": 2, "name": "Mystery"}],
            "production_countries": [
                {"iso_3166_1": "CZ", "name": "Czech Republic"},
                {"iso_3166_1": "DE", "name": "Germany"},
            ],
            "poster_path": "/poster.jpg",
            "backdrop_path": "/backdrop.jpg",
            "external_ids": {"imdb_id": "tt0000303", "wikidata_id": "Q303"},
        }
    )

    movie = map_movie_details(item, _settings())

    assert movie.title == "Localized Movie"
    assert movie.original_title == "Original Movie"
    assert movie.released_on == date(2023, 5, 6)
    assert movie.runtime is not None
    assert movie.runtime.minutes == runtime_minutes
    assert [genre.name for genre in movie.genres] == ["Drama", "Mystery"]
    assert [(country.code, country.name) for country in movie.production_countries] == [
        ("CZ", "Czech Republic"),
        ("DE", "Germany"),
    ]
    assert [(artwork.type, artwork.url) for artwork in movie.artwork] == [
        (ArtworkType.POSTER, "https://image.tmdb.org/t/p/w500/poster.jpg"),
        (ArtworkType.BACKDROP, "https://image.tmdb.org/t/p/w1280/backdrop.jpg"),
    ]
    assert movie.external_ids == frozenset(
        {
            ExternalId("tmdb", "303"),
            ExternalId("imdb", "tt0000303"),
            ExternalId("wikidata", "Q303"),
        }
    )


def test_tv_details_handle_partial_optional_metadata() -> None:
    """Verify missing metadata and unusable runtimes remain explicitly unknown."""
    item = TmdbTvDetails.model_validate(
        {
            "id": 404,
            "name": "Synthetic Series",
            "original_name": " ",
            "first_air_date": "",
            "episode_run_time": [0, -1],
            "external_ids": {"tvdb_id": 4404},
        }
    )

    show = map_tv_details(item, _settings())

    assert show.original_title is None
    assert show.first_aired_on is None
    assert show.episode_runtime is None
    assert show.genres == ()
    assert show.production_countries == ()
    assert show.artwork == ()
    assert show.external_ids == frozenset({ExternalId("tmdb", "404"), ExternalId("tvdb", "4404")})
