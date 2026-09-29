"""Offline tests for the production ASGI entry point."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from media_recommender.asgi import app

if TYPE_CHECKING:
    from httpx import Client


def _client(application: FastAPI) -> Client:
    """Return a statically typed synchronous client for an application.

    :param application: Application to exercise.
    :return: HTTPX-compatible test client.
    """
    return cast("Client", TestClient(application))


def test_asgi_entry_point_exposes_fastapi_application() -> None:
    """Expose the Web application factory result as the ASGI application."""
    assert isinstance(app, FastAPI)


def test_asgi_entry_point_serves_liveness() -> None:
    """Serve the versionless liveness endpoint without external dependencies."""
    response = _client(app).get("/health/live")

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"status": "ok"}
