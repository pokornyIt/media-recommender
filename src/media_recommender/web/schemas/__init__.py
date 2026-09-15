"""Reusable public HTTP schemas for the Web interface."""

from media_recommender.web.schemas.errors import ErrorDetail, ErrorResponse
from media_recommender.web.schemas.health import LivenessResponse

__all__ = ["ErrorDetail", "ErrorResponse", "LivenessResponse"]
