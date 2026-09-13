"""Tests for provider-independent media domain types."""

from dataclasses import FrozenInstanceError
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from media_recommender.domain import (
    Artwork,
    ArtworkType,
    Country,
    ExternalId,
    Genre,
    MediaId,
    MediaType,
    Movie,
    Runtime,
    TVShow,
)

if TYPE_CHECKING:
    from collections.abc import Callable

MOVIE_RELEASE_YEAR = 2024
TV_SHOW_RELEASE_YEAR = 2022


def test_movie_exposes_normalized_metadata() -> None:
    """Verify a movie carries normalized provider-independent metadata."""
    movie = Movie(
        id=MediaId(UUID("b23ffbec-7db1-42f6-a894-cad67c847c7f")),
        title="  Synthetic Journey  ",
        original_title="  Synthetische Reise  ",
        released_on=date(2024, 3, 15),
        runtime=Runtime(112),
        genres=(Genre("Drama"),),
        production_countries=(Country("de", "Germany"),),
        artwork=(Artwork(ArtworkType.POSTER, "https://images.example.test/poster.jpg", "DE"),),
        external_ids=frozenset({ExternalId("TMDB", "123"), ExternalId("imdb", "tt0000123")}),
    )

    assert movie.media_type is MediaType.MOVIE
    assert movie.title == "Synthetic Journey"
    assert movie.original_title == "Synthetische Reise"
    assert movie.release_date == date(2024, 3, 15)
    assert movie.release_year == MOVIE_RELEASE_YEAR
    assert movie.production_countries == (Country("DE", "Germany"),)
    assert movie.artwork[0].language == "de"
    assert {external_id.namespace for external_id in movie.external_ids} == {"tmdb", "imdb"}


def test_tv_show_has_explicit_type_and_first_air_date() -> None:
    """Verify TV-show metadata is distinct from movie metadata."""
    show = TVShow(
        id=MediaId(UUID("f495db93-c1ca-424e-8cd7-e3137541df70")),
        title="Synthetic Stories",
        first_aired_on=date(2022, 9, 4),
        episode_runtime=Runtime(48),
    )

    assert show.media_type is MediaType.TV_SHOW
    assert show.release_date == date(2022, 9, 4)
    assert show.release_year == TV_SHOW_RELEASE_YEAR


def test_media_id_is_provider_independent_and_immutable() -> None:
    """Verify internal identity is UUID-based and cannot be changed."""
    media_id = MediaId.new()

    assert isinstance(media_id.value, UUID)
    with pytest.raises(FrozenInstanceError):
        media_id.value = UUID("83f75fbe-a84b-43bf-ad76-46e8fd31f014")  # type: ignore[misc]


@pytest.mark.parametrize(
    ("factory", "expected_message"),
    [
        (lambda: ExternalId(" ", "123"), "namespace"),
        (lambda: ExternalId("tmdb", " "), "value"),
        (lambda: Genre(" "), "Genre name"),
        (lambda: Country("CZE", "Czechia"), "Country code"),
        (lambda: Country("CZ", " "), "Country name"),
        (lambda: Runtime(0), "Runtime"),
        (lambda: Runtime(minutes=True), "Runtime"),
        (lambda: Artwork(ArtworkType.POSTER, "/poster.jpg"), "Artwork URL"),
        (lambda: Artwork(ArtworkType.POSTER, "https://example.test/poster.jpg", " "), "Artwork language"),
    ],
)
def test_value_objects_reject_invalid_values(factory: Callable[[], object], expected_message: str) -> None:
    """Verify invalid metadata is rejected at the domain boundary."""
    with pytest.raises(ValueError, match=expected_message):
        factory()


def test_media_rejects_duplicate_external_id_namespaces() -> None:
    """Verify one title cannot have conflicting IDs from one namespace."""
    with pytest.raises(ValueError, match="multiple external IDs"):
        Movie(
            id=MediaId.new(),
            title="Synthetic Conflict",
            external_ids=frozenset({ExternalId("tmdb", "1"), ExternalId("TMDB", "2")}),
        )


def test_unknown_release_date_has_no_release_year() -> None:
    """Verify incomplete provider metadata remains representable."""
    movie = Movie(id=MediaId.new(), title="Synthetic Unknown")

    assert movie.release_date is None
    assert movie.release_year is None
