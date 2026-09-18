"""ASGI middleware that bounds request bodies before multipart parsing."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from starlette.responses import HTMLResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

_CONTENT_LENGTH_HEADER = b"content-length"
_TOO_LARGE_BODY = "<h1>Request rejected</h1><p>The upload exceeds the maximum allowed size.</p>"


class RequestBodyLimitMiddleware:
    """Reject oversized request bodies before the application can spool them.

    The middleware checks a declared ``Content-Length`` first and then counts the
    actual streamed bytes, so a missing or dishonest length cannot bypass the
    limit. Matching requests that exceed the limit receive a sanitized response
    and never reach the wrapped application.
    """

    def __init__(self, app: ASGIApp, *, path: str, max_bytes: int) -> None:
        """Store the guarded path and byte limit.

        :param app: Wrapped ASGI application.
        :param path: Exact request path whose body is bounded.
        :param max_bytes: Maximum accepted request body size in bytes.
        """
        self._app = app
        self._path = path
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Bound the body of matching requests and delegate all others.

        :param scope: ASGI connection scope.
        :param receive: ASGI receive callable.
        :param send: ASGI send callable.
        """
        if not self._matches(scope):
            await self._app(scope, receive, send)
            return
        if _declared_length(scope) > self._max_bytes:
            await _send_too_large(scope, receive, send)
            return
        await self._run_limited(scope, receive, send)

    def _matches(self, scope: Scope) -> bool:
        """Return whether the scope is the guarded state-changing request.

        :param scope: ASGI connection scope.
        :return: Whether the request body should be bounded.
        """
        return scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == self._path

    async def _run_limited(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the wrapped application with a bounded receive stream.

        :param scope: ASGI HTTP connection scope.
        :param receive: ASGI receive callable.
        :param send: ASGI send callable.
        :raises Exception: If the wrapped application raises and the body limit was not exceeded.
        """
        received = 0
        exceeded = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def guarded_send(message: Message) -> None:
            if not exceeded:
                await send(message)

        try:
            await self._app(scope, limited_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            await _send_too_large(scope, receive, send)


def _declared_length(scope: Scope) -> int:
    """Return the declared ``Content-Length``, or ``-1`` when absent or invalid.

    :param scope: ASGI HTTP connection scope.
    :return: Declared body length, or ``-1`` when it cannot be trusted.
    """
    for name, value in scope.get("headers", ()):
        if name == _CONTENT_LENGTH_HEADER:
            try:
                return int(value)
            except ValueError:
                return -1
    return -1


async def _send_too_large(scope: Scope, receive: Receive, send: Send) -> None:
    """Send a sanitized response for an oversized request body.

    :param scope: ASGI HTTP connection scope.
    :param receive: ASGI receive callable.
    :param send: ASGI send callable.
    """
    response = HTMLResponse(content=_TOO_LARGE_BODY, status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
    await response(scope, receive, send)
