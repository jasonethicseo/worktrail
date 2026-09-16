"""확장 47호 — 로컬 창(ui_server)이 POST 도 중계한다. 보장할 것:
(1) 화면의 POST 본문이 그대로 진짜 서버에 닿고, Authorization 은 브라우저 것이 아니라 로컬 창의 토큰이다
(2) 진짜 서버의 상태 코드와 본문이 그대로 돌아온다(400 도)
(3) /api 밖의 POST 는 404"""
from __future__ import annotations

import http.server
import json
import threading
import urllib.error
import urllib.request

import pytest

from casebook.adapters import ui_server


def _serve(handler):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture()
def upstream():
    seen = []

    class Up(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def _reply(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_GET(self):
            seen.append(("GET", self.path, self.headers.get("Authorization"), None)); self._reply(200, {"ok": "get"})
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n)
            seen.append(("POST", self.path, self.headers.get("Authorization"), json.loads(raw)))
            body = json.loads(raw)
            if body.get("merge_into") == body.get("topic_id"):
                return self._reply(400, {"message": "a topic cannot be merged into itself"})
            self._reply(200, {"topic_id": body.get("merge_into"), "merged": {"moved": 2}})

    srv, base = _serve(Up)
    yield base, seen
    srv.shutdown()


def test_post_는_본문을_그대로_넘기고_토큰은_로컬_창_것이다(upstream):
    base, seen = upstream
    srv, local = _serve(ui_server._handler(base, base, "LOCAL-TOKEN"))
    try:
        req = urllib.request.Request(local + ui_server.API_PREFIX + "/app/topic", method="POST",
                                     data=json.dumps({"topic_id": 5, "merge_into": 1}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer via-local-proxy"})
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200 and json.loads(r.read()) == {"topic_id": 1, "merged": {"moved": 2}}
        assert seen[-1] == ("POST", "/app/topic", "Bearer LOCAL-TOKEN", {"topic_id": 5, "merge_into": 1})   # (1)

        req = urllib.request.Request(local + ui_server.API_PREFIX + "/app/topic", method="POST",
                                     data=json.dumps({"topic_id": 1, "merge_into": 1}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer via-local-proxy"})
        with pytest.raises(urllib.error.HTTPError) as e:                                                    # (2)
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 400 and json.loads(e.value.read())["message"].endswith("into itself")

        req = urllib.request.Request(local + "/elsewhere", method="POST", data=b"{}")
        with pytest.raises(urllib.error.HTTPError) as e:                                                    # (3)
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 404

        req = urllib.request.Request(local + ui_server.API_PREFIX + "/app/topic",
                                     headers={"Authorization": "Bearer via-local-proxy"})
        with urllib.request.urlopen(req, timeout=5) as r:                                                   # GET 은 종전대로
            assert json.loads(r.read()) == {"ok": "get"} and seen[-1][0] == "GET"
    finally:
        srv.shutdown()


def test_로컬_창은_아무_요청이나_중계하지_않는다(upstream):
    """127.0.0.1 에 떠 있어도 문지기가 필요하다 — 이 창은 사용자의 토큰을 들고 있다.
    전에는 검사가 없어 이 포트에 닿는 무엇이든(다른 프로세스·브라우저의 남의 탭) 사용자 권한 전부를 썼다."""
    base, seen = upstream
    srv, local = _serve(ui_server._handler(base, base, "LOCAL-TOKEN", "SESSION-KEY"))
    url = local + ui_server.API_PREFIX + "/app/topic"
    try:
        def refused(headers) -> tuple[int, str]:
            req = urllib.request.Request(url, headers=headers)
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=5)
            return e.value.code, json.loads(e.value.read())["message"]

        before = len(seen)
        code, msg = refused({})                                        # 열쇠 없이
        assert code == 403 and "이 창이 낸 페이지가 아니다" in msg
        code, msg = refused({"Authorization": "Bearer WRONG"})         # 틀린 열쇠
        assert code == 403
        code, msg = refused({"Authorization": "Bearer SESSION-KEY",    # 남의 페이지에서
                             "Origin": "https://evil.example"})
        assert code == 403 and "다른 페이지에서 온 요청" in msg
        assert len(seen) == before                                     # 진짜 서버까지 가지 않았다

        req = urllib.request.Request(url, headers={"Authorization": "Bearer SESSION-KEY"})
        with urllib.request.urlopen(req, timeout=5) as r:              # 제 열쇠면 지난다
            assert json.loads(r.read()) == {"ok": "get"}
        assert seen[-1][2] == "Bearer LOCAL-TOKEN"                     # 진짜 토큰은 여기서 붙는다
    finally:
        srv.shutdown()


def test_주입된_페이지가_그_판의_열쇠를_들고_간다():
    """열쇠는 창이 낸 페이지에만 들어간다 — 페이지를 받지 않은 쪽은 알 수 없다."""
    html = b'<head></head><body>' + ui_server.CONFIG_TAG.encode() + b'</body>'
    out = ui_server.inject(html, ui_server.API_PREFIX, "K-123").decode()
    assert '"K-123"' in out and "via-local-proxy" not in out
