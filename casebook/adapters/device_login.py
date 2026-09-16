"""확장 89호 — 터미널에서 로그인한다 (RFC 8628 Device Authorization Grant, D15154).

    casebook-login              서버 주소만 있으면 된다. 나머지는 서버가 알려 준다.
    casebook-login --status     지금 누구로 붙어 있나
    casebook-login --logout     이 맥에서 토큰을 지운다

왜 기기 흐름인가: 코딩 에이전트를 쓰는 사람은 터미널 앞에 있고, 브라우저로 돌아올 redirect URI 를
받아 줄 서버가 그 맥에 없다. 기기 흐름은 코드 한 조각을 보여 주고 사람이 아무 브라우저에서나
그것을 넣게 한다 — 127.0.0.1 에 포트를 열 필요도, 서버가 그 맥을 알 필요도 없다.

■ 설정이 없다
사람이 아는 것은 서버 주소 하나뿐이다. 거기서 자원 서버 메타데이터를 읽고, 그것이 가리키는
인가 서버의 discovery 를 읽고, 거기서 기기 인증 주소와 토큰 주소를 얻는다. client_id 도 서버가
메타데이터에 실어 준다(oauth.protected_resource_metadata 참고) — 공개 앱이라 비밀이 아니다.

■ 저장한 것은 갱신된다
access token 은 짧다. offline_access 를 함께 요청해 refresh token 을 받아 두고, 만료 60초 전부터
조용히 갱신한다. 갱신이 거절되면(회수됐거나 만료) 지우고 다시 로그인하라고 말한다 — 조용히
옛 토큰으로 재시도하지 않는다.

■ 비밀은 파일 하나에 0600 으로
~/.casebook/oauth.json. 경로 토큰(remote-url)과 같은 급의 비밀이다. 화면·기록·채팅에 올리지 않는다.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from casebook.core.clientmode import say

TOKEN_FILE = "~/.casebook/oauth.json"
RESOURCE_METADATA = "/.well-known/oauth-protected-resource/mcp"
AS_DISCOVERY = "/.well-known/oauth-authorization-server"
SCOPES = "openid profile email offline_access"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
_EARLY = 60.0            # 만료 이 초 전부터 갱신한다 — 요청 도중에 만료되는 것을 피한다
_MAX_WAIT = 15 * 60      # 사람이 브라우저를 여는 데 주는 시간
# Cloudflare 는 기본 User-Agent(Python-urllib/*)를 봇으로 보고 1010 으로 끊는다 — WorkOS 가 그 뒤에
# 있어서 실제로 끊겼다(2026-09-14 실측: UA 없이 403 error_name browser_signature_banned, 이름을
# 넣으면 200). 밖에 나가 보기 전에는 안 보이는 자리라 여기 못박아 둔다.
USER_AGENT = "casebook-login/1.0 (+https://github.com/jasonethicseo/casebook)"


class LoginError(RuntimeError):
    """로그인을 끝낼 수 없다. 사람에게 보여 줄 말이 들어 있다."""


def _aud(token: str) -> str:
    """access token 의 aud 를 **검증 없이** 읽는다.

    이것으로 무엇을 허락하지 않는다 — 자원 지정 교환이 필요한지만 고른다. 진짜 판정은
    서버가 서명을 보고 한다(core/oauth.verify). 못 읽으면 빈 문자열이라 교환을 한 번 더 할 뿐이다."""
    import base64
    try:
        part = token.split(".")[1]
        body = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except Exception:                                            # noqa: BLE001 — JWT 가 아닐 수도 있다
        return ""
    aud = body.get("aud")
    return aud[0] if isinstance(aud, list) and aud else (aud or "")


def _scope_to_resource(tok: dict, cfg: dict) -> dict:
    """기기 코드로 받은 토큰의 aud 가 우리 자원이 아니면, 갱신 한 번으로 자원 지정 토큰을 받는다.

    실측 2026-09-14: AuthKit 은 resource 를 refresh_token 교환에서는 받아 주는데 device_code
    교환에서는 무시한다 — 기기 인증 요청과 토큰 교환 양쪽에 실어도 aud 가 그 환경의 기본
    클라이언트로 온다. RFC 8707 은 토큰 요청에 resource 를 싣는 것을 허용하므로, 로그인 직후
    한 번 더 교환해 제자리를 찾는다. aud 가 이미 맞으면(인가 서버가 고치면) 아무것도 하지 않는다."""
    want = cfg.get("resource") or ""
    if not want or _aud(tok.get("access_token", "")) == want or not tok.get("refresh_token"):
        return tok
    status, body = _post_form(cfg["token"], {
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
        "client_id": cfg["client_id"], "resource": want})
    if status != 200 or not body.get("access_token"):
        return tok                                               # 안 되면 받은 것을 그대로 둔다
    body.setdefault("refresh_token", tok["refresh_token"])
    return body


def _path() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CASEBOOK_OAUTH_FILE") or TOKEN_FILE).expanduser()


def _get_json(url: str, timeout: int = 15) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310 — https 주소
        return json.loads(r.read().decode())


def _post_form(url: str, form: dict[str, str], timeout: int = 15) -> tuple[int, dict]:
    """토큰 주소는 폼으로 받는다. 4xx 도 본문에 error 를 실어 보내므로 몸통을 읽어 돌려준다."""
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "Accept": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:                                        # noqa: BLE001 — 본문이 JSON 이 아니다
            return e.code, {}


# ── 서버가 알려 주는 것 ──────────────────────────────────────────────────────
def discover(base: str) -> dict:
    """서버 주소 하나에서 로그인에 필요한 전부를 얻는다.

    base 는 https://host 다. remote-url 이 .../mcp/<token> 꼴이면 그 앞부분만 쓴다 —
    로그인한 뒤에는 경로에 토큰을 싣지 않는다."""
    base = base.rstrip("/")
    cut = base.find("/mcp")
    if cut > 0:
        base = base[:cut]
    try:
        res = _get_json(base + RESOURCE_METADATA)
    except Exception as exc:                                     # noqa: BLE001
        raise LoginError(say(
            f"이 서버는 로그인을 받지 않는다 — {base}{RESOURCE_METADATA} 를 읽을 수 없다 ({exc}).\n"
            "  받은 설치 한 줄을 그대로 쓰면 된다. 운영자에게 물어본다.",
            f"This server does not accept logins — could not read {base}{RESOURCE_METADATA} ({exc}).\n"
            "  Use the install line you were given as is, or ask the operator.")) from None
    servers = res.get("authorization_servers") or []
    cid = (res.get("casebook_device_client_id") or "").strip()
    if not servers or not cid:
        raise LoginError(say(
            "이 서버는 기기 로그인을 아직 열지 않았다 — 운영자에게 물어본다.",
            "This server has not opened device login yet — ask the operator."))
    meta = _get_json(servers[0].rstrip("/") + AS_DISCOVERY)
    for k in ("device_authorization_endpoint", "token_endpoint"):
        if not meta.get(k):
            raise LoginError(say(f"인가 서버에 {k} 가 없다 — 기기 로그인을 지원하지 않는다.",
                                 f"the authorization server has no {k} — device login is unsupported."))
    return {"base": base, "client_id": cid, "issuer": servers[0],
            "device": meta["device_authorization_endpoint"], "token": meta["token_endpoint"],
            "resource": res.get("resource", "")}


# ── 토큰 보관 ────────────────────────────────────────────────────────────────
def save(tok: dict, cfg: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": time.time() + float(tok.get("expires_in") or 3600),
            "issuer": cfg["issuer"], "client_id": cfg["client_id"], "token_endpoint": cfg["token"],
            "base": cfg["base"], "resource": cfg.get("resource", "")}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)                                          # 쓰기 전에 권한부터 — 잠깐도 열지 않는다
    tmp.replace(p)


def load() -> dict | None:
    p = _path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                             # noqa: BLE001 — 깨졌으면 없는 것과 같다
        return None


def logout() -> bool:
    p = _path()
    if not p.is_file():
        return False
    p.unlink()
    return True


def _refresh(saved: dict) -> dict | None:
    """refresh token 으로 새 access token 을 받는다. 거절되면 None — 조용히 옛 것을 쓰지 않는다."""
    if not saved.get("refresh_token"):
        return None
    form = {"grant_type": "refresh_token", "refresh_token": saved["refresh_token"],
            "client_id": saved["client_id"]}
    if saved.get("resource"):
        form["resource"] = saved["resource"]        # 빼면 aud 가 기본 클라이언트로 되돌아간다
    status, body = _post_form(saved["token_endpoint"], form)
    if status != 200 or not body.get("access_token"):
        return None
    saved = dict(saved)
    saved["access_token"] = body["access_token"]
    saved["refresh_token"] = body.get("refresh_token") or saved["refresh_token"]
    saved["expires_at"] = time.time() + float(body.get("expires_in") or 3600)
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(p)
    return saved


def access_token() -> str | None:
    """지금 쓸 수 있는 access token. 없으면 None — 부르는 쪽이 옛 경로 토큰으로 간다.

    갱신은 한 번에 하나만 한다(확장 103호). AuthKit 은 갱신할 때마다 refresh token 을 새것으로
    바꾸므로, 둘이 동시에 갱신하면 뒤엣것이 이미 폐기된 것을 내밀어 로그인이 통째로 끊긴다.
    자물쇠를 잡은 뒤 파일을 다시 읽는 것이 요점이다 — 기다리는 동안 남이 이미 받아 놓았다."""
    saved = load()
    if not saved or not saved.get("access_token"):
        return None
    if time.time() < float(saved.get("expires_at") or 0) - _EARLY:
        return saved["access_token"]
    with _REFRESH_LOCK:
        saved = load() or saved
        if not saved.get("access_token"):
            return None
        if time.time() < float(saved.get("expires_at") or 0) - _EARLY:
            return saved["access_token"]                         # 남이 갱신해 두었다
        fresh = _refresh(saved)
        return fresh["access_token"] if fresh else None


def _bare(url: str) -> str:
    """경로에 실린 토큰을 뗀 주소. 로그인한 뒤에는 주소가 열쇠가 아니다."""
    cut = url.find("/mcp")
    return (url[:cut] + "/mcp") if cut > 0 else url


def route(url: str) -> tuple[str, dict[str, str]]:
    """어디로, 어떤 머리를 달고 붙나 (확장 96호). 서버에 붙는 넷이 모두 이 한 자리를 쓴다.

    로그인해 두었으면 경로에서 토큰을 떼고 Authorization 을 준다. 아니면 받은 주소를 그대로
    돌려주고 머리는 비운다 — 주소 안의 토큰이 그때는 유일한 열쇠다.

    왜 한 자리인가: 확장 89호가 프록시만 고쳤더니 창·훅·backfill 셋이 경로 토큰에 남았고,
    그 상태로 구형 경로를 닫으면 창이 안 뜨고 훅 다섯이 **조용히** 죽는다(증거 #2151).
    붙는 방법을 넷이 각자 알고 있으면 이런 어긋남이 또 생긴다.

    여기서 받은 머리는 **그 한 번의 요청에만** 쓴다. 오래 사는 연결에 박아 두면 안 된다 —
    토큰 수명이 300초라 5분 뒤부터 전부 401 이다. 그런 곳은 route_auth 를 쓴다(확장 103호).
    창(ui_server)은 요청마다 이것을 부르므로 그대로 둔다."""
    tok = access_token()
    if not tok:
        return url, {}
    return _bare(url), {"Authorization": f"Bearer {tok}"}


_REFRESH_LOCK = threading.Lock()
_BEARER: object | None = None


def bearer():
    """요청마다 그때 유효한 토큰을 다는 httpx 인증기 (확장 103호).

    왜 인증기인가: access token 수명이 300초다(WorkOS 실측 2026-09-14, iat/exp 차이). 프록시는
    몇 시간을 사는데 붙을 때 받은 머리를 그대로 들고 있으면 5분 뒤부터 서버가 전부 401 로
    돌려보낸다. access_token() 이 만료 60초 전부터 조용히 갱신하므로, 매 요청에 그것을 부르면
    만료라는 것이 아예 보이지 않는다. 갱신은 망을 타는 동기 호출이라 스레드로 보낸다 —
    이벤트 루프를 막으면 그 연결의 다른 요청이 전부 멈춘다."""
    global _BEARER
    if _BEARER is None:
        import anyio
        import httpx2

        class _Bearer(httpx2.Auth):
            async def async_auth_flow(self, request):
                tok = await anyio.to_thread.run_sync(access_token)
                if tok:
                    request.headers["Authorization"] = f"Bearer {tok}"
                yield request

            def sync_auth_flow(self, request):
                tok = access_token()
                if tok:
                    request.headers["Authorization"] = f"Bearer {tok}"
                yield request

        _BEARER = _Bearer()
    return _BEARER


def route_auth(url: str) -> tuple[str, object | None]:
    """route 와 같은 판정인데, 머리를 한 번 박는 대신 요청마다 다는 인증기를 준다.

    오래 사는 연결(프록시·backfill·훅)은 이것을 쓴다. 판정 자체는 route 와 같아야 한다 —
    로그인 전이거나 갱신이 거절되면 주소를 그대로 두고 None 을 준다(경로 토큰으로 간다)."""
    if not access_token():
        return url, None
    return _bare(url), bearer()


# ── 기기 흐름 ────────────────────────────────────────────────────────────────
def login(base: str, show=print, sleep=time.sleep) -> dict:
    """RFC 8628. 코드를 보여 주고 사람이 브라우저에서 넣을 때까지 기다린다."""
    cfg = discover(base)
    # RFC 8707 — 어느 자원에 쓸 토큰인지 밝힌다. 이것을 빼면 AuthKit 이 aud 를 그 환경의 기본
    # 클라이언트로 넣어 주고(실측 2026-09-14: aud=client_01…), 우리 문은 그 토큰을
    # InvalidAudienceError 로 거절한다. 대시보드의 "Default" resource indicator 는 이 흐름에
    # 적용되지 않았다 — 보내야 한다.
    form = {"client_id": cfg["client_id"], "scope": SCOPES}
    if cfg.get("resource"):
        form["resource"] = cfg["resource"]
    status, dev = _post_form(cfg["device"], form)
    if status != 200 or not dev.get("device_code"):
        raise LoginError(say(f"기기 인증을 시작하지 못했다 ({status}) {dev.get('error', '')}",
                             f"could not start device authorization ({status}) {dev.get('error', '')}"))
    uri = dev.get("verification_uri_complete") or dev.get("verification_uri", "")
    code = dev.get("user_code", "")
    show(say(f"\n브라우저에서 열고 코드를 넣는다:\n  {dev.get('verification_uri', uri)}\n  코드  {code}\n",
             f"\nOpen this in a browser and enter the code:\n  {dev.get('verification_uri', uri)}\n"
             f"  code  {code}\n"))
    if dev.get("verification_uri_complete"):
        show(say(f"  (코드가 이미 들어간 주소: {dev['verification_uri_complete']})\n",
                 f"  (or open this, the code is already in it: {dev['verification_uri_complete']})\n"))

    interval = float(dev.get("interval") or 5)
    deadline = time.monotonic() + min(float(dev.get("expires_in") or _MAX_WAIT), _MAX_WAIT)
    show(say("기다리는 중… (Ctrl+C 로 그만둔다)", "Waiting… (Ctrl+C to stop)"))
    while time.monotonic() < deadline:
        sleep(interval)
        exchange = {"grant_type": DEVICE_GRANT, "device_code": dev["device_code"],
                    "client_id": cfg["client_id"]}
        if cfg.get("resource"):
            exchange["resource"] = cfg["resource"]
        status, body = _post_form(cfg["token"], exchange)
        err = body.get("error")
        if status == 200 and body.get("access_token"):
            body = _scope_to_resource(body, cfg)
            save(body, cfg)
            return body
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5                                         # RFC 8628 5.2 — 시키는 대로 늦춘다
            continue
        if err == "expired_token":
            raise LoginError(say("코드가 만료됐다 — 다시 실행한다.", "the code expired — run it again."))
        if err == "access_denied":
            raise LoginError(say("로그인이 거절됐다.", "the login was denied."))
        raise LoginError(say(f"로그인에 실패했다 ({status}) {err or ''}",
                             f"login failed ({status}) {err or ''}"))
    raise LoginError(say("기다리다 시간이 다 됐다 — 다시 실행한다.", "timed out waiting — run it again."))


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    from casebook.core import clientmode
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--logout" in argv:
        gone = logout()
        print(say("토큰을 지웠다." if gone else "로그인돼 있지 않다.",
                  "Token removed." if gone else "Not logged in."))
        return 0
    if "--status" in argv:
        saved = load()
        if not saved:
            print(say("로그인돼 있지 않다.", "Not logged in."))
            return 1
        left = int(float(saved.get("expires_at") or 0) - time.time())
        print(say(f"서버 {saved.get('base')} · 인가 {saved.get('issuer')}\n"
                  f"  access token {'유효' if left > 0 else '만료'} ({abs(left)}초 {'남음' if left > 0 else '지남'})"
                  f" · 갱신 토큰 {'있음' if saved.get('refresh_token') else '없음'}",
                  f"server {saved.get('base')} · authorization {saved.get('issuer')}\n"
                  f"  access token {'valid' if left > 0 else 'expired'} ({abs(left)}s "
                  f"{'left' if left > 0 else 'ago'}) · refresh token "
                  f"{'yes' if saved.get('refresh_token') else 'no'}"))
        return 0
    base = next((a for a in argv if not a.startswith("-")), "") or (clientmode.remote_url() or "")
    if not base:
        print(say("서버 주소를 모른다 — casebook-login https://<host> 처럼 준다.",
                  "No server address — run: casebook-login https://<host>"), file=sys.stderr)
        return 2
    try:
        login(base)
    except LoginError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(say("\n그만뒀다.", "\nStopped."), file=sys.stderr)
        return 130
    saved = load() or {}
    print(say(f"로그인됐다. 토큰은 {_path()} 에 있다(0600) — 화면·채팅에 올리지 않는다.",
              f"Logged in. The token is in {_path()} (0600) — do not paste it anywhere."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
