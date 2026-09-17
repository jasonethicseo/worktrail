"""Small ASGI boundary that rejects request bodies before application parsing."""

from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_MAX_REQUEST_BYTES = 1_048_576


def configured_limit() -> int:
    raw = os.environ.get("CASEBOOK_MAX_REQUEST_BYTES", str(DEFAULT_MAX_REQUEST_BYTES)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("CASEBOOK_MAX_REQUEST_BYTES must be an integer") from None
    if value < 1:
        raise ValueError("CASEBOOK_MAX_REQUEST_BYTES must be positive")
    return value


class RequestSizeLimit:
    def __init__(self, app: Any, max_bytes: int | None = None) -> None:
        self.app = app
        self.max_bytes = configured_limit() if max_bytes is None else max_bytes
        if hasattr(app, "lifespan"):
            self.lifespan = app.lifespan

    def __getattr__(self, name: str) -> Any:
        """Keep framework inspection working through this transparent ASGI wrapper."""
        return getattr(self.app, name)

    async def _reject(self, send) -> None:
        body = json.dumps({
            "message": f"request too large; maximum is {self.max_bytes} bytes",
            "error": f"request too large; maximum is {self.max_bytes} bytes",
        }).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > self.max_bytes:
            return await self._reject(send)

        messages, total = [], 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    return await self._reject(send)
                if not message.get("more_body", False):
                    break
            elif message.get("type") == "http.disconnect":
                break

        async def replay():
            if messages:
                return messages.pop(0)
            # After the body, wait on the real channel. mcp's SSE responses call receive() in a loop until
            # http.disconnect; answering at once with an empty request made that loop spin without yielding
            # and froze the whole server (2026-09-17).
            return await receive()

        return await self.app(scope, replay, send)
