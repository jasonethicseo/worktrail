"""HTTP 표면 32개 — Xano api group 3개의 로컬 짝 26 + intake(21호) + Tracker 읽기 2개(22호).

기존 프론트가 config 만 바꿔 붙는 것이 인수 조건이다:
  web/app/config.js      APP_BASE  → http://<host>/app   AUTH_BASE → http://<host>/auth
  web/tickets/tickets.config.js  TICKETS_BASE → http://<host>/tickets  (+ 위 둘)
프론트는 {BASE}/case…, {BASE}/auth/login, {BASE}/ticket… 로 부르므로 그룹 프리픽스를
그대로 유지한다.

ROUTES 는 표면의 명세다(모듈 임포트만으로 읽힌다 — 불변식 13 검사가 쓴다).
FastAPI 는 create_app() 안에서만 임포트한다 — 코어 테스트는 fastapi 없이 돈다.
"""
from __future__ import annotations

from typing import Any

from casebook.core import oauth   # 확장 97호 — 이 문도 인가 서버의 JWT 를 받는다

# 확장 122호 — 구형 경로를 닫은 서버가 API 문에서 내는 거절문. MCP 문(mcp_server)의 것과 같은 말이다.
_LEGACY_CLOSED = ("The address-in-the-URL token is closed on this server. "
                  "Log in once from a terminal: casebook-login, then the client "
                  "sends the token in the Authorization header instead.")
_PASSWORD_CLOSED = ("Password sign-up and login are closed on this server; it uses OAuth. "
                    "Log in once from a terminal: casebook-login.")

# 라우트 → 허용 메서드. message·evidence·ledger 에 PUT/PATCH/DELETE 가 없는 것이
# 불변식 13 의 HTTP 표면 보장이다(Xano 에서는 "엔드포인트를 안 만듦"이 보장이었다).
ROUTES: dict[str, tuple[str, ...]] = {
    # Authentication 그룹
    "/auth/auth/signup": ("POST",),
    "/auth/auth/login": ("POST",),
    "/auth/auth/me": ("GET",),
    # app 그룹
    "/app/case": ("GET", "POST"),
    "/app/case/{case_id}": ("GET",),
    "/app/case/{case_id}/status": ("POST",),
    "/app/case/{case_id}/ledger": ("GET",),
    "/app/thread": ("GET",),         # 확장 22호 — Tracker: 저장소별 스레드 전부 (읽기 전용)
    "/app/thread/{case_id}": ("GET",),   # 확장 22호 — Tracker: 스레드 7칸 상태(resume) + 커밋 (읽기 전용)
    "/app/status": ("GET",),         # 확장 31호 — 현황: 주제로 묶은 스레드, 단계·차례·결과(읽을 때 도출)
    "/app/review": ("GET",),
    "/app/prior": ("GET",),          # 확장 43호 — 과거 기록: 작업공간 묶음, ?repo_id=N 이면 그 세션 목록
    "/app/prior/{session_id}": ("GET",),   # 그 세션에서 사람이 친 말 전부, 순서대로         # 확장 24호 — Review: 주제별 회고(사건은 읽을 때 도출)
    "/app/join": ("GET", "POST"),    # 확장 108호 — 가입 신청 줄 보기 · 승인/거절(body: user_id, action). 운영자 전용
    "/app/topic": ("GET", "POST"),   # 확장 24호 — 주제 목록 · 만들기/스레드 넣기(body: name|topic_id, case_ids, summary, repos, remove)
                                     # 확장 47호 — 합치기(topic_id + merge_into) · 옮기기(topic_id + case_ids + from_topic_id)
}


# 조사(casebook)와 tickets 의 표면 — 테스터에게는 열리지 않는다(인계서 4절).
# 나머지 /app/* 는 Worktrail 기록이라 worktrail 범위로 연다. 경로로 막는다: 화면에서
# 메뉴를 숨기는 것으로는 직접 요청을 못 막는다.
_OPERATOR_SUFFIXES = ("/turn", "/recovery", "/draft-query", "/web-lookup", "/record")


