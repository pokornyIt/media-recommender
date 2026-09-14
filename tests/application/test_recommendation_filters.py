"""Tests for deterministic provider-independent recommendation filtering."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from media_recommender.application import (
    AvailabilityCriterion,
    AvailabilitySourceKind,
    FilterReason,
    GenreMatch,
    ProductionRegion,
    RecommendationCandidate,
    RecommendationCriteria,
    RecommendationFilterResult,
    RecommendationFilterService,
    WatchRequirement,
)
from media_recommender.domain import (
    AvailabilityProvenance,
    AvailabilityType,
    Country,
    Genre,
    LibraryPresence,
    LibraryPresenceId,
    LikeState,
    MediaId,
    MediaType,
    Movie,
    Preference,
    PreferenceEffect,
    PreferenceId,
    PreferenceKind,
    ProfileId,
    Rating,
    RatingId,
    Runtime,
    SourceProvenance,
    StreamingAvailability,
    StreamingService,
    WatchStatus,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

SYNTHETIC_NOW = datetime(2026, 9, 14, 20, tzinfo=UTC)


class InMemoryRecommendationDataSource:
    """Return profile-specific synthetic candidate snapshots."""

    def __init__(
        self,
        candidates: dict[ProfileId, Sequence[RecommendationCandidate]],
        preferences: dict[ProfileId, Sequence[Preference]] | None = None,
    ) -> None:
        """Store candidates and optional persisted profile criteria."""
        self.candidates = candidates
        self.preferences = preferences or {}

    async def list_candidates(
        self,
        profile_id: ProfileId,
        media_types: frozenset[MediaType],
    ) -> Sequence[RecommendationCandidate]:
        """Return the profile's candidates restricted by catalog type."""
        candidates = self.candidates.get(profile_id, ())
        return tuple(
            candidate for candidate in candidates if not media_types or candidate.media.media_type in media_types
        )

    async def list_preferences(self, profile_id: ProfileId) -> Sequence[Preference]:
        """Return the profile's persisted preferences."""
        return self.preferences.get(profile_id, ())


def _movie(
    title: str,
    *,
    genres: tuple[str, ...] = ("Science Fiction",),
    countries: tuple[str, ...] = ("US",),
    runtime: int | None = 120,
    year: int | None = 2020,
) -> Movie:
    """Build a synthetic movie with selected filtering metadata."""
    country_names = {"US": "United States", "JP": "Japan", "FR": "France", "BR": "Brazil", "ZA": "South Africa"}
    return Movie(
        id=MediaId.new(),
        title=title,
        genres=tuple(Genre(genre) for genre in genres),
        production_countries=tuple(Country(code, country_names[code]) for code in countries),
        runtime=Runtime(runtime) if runtime is not None else None,
        released_on=date(year, 1, 1) if year is not None else None,
    )


def _streaming(media: Movie, service: str, region: str = "CZ") -> StreamingAvailability:
    """Build one synthetic subscription availability fact."""
    return StreamingAvailability(
        media_id=media.id,
        service=StreamingService(service.casefold().replace(" ", "-"), service),
        region=region,
        availability_type=AvailabilityType.SUBSCRIPTION,
        provenance=AvailabilityProvenance("tmdb", SYNTHETIC_NOW, "JustWatch"),
    )


def _library(media: Movie, profile_id: ProfileId, provider: str = "jellyfin") -> LibraryPresence:
    """Build one synthetic current local-library presence."""
    return LibraryPresence(
        id=LibraryPresenceId.new(),
        profile_id=profile_id,
        media_id=media.id,
        available=True,
        provenance=SourceProvenance(
            provider=provider,
            source_record_id=str(media.id.value),
            imported_at=SYNTHETIC_NOW,
        ),
    )


def _rating(
    media: Movie, profile_id: ProfileId, *, value: float | None = None, like: LikeState | None = None
) -> Rating:
    """Build one synthetic personal rating or reaction."""
    return Rating(
        id=RatingId.new(),
        profile_id=profile_id,
        media_id=media.id,
        value=value,
        like_state=like,
        provenance=SourceProvenance(provider="netflix", imported_at=SYNTHETIC_NOW),
    )


def _filter(
    candidates: Sequence[RecommendationCandidate],
    criteria: RecommendationCriteria,
    *,
    profile_id: ProfileId | None = None,
    preferences: Sequence[Preference] = (),
) -> RecommendationFilterResult:
    """Evaluate synthetic candidates through the public service."""
    selected_profile = profile_id or ProfileId.new()
    source = InMemoryRecommendationDataSource(
        {selected_profile: candidates},
        {selected_profile: preferences},
    )
    return asyncio.run(RecommendationFilterService(source).filter(selected_profile, criteria))


