"""확장 89호 — 터미널 로그인(RFC 8628). 보장할 것:
(1) 서버 주소 하나에서 로그인에 필요한 전부를 얻는다 — 클라이언트에 설정이 없다
(2) authorization_pending 과 slow_down 을 규약대로 다룬다 (늦추라면 늦춘다)
(3) 토큰 파일은 0600 이고, 임시 파일도 잠깐도 열리지 않는다
(4) 만료가 가까우면 조용히 갱신하고, 갱신이 거절되면 옛 토큰을 쓰지 않는다
(5) 기기 로그인을 열지 않은 서버에는 사람이 읽을 말로 거절한다
"""
from __future__ import annotations

import http.server
import json
import os
import threading
import time
import urllib.parse

import pytest

from casebook.adapters import device_login as dl

CID = "client_test_1"
ISS = ""          # 서버가 뜬 뒤에 채운다


class Fake:
    """인가 서버 + 자원 서버를 한 자리에 띄운다. 무엇을 어떻게 대답할지 시험이 정한다."""

    def __init__(self):
        self.token_calls = 0
        self.script: list[tuple[int, dict]] = []      # 토큰 주소가 차례로 내놓을 답
        self.device_status = 200
        self.device_body: dict | None = None
        self.expose_client_id = True
        self.last_form: dict[str, str] = {}
        self.seen_ua: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def _send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                outer.seen_ua.append(self.headers.get("User-Agent", ""))
                if self.path == dl.RESOURCE_METADATA:
                    doc = {"resource": outer.base + "/mcp", "authorization_servers": [outer.base],
                           "bearer_methods_supported": ["header"]}
                    if outer.expose_client_id:
                        doc["casebook_device_client_id"] = CID
                    return self._send(200, doc)
                if self.path == dl.AS_DISCOVERY:
                    return self._send(200, {
                        "issuer": outer.base,
                        "device_authorization_endpoint": outer.base + "/dev",
                        "token_endpoint": outer.base + "/tok"})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                outer.seen_ua.append(self.headers.get("User-Agent", ""))
                n = int(self.headers.get("Content-Length") or 0)
                outer.last_form = {k: v[0] for k, v in
                                   urllib.parse.parse_qs(self.rfile.read(n).decode()).items()}
                if self.path == "/dev":
                    return self._send(outer.device_status, outer.device_body or {
                        "device_code": "dev-code", "user_code": "ABCD-EFGH",
                        "verification_uri": outer.base + "/activate",
                        "verification_uri_complete": outer.base + "/activate?code=ABCD-EFGH",
                        "interval": 0, "expires_in": 600})
                if self.path == "/tok":
                    outer.token_calls += 1
                    status, body = outer.script.pop(0) if outer.script else (400, {"error": "bad"})
                    return self._send(status, body)
                self._send(404, {"error": "not found"})

            def log_message(self, *a):
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self):
        self.srv.shutdown(); self.srv.server_close()


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setenv("CASEBOOK_OAUTH_FILE", str(tmp_path / "oauth.json"))
    monkeypatch.delenv("CASEBOOK_LANG", raising=False)
    f = Fake()
    _RESOURCE[0] = f.base + "/mcp"
    yield f
    f.stop()


def _jwt(aud: str, name: str = "at-1") -> str:
    """서명 없는 JWT 꼴. 클라이언트는 aud 를 **검증 없이** 읽어 자원 지정 교환이 필요한지만 고른다."""
    import base64
    b64 = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'none'})}.{b64({'aud': aud, 'name': name})}.x"


def _ok(aud: str | None = None, **over):
    """기본은 aud 가 이미 우리 자원인 토큰 — 그러면 교환을 한 번 더 하지 않는다."""
    return {"access_token": _jwt(aud if aud is not None else _RESOURCE[0]),
            "refresh_token": "rt-1", "expires_in": 3600, **over}


_RESOURCE = [""]          # fake fixture 가 채운다 (base 를 알아야 만들 수 있다)


def _name(token: str) -> str:
    import base64
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))["name"]


# ── (1) 설정이 없다 ─────────────────────────────────────────────────────────
def test_서버_주소_하나에서_전부_얻는다(fake):
    cfg = dl.discover(fake.base)
    assert cfg["client_id"] == CID
    assert cfg["device"].endswith("/dev") and cfg["token"].endswith("/tok")


