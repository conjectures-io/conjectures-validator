from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    """Bound HTTP bodies before authentication or JSON parsing."""

    def __init__(self, app: Any, maximum: int):
        self.app = app
        self.maximum = maximum

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: dict[bytes, list[bytes]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), []).append(value)
        encoding = headers.get(b"content-encoding", [])
        if encoding and any(value.lower() not in {b"", b"identity"} for value in encoding):
            await JSONResponse(
                {"detail": "compressed request bodies are not accepted"},
                status_code=415,
            )(scope, receive, send)
            return
        lengths = headers.get(b"content-length", [])
        if len(lengths) > 1:
            await JSONResponse({"detail": "ambiguous content length"}, status_code=400)(
                scope, receive, send
            )
            return
        if lengths:
            raw_length = lengths[0]
            if not raw_length or any(byte < 48 or byte > 57 for byte in raw_length):
                await JSONResponse({"detail": "invalid content length"}, status_code=400)(
                    scope, receive, send
                )
                return
            content_length = int(raw_length)
            if content_length > self.maximum:
                await JSONResponse({"detail": "request body is too large"}, status_code=413)(
                    scope, receive, send
                )
                return

        content = bytearray()
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            content.extend(message.get("body", b""))
            if len(content) > self.maximum:
                await JSONResponse({"detail": "request body is too large"}, status_code=413)(
                    scope, receive, send
                )
                return
            more = bool(message.get("more_body", False))

        delivered = False

        async def bounded_receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(content), "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, bounded_receive, send)
