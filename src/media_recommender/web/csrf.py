"""Minimal session-backed CSRF protection for server-rendered forms."""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated

from fastapi import Form, Request

CSRF_SESSION_KEY = "csrf_token"
CSRF_FIELD_NAME = "csrf_token"
_TOKEN_BYTES = 32


class CsrfValidationError(Exception):
    """Raised when a state-changing form submission fails CSRF validation."""


def get_csrf_token(request: Request) -> str:
    """Return the session CSRF token, creating one when the session has none.

    :param request: Incoming request carrying the signed server session.
    :return: Non-empty CSRF token bound to the current session.
    """
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        request.session[CSRF_SESSION_KEY] = token
    return token


def csrf_token_matches(request: Request, submitted: str) -> bool:
    """Compare a submitted token with the session token in constant time.

    :param request: Incoming request carrying the signed server session.
    :param submitted: Token value submitted by the browser form.
    :return: Whether the submitted token matches the session token.
    """
    expected = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(expected, str) or not expected:
        return False
    return hmac.compare_digest(expected, submitted)


def same_origin(request: Request) -> bool:
    """Return whether a browser-supplied ``Origin`` header matches this application.

    Requests without an ``Origin`` header are accepted so that non-browser
    clients and same-origin form posts remain usable; the session-bound CSRF
    token remains the primary control.

    :param request: Incoming request that may carry an ``Origin`` header.
    :return: Whether the request origin is absent or exactly matches the application.
    """
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return origin.rstrip("/") == str(request.base_url).rstrip("/")


def require_csrf_token(
    request: Request,
    csrf_token: Annotated[str, Form()] = "",
) -> None:
    """Reject a state-changing form submission without a valid CSRF token.

    :param request: Incoming request carrying the signed server session.
    :param csrf_token: Hidden form token submitted by the browser.
    :raises CsrfValidationError: If the origin or CSRF token is invalid.
    """
    if not same_origin(request) or not csrf_token_matches(request, csrf_token):
        raise CsrfValidationError
