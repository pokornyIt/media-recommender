"""TMDB implementation of the metadata-provider application contract."""

from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import TYPE_CHECKING, Self

from media_recommender.domain import MediaType
from media_recommender.integrations.errors import ProviderResponseError
from media_recommender.integrations.http import ProviderHttpClient
from media_recommender.integrations.tmdb.mapper import (
    map_movie_details,
    map_movie_search_result,
    map_tv_details,
    map_tv_search_result,
    map_watch_provider_offers,
)
from media_recommender.integrations.tmdb.models import (
    TmdbMovieDetails,
    TmdbMovieSearchResponse,
    TmdbTvDetails,
    TmdbTvSearchResponse,
    TmdbWatchProviderResponse,
)

REGION_CODE_LENGTH = 2

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

    import httpx

    from media_recommender.application import AvailabilityOffer, MediaSearchResult
    from media_recommender.config import TmdbSettings
    from media_recommender.domain import ExternalId, Media


class TmdbMetadataProvider:
    """Search and normalize movie and TV metadata from TMDB."""

    source: str = "tmdb"
    attribution: str | None = "JustWatch"

    def __init__(
        self,
        settings: TmdbSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Create a TMDB provider with one shared HTTP connection pool.

        :param settings: Validated TMDB configuration.
        :param transport: Optional transport used by offline tests.
        """
        self._settings = settings
        self._http = ProviderHttpClient(settings, transport=transport)

    async def __aenter__(self) -> Self:
        """Return this provider for use as an asynchronous context manager.

        :return: This TMDB provider.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the provider's HTTP connection pool.

        :param exc_type: Exception type raised in the managed block, if any.
        :param exc_value: Exception raised in the managed block, if any.
        :param traceback: Exception traceback, if any.
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Close the provider's HTTP connection pool."""
        await self._http.aclose()

    async def search(
        self,
        query: str,
        *,
        media_type: MediaType | None = None,
    ) -> Sequence[MediaSearchResult]:
        """Search TMDB for normalized movie or TV summaries.

        When no media type is specified, movie and TV searches run concurrently
        and movie results precede TV results in the returned sequence.

        :param query: Non-empty title query.
        :param media_type: Optional media kind to restrict the search.
        :return: Provider-independent search summaries.
        :raises ValueError: If the query is empty.
        """
        normalized_query = query.strip()
        if not normalized_query:
            msg = "TMDB search query must not be empty"
            raise ValueError(msg)

        if media_type is MediaType.MOVIE:
            return await self._search_movies(normalized_query)
        if media_type is MediaType.TV_SHOW:
            return await self._search_tv(normalized_query)

        movies, tv_shows = await asyncio.gather(
            self._search_movies(normalized_query),
            self._search_tv(normalized_query),
        )
        return (*movies, *tv_shows)

    async def get_details(self, external_id: ExternalId, media_type: MediaType) -> Media | None:
        """Retrieve and normalize TMDB details for one media item.

        :param external_id: TMDB-namespaced numeric identity.
        :param media_type: Kind of details to retrieve.
        :return: Normalized details, or ``None`` for a foreign, invalid, or missing identity.
        :raises ProviderResponseError: If TMDB returns an unexpected non-success response.
        """
        tmdb_id = _parse_tmdb_id(external_id)
        if tmdb_id is None:
            return None

        try:
            if media_type is MediaType.MOVIE:
                item = await self._http.request_json(
                    "GET",
                    f"movie/{tmdb_id}",
                    TmdbMovieDetails,
                    params=self._detail_params,
                    headers=self._headers,
                )
                return map_movie_details(item, self._settings)

            item = await self._http.request_json(
                "GET",
                f"tv/{tmdb_id}",
                TmdbTvDetails,
                params=self._detail_params,
                headers=self._headers,
            )
            return map_tv_details(item, self._settings)
        except ProviderResponseError as error:
            if error.status_code == HTTPStatus.NOT_FOUND:
                return None
            raise

    async def get_availability(
        self,
        external_id: ExternalId,
        media_type: MediaType,
        region: str,
    ) -> Sequence[AvailabilityOffer]:
        """Retrieve current normalized watch-provider offers for one region.

        :param external_id: TMDB-namespaced numeric identity.
        :param media_type: Kind of media to retrieve.
        :param region: ISO 3166-1 alpha-2 region selected by the caller.
        :return: Complete regional offer snapshot; empty for invalid, missing, or unavailable media.
        :raises ValueError: If the region is not a valid country code.
        :raises ProviderResponseError: If TMDB returns an unexpected non-success response.
        """
        normalized_region = region.strip().upper()
        if (
            len(normalized_region) != REGION_CODE_LENGTH
            or not normalized_region.isascii()
            or not normalized_region.isalpha()
        ):
            msg = "TMDB availability region must be a two-letter ISO 3166-1 alpha-2 code"
            raise ValueError(msg)
        tmdb_id = _parse_tmdb_id(external_id)
        if tmdb_id is None:
            return ()

        media_path = "movie" if media_type is MediaType.MOVIE else "tv"
        try:
            response = await self._http.request_json(
                "GET",
                f"{media_path}/{tmdb_id}/watch/providers",
                TmdbWatchProviderResponse,
                headers=self._headers,
            )
        except ProviderResponseError as error:
            if error.status_code == HTTPStatus.NOT_FOUND:
                return ()
            raise
        return map_watch_provider_offers(response, normalized_region)

    async def _search_movies(self, query: str) -> tuple[MediaSearchResult, ...]:
        """Search the TMDB movie endpoint.

        :param query: Normalized non-empty title query.
        :return: Provider-independent movie summaries.
        """
        response = await self._http.request_json(
            "GET",
            "search/movie",
            TmdbMovieSearchResponse,
            params={"query": query, "language": self._settings.language, "include_adult": False},
            headers=self._headers,
        )
        return tuple(map_movie_search_result(item) for item in response.results)

    async def _search_tv(self, query: str) -> tuple[MediaSearchResult, ...]:
        """Search the TMDB TV endpoint.

        :param query: Normalized non-empty title query.
        :return: Provider-independent TV summaries.
        """
        response = await self._http.request_json(
            "GET",
            "search/tv",
            TmdbTvSearchResponse,
            params={"query": query, "language": self._settings.language, "include_adult": False},
            headers=self._headers,
        )
        return tuple(map_tv_search_result(item) for item in response.results)

    @property
    def _headers(self) -> dict[str, str]:
        """Return TMDB authorization and response headers.

        :return: Headers for a TMDB API request.
        """
        return {
            "Authorization": f"Bearer {self._settings.api_token.get_secret_value()}",
            "Accept": "application/json",
        }

    @property
    def _detail_params(self) -> dict[str, str]:
        """Return common detail parameters including external identities.

        :return: Parameters for TMDB movie and TV detail requests.
        """
        return {"language": self._settings.language, "append_to_response": "external_ids"}


def _parse_tmdb_id(external_id: ExternalId) -> int | None:
    """Parse a positive numeric identity from the TMDB namespace.

    :param external_id: Provider-namespaced identity.
    :return: Positive TMDB identity, or ``None`` when it cannot identify TMDB media.
    """
    if external_id.namespace != "tmdb":
        return None
    try:
        value = int(external_id.value)
    except ValueError:
        return None
    return value if value > 0 else None
