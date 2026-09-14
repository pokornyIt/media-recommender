"""Deterministic filtering for structured recommendation criteria."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from media_recommender.application.regions import ProductionRegion, production_region
from media_recommender.domain import Movie, PreferenceEffect, PreferenceKind, WatchStatus

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import date

    from media_recommender.domain import (
        AvailabilityType,
        LibraryPresence,
        LikeState,
        Media,
        MediaType,
        Preference,
        ProfileId,
        Rating,
        StreamingAvailability,
    )

MINIMUM_RELEASE_YEAR = 1870
MAXIMUM_PERSONAL_RATING = 10
REGION_CODE_LENGTH = 2


class GenreMatch(StrEnum):
    """Whether included genres use any-of or all-of semantics."""

    ANY = "any"
    ALL = "all"


class WatchRequirement(StrEnum):
    """Required profile-specific watch knowledge."""

    ANY = "any"
    WATCHED = "watched"
    UNWATCHED = "unwatched"
    NOT_WATCHED = "not_watched"


class AvailabilitySourceKind(StrEnum):
    """Kind of availability that may satisfy a hard source requirement."""

    LOCAL_LIBRARY = "local_library"
    STREAMING = "streaming"


@dataclass(frozen=True, slots=True, kw_only=True)
class AvailabilityCriterion:
    """One alternative in an OR-combined availability requirement."""

    kind: AvailabilitySourceKind
    provider: str
    region: str | None = None
    source_provider: str | None = None
    availability_types: frozenset[AvailabilityType] = frozenset()

    def __post_init__(self) -> None:
        """Normalize and validate source-specific fields.

        :raises ValueError: If required fields are empty or incompatible with the source kind.
        """
        provider = self.provider.strip()
        if not provider:
            msg = "Availability criterion provider must not be empty"
            raise ValueError(msg)
        object.__setattr__(self, "provider", provider)

        if self.kind is AvailabilitySourceKind.LOCAL_LIBRARY:
            if self.region is not None or self.source_provider is not None or self.availability_types:
                msg = "Local-library criteria cannot specify region, source provider, or availability types"
                raise ValueError(msg)
            object.__setattr__(self, "provider", provider.lower())
            return

        if self.region is None:
            msg = "Streaming availability criteria require a region"
            raise ValueError(msg)
        object.__setattr__(self, "region", _normalized_country_code(self.region, "Streaming region"))
        if self.source_provider is not None:
            source_provider = self.source_provider.strip().lower()
            if not source_provider:
                msg = "Streaming source provider must not be empty"
                raise ValueError(msg)
            object.__setattr__(self, "source_provider", source_provider)


@dataclass(frozen=True, slots=True, kw_only=True)
class RecommendationCriteria:
    """Typed hard constraints kept separate from later ranking preferences."""

    media_types: frozenset[MediaType] = frozenset()
    include_genres: frozenset[str] = frozenset()
    genre_match: GenreMatch = GenreMatch.ALL
    exclude_genres: frozenset[str] = frozenset()
    include_countries: frozenset[str] = frozenset()
    exclude_countries: frozenset[str] = frozenset()
    include_regions: frozenset[ProductionRegion] = frozenset()
    exclude_regions: frozenset[ProductionRegion] = frozenset()
    minimum_runtime_minutes: int | None = None
    maximum_runtime_minutes: int | None = None
    minimum_release_year: int | None = None
    maximum_release_year: int | None = None
    released_from: date | None = None
    released_until: date | None = None
    watch: WatchRequirement = WatchRequirement.ANY
    minimum_personal_rating: float | None = None
    excluded_like_states: frozenset[LikeState] = frozenset()
    availability_any_of: tuple[AvailabilityCriterion, ...] = ()
    apply_profile_exclusions: bool = True

    def __post_init__(self) -> None:
        """Normalize text and validate hard-constraint ranges.

        :raises ValueError: If ranges are invalid or text criteria are empty.
        """
        object.__setattr__(self, "include_genres", _normalized_text_set(self.include_genres, "Included genre"))
        object.__setattr__(self, "exclude_genres", _normalized_text_set(self.exclude_genres, "Excluded genre"))
        object.__setattr__(
            self,
            "include_countries",
            _normalized_country_set(self.include_countries, "Included country"),
        )
        object.__setattr__(
            self,
            "exclude_countries",
            _normalized_country_set(self.exclude_countries, "Excluded country"),
        )
        _validate_range(
            self.minimum_runtime_minutes,
            self.maximum_runtime_minutes,
            minimum_allowed=1,
            label="Runtime",
        )
        _validate_range(
            self.minimum_release_year,
            self.maximum_release_year,
            minimum_allowed=MINIMUM_RELEASE_YEAR,
            label="Release year",
        )
        if (
            self.released_from is not None
            and self.released_until is not None
            and self.released_from > self.released_until
        ):
            msg = "Release date minimum must not exceed maximum"
            raise ValueError(msg)
        if (
            self.minimum_personal_rating is not None
            and not 0 <= self.minimum_personal_rating <= MAXIMUM_PERSONAL_RATING
        ):
            msg = "Minimum personal rating must be between 0 and 10"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class RecommendationCandidate:
    """All normalized facts required to filter one catalog item."""

    media: Media
    watch_status: WatchStatus = WatchStatus.UNKNOWN
    ratings: tuple[Rating, ...] = ()
    library_presence: tuple[LibraryPresence, ...] = ()
    streaming_availability: tuple[StreamingAvailability, ...] = ()


class FilterReason(StrEnum):
    """Structured reason why a candidate failed a hard constraint."""

    MEDIA_TYPE = "media_type"
    GENRE_NOT_INCLUDED = "genre_not_included"
    GENRE_EXCLUDED = "genre_excluded"
    PRODUCTION_COUNTRY_NOT_INCLUDED = "production_country_not_included"
    PRODUCTION_COUNTRY_EXCLUDED = "production_country_excluded"
    PRODUCTION_REGION_NOT_INCLUDED = "production_region_not_included"
    PRODUCTION_REGION_EXCLUDED = "production_region_excluded"
    RUNTIME_UNKNOWN = "runtime_unknown"
    RUNTIME_OUT_OF_RANGE = "runtime_out_of_range"
    RELEASE_DATE_UNKNOWN = "release_date_unknown"
    RELEASE_DATE_OUT_OF_RANGE = "release_date_out_of_range"
    WATCH_STATUS_UNKNOWN = "watch_status_unknown"
    WATCH_STATUS_MISMATCH = "watch_status_mismatch"
    RATING_UNKNOWN = "rating_unknown"
    RATING_BELOW_MINIMUM = "rating_below_minimum"
    LIKE_STATE_EXCLUDED = "like_state_excluded"
    AVAILABILITY_NOT_FOUND = "availability_not_found"
    PROFILE_PREFERENCE_EXCLUDED = "profile_preference_excluded"


@dataclass(frozen=True, slots=True)
class FilterExclusion:
    """One machine-readable failed constraint with matching values."""

    reason: FilterReason
    values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RecommendationDecision:
    """Filtering decision for one candidate."""

    media: Media
    exclusions: tuple[FilterExclusion, ...]

    @property
    def accepted(self) -> bool:
        """Return whether the candidate satisfied every hard constraint.

        :return: ``True`` when no exclusion reason exists.
        """
        return not self.exclusions


@dataclass(frozen=True, slots=True)
class RecommendationFilterResult:
    """Deterministically ordered accepted media and all candidate decisions."""

    accepted: tuple[Media, ...]
    decisions: tuple[RecommendationDecision, ...]


class RecommendationDataSource(Protocol):
    """Load normalized candidate facts without exposing persistence details."""

    async def list_candidates(
        self,
        profile_id: ProfileId,
        media_types: frozenset[MediaType],
    ) -> Sequence[RecommendationCandidate]:
        """Return candidate snapshots for one profile.

        :param profile_id: Personal-state owner.
        :param media_types: Optional catalog type restriction.
        :return: Normalized candidate snapshots.
        """
        ...

    async def list_preferences(self, profile_id: ProfileId) -> Sequence[Preference]:
        """Return persisted profile preferences and exclusions.

        :param profile_id: Personal-state owner.
        :return: Persisted preference records.
        """
        ...


class RecommendationFilterService:
    """Apply reproducible hard constraints to normalized candidate snapshots."""

    def __init__(self, data_source: RecommendationDataSource) -> None:
        """Initialize the filtering service.

        :param data_source: Provider-independent candidate data source.
        """
        self._data_source = data_source

    async def filter(
        self,
        profile_id: ProfileId,
        criteria: RecommendationCriteria,
    ) -> RecommendationFilterResult:
        """Return candidates satisfying all explicit and persisted exclusions.

        :param profile_id: Owner of personal state used by the evaluation.
        :param criteria: Typed hard constraints.
        :return: Accepted media and structured decisions for every candidate.
        """
        candidates = await self._data_source.list_candidates(profile_id, criteria.media_types)
        preferences = (
            tuple(await self._data_source.list_preferences(profile_id)) if criteria.apply_profile_exclusions else ()
        )
        return apply_recommendation_filters(candidates, criteria, preferences)


def apply_recommendation_filters(
    candidates: Sequence[RecommendationCandidate],
    criteria: RecommendationCriteria,
    preferences: Sequence[Preference] = (),
) -> RecommendationFilterResult:
    """Apply hard constraints to already loaded candidate snapshots.

    This entry point lets filtering and ranking share one provider-independent
    snapshot without loading mutable persistence state twice.

    :param candidates: Normalized candidate snapshots.
    :param criteria: Typed hard constraints.
    :param preferences: Persisted profile preferences and exclusions.
    :return: Accepted media and structured decisions for every candidate.
    """
    decisions = tuple(
        RecommendationDecision(candidate.media, _exclusions(candidate, criteria, preferences))
        for candidate in sorted(candidates, key=_candidate_sort_key)
    )
    return RecommendationFilterResult(
        accepted=tuple(decision.media for decision in decisions if decision.accepted),
        decisions=decisions,
    )


def _exclusions(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    preferences: Sequence[Preference],
) -> tuple[FilterExclusion, ...]:
    """Return every failed hard constraint in stable evaluation order.

    :param candidate: Normalized candidate facts.
    :param criteria: Explicit hard constraints.
    :param preferences: Persisted profile criteria.
    :return: Structured exclusion reasons.
    """
    exclusions: list[FilterExclusion] = []
    _filter_media_type(candidate, criteria, exclusions)
    _filter_genres(candidate, criteria, exclusions)
    _filter_production(candidate, criteria, exclusions)
    _filter_runtime(candidate, criteria, exclusions)
    _filter_release(candidate, criteria, exclusions)
    _filter_watch(candidate, criteria, exclusions)
    _filter_ratings(candidate, criteria, exclusions)
    _filter_availability(candidate, criteria, exclusions)
    _filter_preferences(candidate, preferences, exclusions)
    return tuple(exclusions)


def _filter_media_type(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Append an exclusion when media type does not match.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    if criteria.media_types and candidate.media.media_type not in criteria.media_types:
        exclusions.append(FilterExclusion(FilterReason.MEDIA_TYPE, (candidate.media.media_type.value,)))


def _filter_genres(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Apply included and excluded genre constraints.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    genres = {genre.name.casefold() for genre in candidate.media.genres}
    if criteria.include_genres:
        matches = genres & criteria.include_genres
        accepted = bool(matches) if criteria.genre_match is GenreMatch.ANY else criteria.include_genres.issubset(genres)
        if not accepted:
            exclusions.append(FilterExclusion(FilterReason.GENRE_NOT_INCLUDED, tuple(sorted(criteria.include_genres))))
    excluded = genres & criteria.exclude_genres
    if excluded:
        exclusions.append(FilterExclusion(FilterReason.GENRE_EXCLUDED, tuple(sorted(excluded))))


def _filter_production(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Apply production-country and broad-region constraints.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    countries = {country.code for country in candidate.media.production_countries}
    if criteria.include_countries and not countries.intersection(criteria.include_countries):
        exclusions.append(
            FilterExclusion(FilterReason.PRODUCTION_COUNTRY_NOT_INCLUDED, tuple(sorted(criteria.include_countries)))
        )
    excluded_countries = countries & criteria.exclude_countries
    if excluded_countries:
        exclusions.append(FilterExclusion(FilterReason.PRODUCTION_COUNTRY_EXCLUDED, tuple(sorted(excluded_countries))))

    regions = {region for country in countries if (region := production_region(country)) is not None}
    if criteria.include_regions and not regions.intersection(criteria.include_regions):
        exclusions.append(
            FilterExclusion(
                FilterReason.PRODUCTION_REGION_NOT_INCLUDED,
                tuple(sorted(region.value for region in criteria.include_regions)),
            )
        )
    excluded_regions = regions & criteria.exclude_regions
    if excluded_regions:
        exclusions.append(
            FilterExclusion(
                FilterReason.PRODUCTION_REGION_EXCLUDED,
                tuple(sorted(region.value for region in excluded_regions)),
            )
        )


def _filter_runtime(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Apply runtime bounds with explicit missing-metadata behavior.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    if criteria.minimum_runtime_minutes is None and criteria.maximum_runtime_minutes is None:
        return
    runtime = candidate.media.runtime if isinstance(candidate.media, Movie) else candidate.media.episode_runtime
    if runtime is None:
        exclusions.append(FilterExclusion(FilterReason.RUNTIME_UNKNOWN))
        return
    if not _within_int(runtime.minutes, criteria.minimum_runtime_minutes, criteria.maximum_runtime_minutes):
        exclusions.append(FilterExclusion(FilterReason.RUNTIME_OUT_OF_RANGE, (str(runtime.minutes),)))


def _filter_release(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Apply release year and date bounds with explicit unknown handling.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    requires_release = any(
        value is not None
        for value in (
            criteria.minimum_release_year,
            criteria.maximum_release_year,
            criteria.released_from,
            criteria.released_until,
        )
    )
    if not requires_release:
        return
    released_on = candidate.media.release_date
    if released_on is None:
        exclusions.append(FilterExclusion(FilterReason.RELEASE_DATE_UNKNOWN))
        return
    if not _within_int(
        released_on.year, criteria.minimum_release_year, criteria.maximum_release_year
    ) or not _within_date(
        released_on,
        criteria.released_from,
        criteria.released_until,
    ):
        exclusions.append(FilterExclusion(FilterReason.RELEASE_DATE_OUT_OF_RANGE, (released_on.isoformat(),)))


def _filter_watch(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Apply explicit profile-specific three-state watch semantics.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    if criteria.watch in {WatchRequirement.ANY, WatchRequirement.NOT_WATCHED}:
        if criteria.watch is WatchRequirement.NOT_WATCHED and candidate.watch_status is WatchStatus.WATCHED:
            exclusions.append(FilterExclusion(FilterReason.WATCH_STATUS_MISMATCH, (candidate.watch_status.value,)))
        return
    if candidate.watch_status is WatchStatus.UNKNOWN:
        exclusions.append(FilterExclusion(FilterReason.WATCH_STATUS_UNKNOWN))
    elif candidate.watch_status.value != criteria.watch.value:
        exclusions.append(FilterExclusion(FilterReason.WATCH_STATUS_MISMATCH, (candidate.watch_status.value,)))


def _filter_ratings(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Apply numeric rating and explicit reaction exclusions.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    excluded_states = {
        rating.like_state
        for rating in candidate.ratings
        if rating.like_state is not None and rating.like_state in criteria.excluded_like_states
    }
    if excluded_states:
        exclusions.append(
            FilterExclusion(FilterReason.LIKE_STATE_EXCLUDED, tuple(sorted(state.value for state in excluded_states)))
        )
    if criteria.minimum_personal_rating is None:
        return
    values = [rating.value for rating in candidate.ratings if rating.value is not None]
    if not values:
        exclusions.append(FilterExclusion(FilterReason.RATING_UNKNOWN))
    elif max(values) < criteria.minimum_personal_rating:
        exclusions.append(FilterExclusion(FilterReason.RATING_BELOW_MINIMUM, (str(max(values)),)))


def _filter_availability(
    candidate: RecommendationCandidate,
    criteria: RecommendationCriteria,
    exclusions: list[FilterExclusion],
) -> None:
    """Apply OR semantics across selected local and streaming sources.

    :param candidate: Candidate being evaluated.
    :param criteria: Hard constraints to apply.
    :param exclusions: Mutable exclusion accumulator.
    """
    if criteria.availability_any_of and not any(
        availability_matches(candidate, requirement) for requirement in criteria.availability_any_of
    ):
        exclusions.append(FilterExclusion(FilterReason.AVAILABILITY_NOT_FOUND))


def availability_matches(candidate: RecommendationCandidate, criterion: AvailabilityCriterion) -> bool:
    """Return whether one normalized availability alternative is satisfied.

    :param candidate: Candidate being evaluated.
    :param criterion: One acceptable availability source.
    :return: Whether the candidate is available from the source.
    """
    if criterion.kind is AvailabilitySourceKind.LOCAL_LIBRARY:
        return any(
            presence.available and presence.provenance.provider == criterion.provider
            for presence in candidate.library_presence
        )
    provider = criterion.provider.casefold()
    return any(
        availability.region == criterion.region
        and (availability.service.name.casefold() == provider or availability.service.source_id == criterion.provider)
        and (criterion.source_provider is None or availability.provenance.provider == criterion.source_provider)
        and (not criterion.availability_types or availability.availability_type in criterion.availability_types)
        for availability in candidate.streaming_availability
    )


def _filter_preferences(
    candidate: RecommendationCandidate,
    preferences: Sequence[Preference],
    exclusions: list[FilterExclusion],
) -> None:
    """Apply persisted exclusions while leaving ranking preferences untouched.

    :param candidate: Candidate being evaluated.
    :param preferences: Persisted profile preferences and exclusions.
    :param exclusions: Mutable exclusion accumulator.
    """
    exclusions.extend(
        FilterExclusion(
            FilterReason.PROFILE_PREFERENCE_EXCLUDED,
            (preference.kind.value, str(preference.id.value)),
        )
        for preference in sorted(preferences, key=lambda item: str(item.id.value))
        if preference.effect is PreferenceEffect.EXCLUDE and preference_matches(candidate, preference)
    )


def preference_matches(  # noqa: PLR0911 - each typed preference kind has distinct matching semantics.
    candidate: RecommendationCandidate,
    preference: Preference,
) -> bool:
    """Return whether one persisted exclusion applies to a candidate.

    :param candidate: Candidate being evaluated.
    :param preference: Persisted preference to match.
    :return: Whether the preference applies to the candidate.
    """
    value = preference.value
    if preference.kind is PreferenceKind.GENRE:
        return value is not None and any(genre.name.casefold() == value.casefold() for genre in candidate.media.genres)
    if preference.kind is PreferenceKind.PRODUCTION_COUNTRY:
        return value is not None and any(
            country.code == value.upper() for country in candidate.media.production_countries
        )
    if preference.kind is PreferenceKind.PRODUCTION_REGION:
        if value is None:
            return False
        return any(
            region.value == value.casefold()
            for country in candidate.media.production_countries
            if (region := production_region(country.code)) is not None
        )
    if preference.kind is PreferenceKind.PROVIDER:
        if value is None:
            return False
        normalized_value = value.casefold()
        return any(
            presence.available and presence.provenance.provider.casefold() == normalized_value
            for presence in candidate.library_presence
        ) or any(
            availability.service.name.casefold() == normalized_value
            or availability.provenance.provider.casefold() == normalized_value
            for availability in candidate.streaming_availability
        )

    measured = preference_measure(candidate, preference.kind)
    return measured is not None and _within_int(measured, preference.minimum, preference.maximum)


def preference_measure(candidate: RecommendationCandidate, kind: PreferenceKind) -> int | None:
    """Return the candidate value addressed by a numeric preference kind.

    :param candidate: Candidate being evaluated.
    :param kind: Numeric preference kind.
    :return: Matching candidate value, or ``None`` when unknown.
    """
    if kind is PreferenceKind.RELEASE_YEAR:
        return candidate.media.release_year
    if kind is not PreferenceKind.RUNTIME_MINUTES:
        return None
    runtime = candidate.media.runtime if isinstance(candidate.media, Movie) else candidate.media.episode_runtime
    return runtime.minutes if runtime is not None else None


def _within_int(value: int, minimum: int | None, maximum: int | None) -> bool:
    """Return whether an integer lies within inclusive optional bounds.

    :param value: Value to test.
    :param minimum: Optional inclusive lower bound.
    :param maximum: Optional inclusive upper bound.
    :return: Whether the value is within the bounds.
    """
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _within_date(value: date, minimum: date | None, maximum: date | None) -> bool:
    """Return whether a date lies within inclusive optional bounds.

    :param value: Date to test.
    :param minimum: Optional inclusive lower bound.
    :param maximum: Optional inclusive upper bound.
    :return: Whether the date is within the bounds.
    """
    return (minimum is None or value >= minimum) and (maximum is None or value <= maximum)


def _normalized_text_set(values: Iterable[str], label: str) -> frozenset[str]:
    """Return normalized case-insensitive text criteria.

    :param values: Text values to normalize.
    :param label: Field label used in validation errors.
    :return: Normalized values.
    :raises ValueError: If a value is empty after trimming.
    """
    normalized = frozenset(value.strip().casefold() for value in values)
    if "" in normalized:
        msg = f"{label} must not be empty"
        raise ValueError(msg)
    return normalized


def _normalized_country_set(values: Iterable[str], label: str) -> frozenset[str]:
    """Return normalized validated country criteria.

    :param values: Country codes to normalize.
    :param label: Field label used in validation errors.
    :return: Normalized country codes.
    """
    return frozenset(_normalized_country_code(value, label) for value in values)


def _normalized_country_code(value: str, label: str) -> str:
    """Return one uppercase ISO 3166-1 alpha-2 country code.

    :param value: Country code to normalize.
    :param label: Field label used in validation errors.
    :return: Normalized country code.
    :raises ValueError: If the country code is invalid.
    """
    normalized = value.strip().upper()
    if len(normalized) != REGION_CODE_LENGTH or not normalized.isascii() or not normalized.isalpha():
        msg = f"{label} must be a two-letter ISO 3166-1 alpha-2 code"
        raise ValueError(msg)
    return normalized


def _validate_range(minimum: int | None, maximum: int | None, *, minimum_allowed: int, label: str) -> None:
    """Validate an optional inclusive integer range.

    :param minimum: Optional inclusive lower bound.
    :param maximum: Optional inclusive upper bound.
    :param minimum_allowed: Smallest accepted bound.
    :param label: Field label used in validation errors.
    :raises TypeError: If a bound is a boolean.
    :raises ValueError: If a bound is too small or the range is reversed.
    """
    if isinstance(minimum, bool) or isinstance(maximum, bool):
        msg = f"{label} bounds must be integers"
        raise TypeError(msg)
    if (minimum is not None and minimum < minimum_allowed) or (maximum is not None and maximum < minimum_allowed):
        msg = f"{label} bounds must be at least {minimum_allowed}"
        raise ValueError(msg)
    if minimum is not None and maximum is not None and minimum > maximum:
        msg = f"{label} minimum must not exceed maximum"
        raise ValueError(msg)


def _candidate_sort_key(candidate: RecommendationCandidate) -> tuple[str, str]:
    """Return stable catalog ordering for repeatable evaluation.

    :param candidate: Candidate being ordered.
    :return: Case-insensitive title and stable identifier.
    """
    return candidate.media.title.casefold(), str(candidate.media.id.value)
