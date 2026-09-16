"""casebook ui — 로컬 창 (확장 38호). 화면을 브라우저에 띄우되 **토큰은 브라우저에 가지 않는다**.

    casebook-ui            # ~/.casebook/client/bin/casebook-ui — 브라우저가 열린다

왜 이렇게: 비밀은 한 곳에만 둔다는 규칙을 웹 화면에서도 지킨다. 주소에 토큰을 실으면 브라우저 기록·북마크·
스크린샷으로 사본이 늘고, 사용자마다 출처 허용목록을 늘리거나 비밀번호를 만들어 주면 사람이 늘 때마다 손이 간다.
대신 이미 토큰을 들고 있는 클라이언트가 창 노릇을 한다:

- 화면(HTML)은 원격 문에서 받아 온다(`GET <remote-url>/ui`). 그래서 UI 를 고쳐도 사용자는 아무것도 안 해도 된다 —
  클라이언트는 계속 얼어 있다(mcp_proxy 와 같은 원칙).
- 페이지가 부르는 요청은 `/api/...` 로 오고, 이 프로세스가 진짜 서버로 넘기면서 `Authorization` 을 **여기서** 붙인다.
  브라우저는 진짜 토큰을 본 적이 없다. localStorage 에 넣는 값은 자리표시자다.
- 페이지와 요청이 둘 다 127.0.0.1 이라 CORS 가 아예 없다. 로그인 화면도 없다.

127.0.0.1 에만 묶는다. 이 프로세스를 끄면 창도 닫힌다.
"""
from __future__ import annotations

import hmac
import http.server
import json
import os
import pathlib
import secrets
import socket
import sys
import time
import threading
import urllib.error
import urllib.request
import webbrowser

from casebook.core.clientmode import say

USER_AGENT = "casebook-ui/1.0 (+https://github.com/jasonethicseo/casebook)"


API_PREFIX = "/api"
DEFAULT_PORT = 8788
TIMEOUT = 20

# 원본의 두 config 스크립트를 이것으로 갈아 끼운다 — 주소는 이 프로세스, 토큰 자리에는 자리표시자.
CONFIG_TAG = ('<script src="../app/config.js"></script><script src="../app/config.local.js"></script>')
# 진짜 토큰이 아니다 — 프록시가 헤더를 다시 쓴다. 이 창이 돌아가는 동안만 유효한 한 판짜리 값이고,
# /api/* 로 오는 요청이 이 페이지에서 나온 것인지 가리는 데 쓴다(아래 _proxy).
CONFIG_INJECT = (
    '<script>window.CASEBOOK_CONFIG={{APP_BASE:"{p}/app",AUTH_BASE:"{p}/auth",POLL_MS:2000,'
    'STUCK_PENDING_MS:90000,INVITE_REQUIRED:true,LANG:"{lang}"}};'
    'try{{localStorage.setItem("cb_token","{k}");}}catch(e){{}}</script>'
)

PLACEHOLDER = """<!doctype html><meta charset="utf-8"><title>Worktrail</title>
<body style="background:#0f1216;color:#c8d0da;font:14px/1.6 -apple-system,sans-serif;padding:60px;text-align:center">
<p>이 화면은 아직 없다. <a href="/" style="color:#7aa2f7">현황으로 돌아가기</a></p>"""


def split_remote(url: str) -> tuple[str, str]:
    """https://host/mcp/<token> → (https://host, <token>). 형식이 다르면 그대로 알린다."""
    url = url.rstrip("/")
    marker = "/mcp/"
    if marker not in url:
        raise ValueError(f"remote url 이 https://<host>/mcp/<token> 꼴이 아니다: {url}")
    origin, token = url.split(marker, 1)
    if "/" in token or not token:
        raise ValueError(f"remote url 의 토큰 자리가 이상하다: {url}")
    return origin, token


def local_page() -> pathlib.Path | None:
    """이 저장소 안에서 돌고 있으면 그 화면 파일. UI 를 고치는 사람은 배포 없이 새로고침만 하면 된다.

    서버 모드 설치본에는 web/ 가 없어 None 이고, 화면은 원격 문에서 받는다 — 고치면 바로 반영된다.
    로컬 모드 설치본에는 web/ 가 들어 있어(LOCAL_GLOBS) 여기서 걸린다. 그쪽은 화면이 받은 시점에
    얼어 있고, 설치 한 줄을 다시 실행해야 새 화면이 온다 — 아무것도 밖으로 안 보낸다는 약속의 대가다."""
    override = os.environ.get("CASEBOOK_UI_SOURCE")
    if override:
        p = pathlib.Path(override).expanduser()
        return p if p.is_file() else None
    p = pathlib.Path(__file__).resolve().parents[2] / "web/worktrail/index.html"
    return p if p.is_file() else None


