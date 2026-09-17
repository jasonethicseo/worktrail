"""확장 87호 — 남이 내준 토큰을 받는 문. 보장할 것:
(1) 켜기 전에는 아무것도 안 바뀐다 — issuer 가 없으면 종전 HMAC 경로뿐이다
(2) 서명·iss·aud·exp 중 하나만 어긋나도 거절한다
(3) 공개 키를 HMAC 비밀로 쓰는 알고리즘 혼동 공격이 안 통한다
(4) 모르는 이메일은 **행을 만들지 않고** 거절한다 (확장 75호가 막은 무제한 쓰기를 되열지 않는다)
(5) 구형 HMAC 토큰은 oauth 를 켜도 그대로 돈다
(6) 자원 서버 메타데이터는 인증 없이 읽히고, 401 이 그 주소를 머리에 실어 준다
"""
from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

from casebook.core import oauth

ISS = "https://issuer.test"
AUD = "https://casebook.test/mcp"
KID = "test-key-1"
JWKS_HITS: list[str] = []      # 키를 받으러 밖으로 나간 횟수 — fixture 가 채운다


# ── 열쇠 한 벌과 그것을 내주는 JWKS 파일 ─────────────────────────────────────
@pytest.fixture
def keypair(monkeypatch):
    """열쇠 한 벌과 그것을 내주는 JWKS 를 127.0.0.1 에 띄운다.

    file:// 로는 안 된다 — PyJWKClient 가 http/https 만 받는다(실측). 그 거절 때문에 처음에는
    "거절되어야 하는 토큰" 시험들이 JWKS 를 못 받아서 통과하고 있었다. 진짜로 키를 내주는
    자리를 두고, 통과해야 하는 것이 실제로 통과하는지를 함께 봐야 그 시험들이 뜻을 갖는다."""
    import http.server, threading
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    body = json.dumps({"keys": [jwk]}).encode()

    JWKS_HITS.clear()

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            JWKS_HITS.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):    # 조용히
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv(oauth.ISSUER_ENV, ISS)
    monkeypatch.setenv(oauth.AUDIENCE_ENV, AUD)
    monkeypatch.setenv(oauth.JWKS_ENV, f"http://127.0.0.1:{srv.server_address[1]}/jwks")
    oauth.reset_for_tests()
    yield key
    srv.shutdown(); srv.server_close()
    oauth.reset_for_tests()


def sign(key, alg="RS256", kid=KID, **claims):
    import jwt as pyjwt
    now = int(time.time())
    body = {"iss": ISS, "aud": AUD, "exp": now + 300, "iat": now,
            "email": "tester@x.test", **claims}
    return pyjwt.encode(body, key, algorithm=alg, headers={"kid": kid})


# ── (1) 기본은 꺼짐 ─────────────────────────────────────────────────────────
def test_설정이_없으면_꺼져_있다(monkeypatch):
    for k in (oauth.ISSUER_ENV, oauth.AUDIENCE_ENV, oauth.JWKS_ENV):
        monkeypatch.delenv(k, raising=False)
    oauth.reset_for_tests()
    assert oauth.enabled() is False
    with pytest.raises(oauth.OAuthError):
        oauth.verify("a.b.c")


def test_둘_중_하나만_있으면_켜지지_않는다(monkeypatch):
    monkeypatch.setenv(oauth.ISSUER_ENV, ISS)
    monkeypatch.delenv(oauth.AUDIENCE_ENV, raising=False)
    oauth.reset_for_tests()
    assert oauth.enabled() is False          # aud 없이 켜면 아무 자원의 토큰이나 받게 된다


# ── (2) 어긋나면 거절 ───────────────────────────────────────────────────────
def test_맞는_토큰은_통과한다(keypair):
    claims = oauth.verify(sign(keypair))
    assert claims["email"] == "tester@x.test" and claims["iss"] == ISS


@pytest.mark.parametrize("bad,why", [
    ({"aud": "https://other.test/mcp"}, "다른 자원에 발급된 토큰"),
    ({"iss": "https://evil.test"}, "다른 인가 서버"),
    ({"exp": int(time.time()) - 3600}, "만료"),
])
def test_한_칸만_어긋나도_거절한다(keypair, bad, why):
    with pytest.raises(oauth.OAuthError):
        oauth.verify(sign(keypair, **bad))


def test_모르는_kid_는_거절한다(keypair):
    with pytest.raises(oauth.OAuthError):
        oauth.verify(sign(keypair, kid="없는-키"))


def test_다른_열쇠로_서명한_것은_거절한다(keypair):
    from cryptography.hazmat.primitives.asymmetric import rsa
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(oauth.OAuthError):
        oauth.verify(sign(other))            # kid 는 맞지만 서명이 남의 것이다


def test_exp_가_없으면_거절한다(keypair):
    import jwt as pyjwt
    now = int(time.time())
    t = pyjwt.encode({"iss": ISS, "aud": AUD, "iat": now, "email": "tester@x.test"},
                     keypair, algorithm="RS256", headers={"kid": KID})
    with pytest.raises(oauth.OAuthError):
        oauth.verify(t)                      # 만료 없는 토큰은 영원한 토큰이다


# ── (3) 알고리즘 혼동 ───────────────────────────────────────────────────────
def _b64(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hand_rolled(header: dict, payload: dict, secret: bytes | None) -> str:
    """PyJWT 를 거치지 않고 손으로 만든다. 진짜 공격자는 우리 라이브러리의 가드를 쓰지 않는다 —
    pyjwt.encode 는 PEM 을 HMAC 비밀로 쓰는 것을 스스로 거절하므로 그것으로는 이 공격을 재현할 수 없다."""
    import hashlib, hmac
    body = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(payload).encode())}"
    sig = b"" if secret is None else hmac.new(secret, body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64(sig)}"


