"""Tests for provider-independent personal media domain models."""

from datetime import UTC, datetime

import pytest

from media_recommender.domain import (
    LikeState,
    MediaId,
    Preference,
    PreferenceEffect,
    PreferenceId,
    PreferenceKind,
    ProfileId,
    Rating,
    RatingId,
    SourceProvenance,
    WatchState,
    WatchStateId,
    WatchStatus,
)


def test_personal_models_normalize_provider_data_and_keep_unknown_state_explicit() -> None:
    """Verify normalized provenance and optional rating dimensions remain distinct."""
    provenance = SourceProvenance(
        provider="  Synthetic Provider ",
        source_record_id=" rating-1 ",
        synchronization_id=" sync-1 ",
        imported_at=datetime(2026, 9, 14, 10, tzinfo=UTC),
    )
    rating = Rating(
        id=RatingId.new(),
        profile_id=ProfileId.new(),
        media_id=MediaId.new(),
        provenance=provenance,
        like_state=LikeState.LIKED,
        rated_at=datetime(2026, 9, 13, 20, tzinfo=UTC),
    )

    assert provenance.provider == "synthetic provider"
    assert provenance.source_record_id == "rating-1"
    assert rating.value is None
    assert rating.like_state is LikeState.LIKED
    assert rating.rated_at == datetime(2026, 9, 13, 20, tzinfo=UTC)


def test_preference_supports_text_exclusions_and_numeric_ranges() -> None:
    """Verify later deterministic filters can consume concrete criterion shapes."""
    profile_id = ProfileId.new()
    excluded_genre = Preference(
        id=PreferenceId.new(),
        profile_id=profile_id,
        kind=PreferenceKind.GENRE,
        effect=PreferenceEffect.EXCLUDE,
        value=" Horror ",
    )
    runtime_range = Preference(
        id=PreferenceId.new(),
        profile_id=profile_id,
        kind=PreferenceKind.RUNTIME_MINUTES,
        effect=PreferenceEffect.PREFER,
        minimum=80,
        maximum=150,
    )

    assert excluded_genre.value == "Horror"
    assert (runtime_range.minimum, runtime_range.maximum) == (80, 150)


@pytest.mark.parametrize(
    "preference",
    [
        PreferenceKind.GENRE,
        PreferenceKind.RUNTIME_MINUTES,
    ],
)
def test_preference_rejects_missing_criterion_value(preference: PreferenceKind) -> None:
    """Verify preference kinds cannot represent an ambiguous unknown criterion."""
    with pytest.raises(ValueError, match="preferences require"):
        Preference(
            id=PreferenceId.new(),
            profile_id=ProfileId.new(),
            kind=preference,
            effect=PreferenceEffect.PREFER,
        )


def test_personal_timestamps_must_be_timezone_aware() -> None:
    """Verify imports cannot persist timestamps with ambiguous timezone semantics."""
    with pytest.raises(ValueError, match="timezone-aware"):
        SourceProvenance(provider="synthetic", imported_at=datetime(2026, 9, 14))  # noqa: DTZ001


def test_unknown_watch_state_is_represented_by_absence() -> None:
    """Verify an explicit state cannot collapse missing knowledge into unwatched."""
    with pytest.raises(ValueError, match="absence"):
        WatchState(
            id=WatchStateId.new(),
            profile_id=ProfileId.new(),
            media_id=MediaId.new(),
            status=WatchStatus.UNKNOWN,
            provenance=SourceProvenance(provider="synthetic", imported_at=datetime(2026, 9, 14, tzinfo=UTC)),
        )
