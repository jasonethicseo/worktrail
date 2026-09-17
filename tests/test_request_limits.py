"""Public HTTP doors reject oversized bodies before parsing or authentication."""

from fastapi.testclient import TestClient

from casebook.adapters import http_api
from casebook.adapters.mcp_server import build_http_app
from casebook.core.db import SqliteDB
from casebook.core.worktrail import Worktrail


def test_api_rejects_oversized_request(monkeypatch):
    monkeypatch.setenv("CASEBOOK_MAX_REQUEST_BYTES", "128")
    app = Worktrail(SqliteDB(":memory:"))
    client = TestClient(http_api.create_app(app, None), raise_server_exceptions=False)
    response = client.post("/auth/auth/login", content=b"x" * 129)
    assert response.status_code == 413
    assert "too large" in response.json()["message"]


def test_mcp_rejects_oversized_request(monkeypatch):
    monkeypatch.setenv("CASEBOOK_MAX_REQUEST_BYTES", "128")
    app = Worktrail(SqliteDB(":memory:"))
    with TestClient(build_http_app(app), raise_server_exceptions=False) as client:
        response = client.post("/mcp", content=b"x" * 129)
    assert response.status_code == 413
    assert "too large" in response.json()["message"]


def test_replay_waits_for_the_real_receive_after_the_body():
    """Production MCP door froze at 100% CPU on 2026-09-17 20:00 KST. py-spy showed the main thread in
    RequestSizeLimit.replay called from mcp's watch_disconnect, which loops on receive() until http.disconnect
    while an SSE response is open. Once the buffered body was handed out, replay returned an empty
    http.request without awaiting, so that loop never saw the disconnect and never yielded to the event loop."""
    import asyncio
    from casebook.adapters.request_limits import RequestSizeLimit

    seen = []

    async def app(scope, receive, send):
        for _ in range(100):                      # the same loop as watch_disconnect, bounded so the test ends
            message = await receive()
            seen.append(message["type"])
            if message["type"] == "http.disconnect":
                return

    upstream = [{"type": "http.request", "body": b"{}", "more_body": False}, {"type": "http.disconnect"}]

    async def receive():
        await asyncio.sleep(0)
        return upstream.pop(0)

    scope = {"type": "http", "method": "POST", "headers": [(b"content-length", b"2")]}
    asyncio.run(RequestSizeLimit(app, max_bytes=128)(scope, receive, None))
    assert seen == ["http.request", "http.disconnect"]
