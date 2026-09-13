"""Application services orchestrating metadata providers and persistence."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING

from media_recommender.application.errors import (
    InvalidProviderResultError,
    MediaDetailsNotFoundError,
    MetadataProviderNotConfiguredError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from media_recommender.application.catalog import MediaCatalog
    from media_recommender.application.providers import MediaSearchResult, MetadataProvider
    from media_recommender.domain import ExternalId, Media, MediaId, MediaType


class CatalogService:
    """Expose provider-independent media catalog workflows."""

    def __init__(self, catalog: MediaCatalog, providers: Mapping[str, MetadataProvider]) -> None:
        """Initialize catalog orchestration with configured provider namespaces.

        :param catalog: Persistence boundary used for normalized media.
        :param providers: Metadata providers keyed by their primary external-ID namespace.
        :raises ValueError: If a provider namespace is empty or duplicated after normalization.
        """
        normalized_providers: dict[str, MetadataProvider] = {}
        for namespace, provider in providers.items():
            normalized_namespace = namespace.strip().lower()
            if not normalized_namespace:
                msg = "Metadata provider namespace must not be empty"
                raise ValueError(msg)
            if normalized_namespace in normalized_providers:
                msg = f"Duplicate metadata provider namespace: {normalized_namespace}"
                raise ValueError(msg)
            normalized_providers[normalized_namespace] = provider
        self._catalog = catalog
        self._providers = MappingProxyType(normalized_providers)

    async def search(
        self,
        provider_namespace: str,
        query: str,
        *,
        media_type: MediaType | None = None,
    ) -> tuple[MediaSearchResult, ...]:
        """Search one configured metadata provider.

        Provider errors are propagated unchanged so interface layers can apply
        their own presentation or transport-specific translation.

        :param provider_namespace: Primary namespace of the provider to search.
        :param query: Provider search text.
        :param media_type: Optional kind of media to search for.
        :return: Immutable provider-independent matching summaries.
        """
        provider = self._provider(provider_namespace)
        return tuple(await provider.search(query, media_type=media_type))

    async def sync(self, external_id: ExternalId, media_type: MediaType) -> Media:
        """Retrieve provider details and atomically persist their normalized state.

        A prior record carrying the requested external identity keeps its
        provider-independent internal identity. All other catalog metadata is
        replaced by the provider's latest normalized detail, so missing fields
        explicitly clear stale values. Provider and persistence errors are
        propagated unchanged. The repository owns the write transaction and
        rolls it back if persistence fails.

        :param external_id: Provider identity to retrieve and synchronize.
        :param media_type: Expected kind of media.
        :return: Persisted provider-independent media item.
        :raises MediaDetailsNotFoundError: If the provider has no matching details.
        :raises InvalidProviderResultError: If returned details do not carry the requested identity and type.
        """
        provider = self._provider(external_id.namespace)
        details = await provider.get_details(external_id, media_type)
        if details is None:
            raise MediaDetailsNotFoundError(external_id, media_type)
        if details.media_type is not media_type or external_id not in details.external_ids:
            raise InvalidProviderResultError(external_id, media_type)

        existing = await self._catalog.find_by_external_id(external_id)
        synchronized = replace(details, id=existing.id) if existing is not None else details
        await self._catalog.save(synchronized)
        return synchronized

    async def get(self, media_id: MediaId) -> Media | None:
        """Return persisted media by its internal application identity.

        :param media_id: Provider-independent catalog identity.
        :return: Matching media, or ``None`` when absent.
        """
        return await self._catalog.get(media_id)

    async def find_by_external_id(self, external_id: ExternalId) -> Media | None:
        """Return persisted media by a provider-namespaced identity.

        :param external_id: External identity to find.
        :return: Matching media, or ``None`` when absent.
        """
        return await self._catalog.find_by_external_id(external_id)

    def _provider(self, namespace: str) -> MetadataProvider:
        """Return the provider configured for a normalized namespace.

        :param namespace: Provider namespace to resolve.
        :return: Configured metadata provider.
        :raises MetadataProviderNotConfiguredError: If no provider is configured.
        """
        normalized_namespace = namespace.strip().lower()
        try:
            return self._providers[normalized_namespace]
        except KeyError as error:
            raise MetadataProviderNotConfiguredError(normalized_namespace) from error
