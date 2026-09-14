"""Deterministic cross-provider media identity resolution."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from media_recommender.domain import Movie

if TYPE_CHECKING:
    from collections.abc import Iterable

    from media_recommender.application.catalog import MediaCatalog
    from media_recommender.domain import ExternalId, Media, MediaId, MediaType

RUNTIME_TOLERANCE_MINUTES = 5
MINIMUM_RELEASE_YEAR = 1870
_SEPARATOR_PATTERN = re.compile(r"[\W_]+", flags=re.UNICODE)


@dataclass(frozen=True, slots=True, kw_only=True)
class MediaIdentityCandidate:
    """Provider-independent evidence describing a source media item."""

    media_type: MediaType
    title: str
    original_title: str | None = None
    release_year: int | None = None
    runtime_minutes: int | None = None
    external_ids: frozenset[ExternalId] = frozenset()

    def __post_init__(self) -> None:
        """Normalize text and validate candidate evidence.

        :raises ValueError: If titles, dates, runtime, or external identifiers are invalid.
        """
        title = self.title.strip()
        original_title = self.original_title.strip() if self.original_title is not None else None
        if not title:
            msg = "Identity candidate title must not be empty"
            raise ValueError(msg)
        if not normalize_title(title):
            msg = "Identity candidate title must contain letters or numbers"
            raise ValueError(msg)
        if original_title == "":
            msg = "Identity candidate original title must not be empty"
            raise ValueError(msg)
        if original_title is not None and not normalize_title(original_title):
            msg = "Identity candidate original title must contain letters or numbers"
            raise ValueError(msg)
        if isinstance(self.release_year, bool) or (
            self.release_year is not None and self.release_year < MINIMUM_RELEASE_YEAR
        ):
            msg = f"Identity candidate release year must be at least {MINIMUM_RELEASE_YEAR}"
            raise ValueError(msg)
        if isinstance(self.runtime_minutes, bool) or (self.runtime_minutes is not None and self.runtime_minutes <= 0):
            msg = "Identity candidate runtime must be positive"
            raise ValueError(msg)
        namespaces = [external_id.namespace for external_id in self.external_ids]
        if len(namespaces) != len(set(namespaces)):
            msg = "Identity candidate must not have conflicting IDs in one namespace"
            raise ValueError(msg)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "original_title", original_title)


class MatchKind(StrEnum):
    """Observable outcome of deterministic identity resolution."""

    EXACT = "exact"
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class MatchReason(StrEnum):
    """Structured explanation for an identity-resolution outcome."""

    EXTERNAL_ID = "external_id"
    METADATA = "metadata"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    IDENTIFIER_CONFLICT = "identifier_conflict"
    MEDIA_TYPE_CONFLICT = "media_type_conflict"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    NO_CANDIDATE = "no_candidate"


@dataclass(frozen=True, slots=True)
class MediaMatch:
    """Typed identity-resolution result without persistence implementation details."""

    kind: MatchKind
    reason: MatchReason
    media: Media | None = None
    candidate_ids: tuple[MediaId, ...] = ()

    def __post_init__(self) -> None:
        """Validate result shape for its declared outcome.

        :raises ValueError: If the media and candidate fields contradict the outcome kind.
        """
        successful = self.kind in {MatchKind.EXACT, MatchKind.RESOLVED}
        if successful and (self.media is None or self.candidate_ids != (self.media.id,)):
            msg = "Successful matches require exactly the resolved media identity"
            raise ValueError(msg)
        if not successful and self.media is not None:
            msg = "Unresolved matches must not contain a selected media item"
            raise ValueError(msg)
        if self.kind is MatchKind.AMBIGUOUS and not self.candidate_ids:
            msg = "Ambiguous matches require at least one conflicting candidate"
            raise ValueError(msg)
        if self.kind is MatchKind.NOT_FOUND and self.candidate_ids:
            msg = "Not-found matches must not contain candidate identities"
            raise ValueError(msg)


class MediaIdentityEnricher(Protocol):
    """Optionally add normalized evidence without exposing provider transport models."""

    async def enrich(self, candidate: MediaIdentityCandidate) -> MediaIdentityCandidate:
        """Return normalized additional evidence for an identity candidate.

        :param candidate: Original provider-independent evidence.
        :return: Enriched provider-independent evidence.
        """
        ...


class MediaIdentityResolver:
    """Resolve source identities against the shared catalog deterministically."""

    def __init__(self, catalog: MediaCatalog, *, enricher: MediaIdentityEnricher | None = None) -> None:
        """Initialize the resolver with catalog and optional enrichment boundaries.

        :param catalog: Shared catalog reader and writer.
        :param enricher: Optional provider-independent evidence enrichment boundary.
        """
        self._catalog = catalog
        self._enricher = enricher

    async def resolve(self, candidate: MediaIdentityCandidate) -> MediaMatch:
        """Resolve source evidence without guessing among unsafe candidates.

        :param candidate: Provider-independent source identity evidence.
        :return: Exact, resolved, ambiguous, or not-found result.
        """
        exact = await self._match_external_ids(candidate)
        if exact is not None:
            return exact

        evidence = await self._enrich(candidate) if self._enricher is not None else candidate
        if evidence != candidate:
            exact = await self._match_external_ids(evidence)
            if exact is not None:
                return exact

        return await self._match_metadata(evidence)

    async def _match_external_ids(self, candidate: MediaIdentityCandidate) -> MediaMatch | None:
        """Resolve all known identifiers and surface contradictions.

        :param candidate: Candidate carrying zero or more external identities.
        :return: Exact or conflicting result, or ``None`` when no identifier resolves.
        """
        matched_by_id: dict[MediaId, Media] = {}
        for external_id in sorted(candidate.external_ids, key=lambda item: (item.namespace, item.value)):
            matched = await self._catalog.find_by_external_id(external_id)
            if matched is not None:
                matched_by_id[matched.id] = matched
        candidate_ids = _sorted_media_ids(matched_by_id)
        if len(matched_by_id) > 1:
            return MediaMatch(MatchKind.AMBIGUOUS, MatchReason.IDENTIFIER_CONFLICT, candidate_ids=candidate_ids)
        if not matched_by_id:
            return None

        matched = next(iter(matched_by_id.values()))
        if matched.media_type is not candidate.media_type:
            return MediaMatch(
                MatchKind.AMBIGUOUS,
                MatchReason.MEDIA_TYPE_CONFLICT,
                candidate_ids=(matched.id,),
            )
        if _has_identifier_conflict(matched.external_ids, candidate.external_ids):
            return MediaMatch(
                MatchKind.AMBIGUOUS,
                MatchReason.IDENTIFIER_CONFLICT,
                candidate_ids=(matched.id,),
            )
        strengthened = await self._strengthen(matched, candidate.external_ids)
        return MediaMatch(MatchKind.EXACT, MatchReason.EXTERNAL_ID, strengthened, (strengthened.id,))

    async def _enrich(self, candidate: MediaIdentityCandidate) -> MediaIdentityCandidate:
        """Merge optional normalized enrichment with original source identities.

        :param candidate: Original identity evidence.
        :return: Enriched evidence preserving the original provider identifiers.
        :raises ValueError: If enrichment changes media type or contradicts an external identifier.
        """
        if self._enricher is None:
            return candidate
        enriched = await self._enricher.enrich(candidate)
        if enriched.media_type is not candidate.media_type:
            msg = "Identity enrichment must not change media type"
            raise ValueError(msg)
        if _has_identifier_conflict(enriched.external_ids, candidate.external_ids):
            msg = "Identity enrichment returned contradictory external identifiers"
            raise ValueError(msg)
        return replace(enriched, external_ids=enriched.external_ids | candidate.external_ids)

    async def _match_metadata(self, candidate: MediaIdentityCandidate) -> MediaMatch:
        """Match normalized titles only when independent metadata corroborates them.

        :param candidate: Candidate with no already-resolved external identity.
        :return: Resolved, ambiguous, or not-found result.
        """
        if candidate.release_year is None and candidate.runtime_minutes is None:
            return MediaMatch(MatchKind.NOT_FOUND, MatchReason.INSUFFICIENT_EVIDENCE)

        matches = [
            media
            async for media in self._catalog.iter_by_type(candidate.media_type)
            if _metadata_matches(candidate, media)
        ]
        matches.sort(key=lambda item: str(item.id.value))
        candidate_ids = tuple(item.id for item in matches)
        if len(matches) > 1:
            return MediaMatch(MatchKind.AMBIGUOUS, MatchReason.MULTIPLE_CANDIDATES, candidate_ids=candidate_ids)
        if not matches:
            return MediaMatch(MatchKind.NOT_FOUND, MatchReason.NO_CANDIDATE)

        matched = matches[0]
        if _has_identifier_conflict(matched.external_ids, candidate.external_ids):
            return MediaMatch(
                MatchKind.AMBIGUOUS,
                MatchReason.IDENTIFIER_CONFLICT,
                candidate_ids=(matched.id,),
            )
        strengthened = await self._strengthen(matched, candidate.external_ids)
        return MediaMatch(MatchKind.RESOLVED, MatchReason.METADATA, strengthened, (strengthened.id,))

    async def _strengthen(self, media: Media, external_ids: frozenset[ExternalId]) -> Media:
        """Persist newly learned non-conflicting external identities.

        :param media: Safely resolved shared catalog item.
        :param external_ids: Source identities to preserve for later synchronization.
        :return: Media carrying the complete persisted external-ID set.
        """
        combined_ids = media.external_ids | external_ids
        if combined_ids == media.external_ids:
            return media
        strengthened = replace(media, external_ids=combined_ids)
        await self._catalog.save(strengthened)
        return strengthened


def normalize_title(title: str) -> str:
    """Normalize a title for explicit case- and punctuation-insensitive comparison.

    :param title: Title to normalize.
    :return: Unicode-normalized, case-folded title with collapsed separators.
    """
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(_SEPARATOR_PATTERN.sub(" ", normalized).split())


def _metadata_matches(candidate: MediaIdentityCandidate, media: Media) -> bool:
    """Return whether title plus independent evidence safely match one item.

    :param candidate: Source identity evidence.
    :param media: Shared catalog item to compare.
    :return: Whether all comparable evidence agrees and at least one non-title field corroborates the title.
    """
    if not _titles_overlap(candidate, media):
        return False

    corroborated = False
    if candidate.release_year is not None and media.release_year is not None:
        if candidate.release_year != media.release_year:
            return False
        corroborated = True

    media_runtime = _media_runtime_minutes(media)
    if candidate.runtime_minutes is not None and media_runtime is not None:
        if abs(candidate.runtime_minutes - media_runtime) > RUNTIME_TOLERANCE_MINUTES:
            return False
        corroborated = True
    return corroborated


def _titles_overlap(candidate: MediaIdentityCandidate, media: Media) -> bool:
    """Return whether normalized source and catalog title variants intersect.

    :param candidate: Source title evidence.
    :param media: Catalog title metadata.
    :return: Whether any normalized non-empty title is shared.
    """
    candidate_titles = {normalize_title(candidate.title)}
    media_titles = {normalize_title(media.title)}
    if candidate.original_title is not None:
        candidate_titles.add(normalize_title(candidate.original_title))
    if media.original_title is not None:
        media_titles.add(normalize_title(media.original_title))
    return bool(candidate_titles & (media_titles - {""}))


def _media_runtime_minutes(media: Media) -> int | None:
    """Return movie or episode runtime as whole minutes.

    :param media: Catalog movie or TV show.
    :return: Applicable runtime, or ``None`` when unknown.
    """
    runtime = media.runtime if isinstance(media, Movie) else media.episode_runtime
    return runtime.minutes if runtime is not None else None


def _has_identifier_conflict(
    existing_ids: Iterable[ExternalId],
    incoming_ids: Iterable[ExternalId],
) -> bool:
    """Return whether namespaces carry contradictory values.

    :param existing_ids: Identities already attached to a catalog item.
    :param incoming_ids: Newly supplied identities.
    :return: Whether any shared namespace has different values.
    """
    existing_by_namespace = {item.namespace: item.value for item in existing_ids}
    return any(
        external_id.namespace in existing_by_namespace
        and existing_by_namespace[external_id.namespace] != external_id.value
        for external_id in incoming_ids
    )


def _sorted_media_ids(media_by_id: dict[MediaId, Media]) -> tuple[MediaId, ...]:
    """Return deterministic media identities from a match mapping.

    :param media_by_id: Media keyed by internal identity.
    :return: Identities sorted by UUID text.
    """
    return tuple(sorted(media_by_id, key=lambda item: str(item.value)))