def test_공개키를_HMAC_비밀로_쓰는_공격이_안_통한다(keypair):
    """고전적인 알고리즘 혼동: 공개 JWKS 를 아무나 읽어 그것을 HS256 비밀로 삼아 서명한다.
    받는 쪽이 alg 를 토큰에서 읽으면 통과한다. algorithms 를 못박아 막는다."""
    from cryptography.hazmat.primitives import serialization
    pub = keypair.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    now = int(time.time())
    forged = _hand_rolled({"alg": "HS256", "typ": "JWT", "kid": KID},
                          {"iss": ISS, "aud": AUD, "exp": now + 300, "email": "tester@x.test"}, pub)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.verify(forged)
    # 거절 자체로는 부족하다 — PyJWT 에도 "비대칭 키를 HMAC 비밀로 쓰지 말라"는 가드가 따로 있어서,
    # ALGORITHMS 에 HS256 을 넣어도 그 가드가 대신 막아 시험이 통과해 버렸다(실측). 우리가 목록으로
    # 막은 것이 맞는지 보려면 거절한 이유가 알고리즘이어야 한다.
    assert "InvalidAlgorithm" in str(exc.value), exc.value


def test_none_알고리즘도_안_통한다(keypair):
    """서명 자리를 비우고 alg=none 이라고 말하는 토큰. 손으로 만들어야 한다(위와 같은 이유)."""
    now = int(time.time())
    forged = _hand_rolled({"alg": "none", "typ": "JWT", "kid": KID},
                          {"iss": ISS, "aud": AUD, "exp": now + 300, "email": "tester@x.test"}, None)
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.verify(forged)
    assert "InvalidAlgorithm" in str(exc.value), exc.value


# ── (4) 모르는 이메일은 행을 만들지 않는다 ──────────────────────────────────
def _api_stack(tmp_path):
    """API 문과 Worktrail 이 **같은 DB** 를 보게 세운다 — 창이 실제로 그 꼴이다."""
    from casebook.core.app import Casebook
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    from tests.test_http_surface import FakeLLM, FakeSearch
    db = SqliteDB(str(tmp_path / "w.db"))
    return Casebook(db=db, llm=FakeLLM(), search=FakeSearch()), Worktrail(db)


def _wt(tmp_path):
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    return Worktrail(SqliteDB(str(tmp_path / "w.db")))


def test_모르는_이메일은_계정을_만들지_않고_거절한다(keypair, tmp_path):
    wt = _wt(tmp_path)
    before = wt.db.count("user")
    with pytest.raises(oauth.OAuthError):
        oauth.user_of(wt.db, oauth.verify(sign(keypair, email="stranger@x.test")))
    assert wt.db.count("user") == before          # 확장 75호 — 로그인만으로 행이 늘지 않는다


