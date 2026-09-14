"""Tests for deterministic recommendation ranking and explanations."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from media_recommender.application import (
    AvailabilityCriterion,
    AvailabilitySourceKind,
    ConstraintMatchKind,
    RankingReasonKind,
    RankingWeights,
    RecommendationCandidate,
    RecommendationCriteria,
    RecommendationResult,
    RecommendationService,
    RecommendationWarningKind,
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
    from collections.abc import Callable, Sequence

    from media_recommender.domain import MediaType

SYNTHETIC_NOW = datetime(2026, 9, 14, 20, tzinfo=UTC)
DISLIKE_POINTS = -40
PERSONAL_RATING = 7


class InMemoryRecommendationDataSource:
    """Provide one immutable synthetic recommendation snapshot."""

    def __init__(
        self,
        candidates: Sequence[RecommendationCandidate],
        preferences: Sequence[Preference] = (),
    ) -> None:
        """Store synthetic candidates and preferences."""
        self.candidates = tuple(candidates)
        self.preferences = tuple(preferences)
        self.candidate_loads = 0

    async def list_candidates(
        self,
        profile_id: ProfileId,
        media_types: frozenset[MediaType],
    ) -> Sequence[RecommendationCandidate]:
        """Return candidates matching an optional media-type pushdown."""
        del profile_id
        self.candidate_loads += 1
        return tuple(
            candidate for candidate in self.candidates if not media_types or candidate.media.media_type in media_types
        )

    async def list_preferences(self, profile_id: ProfileId) -> Sequence[Preference]:
        """Return persisted synthetic preferences."""
        del profile_id
        return self.preferences


def _movie(
    title: str,
    *,
    genres: tuple[str, ...] = ("Science Fiction",),
    country: str = "US",
    runtime: int | None = 120,
    year: int | None = 2024,
) -> Movie:
    """Build a synthetic movie with selectable recommendation facts."""
    country_names = {"US": "United States", "FR": "France", "JP": "Japan"}
    return Movie(
        id=MediaId.new(),
        title=title,
        genres=tuple(Genre(name) for name in genres),
        production_countries=(Country(country, country_names[country]),),
        runtime=Runtime(runtime) if runtime is not None else None,
        released_on=date(year, 1, 1) if year is not None else None,
    )


def _preference(  # noqa: PLR0913 - mirrors the typed preference value and bounds.
    profile_id: ProfileId,
    kind: PreferenceKind,
    *,
    value: str | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
    effect: PreferenceEffect = PreferenceEffect.PREFER,
) -> Preference:
    """Build one synthetic persisted preference."""
    return Preference(
        id=PreferenceId.new(),
        profile_id=profile_id,
        kind=kind,
        effect=effect,
        value=value,
        minimum=minimum,
        maximum=maximum,
    )


def _rating(
    movie: Movie,
    profile_id: ProfileId,
    *,
    value: float | None = None,
    like_state: LikeState | None = None,
) -> Rating:
    """Build one synthetic profile-owned rating or reaction."""
    return Rating(
        id=RatingId.new(),
        profile_id=profile_id,
        media_id=movie.id,
        value=value,
        like_state=like_state,
        provenance=SourceProvenance("netflix", SYNTHETIC_NOW),
    )


def _library(movie: Movie, profile_id: ProfileId) -> LibraryPresence:
    """Build synthetic Jellyfin library presence."""
    return LibraryPresence(
        id=LibraryPresenceId.new(),
        profile_id=profile_id,
        media_id=movie.id,
        available=True,
        provenance=SourceProvenance("jellyfin", SYNTHETIC_NOW, str(movie.id.value)),
    )


def _streaming(movie: Movie, service: str = "Netflix", region: str = "CZ") -> StreamingAvailability:
    """Build synthetic regional subscription availability."""
    return StreamingAvailability(
        media_id=movie.id,
        service=StreamingService(service.casefold(), service),
        region=region,
        availability_type=AvailabilityType.SUBSCRIPTION,
        provenance=AvailabilityProvenance("tmdb", SYNTHETIC_NOW, "JustWatch"),
    )


def _recommend(
    candidates: Sequence[RecommendationCandidate],
    criteria: RecommendationCriteria | None = None,
    *,
    profile_id: ProfileId | None = None,
    preferences: Sequence[Preference] = (),
    weights: RankingWeights | None = None,
) -> tuple[RecommendationResult, InMemoryRecommendationDataSource]:
    """Run the public recommendation service over synthetic state."""
    source = InMemoryRecommendationDataSource(candidates, preferences)
    selected_profile = profile_id or ProfileId.new()
    selected_criteria = criteria or RecommendationCriteria()
    result = asyncio.run(RecommendationService(source, weights).recommend(selected_profile, selected_criteria))
    return result, source


def test_default_ranking_weights_are_documented_values() -> None:
    """Verify default score magnitudes remain part of the tested contract."""
    assert RankingWeights().values == (100, 5, 30, 30)


@pytest.mark.parametrize(
    "factory",
    [lambda: RankingWeights(liked=-1), lambda: RankingWeights(rating_point=True)],
)
def test_ranking_weights_reject_invalid_magnitudes(factory: Callable[[], RankingWeights]) -> None:
    """Verify configurable score magnitudes remain predictable integers."""
    with pytest.raises(ValueError, match="non-negative integers"):
        factory()


def test_hard_filters_run_before_preferences_and_cannot_be_weakened() -> None:
    """Verify a highly preferred candidate remains excluded by a hard constraint."""
    profile_id = ProfileId.new()
    horror = _movie("Preferred Horror", genres=("Science Fiction", "Horror"))
    drama = _movie("Allowed Drama", genres=("Science Fiction", "Drama"))
    result, source = _recommend(
        (RecommendationCandidate(horror), RecommendationCandidate(drama)),
        RecommendationCriteria(exclude_genres=frozenset({"Horror"})),
        profile_id=profile_id,
        preferences=(_preference(profile_id, PreferenceKind.GENRE, value="Horror"),),
    )

    assert tuple(item.media.title for item in result.recommendations) == ("Allowed Drama",)
    assert tuple(media.title for media in result.filter_result.accepted) == ("Allowed Drama",)
    assert source.candidate_loads == 1


def test_matching_preferences_score_and_rank_candidates_deterministically() -> None:
    """Verify explicit genre and runtime preferences have additive documented weights."""
    profile_id = ProfileId.new()
    both = _movie("Both", genres=("Science Fiction", "Drama"), runtime=95)
    genre_only = _movie("Genre Only", genres=("Science Fiction", "Drama"), runtime=150)
    neither = _movie("Neither", genres=("Science Fiction",), runtime=150)
    preferences = (
        _preference(profile_id, PreferenceKind.GENRE, value="Drama"),
        _preference(profile_id, PreferenceKind.RUNTIME_MINUTES, maximum=100),
    )

    result, _ = _recommend(
        tuple(RecommendationCandidate(movie) for movie in (neither, genre_only, both)),
        profile_id=profile_id,
        preferences=preferences,
    )

    assert tuple((item.media.title, item.rank, item.score) for item in result.recommendations) == (
        ("Both", 1, 200),
        ("Genre Only", 2, 100),
        ("Neither", 3, 0),
    )
    assert tuple(reason.kind for reason in result.recommendations[0].ranking_reasons) == (
        RankingReasonKind.PROFILE_PREFERENCE,
        RankingReasonKind.PROFILE_PREFERENCE,
    )


def test_provider_preference_can_prioritize_jellyfin_over_streaming() -> None:
    """Verify a provider preference scores matching local availability."""
    profile_id = ProfileId.new()
    local = _movie("Local")
    streaming = _movie("Streaming")
    result, _ = _recommend(
        (
            RecommendationCandidate(local, library_presence=(_library(local, profile_id),)),
            RecommendationCandidate(streaming, streaming_availability=(_streaming(streaming),)),
        ),
        profile_id=profile_id,
        preferences=(_preference(profile_id, PreferenceKind.PROVIDER, value="Jellyfin"),),
    )

    assert tuple((item.media.title, item.score) for item in result.recommendations) == (
        ("Local", 100),
        ("Streaming", 0),
    )
    assert result.recommendations[0].availability[0].kind is AvailabilitySourceKind.LOCAL_LIBRARY


def test_ratings_and_reactions_use_configurable_transparent_weights() -> None:
    """Verify numeric ratings and explicit reactions produce signed score reasons."""
    profile_id = ProfileId.new()
    liked = _movie("Liked")
    disliked = _movie("Disliked")
    result, _ = _recommend(
        (
            RecommendationCandidate(liked, ratings=(_rating(liked, profile_id, value=8, like_state=LikeState.LIKED),)),
            RecommendationCandidate(
                disliked,
                ratings=(_rating(disliked, profile_id, value=9, like_state=LikeState.DISLIKED),),
            ),
        ),
        profile_id=profile_id,
        weights=RankingWeights(rating_point=10, liked=25, disliked_penalty=40),
    )

    assert tuple((item.media.title, item.score) for item in result.recommendations) == (
        ("Liked", 105),
        ("Disliked", 50),
    )
    assert result.recommendations[1].ranking_reasons[-1].points == DISLIKE_POINTS


def test_default_ties_and_unknown_metadata_are_stable_and_explicit() -> None:
    """Verify no-signal ranking uses stable ties and exposes missing facts."""
    beta = _movie("beta", runtime=None, year=None)
    alpha = _movie("Alpha", runtime=None, year=None)
    candidates = (RecommendationCandidate(beta), RecommendationCandidate(alpha))

    first, _ = _recommend(candidates)
    second, _ = _recommend(tuple(reversed(candidates)))

    assert tuple(item.media.title for item in first.recommendations) == ("Alpha", "beta")
    assert first == second
    assert first.recommendations[0].score == 0
    assert {warning.kind for warning in first.recommendations[0].warnings} == {
        RecommendationWarningKind.RUNTIME_UNKNOWN,
        RecommendationWarningKind.RELEASE_DATE_UNKNOWN,
        RecommendationWarningKind.WATCH_STATUS_UNKNOWN,
        RecommendationWarningKind.PERSONAL_RATING_UNKNOWN,
        RecommendationWarningKind.AVAILABILITY_UNKNOWN,
    }


def test_equal_scores_and_titles_use_stable_media_identity() -> None:
    """Verify the final tie-break does not depend on input order."""
    first_movie = _movie("Same Title")
    second_movie = _movie("Same Title")
    candidates = (RecommendationCandidate(first_movie), RecommendationCandidate(second_movie))

    result, _ = _recommend(tuple(reversed(candidates)))

    assert tuple(str(item.media.id.value) for item in result.recommendations) == tuple(
        sorted((str(first_movie.id.value), str(second_movie.id.value)))
    )


def test_explanation_contains_matches_availability_and_personal_state() -> None:
    """Verify structured explanations contain only known normalized facts."""
    profile_id = ProfileId.new()
    movie = _movie("Explained", runtime=105)
    criteria = RecommendationCriteria(
        include_genres=frozenset({"Science Fiction"}),
        exclude_genres=frozenset({"Horror"}),
        maximum_runtime_minutes=120,
        watch=WatchRequirement.NOT_WATCHED,
        availability_any_of=(
            AvailabilityCriterion(kind=AvailabilitySourceKind.STREAMING, provider="Netflix", region="CZ"),
        ),
    )
    result, _ = _recommend(
        (
            RecommendationCandidate(
                movie,
                watch_status=WatchStatus.UNWATCHED,
                ratings=(_rating(movie, profile_id, value=PERSONAL_RATING),),
                streaming_availability=(_streaming(movie),),
            ),
        ),
        criteria,
        profile_id=profile_id,
    )
    recommendation = result.recommendations[0]

    assert {match.kind for match in recommendation.matched_constraints} == {
        ConstraintMatchKind.GENRE_INCLUDED,
        ConstraintMatchKind.GENRE_EXCLUSIONS_CLEAR,
        ConstraintMatchKind.RUNTIME,
        ConstraintMatchKind.WATCH_STATUS,
        ConstraintMatchKind.AVAILABILITY,
    }
    assert recommendation.watch_status is WatchStatus.UNWATCHED
    assert recommendation.personal_rating == PERSONAL_RATING
    assert recommendation.availability[0].region == "CZ"
    assert recommendation.availability[0].source_provider == "tmdb"


def test_reference_request_returns_repeatable_ranked_explainable_results() -> None:
    """Verify the Phase 2 reference request works end to end without AI."""
    profile_id = ProfileId.new()
    local = _movie("Local Short", country="FR", runtime=95)
    netflix = _movie("Netflix Long", runtime=140)
    watched = _movie("Watched", runtime=80)
    excluded = _movie("Excluded Horror", genres=("Science Fiction", "Horror"), country="JP", runtime=80)
    candidates = (
        RecommendationCandidate(local, library_presence=(_library(local, profile_id),)),
        RecommendationCandidate(netflix, streaming_availability=(_streaming(netflix),)),
        RecommendationCandidate(
            watched,
            watch_status=WatchStatus.WATCHED,
            streaming_availability=(_streaming(watched),),
        ),
        RecommendationCandidate(excluded, streaming_availability=(_streaming(excluded),)),
    )
    criteria = RecommendationCriteria(
        include_genres=frozenset({"Science Fiction"}),
        exclude_genres=frozenset({"Horror"}),
        maximum_runtime_minutes=150,
        watch=WatchRequirement.NOT_WATCHED,
        availability_any_of=(
            AvailabilityCriterion(kind=AvailabilitySourceKind.STREAMING, provider="Netflix", region="CZ"),
            AvailabilityCriterion(kind=AvailabilitySourceKind.LOCAL_LIBRARY, provider="Jellyfin"),
        ),
    )
    preferences = (
        _preference(profile_id, PreferenceKind.RUNTIME_MINUTES, maximum=100),
        _preference(profile_id, PreferenceKind.PROVIDER, value="Jellyfin"),
    )

    first, _ = _recommend(candidates, criteria, profile_id=profile_id, preferences=preferences)
    second, _ = _recommend(candidates, criteria, profile_id=profile_id, preferences=preferences)

    assert first == second
    assert tuple((item.media.title, item.score) for item in first.recommendations) == (
        ("Local Short", 200),
        ("Netflix Long", 0),
    )
    assert all(item.matched_constraints for item in first.recommendations)
    assert all(item.availability for item in first.recommendations)
