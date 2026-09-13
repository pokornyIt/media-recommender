"""Errors raised by catalog application services."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from media_recommender.domain import ExternalId, MediaType


class CatalogServiceError(Exception):
    """Base class for catalog application-service failures."""


class MetadataProviderNotConfiguredError(CatalogServiceError):
    """No metadata provider is configured for a requested namespace."""

    def __init__(self, namespace: str) -> None:
        """Initialize the error with the requested namespace.

        :param namespace: Provider namespace that is not configured.
        """
        super().__init__(f"Metadata provider is not configured for namespace: {namespace}")
        self.namespace = namespace


class MediaDetailsNotFoundError(CatalogServiceError):
    """A provider has no detail record for a requested identity and type."""

    def __init__(self, external_id: ExternalId, media_type: MediaType) -> None:
        """Initialize the error with the requested media identity.

        :param external_id: External identity that was not found.
        :param media_type: Requested kind of media.
        """
        super().__init__(
            f"Media details were not found for {external_id.namespace}:{external_id.value} ({media_type.value})"
        )
        self.external_id = external_id
        self.media_type = media_type


class InvalidProviderResultError(CatalogServiceError):
    """A provider returned details that do not match the request."""

    def __init__(self, external_id: ExternalId, media_type: MediaType) -> None:
        """Initialize the error with the expected identity and type.

        :param external_id: External identity expected in the result.
        :param media_type: Media type expected in the result.
        """
        super().__init__(
            f"Provider details do not match {external_id.namespace}:{external_id.value} ({media_type.value})"
        )
        self.external_id = external_id
        self.media_type = media_type
