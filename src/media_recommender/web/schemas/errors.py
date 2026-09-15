"""Common HTTP error response schemas."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """A stable public description of one HTTP error."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Common JSON envelope for errors handled by this interface layer."""

    error: ErrorDetail
