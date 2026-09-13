"""Tests for domain and persistence mapping."""

from datetime import date
from uuid import UUID

from media_recommender.domain import Artwork, ArtworkType, Country, ExternalId, Genre, MediaId, Movie, Runtime
from media_recommender.persistence.mapping import media_to_record, record_to_media


def test_movie_round_trips_through_detached_orm_mapping() -> None:
    """Verify mapping retains all normalized movie metadata."""
    movie = Movie(
        id=MediaId(UUID("7dc232f1-9514-48f1-a6cb-4f39e48e71b0")),
        title="Synthetic Mapping",
        original_title="Synthetic Original",
        released_on=date(2025, 1, 12),
        runtime=Runtime(104),
        genres=(Genre("Drama"), Genre("Mystery")),
        production_countries=(Country("CZ", "Czechia"),),
        artwork=(Artwork(ArtworkType.POSTER, "https://images.example.test/mapping.jpg", "en"),),
        external_ids=frozenset({ExternalId("tmdb", "501"), ExternalId("imdb", "tt0000501")}),
    )

    restored = record_to_media(media_to_record(movie))

    assert restored == movie
