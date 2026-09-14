"""Offline HTTP contract tests for the TMDB metadata provider."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest
from pydantic import SecretStr

from media_recommender.application import AvailabilityOffer
from media_recommender.config import TmdbSettings
from media_recommender.domain import AvailabilityType, ExternalId, MediaType, Movie, StreamingService, TVShow
from media_recommender.integrations import (
    ProviderAuthenticationError,
    ProviderInvalidPayloadError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from media_recommender.integrations.tmdb import TmdbMetadataProvider

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from media_recommender.application import MediaSearchResult
    from media_recommender.domain import Media


def _settings() -> TmdbSettings:
    """Return synthetic TMDB settings.

    :return: Settings containing no real credentials.
    """
    return TmdbSettings.model_validate(
        {
            "api_token": SecretStr("synthetic-token"),
            "language": "cs-CZ",
            "poster_size": "w342",
            "backdrop_size": "original",
        }
    )


def _run_with_provider[ResultT](
    handler: Callable[[httpx.Request], httpx.Response],
    operation: Callable[[TmdbMetadataProvider], Awaitable[ResultT]],
) -> ResultT:
    """Run a provider operation through an offline mock transport.

    :param handler: Synthetic HTTP handler.
    :param operation: Asynchronous provider operation to execute.
    :return: Result of the provider operation.
    """

    async def run() -> ResultT:
        """Own and close the provider around one operation."""
        async with TmdbMetadataProvider(_settings(), transport=httpx.MockTransport(handler)) as provider:
            return await operation(provider)

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("media_type", "expected_path", "response_payload", "expected_title"),
    [
        (
            MediaType.MOVIE,
            "/3/search/movie",
            {"results": [{"id": 11, "title": "Synthetic Movie", "release_date": "2020-01-02"}]},
            "Synthetic Movie",
        ),
        (
            MediaType.TV_SHOW,
            "/3/search/tv",
            {"results": [{"id": 22, "name": "Synthetic Series", "first_air_date": "2021-02-03"}]},
            "Synthetic Series",
        ),
    ],
)
def test_search_uses_typed_endpoint_and_bearer_authentication(
    media_type: MediaType,
    expected_path: str,
    response_payload: dict[str, object],
    expected_title: str,
) -> None:
    """Verify movie and TV searches use predictable localized TMDB requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Validate a search request and return synthetic JSON."""
        assert request.url.path == expected_path
        assert request.url.params["query"] == "Synthetic"
        assert request.url.params["language"] == "cs-CZ"
        assert request.url.params["include_adult"] == "false"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        return httpx.Response(200, json=response_payload)

    async def search(provider: TmdbMetadataProvider) -> MediaSearchResult:
        """Search and return the only synthetic result."""
        results = await provider.search("  Synthetic  ", media_type=media_type)
        return results[0]

    result = _run_with_provider(handler, search)

    assert result.title == expected_title
    assert result.media_type is media_type


@pytest.mark.parametrize(
    ("media_type", "expected_path", "payload", "expected_type"),
    [
        (
            MediaType.MOVIE,
            "/3/movie/31",
            {"id": 31, "title": "Movie Details", "runtime": 90},
            Movie,
        ),
        (
            MediaType.TV_SHOW,
            "/3/tv/32",
            {"id": 32, "name": "TV Details", "episode_run_time": [48]},
            TVShow,
        ),
    ],
)
def test_details_append_external_ids_and_normalize_media(
    media_type: MediaType,
    expected_path: str,
    payload: dict[str, object],
    expected_type: type[Media],
) -> None:
    """Verify detail requests append external IDs and return domain media."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Validate a detail request and return synthetic JSON."""
        assert request.url.path == expected_path
        assert request.url.params["language"] == "cs-CZ"
        assert request.url.params["append_to_response"] == "external_ids"
        return httpx.Response(200, json=payload)

    async def get_details(provider: TmdbMetadataProvider) -> Media | None:
        """Retrieve one synthetic media item."""
        return await provider.get_details(ExternalId("tmdb", expected_path.rsplit("/", 1)[-1]), media_type)

    result = _run_with_provider(handler, get_details)

    assert isinstance(result, expected_type)


