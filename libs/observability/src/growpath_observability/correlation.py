import uuid
from contextvars import ContextVar

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = "X-Correlation-Id"

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def get_correlation_id() -> str | None:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


class CorrelationIdMiddleware:
    """Reuses the gateway's correlation ID or mints one, and echoes it in the response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._header = CORRELATION_HEADER.lower().encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        correlation_id = self._incoming_id(scope) or str(uuid.uuid4())
        token = _correlation_id.set(correlation_id)

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((self._header, correlation_id.encode()))
            await send(message)

        try:
            await self._app(scope, receive, send_with_header)
        finally:
            _correlation_id.reset(token)

    def _incoming_id(self, scope: Scope) -> str | None:
        for name, value in scope["headers"]:
            if name == self._header and value:
                return value.decode()
        return None
