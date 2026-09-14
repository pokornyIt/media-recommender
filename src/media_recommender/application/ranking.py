"""Deterministic ranking and factual recommendation explanations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from media_recommender.application.recommendations import (
    AvailabilitySourceKind,
    RecommendationCandidate,
    RecommendationCriteria,
    RecommendationDataSource,
    RecommendationFilterResult,
    apply_recommendation_filters,
    availability_matches,
    preference_matches,
    preference_measure,
)
from media_recommender.application.regions import production_region
from media_recommender.domain import LikeState, PreferenceEffect, PreferenceKind, WatchStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from media_recommender.domain import AvailabilityType, Media, Preference, ProfileId


class ConstraintMatchKind(StrEnum):
    """Kind of hard constraint satisfied by a recommendation."""

    MEDIA_TYPE = "media_type"
    GENRE_INCLUDED = "genre_included"
    GENRE_EXCLUSIONS_CLEAR = "genre_exclusions_clear"
    PRODUCTION_COUNTRY = "production_country"
    PRODUCTION_COUNTRY_EXCLUSIONS_CLEAR = "production_country_exclusions_clear"
    PRODUCTION_REGION = "production_region"
    PRODUCTION_REGION_EXCLUSIONS_CLEAR = "production_region_exclusions_clear"
    RUNTIME = "runtime"
    RELEASE_DATE = "release_date"
    WATCH_STATUS = "watch_status"
    PERSONAL_RATING = "personal_rating"
    LIKE_EXCLUSIONS_CLEAR = "like_exclusions_clear"
    AVAILABILITY = "availability"


class RankingReasonKind(StrEnum):
    """Known personal signal that contributed points to a score."""

    PROFILE_PREFERENCE = "profile_preference"
    PERSONAL_RATING = "personal_rating"
    LIKED = "liked"
    DISLIKED = "disliked"


class RecommendationWarningKind(StrEnum):
    """Known data gap retained in a recommendation explanation."""

    GENRES_UNKNOWN = "genres_unknown"
    PRODUCTION_COUNTRIES_UNKNOWN = "production_countries_unknown"
    RUNTIME_UNKNOWN = "runtime_unknown"
    RELEASE_DATE_UNKNOWN = "release_date_unknown"
    WATCH_STATUS_UNKNOWN = "watch_status_unknown"
    PERSONAL_RATING_UNKNOWN = "personal_rating_unknown"
    AVAILABILITY_UNKNOWN = "availability_unknown"


@dataclass(frozen=True, slots=True)
class RankingWeights:
    """Configurable integer weights for explicit personal signals."""

    preference_match: int = 100
    rating_point: int = 5
    liked: int = 30
    disliked_penalty: int = 30

    def __post_init__(self) -> None:
        """Require non-negative integer weights.

        :raises ValueError: If a weight is negative or a boolean.
        """
        if not all(_valid_weight(value) for value in self.values):
            msg = "Ranking weights must be non-negative integers"
            raise ValueError(msg)

    @property
    def values(self) -> tuple[int, ...]:
        """Return all configured weight values.

        :return: Weight values in stable field order.
        """
        return self.preference_match, self.rating_point, self.liked, self.disliked_penalty


def _valid_weight(value: object) -> bool:
    """Return whether a runtime value is a supported score magnitude.

    :param value: Potential ranking weight.
    :return: Whether the value is a non-negative non-boolean integer.
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True, slots=True)
class ConstraintMatch:
    """One configured hard constraint satisfied by a candidate."""

    kind: ConstraintMatchKind
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RankingReason:
    """One transparent signed contribution to a recommendation score."""

    kind: RankingReasonKind
    points: int
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecommendationWarning:
    """One explicit unknown fact that did not violate a hard constraint."""

    kind: RecommendationWarningKind


@dataclass(frozen=True, slots=True)
class KnownAvailability:
    """Normalized local or streaming availability included in an explanation."""

    kind: AvailabilitySourceKind
    provider: str
    region: str | None = None
    availability_type: AvailabilityType | None = None
    source_provider: str | None = None