def test_not_found_details_return_none() -> None:
    """Verify a missing TMDB item satisfies the provider's optional result contract."""
    result = _run_with_provider(
        lambda _request: httpx.Response(404, json={"status_message": "Not found"}),
        lambda provider: provider.get_details(ExternalId("tmdb", "999"), MediaType.MOVIE),
    )

    assert result is None


@pytest.mark.parametrize(
    ("media_type", "expected_path"),
    [(MediaType.MOVIE, "/3/movie/42/watch/providers"), (MediaType.TV_SHOW, "/3/tv/42/watch/providers")],
)
def test_availability_maps_multiple_services_types_and_selected_region(
    media_type: MediaType,
    expected_path: str,
) -> None:
    """Verify TMDB watch providers become normalized region-specific offers."""

    def handler(request: httpx.Request) -> httpx.Response:
        """Return synthetic multi-region availability."""
        assert request.url.path == expected_path
        assert "language" not in request.url.params
        return httpx.Response(
            200,
            json={
                "id": 42,
                "results": {
                    "CZ": {
                        "flatrate": [
                            {"provider_id": 8, "provider_name": "Netflix"},
                            {"provider_id": 1899, "provider_name": "Max"},
                        ],
                        "rent": [{"provider_id": 8, "provider_name": "Netflix"}],
                    },
                    "SK": {"flatrate": [{"provider_id": 337, "provider_name": "Disney Plus"}]},
                },
            },
        )

    async def get_availability(provider: TmdbMetadataProvider) -> Sequence[AvailabilityOffer]:
        """Retrieve the synthetic Czech offer snapshot."""
        return await provider.get_availability(ExternalId("tmdb", "42"), media_type, "cz")

    offers = _run_with_provider(handler, get_availability)

    assert set(offers) == {
        AvailabilityOffer(StreamingService("8", "Netflix"), AvailabilityType.SUBSCRIPTION),
        AvailabilityOffer(StreamingService("1899", "Max"), AvailabilityType.SUBSCRIPTION),
        AvailabilityOffer(StreamingService("8", "Netflix"), AvailabilityType.RENT),
    }


def test_availability_treats_missing_or_partial_region_as_empty_snapshot() -> None:
    """Verify absent availability categories and regions are not inferred."""
    offers = _run_with_provider(
        lambda _request: httpx.Response(200, json={"id": 42, "results": {"CZ": {}}}),
        lambda provider: provider.get_availability(ExternalId("tmdb", "42"), MediaType.MOVIE, "SK"),
    )

    assert offers == ()


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (401, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
        (503, ProviderUnavailableError),
    ],
)
def test_shared_provider_errors_are_preserved(status_code: int, error_type: type[Exception]) -> None:
    """Verify representative TMDB failures retain shared provider semantics."""
    with pytest.raises(error_type) as caught:
        _run_with_provider(
            lambda _request: httpx.Response(status_code, text="synthetic-token must stay hidden"),
            lambda provider: provider.search("Synthetic", media_type=MediaType.MOVIE),
        )

    assert "synthetic-token" not in str(caught.value)


def test_invalid_search_payload_is_rejected_at_tmdb_boundary() -> None:
    """Verify malformed TMDB fields cannot leak into application results."""
    with pytest.raises(ProviderInvalidPayloadError):
        _run_with_provider(
            lambda _request: httpx.Response(200, json={"results": [{"id": "invalid"}]}),
            lambda provider: provider.search("Synthetic", media_type=MediaType.MOVIE),
        )


def test_foreign_or_invalid_external_id_skips_http() -> None:
    """Verify identities that cannot address TMDB deterministically return no match."""

    def unexpected_request(_request: httpx.Request) -> httpx.Response:
        """Fail if an invalid identity reaches the HTTP boundary."""
        pytest.fail("HTTP must not be called for a foreign or invalid identity")

    foreign = _run_with_provider(
        unexpected_request,
        lambda provider: provider.get_details(ExternalId("imdb", "tt42"), MediaType.MOVIE),
    )
    invalid = _run_with_provider(
        unexpected_request,
        lambda provider: provider.get_details(ExternalId("tmdb", "not-a-number"), MediaType.MOVIE),
    )

    assert foreign is None
    assert invalid is None
