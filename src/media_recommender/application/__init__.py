"""Application-layer contracts and services."""

from media_recommender.application.catalog import MediaCatalogReader, MediaCatalogWriter
from media_recommender.application.providers import MediaSearchResult, MetadataProvider

__all__ = ["MediaCatalogReader", "MediaCatalogWriter", "MediaSearchResult", "MetadataProvider"]