@dataclass(frozen=True, slots=True)
class RankedRecommendation:
    """Ranked media with profile facts and a structured explanation."""

    media: Media
    rank: int
    score: int
    matched_constraints: tuple[ConstraintMatch, ...]
    ranking_reasons: tuple[RankingReason, ...]
    warnings: tuple[RecommendationWarning, ...]
    availability: tuple[KnownAvailability, ...]
    watch_status: WatchStatus
    personal_rating: float | None
    like_states: tuple[LikeState, ...]


@dataclass(frozen=True, slots=True)
class RecommendationResult:
    """Ranked accepted candidates together with all hard-filter decisions."""

    recommendations: tuple[RankedRecommendation, ...]
    filter_result: RecommendationFilterResult


class RecommendationService:
    """Filter and rank one immutable profile-specific candidate snapshot."""

    def __init__(self, data_source: RecommendationDataSource, weights: RankingWeights | None = None) -> None:
        """Initialize deterministic recommendation evaluation.

        :param data_source: Provider-independent candidate data source.
        :param weights: Optional scoring configuration.
        """
        self._data_source = data_source
        self._weights = weights or RankingWeights()

    async def recommend(
        self,
        profile_id: ProfileId,
        criteria: RecommendationCriteria,
    ) -> RecommendationResult:
        """Return filtered, deterministically ranked recommendations.

        :param profile_id: Owner of personal facts and preferences.
        :param criteria: Hard constraints applied before scoring.
        :return: Ranked recommendations and complete filter decisions.
        """
        candidates = tuple(await self._data_source.list_candidates(profile_id, criteria.media_types))
        preferences = tuple(await self._data_source.list_preferences(profile_id))
        filter_preferences: Sequence[Preference] = preferences if criteria.apply_profile_exclusions else ()
        filter_result = apply_recommendation_filters(candidates, criteria, filter_preferences)
        accepted_ids = {media.id for media in filter_result.accepted}
        preferred = tuple(preference for preference in preferences if preference.effect is PreferenceEffect.PREFER)
        ranked = sorted(
            (
                _rank_candidate(candidate, criteria, preferred, self._weights)
                for candidate in candidates
                if candidate.media.id in accepted_ids
            ),
            key=lambda item: (-item.score, item.media.title.casefold(), str(item.media.id.value)),
        )
        return RecommendationResult(
            recommendations=tuple(
                RankedRecommendation(
                    media=item.media,
                    rank=index,
                    score=item.score,
                    matched_constraints=item.matched_constraints,
                    ranking_reasons=item.ranking_reasons,
                    warnings=item.warnings,
                    availability=item.availability,
                    watch_status=item.watch_status,
                    personal_rating=item.personal_rating,
                    like_states=item.like_states,
                )
                for index, item in enumerate(ranked, start=1)
            ),
            filter_result=filter_result,
        )


@dataclass(frozen=True, slots=True)
class _UnrankedRecommendation:
    """Recommendation explanation before its ordinal rank is assigned."""

    media: Media
    score: int
    matched_constraints: tuple[ConstraintMatch, ...]
    ranking_reasons: tuple[RankingReason, ...]
    warnings: tuple[RecommendationWarning, ...]
    availability: tuple[KnownAvailability, ...]
    watch_status: WatchStatus
    personal_rating: float | None
    like_states: tuple[LikeState, ...]


def _rank_candidate(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    preferences: Sequence[Preference],
    weights: RankingWeights,
) -> _UnrankedRecommendation:
    """Score one accepted candidate and assemble its factual explanation.

    :param candidate: Accepted normalized candidate.
    :param criteria: Hard constraints already satisfied by the candidate.
    :param preferences: Persisted positive profile preferences.
    :param weights: Integer scoring configuration.
    :return: Scored recommendation without an ordinal rank.
    """
    reasons = _ranking_reasons(candidate, preferences, weights)
    return _UnrankedRecommendation(
        media=candidate.media,
        score=sum(reason.points for reason in reasons),
        matched_constraints=_matched_constraints(candidate, criteria),
        ranking_reasons=reasons,
        warnings=_warnings(candidate),
        availability=_known_availability(candidate),
        watch_status=candidate.watch_status,
        personal_rating=_personal_rating(candidate),
        like_states=_like_states(candidate),
    )