def test_경로에_토큰이_붙은_주소도_받는다(fake):
    """옛 설치본의 remote-url 은 .../mcp/<token> 이다. 로그인 뒤에는 경로에 토큰을 싣지 않는다."""
    cfg = dl.discover(fake.base + "/mcp/aaaa.bbbb")
    assert cfg["base"] == fake.base


def test_기기_로그인을_안_연_서버는_읽을_수_있게_거절한다(fake):
    fake.expose_client_id = False
    with pytest.raises(dl.LoginError) as exc:
        dl.discover(fake.base)
    assert "운영자" in str(exc.value)


def test_메타데이터가_없으면_무엇을_하라고_말한다(fake):
    with pytest.raises(dl.LoginError) as exc:
        dl.discover("http://127.0.0.1:9")        # 아무도 없는 포트
    assert "설치 한 줄" in str(exc.value)


# ── (2) 규약대로 기다린다 ───────────────────────────────────────────────────
def test_pending_을_지나_받는다(fake):
    fake.script = [(400, {"error": "authorization_pending"}),
                   (400, {"error": "authorization_pending"}),
                   (200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert fake.token_calls == 3
    assert _name(dl.load()["access_token"]) == "at-1"


def test_slow_down_이면_간격을_늘린다(fake):
    waits: list[float] = []
    fake.script = [(400, {"error": "slow_down"}), (200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=waits.append)
    assert waits[1] > waits[0], waits          # RFC 8628 5.2 — 시키면 늦춘다


@pytest.mark.parametrize("err,말", [("expired_token", "만료"), ("access_denied", "거절")])
def test_끝난_이유를_사람_말로_알린다(fake, err, 말):
    fake.script = [(400, {"error": err})]
    with pytest.raises(dl.LoginError) as exc:
        dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert 말 in str(exc.value)


def test_기기_인증을_시작도_못하면_멈춘다(fake):
    fake.device_status, fake.device_body = 400, {"error": "invalid_client"}
    with pytest.raises(dl.LoginError):
        dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert fake.token_calls == 0               # 시작을 못 했으면 폴링도 안 한다


def test_공개_앱이라_secret_을_보내지_않는다(fake):
    fake.script = [(200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert "client_secret" not in fake.last_form
    assert fake.last_form["grant_type"] == dl.DEVICE_GRANT


def test_offline_access_를_함께_요청한다(fake):
    """refresh token 이 없으면 access token 이 만료될 때마다 사람이 다시 로그인해야 한다."""
    assert "offline_access" in dl.SCOPES


# ── (3) 파일 권한 ───────────────────────────────────────────────────────────
def test_토큰_파일은_0600_이다(fake):
    fake.script = [(200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    p = dl._path()
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    assert not p.with_suffix(".tmp").exists()  # 임시 파일이 남지 않는다


# ── (4) 갱신 ────────────────────────────────────────────────────────────────
def test_만료가_가까우면_조용히_갱신한다(fake):
    fake.script = [(200, _ok(expires_in=10)),              # 로그인: 곧 만료
                   (200, _ok(access_token=_jwt(_RESOURCE[0], "at-2"), refresh_token="rt-2", expires_in=3600))]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert _name(dl.access_token()) == "at-2"                     # 갱신이 일어났다
    assert dl.load()["refresh_token"] == "rt-2"            # 새 갱신 토큰도 저장된다
    assert oct(dl._path().stat().st_mode & 0o777) == "0o600"


def test_갱신이_거절되면_옛_토큰을_쓰지_않는다(fake):
    fake.script = [(200, _ok(expires_in=10)), (400, {"error": "invalid_grant"})]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert dl.access_token() is None                       # 조용히 옛 것으로 재시도하지 않는다


def test_아직_넉넉하면_망을_타지_않는다(fake):
    fake.script = [(200, _ok(expires_in=3600))]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    before = fake.token_calls
    assert _name(dl.access_token()) == "at-1"
    assert fake.token_calls == before                      # 갱신 요청을 보내지 않았다


def test_로그인_안_했으면_None_이다(fake):
    assert dl.access_token() is None
    assert dl.load() is None


def test_깨진_파일은_없는_것과_같다(fake):
    p = dl._path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ 망가짐", encoding="utf-8")
    assert dl.load() is None and dl.access_token() is None


# ── (5) CLI ─────────────────────────────────────────────────────────────────
def test_logout_은_파일을_지운다(fake, capsys):
    fake.script = [(200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert dl.main(["--logout"]) == 0 and not dl._path().exists()
    assert dl.main(["--logout"]) == 0                      # 두 번 불러도 죽지 않는다


def test_status_는_로그인_전에_1_을_돌려준다(fake):
    assert dl.main(["--status"]) == 1


def test_주소가_없으면_어떻게_쓰는지_말한다(fake, monkeypatch, capsys):
    monkeypatch.setenv("CASEBOOK_REMOTE_URL", "")
    monkeypatch.setenv("HOME", str(dl._path().parent.parent))   # remote-url 파일이 없는 곳
    assert dl.main([]) == 2
    assert "casebook-login" in capsys.readouterr().err


# ── (6) 프록시가 그 토큰을 쓴다 ─────────────────────────────────────────────
def _client_for(url):
    """프록시의 갈래를 직접 연다 — (붙을 주소, http 클라이언트)."""
    import anyio
    from casebook.adapters.mcp_proxy import _logged_in_client, _oauth_ready

    async def go():
        if not _oauth_ready():
            return url, None, None
        async with _logged_in_client(url) as (u, c):
            # 확장 103호 — 머리는 클라이언트에 박혀 있지 않다. 인증기가 요청마다 단다.
            return u, c, (_flow_token(c.auth) if c is not None and c.auth is not None else None)
    return anyio.run(go)


def test_로그인_전에는_경로_토큰_그대로다(fake):
    u, c, _ = _client_for(fake.base + "/mcp/pathtoken")
    assert u.endswith("/mcp/pathtoken") and c is None


def test_로그인하면_토큰이_주소에서_머리로_옮겨간다(fake):
    fake.script = [(200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    u, c, tok = _client_for(fake.base + "/mcp/pathtoken")
    assert u == fake.base + "/mcp"                  # 경로에서 토큰을 뗀다
    assert _name(tok) == "at-1"                     # 머리로 간다 (요청마다 인증기가 단다)
    assert "authorization" not in dict(c.headers)   # 박아 두지 않는다 — 300초 뒤에도 살아야 한다
    assert "pathtoken" not in u                     # 주소에 비밀이 남지 않는다


def test_갱신까지_실패하면_옛_경로로_되돌아간다(fake):
    """로그인 전에 쓰던 길이 아직 살아 있다 — 헤더로만 붙으면 아무도 못 들어간다."""
    fake.script = [(200, _ok(expires_in=10)), (400, {"error": "invalid_grant"})]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    u, c, _ = _client_for(fake.base + "/mcp/pathtoken")
    assert u.endswith("/mcp/pathtoken") and c is None


def test_기본_User_Agent_로_나가지_않는다(fake):
    """Cloudflare 는 Python-urllib 를 봇으로 보고 1010 으로 끊는다 — WorkOS 가 그 뒤에 있어서
    실제로 끊겼다(2026-09-14: UA 없이 403 browser_signature_banned, 이름을 넣으면 200).
    밖에 나가 보기 전에는 안 보이는 자리라 여기 묶어 둔다."""
    fake.script = [(200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert fake.seen_ua, "요청이 없었다"
    for ua in fake.seen_ua:
        assert ua == dl.USER_AGENT, ua
        assert "urllib" not in ua.lower()


# ── (7) RFC 8707 resource — 빼면 aud 가 남의 것이 된다 ──────────────────────
def test_기기_인증과_토큰_교환에_resource_를_싣는다(fake):
    """실측 2026-09-14: 빼면 AuthKit 이 aud 를 그 환경의 기본 클라이언트로 넣고
    (aud=client_01…) 우리 문이 InvalidAudienceError 로 거절한다.
    대시보드의 "Default" resource indicator 는 이 흐름에 적용되지 않았다."""
    seen = []
    orig = dl._post_form

    def spy(url, form, timeout=15):
        seen.append((url, dict(form)))
        return orig(url, form, timeout)

    fake.script = [(200, _ok())]
    dl._post_form = spy
    try:
        dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    finally:
        dl._post_form = orig
    want = fake.base + "/mcp"                       # 메타데이터의 resource
    dev = next(f for u, f in seen if u.endswith("/dev"))
    tok = next(f for u, f in seen if u.endswith("/tok"))
    assert dev.get("resource") == want, dev
    assert tok.get("resource") == want, tok


def test_갱신에도_resource_를_싣는다(fake):
    """빼면 갱신한 순간 aud 가 되돌아가 그때부터 조용히 401 이 된다."""
    seen = []
    orig = dl._post_form

    def spy(url, form, timeout=15):
        seen.append(dict(form))
        return orig(url, form, timeout)

    fake.script = [(200, _ok(expires_in=10)), (200, _ok(access_token=_jwt(_RESOURCE[0], "at-2"), expires_in=3600))]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    dl._post_form = spy
    try:
        assert _name(dl.access_token()) == "at-2"
    finally:
        dl._post_form = orig
    assert seen[-1].get("grant_type") == "refresh_token"
    assert seen[-1].get("resource") == fake.base + "/mcp", seen[-1]


# ── (8) 기기 코드 교환이 resource 를 무시할 때 ──────────────────────────────
def test_aud_가_남의_것이면_한_번_더_교환한다(fake):
    """실측 2026-09-14: AuthKit 은 resource 를 refresh_token 교환에서는 받고 device_code
    교환에서는 무시한다 — 양쪽에 실어도 aud 가 그 환경의 기본 클라이언트로 온다.
    로그인 직후 갱신 한 번으로 제자리를 찾는다."""
    fake.script = [(200, _ok(aud="client_남의것")),                       # 기기 코드 교환: 틀린 aud
                   (200, _ok(access_token=_jwt(_RESOURCE[0], "at-scoped"),
                             refresh_token="rt-2"))]                      # 자원 지정 교환
    seen = []
    orig = dl._post_form
    dl._post_form = lambda u, f, timeout=15: (seen.append(dict(f)), orig(u, f, timeout))[1]
    try:
        dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    finally:
        dl._post_form = orig
    assert _name(dl.load()["access_token"]) == "at-scoped"                # 교환한 것이 저장됐다
    assert dl.load()["refresh_token"] == "rt-2"                           # 회전도 따라간다
    last = seen[-1]
    assert last["grant_type"] == "refresh_token" and last["resource"] == _RESOURCE[0]


def test_aud_가_이미_맞으면_더_부르지_않는다(fake):
    """인가 서버가 고치면 왕복 하나가 사라진다 — 늘 교환하지 않는다."""
    fake.script = [(200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert fake.token_calls == 1


def test_교환이_안_되면_받은_것을_그대로_둔다(fake):
    """자원 지정이 실패해도 로그인 자체를 버리지 않는다 — 서버가 그 토큰을 거절할 뿐이고,
    그 거절문은 무엇이 잘못됐는지 말한다."""
    fake.script = [(200, _ok(aud="client_남의것")), (400, {"error": "invalid_grant"})]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    assert _name(dl.load()["access_token"]) == "at-1"


# ── (9) 서버에 붙는 넷이 모두 한 자리를 쓴다 (확장 96호) ────────────────────
def test_route_가_로그인_전후를_가른다(fake):
    """로그인 전에는 받은 주소 그대로·머리 없음, 뒤에는 토큰을 뗀 주소·Authorization."""
    before = dl.route(fake.base + "/mcp/pathtoken")
    assert before == (fake.base + "/mcp/pathtoken", {})
    fake.script = [(200, _ok())]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    to, hdr = dl.route(fake.base + "/mcp/pathtoken")
    assert to == fake.base + "/mcp" and "pathtoken" not in to
    assert _name(hdr["Authorization"].split()[1]) == "at-1"


def test_서버에_붙는_넷이_모두_route_를_쓴다():
    """확장 89호는 프록시만 고쳤고 창·훅·backfill 셋이 경로 토큰에 남았다. 그 상태로 구형 경로를
    닫으면 창이 안 뜨고 훅 다섯이 조용히 죽는다 — 사람은 기록이 쌓이는 줄 알고 계속 일한다.
    붙는 방법을 각자 알고 있으면 이 어긋남이 또 생기므로, 넷이 같은 자리를 부르는 것을 계약으로 둔다."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    # 창은 요청마다 route 를 부르므로 머리를 박을 일이 없다. 나머지 셋은 연결이 오래 살아서
    # 요청마다 다는 인증기를 써야 한다(확장 103호) — 어느 쪽이든 device_login 한 자리를 부른다.
    for rel, need in (("casebook/adapters/ui_server.py", "device_login import route"),
                      ("casebook/adapters/mcp_proxy.py", "device_login import route_auth"),
                      ("casebook/adapters/backfill.py", "device_login import route_auth"),
                      ("tools/hooks/casebook_hook.py", "device_login import route_auth")):
        src = (root / rel).read_text(encoding="utf-8")
        assert need in src, f"{rel} 가 {need.split()[-1]} 를 쓰지 않는다"


def test_넷_다_묶음에_들어_있다():
    """훅과 창이 route 를 부르는데 device_login 이 묶음에 없으면 설치본에서 죽는다(확장 94호)."""
    from casebook.adapters import client_dist as cd
    for mode, files in (("server", set(cd.CLIENT_FILES)),
                        ("local", set(cd.local_files(cd.source_root())))):
        for need in ("casebook/adapters/device_login.py", "casebook/adapters/ui_server.py",
                     "casebook/adapters/backfill.py", "tools/hooks/casebook_hook.py"):
            assert need in files, f"{mode} 묶음에 {need} 가 없다"


# ── (10) 확장 102호 — 서버에 붙는 http 클라이언트는 SDK 것이어야 한다 ──────
def test_맨_httpx_클라이언트를_만들지_않는다():
    """맨 httpx2.AsyncClient 는 timeout 5초에 follow_redirects 가 꺼져 있고, SDK 의
    create_mcp_http_client 는 connect 30초·read 300초에 redirect 를 따라간다.
    89호에서 헤더를 붙이려고 직접 만들면서 그 기본값을 잃었고, 그 뒤로 도구 호출이
    응답 없이 매달렸다(실측 2026-09-14: add_evidence 가 330초 넘게 running,
    같은 순간 서버 직접 호출은 0.03초)."""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("casebook/adapters/mcp_proxy.py", "casebook/adapters/backfill.py",
                "tools/hooks/casebook_hook.py"):
        src = (root / rel).read_text(encoding="utf-8")
        made = re.findall(r"httpx2?\.AsyncClient\(", src)
        assert not made, f"{rel} 가 맨 AsyncClient 를 만든다 — create_mcp_http_client 를 쓴다"
        if "create_mcp_http_client" in src:
            # 확장 103호 — 머리를 한 번 박으면 300초 뒤부터 전부 401 이다. 인증기를 넘긴다.
            assert "auth=auth" in src, f"{rel} 가 인증기를 안 넘긴다"
            assert "headers=headers" not in src, f"{rel} 가 아직 머리를 박는다 — 300초 뒤 401 이다"


def test_SDK_클라이언트가_실제로_더_넉넉하다():
    """이 시험이 도는 이유 자체를 붙잡아 둔다 — SDK 기본값이 맨 것보다 넉넉하지 않게 되면
    위 시험은 지키는 뜻이 없어진다."""
    import httpx2
    from mcp.shared._httpx_utils import create_mcp_http_client
    plain, sdk = httpx2.AsyncClient(), create_mcp_http_client()
    assert sdk.timeout.read > (plain.timeout.read or 0) * 10
    assert sdk.follow_redirects and not plain.follow_redirects


# ── (11) 확장 103호 — 토큰은 요청마다 새로 단다 (300초 뒤에도 붙어 있다) ─────
#
# 실측 2026-09-14: WorkOS access token 수명은 300초다(iat 12:48:52 · exp 12:53:52). 확장 89호가
# 붙을 때 받은 머리를 연결에 박아 두었고, 프록시는 몇 시간을 산다. Claude Code 로그를 붙은
# 시각 기준으로 가르면 경계가 정확히 300초다 — 성공 호출은 전부 283초 이내, 매달린 호출은
# 전부 362초 이후. OAuth 이전(경로 토큰, 만료 없음)에는 성공 968건이 최대 140,075초까지 멀쩡했다.
def _flow_token(auth, sync=True) -> str:
    """인증기를 한 요청 흘려 보내고, 그 요청에 실린 토큰을 돌려준다."""
    import httpx2
    req = httpx2.Request("POST", "http://example.test/mcp")
    if sync:
        next(auth.sync_auth_flow(req))
    else:
        import anyio
        async def go():
            gen = auth.async_auth_flow(req)
            await gen.__anext__()
            await gen.aclose()
        anyio.run(go)
    return req.headers["Authorization"].split()[1]


def test_인증기는_요청마다_그때_유효한_토큰을_단다(fake):
    """이것이 증상 자체를 보는 시험이다 — 같은 인증기가, 토큰이 만료된 뒤에도 새것을 단다.
    확장 89호처럼 머리를 한 번 박아 두면 이 시험은 옛 토큰을 보고 실패한다."""
    fake.script = [(200, _ok(expires_in=3600))]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    auth = dl.bearer()
    assert _name(_flow_token(auth)) == "at-1"

    saved = dl.load()                                   # 300초가 지난 것과 같은 상태로 만든다
    saved["expires_at"] = time.time() - 1
    dl._path().write_text(json.dumps(saved), encoding="utf-8")
    fake.script = [(200, _ok(access_token=_jwt(_RESOURCE[0], "at-2"), refresh_token="rt-2"))]

    assert _name(_flow_token(auth)) == "at-2"           # 같은 인증기가 새 토큰을 달았다
    assert _name(_flow_token(auth, sync=False)) == "at-2"   # 비동기 갈래도 같다


def test_route_auth_는_route_와_같은_판정을_한다(fake):
    """로그인 전이거나 갱신이 거절되면 주소를 그대로 두고 경로 토큰으로 간다 — route 와 같아야 한다.
    여기가 어긋나면 갱신이 거절된 순간 경로 토큰까지 잃고 아무 길도 남지 않는다."""
    url = fake.base + "/mcp/pathtoken"
    assert dl.route_auth(url) == (url, None)                    # 로그인 전
    fake.script = [(200, _ok(expires_in=3600))]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    to, auth = dl.route_auth(url)
    assert to == fake.base + "/mcp" and "pathtoken" not in to and auth is not None
    assert (to, bool(auth)) == (dl.route(url)[0], bool(dl.route(url)[1]))

    saved = dl.load()                                           # 갱신이 거절되는 상태
    saved["expires_at"] = time.time() - 1
    dl._path().write_text(json.dumps(saved), encoding="utf-8")
    fake.script = [(400, {"error": "invalid_grant"})]
    assert dl.route_auth(url) == (url, None)                    # 경로 토큰으로 되돌아간다


def test_갱신은_한_번에_하나만_한다(fake):
    """AuthKit 은 갱신할 때마다 refresh token 을 새것으로 바꾼다. 둘이 동시에 갱신하면 뒤엣것이
    이미 폐기된 것을 내밀어 로그인이 통째로 끊긴다 — 인증기를 요청마다 부르므로 실제로 겹친다."""
    fake.script = [(200, _ok(expires_in=3600))]
    dl.login(fake.base, show=lambda *_: None, sleep=lambda _: None)
    saved = dl.load()
    saved["expires_at"] = time.time() - 1
    dl._path().write_text(json.dumps(saved), encoding="utf-8")
    fake.script = [(200, _ok(access_token=_jwt(_RESOURCE[0], "at-2"), refresh_token="rt-2"))]

    before, got, start = fake.token_calls, [], threading.Barrier(8)
    def one():
        start.wait()
        got.append(dl.access_token())
    ts = [threading.Thread(target=one) for _ in range(8)]
    for t in ts: t.start()
    for t in ts: t.join()

    assert fake.token_calls - before == 1, "갱신 요청이 여러 번 나갔다 — refresh token 이 폐기된다"
    assert {_name(t) for t in got} == {"at-2"}         # 여덟 다 새 토큰을 받았다