# 테스터(worktrail 범위)에게 여는 기록 표면. 여기 없는 것은 전부 운영자 것이다 —
# 모르는 경로를 열어 두면 나중에 붙는 표면과 /openapi.json 같은 틀 경로가 그냥 새어 나간다.
_WORKTRAIL_EXACT = frozenset({
    "/app/case", "/app/thread", "/app/status", "/app/review", "/app/prior", "/app/topic",
})
_WORKTRAIL_CASE_SUFFIXES = ("/ledger", "/status")


def operator_only(path: str, method: str = "GET") -> bool:
    """이 경로·메서드가 운영자 범위인가. 기록 표면만 열고 나머지는 닫는다(모르면 닫는다).

    메서드까지 보는 이유: /app/case 는 GET 이면 내 스레드 목록이지만 POST 면 조사 케이스를 새로
    만든다. 같은 경로가 읽기냐 쓰기냐로 갈린다."""
    method = (method or "GET").upper()
    if path == "/app/case":
        return method not in ("GET", "HEAD", "OPTIONS")     # 목록은 열고, 생성은 닫는다
    if path in _WORKTRAIL_EXACT:
        return False
    if path.startswith("/app/thread/") or path.startswith("/app/prior/"):
        return False
    if path.startswith("/app/case/"):
        # /app/case/{id} 와 /ledger · /status 만 기록 표면이다.
        # 조사: /turn · /turn/{id} · /recovery · /draft-query · /web-lookup · /record
        rest = path[len("/app/case/"):]
        return "/" in rest and not path.endswith(_WORKTRAIL_CASE_SUFFIXES)
    return True