def test_운영자가_먼저_만든_계정은_찾는다(keypair, tmp_path):
    wt = _wt(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    uid = oauth.user_of(wt.db, oauth.verify(sign(keypair)))
    assert uid == wt.db.get_by("user", "email", "tester@x.test")["id"]


def test_이메일이_없거나_미확인이면_거절한다(keypair, tmp_path):
    wt = _wt(tmp_path)
    for claims in ({"email": ""}, {"email_verified": False}):
        with pytest.raises(oauth.OAuthError):
            oauth.user_of(wt.db, oauth.verify(sign(keypair, **claims)))


def test_대소문자와_공백은_같은_계정이다(keypair, tmp_path):
    wt = _wt(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    assert oauth.user_of(wt.db, oauth.verify(sign(keypair, email="  TESTER@X.TEST  "))) > 0


# ── (5)(6) 문에 붙은 뒤 ─────────────────────────────────────────────────────
def _door(tmp_path):
    from starlette.testclient import TestClient
    from casebook.adapters.mcp_server import build_http_app
    wt = _wt(tmp_path)
    return wt, TestClient(build_http_app(wt))


def test_구형_HMAC_토큰은_oauth_를_켜도_그대로_돈다(keypair, tmp_path):
    from casebook.adapters.mcp_server import mint_token, user_from_request
    from starlette.requests import Request
    wt = _wt(tmp_path)
    wt.signup("사람", "old@x.test", "pw")
    tok = mint_token(wt, "old@x.test")
    assert not oauth.looks_like_jwt(tok)              # 꼴이 다르므로 갈래가 안 섞인다
    scope = {"type": "http", "path": "/mcp/" + tok, "headers": [], "method": "POST",
             "query_string": b"", "scheme": "https", "server": ("h", 443)}
    assert user_from_request(wt, Request(scope)) is not None


def test_메타데이터는_인증_없이_읽힌다(keypair, tmp_path):
    wt, c = _door(tmp_path)
    for path in ("/.well-known/oauth-protected-resource/mcp",
                 "/.well-known/oauth-protected-resource"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.json() == {"resource": AUD, "authorization_servers": [ISS],
                            "bearer_methods_supported": ["header"]}


def test_401_이_메타데이터_주소를_머리에_실어_준다(keypair, tmp_path):
    wt, c = _door(tmp_path)
    r = c.post("/mcp", json={})
    assert r.status_code == 401
    hdr = r.headers.get("www-authenticate", "")
    assert hdr.startswith("Bearer resource_metadata=")
    assert "/.well-known/oauth-protected-resource/mcp" in hdr


def test_꺼져_있으면_메타데이터도_401_머리도_없다(tmp_path, monkeypatch):
    for k in (oauth.ISSUER_ENV, oauth.AUDIENCE_ENV, oauth.JWKS_ENV):
        monkeypatch.delenv(k, raising=False)
    oauth.reset_for_tests()
    wt, c = _door(tmp_path)
    assert c.get("/.well-known/oauth-protected-resource/mcp").status_code == 404
    r = c.post("/mcp", json={})
    assert r.status_code == 401 and "www-authenticate" not in {k.lower() for k in r.headers}


# ── (7) 구형 경로 닫기 (확장 92호, D15352) ─────────────────────────────────
def test_켜지_않고_닫으면_부팅에서_막는다(monkeypatch):
    """oauth 를 안 켠 채 닫으면 아무도 못 들어온다. 조용히 전원을 잠그지 않는다."""
    for k in (oauth.ISSUER_ENV, oauth.AUDIENCE_ENV):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    oauth.reset_for_tests()
    assert oauth.only() is False                  # 그 조합 자체가 서지 않는다
    with pytest.raises(oauth.OAuthError) as exc:
        oauth.preflight()
    assert oauth.ONLY_ENV in str(exc.value)


def test_닫기_전에는_모두_구형으로_들어온다(keypair, monkeypatch, tmp_path):
    monkeypatch.delenv(oauth.ONLY_ENV, raising=False)
    assert oauth.only() is False
    assert oauth.legacy_allowed(3) and oauth.legacy_allowed(999)


def test_닫으면_적어_둔_계정만_구형으로_들어온다(keypair, monkeypatch):
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.setenv(oauth.LEGACY_USERS_ENV, "3, 23")
    assert oauth.only() is True
    assert oauth.legacy_users() == frozenset({3, 23})
    assert oauth.legacy_allowed(3) and oauth.legacy_allowed(23)
    assert not oauth.legacy_allowed(25)


def test_목록이_비면_아무도_안_열린다(keypair, monkeypatch):
    """기본이 빈 목록이다 — 적지 않으면 열리지 않는다. 문 전체를 여는 스위치가 아니다."""
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.delenv(oauth.LEGACY_USERS_ENV, raising=False)
    assert oauth.legacy_users() == frozenset()
    assert not oauth.legacy_allowed(3)


def test_못_읽는_값은_열어_주지_않는다(keypair, monkeypatch):
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.setenv(oauth.LEGACY_USERS_ENV, "3, 없는값, ., 23x")
    assert oauth.legacy_users() == frozenset({3})


def test_닫은_문은_구형_토큰을_401_로_돌려보낸다(keypair, tmp_path, monkeypatch):
    from casebook.adapters.mcp_server import mint_token
    from starlette.testclient import TestClient
    from casebook.adapters.mcp_server import build_http_app
    wt = _wt(tmp_path)
    wt.signup("옛사람", "old@x.test", "pw")
    wt.signup("웹커넥터", "web@x.test", "pw")
    old_tok = mint_token(wt, "old@x.test")
    web_uid = wt.db.get_by("user", "email", "web@x.test")["id"]
    web_tok = mint_token(wt, "web@x.test")
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.setenv(oauth.LEGACY_USERS_ENV, str(web_uid))       # 웹 커넥터만 열어 둔다
    with TestClient(build_http_app(wt)) as c:       # lifespan 을 돌려야 세션 관리자가 선다
        r = c.post(f"/mcp/{old_tok}", json={},
                   headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 401 and "casebook-login" in r.json()["error"]
        r = c.post(f"/mcp/{web_tok}", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                              "clientInfo": {"name": "t", "version": "1"}}},
                   headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 200, (r.status_code, r.text[:200])  # 적어 둔 계정은 그대로 들어온다


# ── (8) API 문도 같은 정체성을 본다 (확장 97호) ─────────────────────────────
def test_API_문이_JWT_와_구형_토큰을_둘_다_받는다(keypair, tmp_path):
    """로컬 창은 MCP 문이 아니라 API 문(/app/…)으로 간다. 한쪽만 OAuth 를 알면 창이 로그인 뒤에도
    구형 토큰에 묶인다 — 실제로 창이 OAuth 토큰을 보내자 api 가 401 을 냈고 창은 502 였다."""
    from starlette.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.adapters.mcp_server import mint_token
    from casebook.core.tickets import Tickets
    cb, wt = _api_stack(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    hmac_tok = mint_token(wt, "tester@x.test")
    jwt_tok = sign(keypair)                       # email=tester@x.test
    c = TestClient(http_api.create_app(cb, Tickets(cb.db)), raise_server_exceptions=False)
    for label, tok in (("구형 HMAC", hmac_tok), ("OAuth JWT", jwt_tok)):
        r = c.get("/app/case", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, f"{label}: {r.status_code} {r.text[:160]}"
    r = c.get("/app/case", headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.eyJhIjoxfQ.AA"})
    assert r.status_code == 401, r.text[:160]     # 가짜 JWT 는 거절


def test_API_문의_me_가_JWT_로도_답한다(keypair, tmp_path):
    from starlette.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    cb, wt = _api_stack(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    c = TestClient(http_api.create_app(cb, Tickets(cb.db)), raise_server_exceptions=False)
    r = c.get("/auth/auth/me", headers={"Authorization": f"Bearer {sign(keypair)}"})
    assert r.status_code == 200 and r.json()["email"] == "tester@x.test", r.text[:160]


def test_구형_경로를_닫으면_API_문도_닫힌다(keypair, tmp_path, monkeypatch):
    """확장 122호 (D15844). 4단계를 닫았다고 적은 뒤 실측하니 MCP 문은 401 인데 같은 토큰으로
    /app/status 가 200 이었다(2026-09-15) — legacy_allowed 검사가 MCP 문에만 있었다."""
    from starlette.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.adapters.mcp_server import mint_token
    from casebook.core.tickets import Tickets
    cb, wt = _api_stack(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    old = mint_token(wt, "tester@x.test")
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.delenv(oauth.LEGACY_USERS_ENV, raising=False)
    c = TestClient(http_api.create_app(cb, Tickets(cb.db)), raise_server_exceptions=False)
    r = c.get("/app/case", headers={"Authorization": f"Bearer {old}"})
    assert r.status_code == 401 and "casebook-login" in r.text, (r.status_code, r.text[:160])
    assert c.get("/auth/auth/me", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    # OAuth 로는 그대로 들어온다
    r = c.get("/app/case", headers={"Authorization": f"Bearer {sign(keypair)}"})
    assert r.status_code == 200, r.text[:160]
    # 허용 목록에 적힌 계정은 구형으로도 지난다 (D15352)
    uid = wt.db.get_by("user", "email", "tester@x.test")["id"]
    monkeypatch.setenv(oauth.LEGACY_USERS_ENV, str(uid))
    assert c.get("/app/case", headers={"Authorization": f"Bearer {old}"}).status_code == 200
    # 닫지 않은 서버(로컬 창)는 종전 그대로다
    monkeypatch.delenv(oauth.ONLY_ENV)
    monkeypatch.delenv(oauth.LEGACY_USERS_ENV)
    assert c.get("/app/case", headers={"Authorization": f"Bearer {old}"}).status_code == 200


def test_구형_경로를_닫으면_비밀번호_가입과_로그인도_닫힌다(keypair, tmp_path, monkeypatch):
    """비밀번호 로그인이 내주는 authToken 은 커넥터 토큰과 같은 서명이라 받는 쪽이 구별할 수 없다.
    문을 닫아 둔 채 가입만 열어 두면 기록에는 못 닿아도 계정은 계속 만들어진다."""
    from starlette.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    cb, wt = _api_stack(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    before = wt.db.count("user")
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.delenv(oauth.LEGACY_USERS_ENV, raising=False)
    c = TestClient(http_api.create_app(cb, Tickets(cb.db)), raise_server_exceptions=False)
    r = c.post("/auth/auth/signup", json={"name": "n", "email": "new@x.test", "password": "pw"})
    assert r.status_code == 401, r.text[:160]
    assert wt.db.count("user") == before              # 행이 늘지 않는다
    r = c.post("/auth/auth/login", json={"email": "tester@x.test", "password": "pw"})
    assert r.status_code == 401, r.text[:160]
    # 허용 목록의 계정은 로그인이 지난다 — 그 토큰은 _user 를 지나므로. 다른 계정은 여전히 막힌다.
    uid = wt.db.get_by("user", "email", "tester@x.test")["id"]
    monkeypatch.setenv(oauth.LEGACY_USERS_ENV, str(uid + 1000))
    r = c.post("/auth/auth/login", json={"email": "tester@x.test", "password": "pw"})
    assert r.status_code == 401, r.text[:160]
    monkeypatch.setenv(oauth.LEGACY_USERS_ENV, str(uid))
    r = c.post("/auth/auth/login", json={"email": "tester@x.test", "password": "pw"})
    assert r.status_code == 200 and "authToken" in r.json(), r.text[:160]
    # 닫지 않은 서버(로컬 창)는 종전 그대로다
    monkeypatch.delenv(oauth.ONLY_ENV)
    monkeypatch.delenv(oauth.LEGACY_USERS_ENV)
    r = c.post("/auth/auth/signup", json={"name": "n", "email": "new@x.test", "password": "pw"})
    assert r.status_code == 200, r.text[:160]


def test_머리로_붙으면_경로_토큰_없이도_화면을_받는다(keypair, tmp_path, monkeypatch):
    """창은 화면 HTML 도 서버에서 받아 온다. 그 길이 /mcp/<토큰>/ui 뿐이면 구형 경로를 닫는 순간
    창이 API 가 아니라 첫 화면에서 죽는다 — 실제로 502 였다(2026-09-14)."""
    from starlette.testclient import TestClient
    from casebook.adapters.mcp_server import build_http_app, mint_token, DIST_NAMES
    wt = _wt(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    old = mint_token(wt, "tester@x.test")
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.delenv(oauth.LEGACY_USERS_ENV, raising=False)
    with TestClient(build_http_app(wt)) as c:
        # 기록에 닿는 길은 닫혔다
        assert c.post(f"/mcp/{old}", json={}).status_code == 401
        # 머리로 붙으면 경로 토큰 없이 받는다
        r = c.get("/mcp/ui", headers={"Authorization": f"Bearer {sign(keypair)}"})
        assert r.status_code == 200 and b"<" in r.content, r.status_code
        assert "ui" in DIST_NAMES
        assert c.get("/mcp/ui").status_code == 401                 # 인증 없이는 못 받는다


def test_닫아도_클라이언트를_나눠_주는_길은_열려_있다(keypair, tmp_path, monkeypatch):
    """닭과 달걀 — 로그인하려면 casebook-login 이 있어야 하고 그것은 install.sh 로 깔린다.
    닫자 install.sh 가 401 이 되어 재설치가 조용히 실패했다(2026-09-14). 닫는 것은 기록에
    닿는 길이지 클라이언트를 나눠 주는 길이 아니다 — 여기서 나가는 것은 누구의 기록도 아니다."""
    from starlette.testclient import TestClient
    from casebook.adapters.mcp_server import build_http_app, mint_token
    wt = _wt(tmp_path)
    wt.signup("사람", "tester@x.test", "pw")
    tok = mint_token(wt, "tester@x.test")
    monkeypatch.setenv(oauth.ONLY_ENV, "1")
    monkeypatch.delenv(oauth.LEGACY_USERS_ENV, raising=False)
    with TestClient(build_http_app(wt)) as c:
        assert c.post(f"/mcp/{tok}", json={}).status_code == 401           # 기록은 막힌다
        for name in ("install.sh", "ui", "client.tar.gz"):
            r = c.get(f"/mcp/{tok}/{name}")
            assert r.status_code == 200, f"{name}: {r.status_code}"        # 초대장은 지난다
        assert c.get("/mcp/anytoken/install.sh").status_code == 401        # 아무 토큰이나는 아니다


# ── (9) 신청제 (확장 100호, D15414) ─────────────────────────────────────────
def _join_client(tmp_path):
    from starlette.testclient import TestClient
    from casebook.adapters.mcp_server import build_http_app
    wt = _wt(tmp_path)
    return wt, TestClient(build_http_app(wt))


def _hdr(keypair, email="newbie@x.test", **extra):
    return {"Authorization": f"Bearer {sign(keypair, email=email, **extra)}"}


def test_신청하면_자리가_있는_동안은_그_자리에서_열린다(keypair, tmp_path):
    """확장 119호 — 종전에는 전부 pending 이라 운영자가 누를 때까지 아무도 못 썼다."""
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    r = c.post("/join/request", headers=_hdr(keypair))
    assert r.status_code == 200 and r.json()["state"] == access.STATE_ALLOWED
    uid = wt.db.get_by("user", "email", "newbie@x.test")["id"]
    assert access.state(wt.db, uid)["state"] == access.STATE_ALLOWED
    # 열렸어도 안내문 동의 전에는 기록이 오가지 않는다(확장 115호)
    with pytest.raises(Exception) as exc:
        access.check(wt.db, uid)
    assert "data notice" in str(exc.value)


def test_자리가_차면_그_뒤로는_줄을_선다(keypair, tmp_path):
    """pending 은 이제 "운영자가 아직 안 봤다" 가 아니라 "자리가 없다" 는 뜻이다."""
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    for i in range(access.TESTER_SEATS):
        assert c.post("/join/request", headers=_hdr(keypair, email=f"s{i}@x.test")
                      ).json()["state"] == access.STATE_ALLOWED
    r = c.post("/join/request", headers=_hdr(keypair, email="late@x.test"))
    assert r.json()["state"] == access.STATE_PENDING
    uid = wt.db.get_by("user", "email", "late@x.test")["id"]
    with pytest.raises(Exception) as exc:
        access.check(wt.db, uid)
    assert "waiting for the operator" in str(exc.value)
    assert c.get("/join/status", headers=_hdr(keypair, email="late@x.test")
                 ).json()["seats"] == {"taken": 5, "total": 5, "left": 0}


def test_두_번_눌러도_한_줄만_선다(keypair, tmp_path):
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    assert c.post("/join/request", headers=_hdr(keypair)).json()["new"] is True
    assert c.post("/join/request", headers=_hdr(keypair)).json()["new"] is False
    assert wt.db.count("user_access") == 1          # 확장 119호 — 첫 사람은 열려서 서니 pending 이 아니다


def test_대기열이_차면_행을_만들지_않는다(keypair, tmp_path, monkeypatch):
    from casebook.core import access
    monkeypatch.setattr(access, "PENDING_CAP", 2)
    monkeypatch.setattr(access, "TESTER_SEATS", 0)   # 확장 119호 — 자리가 없어야 줄이 선다
    wt, c = _join_client(tmp_path)
    for i in range(2):
        assert c.post("/join/request", headers=_hdr(keypair, email=f"a{i}@x.test")).status_code == 200
    before = wt.db.count("user")
    r = c.post("/join/request", headers=_hdr(keypair, email="late@x.test"))
    assert r.status_code == 429 and "full" in r.json()["error"]
    assert wt.db.count("user") == before            # 상한을 넘으면 행이 늘지 않는다


def test_로그인하지_않으면_줄을_설_수_없다(keypair, tmp_path):
    wt, c = _join_client(tmp_path)
    assert c.post("/join/request").status_code == 401
    assert c.post("/join/request", headers={"Authorization": "Bearer eyJhbGciOiJSUzI1NiJ9.eyJhIjoxfQ.AA"}).status_code == 401
    assert wt.db.count("user") == 0                 # 가짜로는 행이 안 생긴다


def test_JWT_꼴이_아니면_밖으로_나가지_않는다(keypair, tmp_path):
    """거절되는 것만으로는 부족하다. 꼴 검사를 빼도 verify 가 잡기는 하는데, 그때는 쓰레기
    토큰마다 JWKS 를 받으러 밖으로 나간다 — 인증 없는 요청이 우리를 인가 서버에 대한 요청
    증폭기로 만드는 자리다(확장 75호와 같은 부류). 꼴로 먼저 잘라야 한다."""
    wt, c = _join_client(tmp_path)
    c.get("/join/status", headers=_hdr(keypair))     # 한 번은 진짜로 — 여기서 키를 받는다
    before = len(JWKS_HITS)
    for junk in ("", "not-a-jwt", "a.b", "....", "Bearer"):
        assert c.post("/join/request", headers={"Authorization": f"Bearer {junk}"}).status_code == 401
    assert len(JWKS_HITS) == before, JWKS_HITS[before:]
    assert wt.db.count("user") == 0


def test_상태는_줄을_선_사람에게_설치_줄을_주지_않는다(keypair, tmp_path, monkeypatch):
    from casebook.core import access
    monkeypatch.setattr(access, "TESTER_SEATS", 0)   # 확장 119호 — 자리가 없어야 줄이 선다
    wt, c = _join_client(tmp_path)
    assert c.get("/join/status", headers=_hdr(keypair)).json()["state"] == "none"
    c.post("/join/request", headers=_hdr(keypair))
    body = c.get("/join/status", headers=_hdr(keypair)).json()
    assert body["state"] == access.STATE_PENDING and "install" not in body
    uid = wt.db.get_by("user", "email", "newbie@x.test")["id"]
    access.set_state(wt.db, uid, access.STATE_ALLOWED)
    # 확장 115호 — 승인만으로는 아직 아니다. 동의를 남겨야 줄이 나온다.
    c.post("/join/consent", headers=_hdr(keypair))
    body = c.get("/join/status", headers=_hdr(keypair)).json()
    assert body["state"] == access.STATE_ALLOWED
    # 확장 126호 — 모드는 신청 화면에서 이미 골랐으므로 설치기에게 넘긴다. 윈도우는 sh 가 없어
    # 지시문 주소를 준다 — 돌지 않는 줄을 쥐여 주지 않는다.
    assert body["install"]["server"].endswith("| sh -s -- --server")
    assert body["install"]["local"].endswith("| sh -s -- --local")
    assert body["install"]["windows"].endswith("/windows.md")


# ── 확장 115호 — 동의를 남기는 길 ────────────────────────────────────────────
# 증상: 신청제로 들어온 계정은 consent_version 이 None 인데 access.check 가 그것을 요구한다.
# 동의를 남기는 길이 CLI 하나뿐이라, 승인받은 테스터가 첫 기록에서 403 을 받고 빠져나올 수 없었다
# (실측 2026-09-15, 시험용 구글 계정: 승인 뒤에도 커넥터가 "인증 실패"). C15448 에 따라
# 아래 넷은 고치기 전 코드에서 실패한다 — /join/consent 가 없어 404 이고, 동의 없이 줄이 나온다.

def test_승인만으로는_설치_줄을_주지_않는다_동의가_먼저다(keypair, tmp_path):
    """줄이 곧 기록에 닿는 열쇠다. 동의가 없으면 그 열쇠로 첫 기록에서 403 을 받는다 —
    쓸 수 없는 줄을 쥐여 주면 사람은 설치가 고장 난 줄 알고 몇 번이고 다시 돌린다."""
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    c.post("/join/request", headers=_hdr(keypair))
    uid = wt.db.get_by("user", "email", "newbie@x.test")["id"]
    access.set_state(wt.db, uid, access.STATE_ALLOWED)

    body = c.get("/join/status", headers=_hdr(keypair)).json()
    assert body["state"] == access.STATE_ALLOWED
    assert "install" not in body, "동의 전인데 설치 줄이 나왔다"
    assert body["consent"] is None and body["notice"] == access.NOTICE_VERSION
    # 그리고 실제로 기록도 막혀 있다 — 줄을 안 주는 것이 정직한 상태다
    with pytest.raises(Exception):
        access.check(wt.db, uid)


def test_신청_페이지에서_동의하면_기록이_열린다(keypair, tmp_path):
    """설치기에서만 받으면 웹 커넥터로만 쓰는 사람이 영영 동의를 남길 수 없다. 그래서 여기서 받는다."""
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    c.post("/join/request", headers=_hdr(keypair))
    uid = wt.db.get_by("user", "email", "newbie@x.test")["id"]
    access.set_state(wt.db, uid, access.STATE_ALLOWED)

    r = c.post("/join/consent", headers=_hdr(keypair))
    assert r.status_code == 200 and r.json()["consent"] == access.NOTICE_VERSION
    assert access.state(wt.db, uid)["consent_version"] == access.NOTICE_VERSION
    access.check(wt.db, uid)                      # 이제 지난다 — 여기서 터지면 안 된다

    body = c.get("/join/status", headers=_hdr(keypair)).json()
    assert body["install"]["server"].startswith("curl -fsSL")


def test_동의_판은_서버가_정한다(keypair, tmp_path):
    """브라우저가 대는 판을 그대로 박으면, 안내문을 안 읽고도 아무 판이나 남길 수 있다."""
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    c.post("/join/request", headers=_hdr(keypair))
    uid = wt.db.get_by("user", "email", "newbie@x.test")["id"]

    c.post("/join/consent", headers=_hdr(keypair), json={"version": "내가 정한 판"})
    assert access.state(wt.db, uid)["consent_version"] == access.NOTICE_VERSION


def test_줄을_서지_않은_사람은_동의를_남길_수_없다(keypair, tmp_path):
    """계정이 없는데 동의만 남으면 주인 없는 줄이 생긴다. 신청이 먼저다."""
    wt, c = _join_client(tmp_path)
    r = c.post("/join/consent", headers=_hdr(keypair))
    assert r.status_code == 404
    assert wt.db.get_by("user", "email", "newbie@x.test") is None


def test_거절한_사람과_줄을_선_사람이_갈린다(keypair, tmp_path, monkeypatch):
    """blocked 는 "봤고 거절했다", pending 은 "자리가 없다" — 둘을 갈라 두는 이유는 그대로다."""
    from casebook.core import access
    monkeypatch.setattr(access, "TESTER_SEATS", 0)   # 확장 119호 — 자리가 없어야 줄이 선다
    wt, c = _join_client(tmp_path)
    c.post("/join/request", headers=_hdr(keypair, email="a@x.test"))
    c.post("/join/request", headers=_hdr(keypair, email="b@x.test"))
    uid = wt.db.get_by("user", "email", "b@x.test")["id"]
    access.set_state(wt.db, uid, access.STATE_BLOCKED)
    assert [p["email"] for p in access.pending(wt.db)] == ["a@x.test"]
    assert access.pending_count(wt.db) == 1


# ── (10) 신청 페이지 (확장 101호) ───────────────────────────────────────────
def test_신청_페이지는_인증_없이_보인다(keypair, tmp_path):
    wt, c = _join_client(tmp_path)
    for path in ("/join", "/join/", "/join/callback"):
        r = c.get(path)
        assert r.status_code == 200, f"{path}: {r.status_code}"
        assert r.headers["content-type"].startswith("text/html"), path
        assert b"<title>Worktrail</title>" in r.content, path


def test_페이지에_비밀이_없다(keypair, tmp_path):
    """client_id 도 issuer 도 페이지에 박아 두지 않는다 — 메타데이터에서 읽는다.
    설정이 없다는 것이 이 페이지의 요점이고, 박아 두면 환경마다 다른 판을 배포해야 한다."""
    wt, c = _join_client(tmp_path)
    body = c.get("/join").text
    assert "client_01" not in body and "authkit.app" not in body
    assert "/.well-known/oauth-protected-resource/mcp" in body
    for secret in ("sk_", "client_secret", "BEGIN "):
        assert secret not in body, secret


def test_PKCE_로_교환하고_검증자를_한_번만_쓴다(keypair, tmp_path):
    """공개 앱이라 secret 이 없다. code_verifier 는 리다이렉트를 건너야 해서 sessionStorage 에
    잠깐 두는데, 돌아오는 즉시 지워야 한다 — 남으면 다음 사람이 같은 브라우저에서 주울 수 있다."""
    wt, c = _join_client(tmp_path)
    body = c.get("/join").text
    assert 'code_challenge_method: "S256"' in body
    assert "sessionStorage.setItem(VERIFIER_KEY" in body
    assert "sessionStorage.removeItem(VERIFIER_KEY)" in body
    assert "localStorage" not in body          # 토큰도 검증자도 오래 두지 않는다


def test_oauth_를_안_켠_서버는_페이지도_안_낸다(tmp_path, monkeypatch):
    for k in (oauth.ISSUER_ENV, oauth.AUDIENCE_ENV):
        monkeypatch.delenv(k, raising=False)
    oauth.reset_for_tests()
    wt, c = _join_client(tmp_path)
    assert c.get("/join").status_code == 404
    assert c.post("/join/request").status_code == 404


# ── 확장 111호 — 토큰 교환은 서버가 대신한다 ────────────────────────────────
def test_신청_페이지는_인가_서버_토큰_문을_직접_부르지_않는다():
    """AuthKit 의 토큰 문은 Access-Control-Allow-Origin 을 주지 않는다. 브라우저가 직접 부르면
    CORS 로 막혀 신청이 콜백에서 끝난다 — 실측 2026-09-15 /join/callback:
      Access to fetch at 'https://…authkit.app/oauth2/token' from origin
      'https://casebook-api.syncflo.cloud' has been blocked by CORS policy
    확장 101호가 그것을 브라우저에서 하게 만들었다. 되돌리면 이 시험이 깨진다."""
    page = (pathlib.Path(__file__).resolve().parent.parent / "web/join/index.html").read_text(encoding="utf-8")
    assert 'fetch("/join/exchange"' in page, "교환을 우리 서버에 맡기지 않는다"
    code = "\n".join(ln for ln in page.splitlines() if not ln.strip().startswith("//"))
    assert "as.token_endpoint" not in code and "cfg.token" not in code, "페이지가 아직 토큰 문 주소를 쓴다"
    assert "grant_type" not in code, "페이지가 아직 토큰 요청을 만든다"
    # 브라우저가 대는 것은 code 와 code_verifier 뿐이다 — client_id·resource·redirect_uri 는 서버가 정한다
    body = page.split('fetch("/join/exchange"', 1)[1][:300]
    assert "code_verifier: verifier" in body and "client_id" not in body and "redirect_uri" not in body


def test_교환_길은_code_와_verifier_를_요구한다():
    """열린 길이라 아직 토큰이 없는 사람이 부른다 — 그래서 Authorization 검사 앞에 선다.
    둘 중 하나라도 없으면 인가 서버를 부르지 않는다."""
    from casebook.adapters import mcp_server
    assert mcp_server.JOIN_EXCHANGE == "/join/exchange"
    assert mcp_server.JOIN_EXCHANGE in mcp_server.JOIN_PATHS
    src = (pathlib.Path(mcp_server.__file__)).read_text(encoding="utf-8")
    seg = src.split("if path == JOIN_EXCHANGE:", 1)[1].split("auth = (request.headers", 1)[0]
    assert 'if not code or not verifier:' in seg and '"POST only"' in seg
    # redirect_uri 를 브라우저가 대지 않는다 — 서버가 짓는다
    assert '_public_base(request) + "/join/callback"' in seg


def test_exchange_code_는_자원_지정_교환을_한_번_더_한다(monkeypatch):
    """AuthKit 은 authorization_code 교환에서 resource 를 무시한다(확장 91호 실측). aud 가 우리
    자원이 아니면 refresh 로 한 번 더 바꾼다 — 안 그러면 우리 문이 InvalidAudienceError 로 막는다."""
    import base64, json as _json
    from casebook.core import oauth as o
    def jwt(aud):
        body = base64.urlsafe_b64encode(_json.dumps({"aud": aud}).encode()).rstrip(b"=").decode()
        return f"x.{body}.y"
    calls = []
    monkeypatch.setattr(o, "_token_endpoint", lambda: "https://as.test/token")
    monkeypatch.setattr(o, "audience", lambda: "https://ours.test/mcp")
    monkeypatch.setattr(o, "client_id", lambda: "cid")
    def fake_post(url, form, timeout=15):
        calls.append(form["grant_type"])
        if form["grant_type"] == "authorization_code":
            return {"access_token": jwt("someone-else"), "refresh_token": "rt"}
        return {"access_token": jwt("https://ours.test/mcp")}
    monkeypatch.setattr(o, "_post_form", fake_post)
    tok = o.exchange_code("code", "verifier", "https://ours.test/join/callback")
    assert calls == ["authorization_code", "refresh_token"]
    assert o._aud_of(tok) == "https://ours.test/mcp"


def test_교환이_거절되면_사람이_읽을_말로_올린다(monkeypatch):
    from casebook.core import oauth as o
    monkeypatch.setattr(o, "_token_endpoint", lambda: "https://as.test/token")
    monkeypatch.setattr(o, "audience", lambda: "")
    monkeypatch.setattr(o, "client_id", lambda: "cid")
    monkeypatch.setattr(o, "_post_form", lambda *a, **k: {"error": "invalid_grant",
                                                         "error_description": "code already used"})
    with pytest.raises(o.OAuthError, match="code already used"):
        o.exchange_code("code", "verifier", "https://ours.test/join/callback")


def test_로컬을_고른_사람은_다른_안내문에_동의한다(keypair, tmp_path):
    """확장 126호 — 로컬은 서버 보관·삭제·열람·종료가 해당되지 않는다. 그 사람이 맡기는 것은
    신청한 구글 이메일 하나뿐이다. 같은 글을 읽히면 사실이 아닌 것에 동의를 받는 셈이고,
    실제로 주저를 만든다(사용자 2026-09-17)."""
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    c.post("/join/request", headers=_hdr(keypair))
    uid = wt.db.get_by("user", "email", "newbie@x.test")["id"]

    r = c.post("/join/consent", headers=_hdr(keypair), json={"mode": "local"})
    assert r.status_code == 200
    assert access.state(wt.db, uid)["consent_version"] == access.NOTICE_VERSION_LOCAL
    assert access.NOTICE_VERSION_LOCAL != access.NOTICE_VERSION

    # 서버를 고르면 서버 안내문 판이 박힌다
    c.post("/join/consent", headers=_hdr(keypair), json={"mode": "server"})
    assert access.state(wt.db, uid)["consent_version"] == access.NOTICE_VERSION
    # 모드를 안 주면 더 엄격한 쪽(서버)으로 적는다 — 덜 읽은 것으로 기록하지 않는다
    c.post("/join/consent", headers=_hdr(keypair))
    assert access.state(wt.db, uid)["consent_version"] == access.NOTICE_VERSION


# ── 확장 127호 — 들어오는 길을 실제보다 좁게 말하지 않는다 ──────────────────
def test_신청_화면이_들어오는_길을_좁게_말하지_않는다():
    """화면이 문 하나만 이름 지어 부르면, 그 계정이 없는 사람은 자기가 못 쓰는 줄 알고 닫는다.

    로그인 문은 다섯이다 — 실측 2026-09-17, AuthKit 로그인 화면의 링크:
      provider=GoogleOAuth · MicrosoftOAuth · GitHubOAuth · AppleOAuth · 그리고 이메일.
    윈도우는 122~125호로 제 지시문을 타고 들어오므로 WSL 을 시킬 일이 없다.

    사용자 2026-09-17: "맥/리눅스/wsl 에서 쓴다는 이제 안 쓰니까 바꿔야지. 구글 계정으로
    신청한다는 것도 구글 아니라 다른 것도 sso 되니까 바꾸고."
    """
    page = (pathlib.Path(__file__).resolve().parent.parent / "web/join/index.html").read_text(encoding="utf-8")
    code = "\n".join(ln for ln in page.splitlines() if not ln.strip().startswith("//"))

    assert "WSL" not in code, "윈도우는 제 지시문으로 들어온다 — WSL 터미널을 시키지 않는다"

    btn = code.split('id="go"', 1)[1].split("</button>", 1)[0]
    assert "구글" not in btn and "Google" not in btn, \
        f"로그인 단추가 제공자 하나를 이름 지어 부른다: {btn!r}"

    notice = code.split("const NOTICE_LOCAL", 1)[1].split("const NOTICE ", 1)[0]
    assert "구글" not in notice and "Google" not in notice, \
        "로컬 안내문이 이메일 주소를 '구글 이메일' 이라고 좁혀 부른다"


# ── 확장 129호 — 안내문 판을 올리면 실제로 다시 읽힌다 ──────────────────────
def test_옛_판에_동의한_계정은_안내문을_다시_받는다(keypair, tmp_path):
    """NOTICE_VERSION 을 올려도 아무 일이 일어나지 않던 자리다.

    동의를 보는 곳이 전부 "값이 있는가" 만 물었다 — access.check 의 `not consent_version`,
    /join/status 의 `and st["consent_version"]`. 그래서 안내문 문구를 고치고 판을 올려도
    옛 판에 동의한 사람은 그대로 설치 줄을 받고, 바뀐 글을 영영 보지 않는다.

    사용자 2026-09-17 "올려" — 올리는 행위에 뜻이 생기려면 묻는 쪽이 판을 비교해야 한다.
    옛 동의 기록 자체는 지우지 않는다. 언제 무엇에 동의했는지가 기록이다.
    """
    from casebook.core import access
    wt, c = _join_client(tmp_path)
    c.post("/join/request", headers=_hdr(keypair))
    uid = wt.db.get_by("user", "email", "newbie@x.test")["id"]

    # 지금 판에 동의하면 설치 줄이 나온다
    c.post("/join/consent", headers=_hdr(keypair), json={"mode": "local"})
    now = c.get("/join/status", headers=_hdr(keypair)).json()
    assert now["consent_current"] is True and "install" in now

    # 안내문이 바뀌어 판이 올라간 뒤 — 같은 계정이 설치 줄 대신 안내문을 다시 받는다
    access.record_consent(wt.db, uid, "2026-09-15+local")
    old = c.get("/join/status", headers=_hdr(keypair)).json()
    assert old["consent"] == "2026-09-15+local", "동의한 사실은 그대로 남는다"
    assert old["consent_current"] is False
    assert "install" not in old, "옛 판에 동의한 사람에게 설치 줄을 주면 바뀐 글을 못 본다"

    # grandfathered 는 동의가 아니다 — 표가 생길 때 적어 둔 표시일 뿐이다
    assert access.notice_current(access.CONSENT_GRANDFATHERED) is False
    assert access.notice_current(None) is False

    # 화면도 값이 있는가가 아니라 판을 본다
    page = (pathlib.Path(__file__).resolve().parent.parent / "web/join/index.html").read_text(encoding="utf-8")
    assert "body.consent_current" in page, "화면이 아직 동의 여부만 보고 판을 안 본다"


def test_동의를_받으면_일하다_끊기지는_않는다(tmp_path):
    """판이 올라가도 기록은 계속 간다. 다시 읽히는 것이 목적이지 일을 끊는 것이 목적이 아니다.

    check 까지 판을 보게 하면, 옛 판에 동의한 사람의 에이전트가 브라우저로 가서 다시 동의할
    때까지 매 기록에서 403 을 받는다 — 그 사람은 무엇이 고장 났는지 알 길이 없다."""
    from casebook.core import access
    wt, _ = _join_client(tmp_path)
    u = wt.db.add("user", {"email": "old@x.test", "name": "old"})
    access.set_state(wt.db, u["id"], access.STATE_ALLOWED)
    access.record_consent(wt.db, u["id"], "2026-09-15")        # 옛 판
    assert access.notice_current("2026-09-15") is False
    assert access.check(wt.db, u["id"])["consent_version"] == "2026-09-15"