def _reason_set(result: RecommendationFilterResult, title: str) -> set[FilterReason]:
    """Return exclusion codes for one title from a filter result."""
    decision = next(item for item in result.decisions if item.media.title == title)
    return {exclusion.reason for exclusion in decision.exclusions}


def test_genre_filters_support_all_any_and_explicit_exclusions() -> None:
    """Verify combined genre semantics and structured exclusion reasons."""
    science_drama = _movie("Science Drama", genres=("Science Fiction", "Drama"))
    science_horror = _movie("Science Horror", genres=("Science Fiction", "Horror"))
    candidates = tuple(RecommendationCandidate(movie) for movie in (science_horror, science_drama))

    all_result = _filter(
        candidates,
        RecommendationCriteria(
            include_genres=frozenset({"science fiction", "drama"}),
            exclude_genres=frozenset({"horror"}),
        ),
    )
    any_result = _filter(
        candidates,
        RecommendationCriteria(
            include_genres=frozenset({"science fiction", "drama"}),
            genre_match=GenreMatch.ANY,
        ),
    )

    assert tuple(media.title for media in all_result.accepted) == ("Science Drama",)
    assert _reason_set(all_result, "Science Horror") == {
        FilterReason.GENRE_NOT_INCLUDED,
        FilterReason.GENRE_EXCLUDED,
    }
    assert tuple(media.title for media in any_result.accepted) == ("Science Drama", "Science Horror")


def test_country_region_runtime_and_release_filters_expose_unknown_metadata() -> None:
    """Verify metadata constraints reject excluded regions and unknown required values."""
    european = _movie("European", countries=("FR",), runtime=95, year=2022)
    asian = _movie("Asian", countries=("JP",), runtime=95, year=2022)
    unknown = _movie("Unknown", countries=("US",), runtime=None, year=None)
    criteria = RecommendationCriteria(
        include_countries=frozenset({"FR", "US"}),
        exclude_countries=frozenset({"JP"}),
        exclude_regions=frozenset({ProductionRegion.ASIA}),
        minimum_runtime_minutes=80,
        maximum_runtime_minutes=100,
        minimum_release_year=2020,
        maximum_release_year=2025,
        released_from=date(2021, 1, 1),
        released_until=date(2023, 1, 1),
    )

    result = _filter(tuple(RecommendationCandidate(movie) for movie in (unknown, asian, european)), criteria)

    assert tuple(media.title for media in result.accepted) == ("European",)
    assert FilterReason.PRODUCTION_REGION_EXCLUDED in _reason_set(result, "Asian")
    assert FilterReason.PRODUCTION_COUNTRY_EXCLUDED in _reason_set(result, "Asian")
    assert _reason_set(result, "Unknown") == {FilterReason.RUNTIME_UNKNOWN, FilterReason.RELEASE_DATE_UNKNOWN}


def test_watch_and_rating_filters_preserve_profile_specific_unknown_state() -> None:
    """Verify explicit unwatched differs from unknown and dislikes remain independent."""
    profile_id = ProfileId.new()
    explicit = _movie("Explicit Unwatched")
    unknown = _movie("Unknown Watch State")
    disliked = _movie("Disliked")
    candidates = (
        RecommendationCandidate(
            explicit, watch_status=WatchStatus.UNWATCHED, ratings=(_rating(explicit, profile_id, value=8),)
        ),
        RecommendationCandidate(unknown, watch_status=WatchStatus.UNKNOWN),
        RecommendationCandidate(
            disliked,
            watch_status=WatchStatus.UNWATCHED,
            ratings=(_rating(disliked, profile_id, like=LikeState.DISLIKED),),
        ),
    )
    criteria = RecommendationCriteria(
        watch=WatchRequirement.UNWATCHED,
        minimum_personal_rating=7,
        excluded_like_states=frozenset({LikeState.DISLIKED}),
    )

    result = _filter(candidates, criteria, profile_id=profile_id)

    assert tuple(media.title for media in result.accepted) == ("Explicit Unwatched",)
    assert FilterReason.WATCH_STATUS_UNKNOWN in _reason_set(result, "Unknown Watch State")
    assert FilterReason.RATING_UNKNOWN in _reason_set(result, "Unknown Watch State")
    assert FilterReason.LIKE_STATE_EXCLUDED in _reason_set(result, "Disliked")


