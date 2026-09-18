"""Minimal signed-session CSRF tokens for server-rendered state-changing forms.

This is deliberately not an authentication, account, or role system. A random
token is stored in an HMAC-signed cookie and mirrored as a hidden form field.
Submissions are compared in constant time before any application operation.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

COOKIE_NAME = "mr_csrf"
_TOKEN_BYTES = 32


def _sign(value: str, secret: bytes) -> str:
    """Return the HMAC-SHA256 signature of one token value.

    :param value: Random token value to sign.
    :param secret: Runtime session-signing secret.
    :return: Hexadecimal signature string.
    """
    return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()


def issue_signed_token(secret: bytes) -> str:
    """Return a fresh signed cookie value containing a random CSRF token.

    :param secret: Runtime session-signing secret.
    :return: Cookie-safe ``token.signature`` value.
    """
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    return f"{token}.{_sign(token, secret)}"


def session_token(cookie_value: str | None, secret: bytes) -> str | None:
    """Return the token from a signed cookie value, or ``None`` if invalid.

    :param cookie_value: Raw cookie value received from the browser.
    :param secret: Runtime session-signing secret.
    :return: The random token when the signature verifies, otherwise ``None``.
    """
    if not cookie_value or cookie_value.count(".") != 1:
        return None
    token, signature = cookie_value.split(".", 1)
    if not token or not signature:
        return None
    if not hmac.compare_digest(_sign(token, secret), signature):
        return None
    return token


def tokens_match(submitted: str | None, session_token_value: str | None) -> bool:
    """Return whether a submitted form token matches the session token.

    :param submitted: Token value from the submitted hidden form field.
    :param session_token_value: Token value recovered from the signed session cookie.
    :return: Whether both values are present and identical in constant time.
    """
    if not submitted or not session_token_value:
        return False
    return hmac.compare_digest(submitted, session_token_value)
