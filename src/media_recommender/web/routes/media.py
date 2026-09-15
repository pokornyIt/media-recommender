"""Read-only HTTP routes for normalized shared catalog media."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, cast
from uuid import UUID  # noqa: TC003 - FastAPI resolves this path parameter annotation at runtime.

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from media_recommender.application.services import (
    CatalogService,  # noqa: TC001 - FastAPI resolves this dependency annotation at runtime.
)
from media_recommender.domain import MediaId
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.schemas.errors import ErrorDetail, ErrorResponse
from media_recommender.web.schemas.media import MediaResponse, media_to_response

if TYPE_CHECKING:
    from starlette.types import ExceptionHandler

router = APIRouter()


class MediaNotFoundError(Exception):
    """Raised when no shared catalog media has the requested internal identity."""


def get_catalog_service(request: Request) -> CatalogService:
    """Return the catalog service configured by the application's composition root.

    :param request: Incoming request carrying the current application instance.
    :return: Catalog application service used by media routes.
    :raises RuntimeError: If the application has no configured catalog service.
    """
    try:
        return cast("CatalogService", request.app.state.catalog_service)
    except AttributeError as error:
        msg = "Catalog service is not configured"
        raise RuntimeError(msg) from error


async def media_not_found_exception_handler(_: Request, __: Exception) -> JSONResponse:
    """Return the common safe error envelope for an absent catalog media item.

    :return: HTTP 404 response without exposing the requested internal identity.
    """
    response = ErrorResponse(error=ErrorDetail(code="media_not_found", message="Media not found"))
    return JSONResponse(status_code=HTTPStatus.NOT_FOUND, content=response.model_dump())


def register_media_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced specifically by the media API.

    :param app: FastAPI application receiving the media-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {MediaNotFoundError: media_not_found_exception_handler}
    register_exception_handlers(app, handlers)


@router.get(
    "/media/{media_id}",
    response_model=MediaResponse,
    responses={HTTPStatus.NOT_FOUND: {"model": ErrorResponse, "description": "Media was not found."}},
)
async def get_media(
    media_id: UUID,
    catalog_service: Annotated[CatalogService, Depends(get_catalog_service)],
) -> MediaResponse:
    """Retrieve one shared catalog media item by internal identity.

    :param media_id: Internal UUID assigned to the media item.
    :param catalog_service: Injected catalog application service.
    :return: Explicit HTTP metadata for the requested movie or TV show.
    :raises MediaNotFoundError: If no shared catalog media has the requested identity.
    """
    media = await catalog_service.get(MediaId(media_id))
    if media is None:
        raise MediaNotFoundError
    return media_to_response(media)
