"""Application-layer contracts and services."""

from media_recommender.application.catalog import MediaCatalog, MediaCatalogReader, MediaCatalogWriter
from media_recommender.application.errors import (
    CatalogServiceError,
    InvalidProviderResultError,
    MediaDetailsNotFoundError,
    MetadataProviderNotConfiguredError,
)
from media_recommender.application.identity import (
    MatchKind,
    MatchReason,
    MediaIdentityCandidate,
    MediaIdentityEnricher,
    MediaIdentityResolver,
    MediaMatch,
    normalize_title,
)
from media_recommender.application.personal import PersonalMediaRepository, ProfileRepository
from media_recommender.application.providers import MediaSearchResult, MetadataProvider
from media_recommender.application.services import CatalogService

__all__ = [
    "CatalogService",
    "CatalogServiceError",
    "InvalidProviderResultError",
    "MatchKind",
    "MatchReason",
    "MediaCatalog",
    "MediaCatalogReader",
    "MediaCatalogWriter",
    "MediaDetailsNotFoundError",
    "MediaIdentityCandidate",
    "MediaIdentityEnricher",
    "MediaIdentityResolver",
    "MediaMatch",
    "MediaSearchResult",
    "MetadataProvider",
    "MetadataProviderNotConfiguredError",
    "PersonalMediaRepository",
    "ProfileRepository",
    "normalize_title",
]
