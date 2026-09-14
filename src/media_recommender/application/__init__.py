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
from media_recommender.application.imports import (
    IdentityResolver,
    ImportRecordResult,
    ImportRecordStatus,
    InvalidImportRecord,
    PersonalImportKind,
    PersonalImportRecord,
    PersonalImportResult,
    PersonalMediaImportService,
)
from media_recommender.application.library import (
    InvalidLibraryItem,
    LibraryItemResult,
    LibraryItemSnapshot,
    LibraryItemStatus,
    LibrarySynchronizationResult,
    LibrarySynchronizationService,
)
from media_recommender.application.personal import PersonalMediaRepository, ProfileRepository
from media_recommender.application.providers import MediaSearchResult, MetadataProvider
from media_recommender.application.services import CatalogService

__all__ = [
    "CatalogService",
    "CatalogServiceError",
    "IdentityResolver",
    "ImportRecordResult",
    "ImportRecordStatus",
    "InvalidImportRecord",
    "InvalidLibraryItem",
    "InvalidProviderResultError",
    "LibraryItemResult",
    "LibraryItemSnapshot",
    "LibraryItemStatus",
    "LibrarySynchronizationResult",
    "LibrarySynchronizationService",
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
    "PersonalImportKind",
    "PersonalImportRecord",
    "PersonalImportResult",
    "PersonalMediaImportService",
    "PersonalMediaRepository",
    "ProfileRepository",
    "normalize_title",
]
