"""Read-only HTTP routes for deterministic recommendations."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from media_recommender.application import (
    Phase2Orchestrator,  # noqa: TC001 - FastAPI resolves this annotation at runtime.
)
from media_recommender.web.errors import register_exception_handlers
from media_recommender.web.schemas.errors import ErrorDetail, ErrorResponse
from media_recommender.web.schemas.recommendations import (
    RecommendationRequest,
    RecommendationsResponse,
    recommendation_to_response,
    request_to_criteria,
)

if TYPE_CHECKING:
    from starlette.types import ExceptionHandler

router = APIRouter()


class RecommendationCriteriaError(Exception):
    """Raised when validated HTTP data cannot form valid application criteria."""


def get_recommendation_service(request: Request) -> Phase2Orchestrator:
    """Return the recommendation facade configured by the composition root.

    :param request: Incoming request carrying the current application instance.
    :return: Phase 2 recommendation application facade.
    :raises RuntimeError: If no recommendation service is configured.
    """
    try:
        return cast("Phase2Orchestrator", request.app.state.recommendation_service)
    except AttributeError as error:
        msg = "Recommendation service is not configured"
        raise RuntimeError(msg) from error


async def recommendation_criteria_exception_handler(_: Request, __: Exception) -> JSONResponse:
    """Return the common safe error envelope for invalid recommendation criteria.

    :return: HTTP 422 response without internal validation details.
    """
    response = ErrorResponse(
        error=ErrorDetail(code="recommendation_criteria_invalid", message="Invalid recommendation criteria")
    )
    return JSONResponse(status_code=HTTPStatus.UNPROCESSABLE_ENTITY, content=response.model_dump())


def register_recommendation_exception_handlers(app: FastAPI) -> None:
    """Register error handling introduced specifically by the recommendation API.

    :param app: FastAPI application receiving the recommendation-specific handler.
    """
    handlers: dict[type[Exception], ExceptionHandler] = {
        RecommendationCriteriaError: recommendation_criteria_exception_handler,
    }
    register_exception_handlers(app, handlers)


@router.post(
    "/recommendations",
    response_model=RecommendationsResponse,
    responses={
        HTTPStatus.UNPROCESSABLE_ENTITY: {
            "model": ErrorResponse,
            "description": "Recommendation criteria were invalid.",
        }
    },
)
async def recommend(
    request: RecommendationRequest,
    recommendation_service: Annotated[Phase2Orchestrator, Depends(get_recommendation_service)],
) -> RecommendationsResponse:
    """Return accepted deterministic recommendations for explicit JSON criteria.

    This POST operation is read-only computation; it neither changes application
    state nor requires the future HTML-form CSRF convention.

    :param request: Explicit JSON hard constraints.
    :param recommendation_service: Injected deterministic application facade.
    :return: Ordered accepted ranked recommendations only.
    :raises RecommendationCriteriaError: If HTTP criteria violate application invariants.
    """
    try:
        criteria = request_to_criteria(request)
    except ValueError as error:
        raise RecommendationCriteriaError from error
    result = await recommendation_service.recommend(criteria)
    return RecommendationsResponse(
        recommendations=tuple(recommendation_to_response(item) for item in result.recommendations)
    )
