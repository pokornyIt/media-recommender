"""Map validated Jellyfin DTOs into provider-independent library snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ValidationError

from media_recommender.application import InvalidLibraryItem, LibraryItemSnapshot, MediaIdentityCandidate
from media_recommender.domain import ExternalId, MediaType, WatchStatus
from media_recommender.integrations.jellyfin.models import JellyfinLibraryItem

if TYPE_CHECKING:
    from pydantic import JsonValue


JELLYFIN_TICKS_PER_MINUTE = 600_000_000
EXTERNAL_ID_NAMESPACES = {"imdb": "imdb", "tmdb": "tmdb", "tvdb": "tvdb"}


@dataclass(frozen=True, slots=True)
class JellyfinMappedLibrary:
    """Normalized items, diagnostics, and all observable stable source IDs."""

    items: tuple[LibraryItemSnapshot, ...]
    invalid_items: tuple[InvalidLibraryItem, ...]


def map_library_items(raw_items: list[dict[str, JsonValue]]) -> JellyfinMappedLibrary:
    """Validate and normalize Jellyfin items independently.

    :param raw_items: Raw objects from a validated Jellyfin collection envelope.
    :return: Normalized snapshots plus safe per-item diagnostics.
    """
    items: list[LibraryItemSnapshot] = []
    invalid_items: list[InvalidLibraryItem] = []
    for position, raw_item in enumerate(raw_items, start=1):
        source_record_id = _source_record_id(raw_item)
        try:
            item = JellyfinLibraryItem.model_validate(raw_item)
            items.append(map_library_item(item, position=position))
        except ValidationError, ValueError:
            invalid_items.append(
                InvalidLibraryItem(
                    position=position,
                    reason="Invalid Jellyfin library item",
                    source_record_id=source_record_id,
                )
            )
    return JellyfinMappedLibrary(tuple(items), tuple(invalid_items))


def map_library_item(item: JellyfinLibraryItem, *, position: int) -> LibraryItemSnapshot:
    """Map one Jellyfin item to provider-independent identity and personal state.

    :param item: Validated Jellyfin movie or series.
    :param position: One-based position in the provider response.
    :return: Normalized current library snapshot.
    """
    user_data = item.user_data
    return LibraryItemSnapshot(
        position=position,
        source_record_id=item.id,
        candidate=MediaIdentityCandidate(
            media_type=MediaType.MOVIE if item.item_type == "Movie" else MediaType.TV_SHOW,
            title=item.name,
            original_title=_optional_text(item.original_title),
            release_year=item.production_year,
            runtime_minutes=(
                round(item.runtime_ticks / JELLYFIN_TICKS_PER_MINUTE) if item.runtime_ticks is not None else None
            ),
            external_ids=_external_ids(item),
        ),
        watch_status=_watch_status(played=user_data.played if user_data is not None else None),
        play_count=user_data.play_count if user_data is not None else None,
        last_played_at=user_data.last_played_date if user_data is not None else None,
    )


def _external_ids(item: JellyfinLibraryItem) -> frozenset[ExternalId]:
    """Return the stable Jellyfin ID and recognized cross-provider IDs.

    :param item: Validated Jellyfin library item.
    :return: Provider-independent external identities.
    """
    values = {ExternalId("jellyfin", item.id)}
    for provider_name, value in item.provider_ids.items():
        namespace = EXTERNAL_ID_NAMESPACES.get(provider_name.casefold())
        if namespace is not None and value.strip():
            values.add(ExternalId(namespace, value))
    return frozenset(values)


def _watch_status(*, played: bool | None) -> WatchStatus:
    """Preserve watched, unwatched, and unavailable user data distinctly.

    :param played: Jellyfin's explicit per-user playback flag.
    :return: Provider-independent three-state status.
    """
    if played is None:
        return WatchStatus.UNKNOWN
    return WatchStatus.WATCHED if played else WatchStatus.UNWATCHED


def _optional_text(value: str | None) -> str | None:
    """Normalize optional provider text.

    :param value: Optional provider string.
    :return: Stripped non-empty value or ``None``.
    """
    return value.strip() or None if value is not None else None


def _source_record_id(raw_item: dict[str, JsonValue]) -> str | None:
    """Extract a safe stable ID from an otherwise invalid raw item.

    :param raw_item: Unvalidated provider item.
    :return: Non-empty Jellyfin item ID when present.
    """
    value = raw_item.get("Id")
    return value.strip() if isinstance(value, str) and value.strip() else None
