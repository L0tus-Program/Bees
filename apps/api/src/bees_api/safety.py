"""Limites de entrada e cabeçalhos de proteção para a interface e API."""

import asyncio
from collections.abc import Awaitable, Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestSafetyMiddleware:
    def __init__(self, app: ASGIApp, max_body_bytes: int = 65536) -> None:
        self.app = app
        self.limit = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"content-security-policy",
                            b"default-src 'self'; script-src 'self'; style-src 'self'; "
                            b"img-src 'self' data:; connect-src 'self'; object-src 'none'; "
                            b"base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
                        ),
                    ]
                )
                message = {**message, "headers": headers}
            await send(message)

        if scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            lengths = [value for key, value in scope["headers"] if key == b"content-length"]
            try:
                if len(lengths) > 1:
                    raise ValueError()
                length = int(lengths[0]) if lengths else 0
                if length < 0:
                    raise ValueError()
            except ValueError:
                await self._error(scope, receive, secured_send, 400, "invalid_length")
                return
            if length > self.limit:
                await self._error(scope, receive, secured_send, 413, "body_too_large")
                return
            body = bytearray()
            try:
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        if len(body) + len(chunk) > self.limit:
                            await self._error(scope, receive, secured_send, 413, "body_too_large")
                            return
                        body.extend(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                await self._error(scope, receive, secured_send, 408, "body_timeout")
                return

            original_receive = receive
            delivered = False

            async def replay() -> Message:
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await original_receive()

            receive = replay
        await self.app(scope, receive, secured_send)

    @staticmethod
    async def _error(
        scope: Scope,
        receive: Receive,
        send: Callable[[Message], Awaitable[None]],
        status: int,
        code: str,
    ) -> None:
        response = JSONResponse(
            {"error": {"code": code, "message": "Requisição fora dos limites permitidos."}},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