def _ranking_reasons(
    candidate: RecommendationCandidate,
    preferences: Sequence[Preference],
    weights: RankingWeights,
) -> tuple[RankingReason, ...]:
    """Return stable transparent score contributions for one candidate.

    :param candidate: Candidate being scored.
    :param preferences: Positive profile preferences.
    :param weights: Integer scoring configuration.
    :return: Signed score contributions.
    """
    reasons = [
        RankingReason(
            RankingReasonKind.PROFILE_PREFERENCE,
            weights.preference_match,
            _preference_values(preference),
        )
        for preference in sorted(preferences, key=lambda item: str(item.id.value))
        if preference_matches(candidate, preference)
    ]
    rating = _personal_rating(candidate)
    if rating is not None:
        reasons.append(
            RankingReason(RankingReasonKind.PERSONAL_RATING, round(rating * weights.rating_point), (str(rating),))
        )
    like_states = _like_states(candidate)
    if LikeState.LIKED in like_states:
        reasons.append(RankingReason(RankingReasonKind.LIKED, weights.liked))
    if LikeState.DISLIKED in like_states:
        reasons.append(RankingReason(RankingReasonKind.DISLIKED, -weights.disliked_penalty))
    return tuple(reasons)


def _preference_values(preference: Preference) -> tuple[str, ...]:
    """Return a stable machine-readable description of a preference.

    :param preference: Persisted positive preference.
    :return: Preference kind and configured value or bounds.
    """
    return (
        preference.kind.value,
        preference.value or "",
        "" if preference.minimum is None else str(preference.minimum),
        "" if preference.maximum is None else str(preference.maximum),
    )


def _matched_constraints(  # noqa: C901 - each typed hard constraint has distinct explanation data.
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
) -> tuple[ConstraintMatch, ...]:
    """Describe configured hard constraints already satisfied by a candidate.

    :param candidate: Accepted candidate.
    :param criteria: Hard constraints applied before ranking.
    :return: Stable structured matches.
    """
    matches: list[ConstraintMatch] = []
    media = candidate.media
    genres = {genre.name.casefold(): genre.name for genre in media.genres}
    countries = {country.code for country in media.production_countries}
    regions = {region for country in countries if (region := production_region(country)) is not None}
    runtime = preference_measure(candidate, PreferenceKind.RUNTIME_MINUTES)

    if criteria.media_types:
        matches.append(ConstraintMatch(ConstraintMatchKind.MEDIA_TYPE, (media.media_type.value,)))
    if criteria.include_genres:
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.GENRE_INCLUDED,
                tuple(sorted(genres[value] for value in criteria.include_genres if value in genres)),
            )
        )
    if criteria.exclude_genres:
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.GENRE_EXCLUSIONS_CLEAR,
                tuple(sorted(criteria.exclude_genres)),
            )
        )
    if criteria.include_countries:
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.PRODUCTION_COUNTRY,
                tuple(sorted(countries & criteria.include_countries)),
            )
        )
    if criteria.exclude_countries:
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.PRODUCTION_COUNTRY_EXCLUSIONS_CLEAR,
                tuple(sorted(criteria.exclude_countries)),
            )
        )
    if criteria.include_regions:
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.PRODUCTION_REGION,
                tuple(sorted(region.value for region in regions & criteria.include_regions)),
            )
        )
    if criteria.exclude_regions:
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.PRODUCTION_REGION_EXCLUSIONS_CLEAR,
                tuple(sorted(region.value for region in criteria.exclude_regions)),
            )
        )
    if criteria.minimum_runtime_minutes is not None or criteria.maximum_runtime_minutes is not None:
        matches.append(ConstraintMatch(ConstraintMatchKind.RUNTIME, (str(runtime),)))
    released_on = media.release_date
    if released_on is not None and any(
        value is not None
        for value in (
            criteria.minimum_release_year,
            criteria.maximum_release_year,
            criteria.released_from,
            criteria.released_until,
        )
    ):
        matches.append(ConstraintMatch(ConstraintMatchKind.RELEASE_DATE, (released_on.isoformat(),)))
    if criteria.watch.value != "any":
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.WATCH_STATUS,
                (candidate.watch_status.value, criteria.watch.value),
            )
        )
    rating = _personal_rating(candidate)
    if criteria.minimum_personal_rating is not None:
        matches.append(ConstraintMatch(ConstraintMatchKind.PERSONAL_RATING, (str(rating),)))
    if criteria.excluded_like_states:
        matches.append(
            ConstraintMatch(
                ConstraintMatchKind.LIKE_EXCLUSIONS_CLEAR,
                tuple(sorted(state.value for state in criteria.excluded_like_states)),
            )
        )
    matches.extend(
        ConstraintMatch(
            ConstraintMatchKind.AVAILABILITY,
            (criterion.kind.value, criterion.provider, criterion.region or ""),
        )
        for criterion in criteria.availability_any_of
        if availability_matches(candidate, criterion)
    )
    return tuple(matches)