def fetch_page(base: str) -> bytes:
    """화면을 가져온다. 저장소 안이면 그 파일(고치는 즉시 반영), 아니면 원격 문에서 받아 온다.
    원격에서 받으므로 서버가 진실이고, UI 를 고쳐도 설치본은 그대로다."""
    p = local_page()
    if p is not None:
        return p.read_bytes()
    # 확장 98호 — 로그인해 두었으면 경로에서 토큰을 떼고 머리로 받는다. 여기를 빠뜨려서
    # 구형 경로를 닫자 창이 API 가 아니라 **첫 화면**에서 502 였다(실측 2026-09-14).
    from casebook.adapters.device_login import route
    to, headers = route(base)
    req = urllib.request.Request(f"{to.rstrip('/')}/ui", headers={**headers, "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:      # noqa: S310
        return r.read()


def inject(html: bytes, prefix: str = API_PREFIX, session_key: str = "via-local-proxy",
           lang: str = "") -> bytes:
    """config 두 줄을 이 프로세스를 가리키는 한 줄로 바꾼다."""
    text = html.decode("utf-8")
    if CONFIG_TAG not in text:
        raise ValueError("화면에서 config 스크립트를 찾지 못했다 — 서버 화면이 바뀐 것 같다")
    return text.replace(CONFIG_TAG, CONFIG_INJECT.format(p=prefix, k=session_key, lang=lang), 1).encode("utf-8")


def _handler(base: str, origin: str, token: str, session_key: str = "via-local-proxy"):
    """127.0.0.1 에 떠 있어도 문지기는 필요하다. 이 창은 사용자의 토큰을 들고 있으므로,
    /api/* 로 오는 요청이 이 창이 낸 페이지에서 온 것인지 셋으로 가린다:
    한 판짜리 열쇠(session_key) · Host 가 루프백인지(DNS 리바인딩) · 남의 origin 이 아닌지.
    전에는 아무 검사도 없어 이 포트에 닿는 무엇이든 사용자 권한 전부를 썼다."""
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):        # 조용히 — 주소에 아무것도 안 남긴다
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:                                   # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    from casebook.core import clientmode
                    return self._send(200, inject(fetch_page(base), API_PREFIX, session_key,
                                                  clientmode.lang()), "text/html; charset=utf-8")
                except Exception as exc:                            # noqa: BLE001
                    msg = f"<pre>화면을 받아오지 못했다:\n{exc}</pre>".encode()
                    return self._send(502, msg, "text/html; charset=utf-8")
            if path.startswith(API_PREFIX + "/"):
                return self._proxy(path[len(API_PREFIX):])
            return self._send(200, PLACEHOLDER.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:                                  # noqa: N802
            """확장 47호 — 화면이 쓰는 것(주제 합치기·옮기기)도 같은 길로 간다. 본문은 그대로 넘기고 토큰만 붙인다."""
            path = self.path.split("?", 1)[0]
            if not path.startswith(API_PREFIX + "/"):
                return self._send(404, json.dumps({"message": f"not found: {path}"}).encode(), "application/json")
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            return self._proxy(path[len(API_PREFIX):], method="POST", data=body,
                               ctype=self.headers.get("Content-Type") or "application/json")

        def _allowed(self) -> str | None:
            """이 요청을 중계해도 되는가. 안 되면 거절 사유(사람 말)를 돌려준다.

            셋을 본다. (1) Host 가 루프백인가 — 아니면 DNS 리바인딩으로 남의 페이지가 이 포트에
            닿은 것이다. (2) Origin 이 있다면 우리 것인가 — 브라우저가 붙여 보내므로 남의 페이지를
            가려낸다. (3) 이 창이 낸 페이지가 들고 있는 한 판짜리 열쇠를 가져왔는가."""
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
                return f"Host 가 루프백이 아니다: {host!r}"
            origin_hdr = (self.headers.get("Origin") or "").strip()
            if origin_hdr:
                allowed = {f"http://127.0.0.1:{self.server.server_address[1]}",
                           f"http://localhost:{self.server.server_address[1]}"}
                if origin_hdr not in allowed:
                    return f"다른 페이지에서 온 요청이다: {origin_hdr!r}"
            auth = (self.headers.get("Authorization") or "")
            got = auth[len("Bearer "):].strip() if auth.startswith("Bearer ") else ""
            if not hmac.compare_digest(got, session_key):
                return "이 창이 낸 페이지가 아니다 — 창을 새로고침한다"
            return None

        def _proxy(self, upstream_path: str, method: str = "GET", data: bytes | None = None,
                   ctype: str = "application/json") -> None:
            """진짜 서버로 넘긴다. Authorization 은 브라우저가 보낸 것을 버리고 여기서 붙인다."""
            refuse = self._allowed()
            if refuse is not None:
                return self._send(403, json.dumps({"message": refuse}, ensure_ascii=False).encode(),
                                  "application/json")
            query = self.path.split("?", 1)
            url = origin + upstream_path + ("?" + query[1] if len(query) > 1 else "")
            # 확장 96호 — 로그인해 두었으면 그 토큰, 아니면 주소에 실린 경로 토큰.
            # route 가 한 자리에서 정한다(붙는 방법을 창이 따로 알지 않는다).
            from casebook.adapters.device_login import route
            _to, oauth_hdr = route(origin)
            headers = {"Accept": "application/json",
                       **(oauth_hdr or {"Authorization": f"Bearer {token}"})}
            if data is not None:
                headers["Content-Type"] = ctype
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return self._send(r.status, r.read(), r.headers.get("content-type", "application/json"))
            except urllib.error.HTTPError as e:
                return self._send(e.code, e.read() or b"{}", e.headers.get("content-type", "application/json"))
            except Exception as exc:                                # noqa: BLE001
                body = json.dumps({"message": f"서버에 닿지 못했다: {exc}"}).encode()
                return self._send(502, body, "application/json")

    return Handler


def pick_port(preferred: int = DEFAULT_PORT) -> int:
    for port in range(preferred, preferred + 10):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit(say(
        f"{preferred}부터 10개 포트가 모두 쓰이고 있다 — CASEBOOK_UI_PORT 로 지정한다.",
        f"ports {preferred}-{preferred + 9} are all taken — set CASEBOOK_UI_PORT to pick one."))


def start_local_api() -> tuple[str, str]:
    """로컬 모드 — 기록이 이 맥에만 있을 때. 같은 프로세스에서 API 를 띄우고 그 주소와 토큰을 준다.
    창의 나머지는 원격일 때와 똑같다 — 어디로 중계하느냐만 다르다."""
    import threading as _th

    import uvicorn

    from casebook.adapters.http_api import create_app
    from casebook.adapters.mcp_server import build_from_env
    from casebook.core import auth

    cb, uid = build_from_env()
    token = auth.create_token(cb._secret(), uid, 10 * 365 * 86400)
    port = int(os.environ.get("CASEBOOK_API_PORT") or pick_port(8787))
    app = create_app(cb, None, cors_origins=["http://127.0.0.1", "http://localhost"])
    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    _th.Thread(target=server.run, daemon=True).start()
    for _ in range(80):                       # 뜰 때까지 기다린다 — 창이 먼저 열리면 빈 화면이 된다
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", token


def frozen_notice() -> list[str]:
    """로컬 모드 설치본은 화면이 받은 시점에 얼어 있다. 어느 판을 보고 있는지와 갱신하는 법을 알린다.

    여기서 서버에 닿지는 않는다 — 새 판이 있는지 보는 것도 사람이 casebook-update 를 부를 때만이다."""
    root = pathlib.Path(__file__).resolve().parents[2]
    stamp = root / "VERSION"                                   # 설치본에만 있다(install.sh 가 쓴다)
    if not stamp.is_file():
        return []
    try:
        ver = stamp.read_text().strip()
    except OSError:
        return []
    return [say(f"  판 {ver} · 새 판이 나왔는지 보려면 {root / 'bin/casebook-update'}",
                f"  build {ver} · to check for a newer one, run {root / 'bin/casebook-update'}")]


def main() -> None:
    from casebook.core.clientmode import ModeError, resolve
    try:
        _mode, url = resolve()          # server 인데 주소가 없으면 멈춘다 — 빈 로컬 DB 를 열지 않는다
    except ModeError as exc:
        print(say(f"Worktrail 창: {exc}", f"Worktrail window: {exc}"))
        raise SystemExit(1) from None
    if url:
        origin, token = split_remote(url)
    else:
        print(say("기록이 이 맥에만 있다 (로컬 모드) — API 를 여기서 띄운다.",
                  "Records live on this machine only (local mode) — starting the API here."))
        origin, token = start_local_api()
        url = origin
    port = int(os.environ.get("CASEBOOK_UI_PORT") or pick_port())
    session_key = secrets.token_urlsafe(32)          # 이 창이 도는 동안만 유효하다
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler(url, origin, token, session_key))
    local = f"http://127.0.0.1:{port}/"
    print(say(f"Worktrail 창: {local}   (끄려면 Ctrl+C)",
              f"Worktrail window: {local}   (Ctrl+C to stop)"))
    src = local_page()
    print(say(f"  화면 {src if src else origin + ' (원격)'} · 새로고침하면 다시 읽는다",
              f"  page {src if src else origin + ' (remote)'} · reload to read it again"))
    print(say(f"  기록 {origin} · 토큰은 브라우저에 가지 않는다",
              f"  records {origin} · the token never reaches the browser"))
    for line in frozen_notice():
        print(line)
    if not os.environ.get("CASEBOOK_UI_NO_BROWSER"):
        threading.Timer(0.4, lambda: webbrowser.open(local)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(say("\n창을 닫았다.", "\nWindow closed."))
    finally:
        server.server_close()


if __name__ == "__main__":
    sys.exit(main())
