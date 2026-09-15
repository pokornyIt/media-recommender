"""Operational HTTP response schemas."""

from typing import Literal

from pydantic import BaseModel


class LivenessResponse(BaseModel):
    """Response proving only that the HTTP process can serve a request."""

    status: Literal["ok"]