def _warnings(candidate: RecommendationCandidate) -> tuple[RecommendationWarning, ...]:
    """Return explicit missing-data warnings in stable order.

    :param candidate: Candidate whose known data is described.
    :return: Missing-data warnings.
    """
    warnings: list[RecommendationWarning] = []
    if not candidate.media.genres:
        warnings.append(RecommendationWarning(RecommendationWarningKind.GENRES_UNKNOWN))
    if not candidate.media.production_countries:
        warnings.append(RecommendationWarning(RecommendationWarningKind.PRODUCTION_COUNTRIES_UNKNOWN))
    if preference_measure(candidate, PreferenceKind.RUNTIME_MINUTES) is None:
        warnings.append(RecommendationWarning(RecommendationWarningKind.RUNTIME_UNKNOWN))
    if candidate.media.release_date is None:
        warnings.append(RecommendationWarning(RecommendationWarningKind.RELEASE_DATE_UNKNOWN))
    if candidate.watch_status is WatchStatus.UNKNOWN:
        warnings.append(RecommendationWarning(RecommendationWarningKind.WATCH_STATUS_UNKNOWN))
    if _personal_rating(candidate) is None:
        warnings.append(RecommendationWarning(RecommendationWarningKind.PERSONAL_RATING_UNKNOWN))
    if not candidate.library_presence and not candidate.streaming_availability:
        warnings.append(RecommendationWarning(RecommendationWarningKind.AVAILABILITY_UNKNOWN))
    return tuple(warnings)


def _known_availability(candidate: RecommendationCandidate) -> tuple[KnownAvailability, ...]:
    """Return all known current availability facts in deterministic order.

    :param candidate: Candidate whose availability is described.
    :return: Normalized local and streaming availability.
    """
    facts = [
        KnownAvailability(
            kind=AvailabilitySourceKind.LOCAL_LIBRARY,
            provider=presence.provenance.provider,
            source_provider=presence.provenance.provider,
        )
        for presence in candidate.library_presence
        if presence.available
    ]
    facts.extend(
        KnownAvailability(
            kind=AvailabilitySourceKind.STREAMING,
            provider=availability.service.name,
            region=availability.region,
            availability_type=availability.availability_type,
            source_provider=availability.provenance.provider,
        )
        for availability in candidate.streaming_availability
    )
    return tuple(
        sorted(
            facts,
            key=lambda fact: (
                fact.kind.value,
                fact.provider.casefold(),
                fact.region or "",
                fact.availability_type.value if fact.availability_type is not None else "",
                fact.source_provider or "",
            ),
        )
    )


def _personal_rating(candidate: RecommendationCandidate) -> float | None:
    """Return the highest known numeric personal rating.

    :param candidate: Candidate carrying profile-owned ratings.
    :return: Highest rating, or ``None`` when no numeric rating is known.
    """
    values = [rating.value for rating in candidate.ratings if rating.value is not None]
    return max(values, default=None)


def _like_states(candidate: RecommendationCandidate) -> tuple[LikeState, ...]:
    """Return distinct known reactions in stable order.

    :param candidate: Candidate carrying profile-owned reactions.
    :return: Sorted distinct reactions.
    """
    states = {rating.like_state for rating in candidate.ratings if rating.like_state is not None}
    return tuple(sorted(states, key=lambda state: state.value))