def create_app(casebook: Any, tickets: Any, cors_origins: list[str] | None = None,
               enforce_access: bool = False):
    """FastAPI 앱 조립. casebook: core.worktrail.Worktrail(기록만) 또는 core.app.Casebook(조사까지),
    tickets: core.tickets.Tickets 또는 None. 조사·티켓 길은 그 객체가 있을 때만 붙는다(확장 130호).

    cors_origins: 프론트 origin 허용목록. None 이면 "*"(로컬 개발). 서빙 시에는
    main.py 가 CASEBOOK_CORS_ORIGINS 로 넘긴다 — 토큰이 Authorization 헤더로 가므로
    origin 을 좁혀야 남의 페이지가 사용자 브라우저에서 API 를 못 부른다.

    enforce_access: 서버 이용 허용·동의·범위를 검사한다(인계서 4절). 기본은 끔 —
    로컬 모드의 창(ui_server)이 같은 앱을 127.0.0.1 에 띄우는데 거기는 사람이 하나이고
    허용이라는 개념이 없다. 서버 진입점(main.py)이 켠다.
    """
    from fastapi import Body, FastAPI, Header, Request
    from fastapi.concurrency import run_in_threadpool
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    from casebook.core.errors import ApiError, InputError, UnauthorizedError

    api = FastAPI(title="Casebook", docs_url=None, redoc_url=None)

    @api.get("/health")
    async def health():
        # 로드밸런서·업타임 체크용. 26개 표면 밖(그룹 프리픽스 없음)이라 오라클과 무관.
        return {"ok": True, "pending_jobs": casebook.pending_jobs()}

    @api.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError):
        # Xano 에러 봉투와 같은 최소 모양 — 프론트는 j.message 만 읽는다
        return JSONResponse(status_code=exc.http_status, content={"message": exc.message})

    def _user(authorization: str | None) -> int:
        """이 요청이 누구인가. 구형 HMAC 토큰과 인가 서버의 JWT 를 둘 다 받는다 (확장 97호).

        MCP 문과 같은 규칙이다(mcp_server.who_from_request): 꼴로 먼저 가르고 그 길에서만 판정한다.
        API 는 두 번째 문일 뿐 같은 기록을 지킨다 — 한쪽만 OAuth 를 알면 로컬 창이 로그인 뒤에도
        구형 토큰에 묶인다(실측 2026-09-14: 창이 OAuth 토큰을 보내자 api 가 401, 창은 502)."""
        if not authorization or not authorization.startswith("Bearer "):
            raise UnauthorizedError("Missing token.")
        token = authorization[len("Bearer "):]
        if oauth.enabled() and oauth.looks_like_jwt(token):
            try:
                return oauth.user_of(casebook.db, oauth.verify(token))
            except Exception as exc:  # noqa: BLE001 — 서명이 틀렸든 계정이 없든 401 이다
                raise UnauthorizedError("Invalid token.") from None
        uid = casebook.verify(token)
        # 확장 122호 (D15844) — MCP 문(mcp_server 의 legacy_allowed 검사)과 같은 자리. 구형 경로를
        # 닫았으면 이 문도 닫힌다. 실측 2026-09-15: 4단계를 닫고도 같은 토큰으로 MCP 문은 401,
        # /app/status 는 200 이었다 — 검사가 MCP 문에만 있었다. 닫지 않은 서버(로컬 창)는 전부 지난다.
        if not oauth.legacy_allowed(uid):
            raise UnauthorizedError(_LEGACY_CLOSED)
        return uid

    # ── 이용 허용 (인계서 4절) ─────────────────────────────────────────
    # 라우트가 아니라 미들웨어에 두는 이유: 앞으로 붙는 표면도 자동으로 걸리고, 화면을 거치지 않는
    # 직접 요청도 같은 자리에서 막힌다. 로그인·가입·자기 확인(/auth/*)과 /health 는 지난다 —
    # 차단된 사람도 자기가 차단됐다는 것은 알 수 있어야 하고, 그 셋은 작업 기록이 아니다.
    if enforce_access:
        from casebook.core import access
        from casebook.core.errors import AccessDeniedError

        _OPEN_PATHS = ("/health", "/auth/auth/signup", "/auth/auth/login", "/auth/auth/me")

        @api.middleware("http")
        async def _gate(request: Request, call_next):
            path = request.url.path
            if path in _OPEN_PATHS or request.method == "OPTIONS":
                return await call_next(request)
            authorization = request.headers.get("authorization")
            try:
                uid = _user(authorization)
                need = (access.SCOPE_OPERATOR if operator_only(path, request.method)
                        else access.SCOPE_WORKTRAIL)
                await run_in_threadpool(access.check, casebook.db, uid, need)
            except (UnauthorizedError, AccessDeniedError) as exc:
                return JSONResponse(status_code=exc.http_status, content={"message": exc.message})
            return await call_next(request)

    # CORS 는 **마지막에** 붙인다 — Starlette 은 나중에 추가한 미들웨어가 바깥이라, 위 관문보다
    # 뒤에 두어야 관문이 낸 401·403 에도 CORS 헤더가 붙는다. 안 그러면 브라우저가 거절 사유를
    # 읽지 못하고 access.py 가 구별해 만든 세 문장이 화면에서 사라진다.
    api.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"], allow_methods=["*"], allow_headers=["*"],
    )

    # ── Authentication ──────────────────────────────────────────────
    # 확장 122호 (D15844) — 구형 경로를 닫은 서버에는 비밀번호 문도 없다. 여기서 내주는 authToken 은
    # 커넥터 토큰과 같은 서명의 구형 토큰이라 어차피 _user 에서 막히는데, 문만 열어 두면 계정은
    # 계속 만들어진다. 허용 목록(LEGACY_USERS, D15352)의 계정만 로그인이 지난다 — 그 토큰은 _user 를
    # 지나므로. 이 토큰을 서버로 보내는 것은 S3 의 옛 웹 화면(web/app·web/tickets)뿐이고 지금 쓰지
    # 않는다; 다시 쓰게 되면 비밀번호를 되살리지 않고 브라우저 OAuth 로 옮긴다.
    @api.post("/auth/auth/signup")
    async def signup(body: dict = Body(...)):
        if oauth.only():
            raise UnauthorizedError(_PASSWORD_CLOSED)
        return await run_in_threadpool(
            casebook.signup, body.get("name"), body.get("email"), body.get("password"),
            body.get("invite_code"),  # 확장 2호 — 서버가 코드를 요구할 때만 의미 있다
        )

    @api.post("/auth/auth/login")
    async def login(body: dict = Body(...)):
        if oauth.only() and not oauth.legacy_users():
            raise UnauthorizedError(_PASSWORD_CLOSED)      # 열어 둔 계정이 하나도 없으면 비밀번호도 안 본다
        out = await run_in_threadpool(casebook.login, body.get("email"), body.get("password"))
        if not oauth.legacy_allowed(casebook.verify(out["authToken"])):
            raise UnauthorizedError(_PASSWORD_CLOSED)
        return out

    @api.get("/auth/auth/me")
    async def me(authorization: str | None = Header(default=None)):
        uid = _user(authorization)          # 구형·JWT 둘 다 지난다
        token = authorization[len("Bearer "):]
        if oauth.enabled() and oauth.looks_like_jwt(token):
            u = casebook.db.get("user", uid)
            return {"id": u["id"], "name": u["name"], "email": u["email"]}
        return casebook.me(token)

    # ── app ─────────────────────────────────────────────────────────
    @api.get("/app/case")
    async def list_cases(authorization: str | None = Header(default=None)):
        return casebook.list_cases(_user(authorization))

    @api.post("/app/case")
    async def create_case(body: dict = Body(...), authorization: str | None = Header(default=None)):
        return casebook.create_case(_user(authorization), body.get("title"))

    @api.get("/app/case/{case_id}")
    async def get_case(case_id: int, authorization: str | None = Header(default=None)):
        out = casebook.get_case(_user(authorization), case_id)
        return {k: out[k] for k in ("case", "conversation", "evidence", "turns")}

    @api.get("/app/thread")
    async def list_all_threads(authorization: str | None = Header(default=None)):
        return {"repos": casebook.list_all_threads(_user(authorization))}

    @api.get("/app/thread/{case_id}")
    async def thread_state(case_id: int, authorization: str | None = Header(default=None)):
        return casebook.thread_state(_user(authorization), case_id)

    @api.get("/app/status")
    async def status_all(authorization: str | None = Header(default=None)):
        return casebook.status(_user(authorization))

    @api.get("/app/review")
    async def review_all(authorization: str | None = Header(default=None)):
        return casebook.review(_user(authorization))

    @api.get("/app/prior")
    async def prior_archive(repo_id: int | None = None, authorization: str | None = Header(default=None)):
        uid = _user(authorization)
        if repo_id is not None:
            return {"sessions": casebook.prior_sessions(uid, repo_id)}
        return casebook.prior_archive(uid)

    @api.get("/app/prior/{session_id}")
    async def prior_session(session_id: int, authorization: str | None = Header(default=None)):
        return casebook.prior_session(_user(authorization), session_id)

    # ── 가입 신청 (확장 108호, D15414) ───────────────────────────────
    # 사람은 /join 에서 구글로 로그인해 줄을 서고(확장 100·101호), 운영자가 여기서 고른다.
    # 지금까지는 서버에 들어가 `allow EMAIL` 을 손으로 쳐야 했다 — 테스터를 받으려면 매번
    # SSM 을 열어야 하므로 사실상 모집을 못 한다. 경로가 _WORKTRAIL_EXACT 에 없으므로
    # operator_only() 가 기본값(닫힘)으로 판정한다: 테스터는 이 길을 볼 수 없다.
    @api.get("/app/join")
    async def join_queue(authorization: str | None = Header(default=None)):
        from casebook.core import access
        _user(authorization)                       # 미들웨어가 이미 운영자 범위를 봤다
        rows = await run_in_threadpool(access.pending, casebook.db)
        # enforced=False 는 혼자 쓰는 로컬 창이다 — 거기엔 승인할 사람이라는 것이 없다.
        # 화면은 이 값으로 상단바에 자리를 낼지 정한다(빈 메뉴를 보여 주지 않는다).
        return {"pending": rows, "cap": access.PENDING_CAP, "enforced": bool(enforce_access),
                "seats": access.seats(casebook.db)}     # 확장 119호 — 정원이 얼마나 찼나

    @api.post("/app/join")
    async def join_decide(body: dict = Body(...), authorization: str | None = Header(default=None)):
        """승인 또는 거절. 둘 다 되돌릴 수 있다 — 거절은 차단이고, 다시 승인하면 열린다."""
        from casebook.core import access
        _user(authorization)
        uid = int(body.get("user_id") or 0)
        action = (body.get("action") or "").strip()
        if action not in ("allow", "block"):
            raise InputError("action 은 allow 또는 block 이다")
        if casebook.db.get("user", uid) is None:
            raise InputError(f"그런 계정이 없다: {uid}")
        st = await run_in_threadpool(
            access.set_state, casebook.db, uid,
            access.STATE_ALLOWED if action == "allow" else access.STATE_BLOCKED)
        rows = await run_in_threadpool(access.pending, casebook.db)
        return {"state": st["state"], "pending": rows, "cap": access.PENDING_CAP,
                "enforced": bool(enforce_access)}

    @api.get("/app/topic")
    async def list_topics(authorization: str | None = Header(default=None)):
        return {"topics": casebook.list_topics(_user(authorization))}

    @api.post("/app/topic")
    async def topic(body: dict = Body(...), authorization: str | None = Header(default=None)):
        uid = _user(authorization)
        ids = [int(x) for x in (body.get("case_ids") or [])]
        if body.get("topic_id") is not None:
            if body.get("merge_into") is not None:          # 확장 47호 — 합치기: topic_id 가 merge_into 로 들어가 사라진다
                return casebook.merge_topic(uid, int(body["topic_id"]), int(body["merge_into"]))
            if body.get("from_topic_id") is not None:       # 확장 47호 — 옮기기: from_topic_id 에서 빼고 topic_id 에 넣는다
                return casebook.move_topic(uid, ids, int(body["from_topic_id"]), int(body["topic_id"]))
            if "conclusion" in body and not ids:
                return casebook.conclude_topic(uid, int(body["topic_id"]), body.get("conclusion"))
            return casebook.assign_topic(uid, int(body["topic_id"]), ids, remove=bool(body.get("remove")))
        return casebook.topic_by_name(uid, body.get("name") or "", body.get("summary") or "", body.get("repos") or [], ids,
                                      conclusion=body.get("conclusion"))

    @api.post("/app/case/{case_id}/status")
    async def set_status(case_id: int, body: dict = Body(...),
                         authorization: str | None = Header(default=None)):
        uid, status, result = _user(authorization), body.get("status"), body.get("result")
        if status == "resolved":
            # 확장 48호 — 화면에서 닫는 것도 MCP close_thread 와 같은 뜻이다: 묶인 worktree 를 푼다.
            # 안 풀면 그 폴더의 다음 세션이 닫힌 스레드에 붙은 채 시작한다.
            casebook.close_thread(uid, case_id, result)
            return {"case_id": case_id, "status": "resolved"}      # 응답 모양은 종전 그대로(test_http_surface 가 고정)
        return casebook.set_status(uid, case_id, status, result)

    @api.get("/app/case/{case_id}/ledger")
    async def list_ledger(case_id: int, authorization: str | None = Header(default=None)):
        return casebook.list_ledger(_user(authorization), case_id)

    # 확장 130호 (D16054) — 조사·티켓의 길은 private 전용 모듈(http_api_legacy)에 있다. 조사 객체(intake 가
    # 있는 것)나 tickets 가 주어졌을 때만 늦게 불러 붙인다: Worktrail 만 띄우는 서버와 로컬 창은 그 모듈이
    # 없어도 돈다 — 공개 저장소와 설치 묶음에는 그 파일이 없다.
    if tickets is not None or hasattr(casebook, "intake"):
        from casebook.adapters import http_api_legacy
        http_api_legacy.mount(api, casebook, tickets, _user, run_in_threadpool)

    return api
