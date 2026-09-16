"""확장 87호 — 남이 내준 토큰을 받는다 (온보딩·인증 인계서 2절, D15154).

지금 서버의 문지기는 무상태 HMAC {id, exp} 하나다. 주소가 곧 열쇠라 URL 한 줄이 새면 기록이
통째로 열리고(C14993), 그래서 테스터를 5명으로 묶어 두었다. 그 매듭을 푸는 것이 이 파일이다 —
인가 서버(WorkOS AuthKit)가 구글 로그인을 받아 JWT 를 내주고, 우리는 그것을 **검증만** 한다.

    verify(token) -> claims      서명·iss·aud·exp 를 본다. 하나라도 어긋나면 OAuthError.
    user_of(db, claims) -> int   claims 의 이메일에 해당하는 계정. 없으면 만들지 않고 거절한다.
    enabled()                    issuer 가 설정돼 있을 때만 참. 기본은 꺼짐.

■ 켜기 전에는 한 글자도 안 바뀐다
CASEBOOK_OAUTH_ISSUER 가 비어 있으면 enabled() 가 거짓이고 user_from_request 는 종전 경로만 탄다.
운영 전환은 사용자가 결과를 보고 정한다(D15154) — 이 파일이 들어간 것만으로 켜지지 않는다.

■ 계정을 만들지 않는다
mint_token 은 이메일을 보면 계정을 만든다. 그 버릇을 여기로 가져오면 안 된다 — 구글 계정은
누구나 만들 수 있으므로, 로그인만으로 행이 생기면 확장 75호가 막은 "인증 전 무제한 쓰기"가
이름만 바꿔 돌아온다. 검증을 통과한 토큰이어도 마찬가지다: 서명이 진짜라는 것은 그 사람이
구글 계정을 가졌다는 뜻일 뿐, 이 서버를 쓸 사람이라는 뜻이 아니다. 모르는 이메일은 행을 만들지
않고 거절하고, 계정은 운영자가 먼저 만든다(mint EMAIL → allow EMAIL). 정원이 5명이므로(D15134)
손으로 여는 것이 곧 정원 노릇을 한다.

■ 검증은 공개 키로만 한다
JWKS 는 공개 문서라 API Key(sk_…)가 필요 없다. 그래서 이 서버에 WorkOS 비밀이 없다 —
샐 것이 없다. 키 회전은 모르는 kid 를 만났을 때 한 번 다시 받아 따라간다(아래 _jwks 참고).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from typing import Any

ISSUER_ENV = "CASEBOOK_OAUTH_ISSUER"
AUDIENCE_ENV = "CASEBOOK_OAUTH_AUDIENCE"
JWKS_ENV = "CASEBOOK_OAUTH_JWKS"
CLIENT_ID_ENV = "CASEBOOK_OAUTH_CLIENT_ID"
ONLY_ENV = "CASEBOOK_OAUTH_ONLY"            # 구형 경로 토큰을 닫는다 (확장 92호)
LEGACY_USERS_ENV = "CASEBOOK_LEGACY_USERS"  # 닫은 뒤에도 구형으로 붙을 수 있는 계정 id 들

ALGORITHMS = ("RS256", "ES256")       # AuthKit 은 RS256 이다. 대칭키(HS*)는 절대 받지 않는다 —
                                      # 공개 JWKS 를 HMAC 비밀로 쓰는 알고리즘 혼동 공격의 입구다.
_DISCOVERY = "/.well-known/oauth-authorization-server"
_LEEWAY = 60                          # 시계 차이. 서버 둘의 시계가 정확히 같을 수 없다.
_REFETCH_MIN_INTERVAL = 30.0          # 모르는 kid 로 JWKS 를 무한정 다시 받게 만들 수 없다
# 인가 서버가 Cloudflare 뒤에 있으면 기본 User-Agent 가 1010 으로 끊긴다(device_login.USER_AGENT 참고).
# discovery 와 JWKS 도 같은 문을 지나므로 여기서도 이름을 밝힌다.
USER_AGENT = "casebook-server/1.0 (+https://github.com/jasonethicseo/casebook)"


class OAuthError(Exception):
    """토큰이 이 서버 것이 아니다. 이유를 담되 밖으로는 뭉뚱그려 나간다."""


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip().rstrip("/")


def issuer() -> str:
    return _env(ISSUER_ENV)


def audience() -> str:
    """이 자원 서버의 이름. 토큰의 aud 가 이것이어야 한다.

    WorkOS 대시보드의 MCP resource indicator 와 **같은 문자열**이어야 한다. 다르면 다른 자원에
    발급된 토큰을 여기서 받아 주게 된다 — 혼동된 대리인(confused deputy)이 되는 자리다."""
    return _env(AUDIENCE_ENV)


def client_id() -> str:
    """기기 로그인이 쓸 공개 클라이언트 id. 비밀이 아니다 — PKCE 공개 앱이라 secret 이 없다."""
    return (os.environ.get(CLIENT_ID_ENV) or "").strip()


def enabled() -> bool:
    return bool(issuer() and audience())


def only() -> bool:
    """구형 경로 토큰(/mcp/<토큰>·Bearer HMAC)을 닫았나. oauth 가 켜져 있을 때만 참이다.

    켜지 않고 닫으면 아무도 못 들어간다. 그 조합을 설정 실수로 만들 수 있으므로 여기서 막고,
    preflight 가 부팅 때 크게 말한다 — 조용히 전원을 잠그는 일이 없게."""
    return enabled() and (os.environ.get(ONLY_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def legacy_users() -> frozenset[int]:
    """닫은 뒤에도 구형 경로로 붙을 수 있는 계정 (D15352).

    웹 커넥터(claude.ai·ChatGPT)는 터미널이 없어 기기 흐름을 쓸 수 없다. 브라우저 OAuth 를
    세울 때까지 그 계정만 운영자가 명시적으로 열어 둔다 — 기본은 빈 목록이라, 적지 않으면
    아무도 열리지 않는다. 계정별로 두는 것이 요점이다: 문 전체를 여는 스위치가 아니다."""
    raw = (os.environ.get(LEGACY_USERS_ENV) or "").replace(",", " ").split()
    out = set()
    for x in raw:
        try:
            out.add(int(x))
        except ValueError:
            continue                          # 못 읽는 값은 없는 것과 같다 — 열어 주지 않는다
    return frozenset(out)


def legacy_allowed(user_id: int) -> bool:
    """이 계정이 구형 경로로 들어와도 되나. 닫지 않았으면 전부 된다."""
    return (not only()) or user_id in legacy_users()


# ── JWKS ────────────────────────────────────────────────────────────────────
_lock = threading.Lock()
_jwks_uri: str | None = None
_client: Any = None
_last_refetch = 0.0


def _discover_jwks_uri() -> str:
    """issuer 의 discovery 문서에서 jwks_uri 를 읽는다. 환경변수로 못박아 둘 수도 있다.

    한 번만 부르고 프로세스에 들고 있는다 — 인가 서버의 주소는 배포 중에 바뀌지 않는다."""
    fixed = _env(JWKS_ENV)
    if fixed:
        return fixed
    url = issuer() + _DISCOVERY
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:       # noqa: S310 — https 고정 주소
        doc = json.loads(r.read().decode())
    uri = (doc.get("jwks_uri") or "").strip()
    if not uri:
        raise OAuthError(f"discovery 에 jwks_uri 가 없다: {url}")
    if doc.get("issuer", "").rstrip("/") != issuer():
        raise OAuthError("discovery 의 issuer 가 설정과 다르다")
    return uri


def _jwks():
    """PyJWKClient 하나를 프로세스에 들고 있는다. 키는 캐시되고 lifespan 뒤에 다시 받는다."""
    global _jwks_uri, _client
    with _lock:
        if _client is None:
            from jwt import PyJWKClient
            _jwks_uri = _jwks_uri or _discover_jwks_uri()
            _client = PyJWKClient(_jwks_uri, cache_keys=True, lifespan=600,
                                  headers={"User-Agent": USER_AGENT})
        return _client


def _signing_key(token: str):
    """kid 에 맞는 공개 키. 모르는 kid 면 한 번만 다시 받아 본다 — 키 회전을 따라가되,
    모르는 kid 를 계속 보내는 것으로 우리를 인가 서버에 대한 요청 증폭기로 쓸 수 없게 한다."""
    global _client, _last_refetch
    try:
        return _jwks().get_signing_key_from_jwt(token)
    except Exception:                                        # noqa: BLE001 — 키가 없거나 못 받았다
        now = time.monotonic()
        with _lock:
            if now - _last_refetch < _REFETCH_MIN_INTERVAL:
                raise
            _last_refetch = now
            _client = None                                   # 다음 _jwks() 가 새로 만든다
        return _jwks().get_signing_key_from_jwt(token)


def looks_like_jwt(token: str) -> bool:
    """구형 HMAC 토큰과 구별한다. JWT 는 점 둘로 나뉜 세 조각이고, 우리 HMAC 토큰은 그렇지 않다."""
    return token.count(".") == 2 and all(token.split("."))


def verify(token: str) -> dict[str, Any]:
    """서명·iss·aud·exp 를 본다. 통과하면 claims, 아니면 OAuthError."""
    if not enabled():
        raise OAuthError("oauth 가 켜져 있지 않다")
    import jwt as pyjwt
    try:
        return pyjwt.decode(
            token, _signing_key(token).key, algorithms=list(ALGORITHMS),
            audience=audience(), issuer=issuer(), leeway=_LEEWAY,
            options={"require": ["exp", "iss", "aud"], "verify_signature": True,
                     "verify_exp": True, "verify_iss": True, "verify_aud": True})
    except OAuthError:
        raise
    except Exception as exc:                                 # noqa: BLE001 — 어떤 실패든 401 이다
        raise OAuthError(f"{type(exc).__name__}: {exc}") from None


def email_of(claims: dict[str, Any]) -> str:
    """이 토큰이 가리키는 사람. AuthKit 은 email 클레임을 싣는다.

    email_verified 가 명시적으로 거짓이면 받지 않는다 — 없으면 따지지 않는다(구글 로그인은
    검증된 주소만 내주고, AuthKit 이 그 칸을 늘 싣지는 않는다)."""
    if claims.get("email_verified") is False:
        raise OAuthError("확인되지 않은 이메일이다")
    email = (claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        # 실측 2026-09-14: AuthKit 의 access token 에는 sub 만 있고 email 은 id_token 에만 있다.
        # userinfo 는 자원 지정 토큰(aud=우리 자원)으로는 401 이고, introspection 은 client_secret 을
        # 요구해 공개 앱에서 못 쓴다. 그래서 인가 서버 쪽에서 access token 에 email 을 넣어 줘야 한다 —
        # 무엇을 해야 하는지까지 말한다. 그러지 않으면 밖에서 보이는 것은 그냥 401 이다.
        raise OAuthError(
            f"the access token carries no email claim (sub {claims.get('sub', '?')}). "
            "The authorization server has to include it — in WorkOS: Dashboard → Authentication → "
            'Features → JWT Template, add "email": {{ user.email }}')
    return email


def user_of(db: Any, claims: dict[str, Any]) -> int:
    """claims 의 이메일에 해당하는 계정 id. **없으면 만들지 않고 거절한다**(위 머리말 참고)."""
    email = email_of(claims)
    user = db.get_by("user", "email", email)
    if user is None:
        raise OAuthError(f"등록되지 않은 계정이다: {email}")
    return user["id"]


def preflight() -> str:
    """부팅 때 한 번 부른다. 켜져 있는데 설 수 없으면 **여기서** 죽는다.

    이것이 없으면 실패가 요청마다 401 로 흩어진다 — PyJWT 가 안 깔렸든, issuer 주소가 오타든,
    discovery 가 막혔든 밖에서 보이는 것은 똑같이 "unauthorized" 다. 운영자는 토큰을 의심하며
    한나절을 쓰게 된다. 켜는 사람은 서버를 재시작하는 사람이므로, 그 자리에서 알려 준다."""
    if (os.environ.get(ONLY_ENV) or "").strip() and not enabled():
        raise OAuthError(
            f"{ONLY_ENV} 가 켜져 있는데 oauth 가 꺼져 있다 — 그대로 두면 아무도 들어올 수 없다. "
            f"{ISSUER_ENV} 와 {AUDIENCE_ENV} 를 함께 설정하거나 {ONLY_ENV} 를 끈다.")
    if not enabled():
        return ""
    try:
        import jwt as pyjwt                                        # noqa: F401
    except ImportError:
        raise OAuthError(
            f"{ISSUER_ENV} 가 설정돼 oauth 를 켰는데 PyJWT 가 없다. "
            "pip install 'casebook[oauth]' 또는 pip install 'pyjwt[crypto]'") from None
    uri = _jwks_uri or _discover_jwks_uri()
    _jwks()                                                        # 키를 한 번 받아 본다
    return uri


def reset_for_tests() -> None:
    """프로세스에 들고 있는 JWKS 를 버린다. 테스트가 환경변수를 바꿔 가며 돌 수 있게."""
    global _jwks_uri, _client, _last_refetch
    with _lock:
        _jwks_uri = None
        _client = None
        _last_refetch = 0.0


# ── 자원 서버 메타데이터 (RFC 9728) ──────────────────────────────────────────
def protected_resource_metadata(resource: str | None = None) -> dict[str, Any]:
    """MCP 클라이언트가 401 을 받고 읽는 문서. "이 자원은 저 인가 서버가 지킨다" 한 줄이다.

    이것이 있어야 클라이언트가 어디로 로그인하러 갈지 스스로 안다 — 없으면 사람이 주소를
    받아 적어야 하고, 그것이 지금의 /mcp/<토큰> 방식이다."""
    doc: dict[str, Any] = {
        "resource": resource or audience(),
        "authorization_servers": [issuer()],
        "bearer_methods_supported": ["header"],
    }
    # RFC 9728 에 없는 칸이다. 표준은 클라이언트가 자기 client_id 를 이미 안다고 보지만, 우리
    # 클라이언트는 서버 주소 하나만 들고 설치된다 — DCR 은 꺼 두었고(대시보드) CIMD 는 클라이언트가
    # 자기 메타데이터 문서를 웹에 띄워야 해서 설치 한 줄로 끝나지 않는다. 공개 앱의 client_id 는
    # 비밀이 아니므로(PKCE 를 쓰고 secret 이 없다) 여기 실어 보낸다. 모르는 칸은 다른 MCP
    # 클라이언트가 그냥 무시한다.
    if client_id():
        doc["casebook_device_client_id"] = client_id()
    return doc


def www_authenticate(metadata_url: str) -> str:
    """401 에 붙이는 머리. 클라이언트는 이 주소에서 위 문서를 읽는다."""
    return f'Bearer resource_metadata="{metadata_url}"'


# ── 신청 페이지의 토큰 교환 (확장 111호) ────────────────────────────────────
# 브라우저가 인가 서버의 토큰 문을 직접 부를 수 없다: AuthKit 이 Access-Control-Allow-Origin 을
# 주지 않아 CORS 로 막힌다(실측 2026-09-15 /join/callback: "Failed to fetch"). 확장 101호는 그것을
# 브라우저에서 하게 만들어 신청이 콜백에서 끝났다. 교환만 서버가 대신한다 — 우리 서버는 이미
# 인가 서버와 이야기하고 있고(JWKS), 여기는 출처 제한이 없다.
def _token_endpoint() -> str:
    """issuer 의 discovery 에서 token_endpoint 를 읽는다. jwks 와 같은 문서다."""
    url = issuer() + _DISCOVERY
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:        # noqa: S310 — https 고정 주소
        doc = json.loads(r.read().decode())
    if doc.get("issuer", "").rstrip("/") != issuer():
        raise OAuthError("discovery 의 issuer 가 설정과 다르다")
    end = (doc.get("token_endpoint") or "").strip()
    if not end:
        raise OAuthError(f"discovery 에 token_endpoint 가 없다: {url}")
    return end


def _post_form(url: str, form: dict[str, str], timeout: int = 15) -> dict[str, Any]:
    import urllib.error
    import urllib.parse
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:   # noqa: S310
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:                       # 4xx 도 본문에 error 를 싣는다
        try:
            return json.loads(e.read().decode() or "{}")
        except Exception:                                     # noqa: BLE001
            return {"error": f"http_{e.code}"}


def exchange_code(code: str, verifier: str, redirect_uri: str) -> str:
    """인가 코드를 access token 으로 바꾼다. client_id·resource 는 서버가 정한다 —
    브라우저가 보내는 것은 그 흐름을 시작한 본인만 아는 code 와 code_verifier 뿐이다.

    aud 가 우리 자원이 아니면 갱신 한 번으로 제자리를 찾는다 — device_login._scope_to_resource
    와 같은 이유다(AuthKit 이 authorization_code 교환에서 resource 를 무시한다, 확장 91호)."""
    want = audience()
    form = {"grant_type": "authorization_code", "code": code, "client_id": client_id(),
            "redirect_uri": redirect_uri, "code_verifier": verifier}
    if want:
        form["resource"] = want
    body = _post_form(_token_endpoint(), form)
    if body.get("error") or not body.get("access_token"):
        raise OAuthError(body.get("error_description") or body.get("error") or "token exchange failed")
    if want and body.get("refresh_token") and _aud_of(body["access_token"]) != want:
        again = _post_form(_token_endpoint(), {
            "grant_type": "refresh_token", "refresh_token": body["refresh_token"],
            "client_id": client_id(), "resource": want})
        if again.get("access_token"):
            body = again
    return body["access_token"]


def _aud_of(token: str) -> str:
    """서명을 보지 않고 aud 만 읽는다 — 자원 지정 교환이 한 번 더 필요한지만 고른다.
    진짜 판정은 verify() 가 한다."""
    import base64
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:                                         # noqa: BLE001
        return ""
    aud = claims.get("aud")
    return aud[0] if isinstance(aud, list) and aud else (aud or "")
