"""Common JSON error responses and exception-handler registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Request  # noqa: TC002 - FastAPI resolves this special parameter annotation at runtime.
from fastapi.responses import JSONResponse

from media_recommender.web.schemas.errors import ErrorDetail, ErrorResponse

if TYPE_CHECKING:
    from collections.abc import Mapping

    from fastapi import FastAPI
    from starlette.types import ExceptionHandler


def register_exception_handlers(
    app: FastAPI,
    handlers: Mapping[type[Exception], ExceptionHandler] | None = None,
) -> None:
    """Register optional specific handlers and the safe unexpected-error fallback.

    FastAPI and Starlette's existing handlers are intentionally retained. They
    continue to handle framework HTTP and validation exceptions more
    specifically than the ``Exception`` fallback registered here.

    :param app: Application receiving exception handlers.
    :param handlers: Optional feature-specific handlers supplied by later routes.
    """
    for exception_type, handler in (handlers or {}).items():
        app.add_exception_handler(exception_type, handler)
    app.add_exception_handler(Exception, unexpected_exception_handler)


async def unexpected_exception_handler(_: Request, __: Exception) -> JSONResponse:
    """Return a generic response without revealing an unexpected exception.

    :return: Safe HTTP 500 error envelope.
    """
    response = ErrorResponse(error=ErrorDetail(code="internal_error", message="Internal server error"))
    return JSONResponse(status_code=500, content=response.model_dump())