def test_streaming_and_jellyfin_availability_use_or_and_region_semantics() -> None:
    """Verify Netflix CZ or Jellyfin accepts either source but not another region."""
    profile_id = ProfileId.new()
    netflix_cz = _movie("Netflix CZ")
    netflix_sk = _movie("Netflix SK")
    jellyfin = _movie("Jellyfin")
    unavailable = _movie("Unavailable")
    candidates = (
        RecommendationCandidate(netflix_cz, streaming_availability=(_streaming(netflix_cz, "Netflix", "CZ"),)),
        RecommendationCandidate(netflix_sk, streaming_availability=(_streaming(netflix_sk, "Netflix", "SK"),)),
        RecommendationCandidate(jellyfin, library_presence=(_library(jellyfin, profile_id),)),
        RecommendationCandidate(unavailable),
    )
    criteria = RecommendationCriteria(
        availability_any_of=(
            AvailabilityCriterion(
                kind=AvailabilitySourceKind.STREAMING,
                provider="Netflix",
                region="CZ",
                source_provider="tmdb",
                availability_types=frozenset({AvailabilityType.SUBSCRIPTION}),
            ),
            AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider="Jellyfin"),
        )
    )

    result = _filter(candidates, criteria, profile_id=profile_id)

    assert tuple(media.title for media in result.accepted) == ("Jellyfin", "Netflix CZ")
    assert _reason_set(result, "Netflix SK") == {FilterReason.AVAILABILITY_NOT_FOUND}
    assert _reason_set(result, "Unavailable") == {FilterReason.AVAILABILITY_NOT_FOUND}


def test_persisted_exclusions_apply_but_ranking_preferences_do_not() -> None:
    """Verify profile exclusions are hard constraints while preferences remain for ranking."""
    profile_id = ProfileId.new()
    horror = _movie("Horror", genres=("Horror",))
    drama = _movie("Long Drama", genres=("Drama",), runtime=180)
    preferences = (
        Preference(
            id=PreferenceId.new(),
            profile_id=profile_id,
            kind=PreferenceKind.GENRE,
            effect=PreferenceEffect.EXCLUDE,
            value="Horror",
        ),
        Preference(
            id=PreferenceId.new(),
            profile_id=profile_id,
            kind=PreferenceKind.RUNTIME_MINUTES,
            effect=PreferenceEffect.PREFER,
            maximum=120,
        ),
    )

    result = _filter(
        (RecommendationCandidate(horror), RecommendationCandidate(drama)),
        RecommendationCriteria(),
        profile_id=profile_id,
        preferences=preferences,
    )

    assert tuple(media.title for media in result.accepted) == ("Long Drama",)
    assert _reason_set(result, "Horror") == {FilterReason.PROFILE_PREFERENCE_EXCLUDED}


def test_reference_use_case_is_deterministic_without_ranking_or_ai() -> None:
    """Verify the project reference request produces the same factual candidate set repeatedly."""
    profile_id = ProfileId.new()
    match = _movie("A Matching Movie", countries=("US",), runtime=125, year=2024)
    library_match = _movie("B Library Match", countries=("FR",), runtime=140, year=2023)
    watched = _movie("C Watched", countries=("US",), runtime=110, year=2024)
    excluded = _movie("D Excluded", genres=("Science Fiction", "Horror"), countries=("JP",), runtime=90, year=2024)
    candidates = (
        RecommendationCandidate(match, streaming_availability=(_streaming(match, "Netflix"),)),
        RecommendationCandidate(library_match, library_presence=(_library(library_match, profile_id),)),
        RecommendationCandidate(
            watched,
            watch_status=WatchStatus.WATCHED,
            streaming_availability=(_streaming(watched, "Netflix"),),
        ),
        RecommendationCandidate(excluded, streaming_availability=(_streaming(excluded, "Netflix"),)),
    )
    criteria = RecommendationCriteria(
        media_types=frozenset({MediaType.MOVIE}),
        include_genres=frozenset({"Science Fiction"}),
        exclude_genres=frozenset({"Horror", "Comedy"}),
        exclude_regions=frozenset({ProductionRegion.AFRICA, ProductionRegion.ASIA, ProductionRegion.SOUTH_AMERICA}),
        maximum_runtime_minutes=150,
        watch=WatchRequirement.NOT_WATCHED,
        availability_any_of=(
            AvailabilityCriterion(kind=AvailabilitySourceKind.STREAMING, provider="Netflix", region="CZ"),
            AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider="Jellyfin"),
        ),
    )
    source = InMemoryRecommendationDataSource({profile_id: candidates})
    service = RecommendationFilterService(source)

    first = asyncio.run(service.filter(profile_id, criteria))
    second = asyncio.run(service.filter(profile_id, criteria))

    assert first == second
    assert tuple(media.title for media in first.accepted) == ("A Matching Movie", "B Library Match")
    assert first.accepted != ()


def test_no_matching_candidates_returns_decisions_without_results() -> None:
    """Verify an empty candidate set remains an explicit deterministic outcome."""
    movie = _movie("Drama", genres=("Drama",))

    result = _filter(
        (RecommendationCandidate(movie),),
        RecommendationCriteria(include_genres=frozenset({"Comedy"})),
    )

    assert result.accepted == ()
    assert len(result.decisions) == 1
    assert _reason_set(result, "Drama") == {FilterReason.GENRE_NOT_INCLUDED}
