"""Server-rendered page and operational routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates  # noqa: TC002 - FastAPI resolves this dependency annotation at runtime.
from pydantic import BaseModel

router = APIRouter()


class LivenessResponse(BaseModel):
    """Response proving only that the HTTP process can serve a request."""

    status: Literal["ok"]


def get_templates(request: Request) -> Jinja2Templates:
    """Return templates initialized by the application factory.

    :param request: Incoming request carrying the current application.
    :return: Shared Jinja2 template renderer.
    """
    return request.app.state.templates


@router.get("/", response_class=HTMLResponse)
async def home(
    request: Request,
    templates: Annotated[Jinja2Templates, Depends(get_templates)],
) -> HTMLResponse:
    """Render the minimal application home page.

    :param request: Incoming browser request.
    :param templates: Shared Jinja2 template renderer.
    :return: Shared-layout home page.
    """
    return templates.TemplateResponse(request, "home.html")


@router.get("/health/live", response_model=LivenessResponse)
async def liveness() -> LivenessResponse:
    """Report that the HTTP process is able to serve requests.

    :return: Liveness status without database or provider access.
    """
    return LivenessResponse(status="ok")
