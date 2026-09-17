"""서버 이용 허용 — 로그인과 이용 허용은 별개다 (온보딩·인증 인계서 4절).

토큰이 진짜라는 것과 이 서버를 써도 된다는 것은 다른 질문이다. 전에는 서명만 맞으면 전부 통과했고,
회수 수단이 `auth_secret` 교체뿐이라 한 사람을 끊으려면 전원이 끊겼다. 여기서 그 둘을 가른다:

    check(db, user_id)                     기록을 읽고 쓸 수 있는가 — 아니면 403
    check(db, user_id, need=SCOPE_OPERATOR)  조사·tickets 까지 열어도 되는가

세 칸을 본다.
- state   allowed | blocked  — 사람(운영자)이 켜고 끄는 스위치. 개별 회수가 여기서 된다.
- scope   worktrail | operator — 테스터는 자기 기록만. 조사와 tickets 는 운영자 것이다.
- consent 동의한 안내문 판과 시각 — 동의 전에는 작업 기록이 오가지 않는다.

기존 계정은 허용으로 시작한다(D15081). 표가 처음 생길 때 그 시점에 있던 사용자를 전부 허용으로 적고
그 사실을 _meta 에 남긴다. 그 뒤에 생기는 계정은 기본 차단이다 — 발급이 곧 가입이던 것을 끊는다.

여기서 정하지 않는 것: 정원(몇 명까지), 안내문 내용과 보관·삭제 기간, 구형 토큰 폐기 시점.
전부 사용자가 확정할 제품 정책이라 숫자나 문구를 임의로 넣지 않는다.
"""
from __future__ import annotations

from typing import Any

from .errors import AccessDeniedError

STATE_ALLOWED = "allowed"
STATE_BLOCKED = "blocked"
# 확장 100호 (D15414) — 신청했고 아직 운영자가 보지 않은 상태. blocked 와 갈라 두는 이유는
# 운영자의 줄에 무엇이 서 있는지 알기 위해서다: blocked 는 "봤고 거절했다" 이고 pending 은 "아직 안 봤다".
# check 에서는 둘 다 막힌다 — allowed 만 지난다.
STATE_PENDING = "pending"
STATES = (STATE_ALLOWED, STATE_BLOCKED, STATE_PENDING)

# 대기열 상한. 정원(D15134, 서버 5명)과 다른 숫자다 — 이것은 아직 아무것도 아닌 행이 무한정
# 쌓이지 않게 막는 자리다. 확장 87호가 "로그인이 계정을 만들지 않는다"고 한 것을 신청제로 풀면서
# 붙인 조건 셋 중 하나(나머지 둘: 서명이 검증된 신원만, pending 으로만).
PENDING_CAP = 20

# 확장 119호 — 신청하면 앞 다섯은 그 자리에서 열린다. 종전에는 전부 pending 이라 운영자가 승인을
# 누를 때까지 아무도 못 썼다: 사람이 병목이고, 밤에 신청한 사람은 아침까지 기다린다. 레딧에서
# 온 사람에게 그것은 "대기 명단" 으로 읽히기도 한다.
# 정원은 D15134 그대로 5명이고, 기존 계정은 정원 밖이다 — grandfather 가 적어 둔 것은 세지 않는다.
# 확장 130호 — 이 숫자는 이제 운영자가 창에서 돌린다. 여기 있는 값은 아직 돌린 적이 없는
# 서버의 출발값이고, 실제로 쓰는 값은 total_seats(db) 가 _meta 에서 읽는다.
TESTER_SEATS = 5
SEATS_KEY = "access_tester_seats"
# 손이 닿는 범위의 천장. 혼자 돌리는 서버라 무한히 열 수 있으면 안 되고, 잘못 눌러 1000 이
# 박히는 것도 막는다. 줄 상한(PENDING_CAP)과 같은 자리에 두는 숫자다.
SEATS_MAX = 20

SCOPE_WORKTRAIL = "worktrail"      # 기록만 — 테스터
SCOPE_OPERATOR = "operator"        # 조사·tickets 까지 — 운영자
SCOPES = (SCOPE_WORKTRAIL, SCOPE_OPERATOR)

# 표가 처음 생긴 시각. 이 값이 있으면 grandfather 는 이미 끝났다는 뜻이다.
GRANDFATHER_KEY = "access_grandfathered_at"
# 기존 계정에 적어 두는 표시 — 실제 동의가 아니다. 언제 동의를 받을지는 운영 적용 전에 따로 정한다.
CONSENT_GRANDFATHERED = "grandfathered"

# 확장 115호 — 지금 보여 주는 데이터 안내문의 판(D15637 의 넷: 보관·삭제·열람·종료).
# 신청 페이지가 이 값의 안내문을 보이고, 동의하면 이 값이 user_access.consent_version 에 박힌다.
# 위 CONSENT_GRANDFATHERED 의 "따로 정한다" 가 여기서 닫힌다 — 그 전까지는 동의를 남기는 길이
# CLI 하나뿐이라 신청제로 들어온 계정이 전부 check 에서 막혔다.
# 날짜인 이유는 판이 바뀐 것을 사람이 바로 읽을 수 있어서다. 안내문 문구를 고치면 이 값도 올린다.
NOTICE_VERSION = "2026-09-17"

# 확장 126호 — 로컬로 쓰기로 한 사람이 동의한 것은 다른 것이다. 그 사람의 기록은 서버에 오지
# 않으므로 보관·삭제·열람·종료가 해당되지 않고, 내가 받는 것은 신청한 이메일 주소 하나뿐이다.
# 서버 안내문을 그대로 읽히면 사실이 아닌 것에 동의를 받는 셈이고, 실제로 주저를 만든다
# (사용자 2026-09-17: "서버에 설치를 전제로 안내문이 뜨던데 그건 주저를 유발하지 않을까").
# 나중에 서버로 바꾸는 사람은 설치 화면이 그때 맡기는 조건을 다시 보여 준다(확장 112호).
NOTICE_VERSION_LOCAL = NOTICE_VERSION + "+local"


def notice_version(mode: str) -> str:
    """이 사람이 읽은 안내문의 판. 서버냐 로컬이냐로 갈린다 — 같은 글이 아니다."""
    return NOTICE_VERSION_LOCAL if (mode or "").strip() == "local" else NOTICE_VERSION


def notice_current(version: str | None) -> bool:
    """이 사람이 동의한 것이 지금 보여 주는 안내문인가.

    확장 129호 — NOTICE_VERSION 을 올려도 아무 일이 일어나지 않던 자리다. 동의를 보는 곳이
    전부 "값이 있는가" 만 물었으므로(check 의 `not consent_version`, /join/status 의
    `and st["consent_version"]`), 판을 올려도 옛 판에 동의한 사람은 그대로 지나갔다.
    판을 올리는 행위에 뜻이 생기려면 묻는 쪽이 판을 비교해야 한다.

    grandfathered 는 동의가 아니다(표가 생길 때 기존 계정에 적어 둔 표시일 뿐) — False 다.

    check() 는 이것을 쓰지 않는다. 일하는 도중 403 을 내면 사람이 브라우저로 가서 다시 동의할
    때까지 에이전트가 멈추는데, 다시 읽히는 것이 목적이지 일을 끊는 것이 목적이 아니다.
    문구가 약해지거나 새 의무가 생겨 정말 끊어야 할 판이 오면 그때 여기를 check 에 건다."""
    return version in (NOTICE_VERSION, NOTICE_VERSION_LOCAL)


def grandfather(db: Any) -> int:
    """처음 한 번 — 그때 있던 사용자를 전부 허용으로 적는다. 두 번째부터는 아무것도 하지 않는다.

    새 계정을 기본 차단으로 두면서 기존 사용자를 끊지 않으려면 경계가 필요하다(D15081).
    그 경계가 이 호출이고, 시각을 _meta 에 남겨 다시 돌지 않게 한다.

    경계의 증거를 둘로 본다: _meta 의 표시와, user_access 에 줄이 하나라도 있는지. 표시만 보면
    그 한 줄이 사라졌을 때(백업 복원·수동 편집) 승인 대기 중인 계정 전부가 허용+운영자로 올라선다 —
    적용 전 백업을 되돌리는 바로 그 절차가 문을 여는 셈이라, 줄이 이미 있으면 돌지 않는다.
    트랜잭션으로 감싼다: 문 둘이 같이 뜨면 같은 user_id 를 두 번 넣다 한쪽이 죽는다."""
    if db.meta(GRANDFATHER_KEY) is not None or db.count("user_access") > 0:
        if db.meta(GRANDFATHER_KEY) is None:
            db.set_meta(GRANDFATHER_KEY, str(db.now_ms()))   # 표시만 잃은 DB — 경계를 다시 세운다
        return 0
    now = db.now_ms()
    n = 0
    with db.transaction():
        if db.meta(GRANDFATHER_KEY) is not None or db.count("user_access") > 0:
            return 0                                          # 다른 문이 먼저 끝냈다
        for user in db.query("user", where={}):
            db.add("user_access", {"user_id": user["id"], "state": STATE_ALLOWED,
                                   "scope": SCOPE_OPERATOR, "consent_version": CONSENT_GRANDFATHERED,
                                   "consent_at": now, "updated_at": now})
            n += 1
        db.set_meta(GRANDFATHER_KEY, str(now))
    return n


def row(db: Any, user_id: int) -> dict[str, Any] | None:
    return db.get_by("user_access", "user_id", user_id)


def state(db: Any, user_id: int) -> dict[str, Any]:
    """이 사용자의 이용 상태. 줄이 없으면 차단으로 본다 — 표가 생긴 뒤에 만들어진 계정이다."""
    r = row(db, user_id)
    if r is None:
        return {"state": STATE_BLOCKED, "scope": SCOPE_WORKTRAIL,
                "consent_version": None, "consent_at": None, "known": False}
    return {"state": r["state"], "scope": r["scope"], "consent_version": r["consent_version"],
            "consent_at": r["consent_at"], "known": True}


def check(db: Any, user_id: int, need: str = SCOPE_WORKTRAIL) -> dict[str, Any]:
    """이 사용자가 지금 이 일을 해도 되는가. 안 되면 403 — 토큰이 진짜여도 막힌다.

    막는 이유를 구별해 알린다(차단·미동의·권한 밖). 어느 쪽이든 기록은 오가지 않는다.

    거절문은 영어다(확장 85호). 이것을 읽는 것은 에이전트이고, 서버는 부른 쪽이 어느 언어로 설치했는지
    알 수 없다 — ~/.casebook/lang 은 그 사람의 맥에 있다. headline.py 의 거절문과 같은 이유다.
    무엇을 해야 하는지까지 말한다: 막힌 사람이 다음에 할 일은 운영자에게 말하는 것뿐이다."""
    s = state(db, user_id)
    if s["state"] == STATE_PENDING:
        # 신청해 둔 사람에게 "허용되지 않았다"고만 말하면 자기가 뭘 잘못한 줄 안다.
        raise AccessDeniedError(
            "Your request is waiting for the operator to look at it, so nothing was recorded yet. "
            "You will be able to use it as soon as it is approved.")
    if s["state"] != STATE_ALLOWED:
        raise AccessDeniedError(
            "This account is not allowed on this server yet, so nothing was recorded. "
            "Ask the operator who gave you the install line to approve the address you installed with.")
    if not s["consent_version"]:
        raise AccessDeniedError(
            "This account has not accepted the data notice, so nothing is recorded or read. "
            "The notice is shown by the install line; run it again to read and accept it.")
    if need == SCOPE_OPERATOR and s["scope"] != SCOPE_OPERATOR:
        raise AccessDeniedError(
            "That tool is not open to this account. This account can use the Worktrail record only.")
    return s


def set_state(db: Any, user_id: int, new_state: str, scope: str | None = None) -> dict[str, Any]:
    """운영자가 켜고 끈다. 끄면 그 사람의 기존 토큰도 그대로 막힌다 — auth_secret 을 건드리지 않고."""
    if new_state not in STATES:
        raise ValueError(f"state 는 {STATES} 중 하나여야 한다: {new_state!r}")
    if scope is not None and scope not in SCOPES:
        raise ValueError(f"scope 는 {SCOPES} 중 하나여야 한다: {scope!r}")
    now = db.now_ms()
    r = row(db, user_id)
    if r is None:
        return db.add("user_access", {"user_id": user_id, "state": new_state,
                                      "scope": scope or SCOPE_WORKTRAIL, "consent_version": None,
                                      "consent_at": None, "updated_at": now})
    patch = {"state": new_state, "updated_at": now}
    if scope is not None:
        patch["scope"] = scope
    return db.edit("user_access", r["id"], patch)


# ── 삭제 (확장 116호, D15637 의 "삭제") ──────────────────────────────────────
# 안내문이 "말씀하시면 그 계정의 기록을 전부 지웁니다, 7일 안에 처리합니다" 라고 약속하는데
# 그것을 할 길이 없었다. 손으로 SQL 을 치면 표를 빠뜨린다 — 표는 열이 넘고 대부분 case_id 로만
# 달려 있어서, user 행만 지우면 기록이 주인 없이 남는다.
#
# repo 는 남긴다: identity 는 저장소를 가리키는 것이라 여러 사람이 같은 값을 쓴다. 남의 스레드가
# 붙어 있는 행을 이 사람 때문에 지울 수 없다. repo 에는 사람의 것이 없다 — 이름과 힌트뿐이다.
FORGET_KEEP = ("repo", "_meta", "sqlite_sequence")


def _forget(db: Any, user_id: int, apply: bool) -> dict[str, int]:
    """표를 훑어 이 계정에 달린 행을 센다(apply=False) 또는 지운다(apply=True).

    표 목록을 박아 두지 않고 그때그때 읽는 이유: 표가 늘어도 조용히 빠뜨리지 않기 위해서다.
    user_id 를 든 표는 그것으로, case_id 만 든 표는 이 사람의 case 목록으로 건다."""
    with db._lock:
        cases = [r[0] for r in db.conn.execute(
            'SELECT id FROM "case" WHERE user_id=?', (user_id,)).fetchall()]
        names = [r[0] for r in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        out: dict[str, int] = {}
        for t in sorted(names):
            if t in FORGET_KEEP or t == "user":
                continue
            cols = {c[1] for c in db.conn.execute(f'PRAGMA table_info("{t}")').fetchall()}
            where, args = [], []
            if "user_id" in cols:
                where.append("user_id=?"); args.append(user_id)
            if "case_id" in cols and cases:
                where.append(f"case_id IN ({','.join('?' * len(cases))})"); args += cases
            if not where:
                continue
            cond = " OR ".join(where)
            n = db.conn.execute(f'SELECT COUNT(*) FROM "{t}" WHERE {cond}', args).fetchone()[0]
            if n:
                out[t] = n
            if apply and n:
                db.conn.execute(f'DELETE FROM "{t}" WHERE {cond}', args)
        n = db.conn.execute("SELECT COUNT(*) FROM user WHERE id=?", (user_id,)).fetchone()[0]
        if n:
            out["user"] = n
        if apply:
            if n:
                db.conn.execute("DELETE FROM user WHERE id=?", (user_id,))
            db.conn.commit()
    return out


def forget_plan(db: Any, user_id: int) -> dict[str, int]:
    """지울 행을 표별로 센다. 아무것도 지우지 않는다 — 누르기 전에 보는 자리다."""
    return _forget(db, user_id, apply=False)


def forget(db: Any, user_id: int) -> dict[str, int]:
    """이 계정의 것을 전부 지운다. 되돌릴 수 없다.

    지운 사실만 _meta 에 남긴다 — 주소는 남기지 않는다(그러면 지운 것이 아니다). 대신 주소의
    해시 앞자리를 둔다: 나중에 "그 주소를 정말 지웠나" 를 같은 해시로 확인할 수 있고, 해시에서
    주소를 되찾을 수는 없다."""
    import hashlib
    import json

    row_ = db.get("user", user_id)
    email = ((row_ or {}).get("email") or "").strip().lower()
    counts = _forget(db, user_id, apply=True)
    log = json.loads(db.meta(FORGOTTEN_KEY) or "[]")
    log.append({"at": db.now_ms(),
                "who": hashlib.sha256(email.encode()).hexdigest()[:12] if email else "-",
                "rows": sum(counts.values())})
    db.set_meta(FORGOTTEN_KEY, json.dumps(log, ensure_ascii=False))
    return counts


FORGOTTEN_KEY = "access_forgotten"     # 지운 기록 — 언제·몇 줄. 주소는 해시 앞자리만.


def record_consent(db: Any, user_id: int, version: str) -> dict[str, Any]:
    """동의한 안내문 판과 시각을 계정에 붙인다. 판이 바뀌면 다시 받는다 — 덮어쓴다."""
    version = (version or "").strip()
    if not version:
        raise ValueError("동의 판(version)이 비어 있다")
    now = db.now_ms()
    r = row(db, user_id)
    if r is None:
        return db.add("user_access", {"user_id": user_id, "state": STATE_BLOCKED,
                                      "scope": SCOPE_WORKTRAIL, "consent_version": version,
                                      "consent_at": now, "updated_at": now})
    return db.edit("user_access", r["id"], {"consent_version": version, "consent_at": now,
                                            "updated_at": now})


# ── 신청 (확장 100호, D15414) ────────────────────────────────────────────────
class QueueFullError(AccessDeniedError):
    """대기열이 찼다. 신청한 사람에게 보여 줄 말이 들어 있다."""


def pending_count(db: Any) -> int:
    return db.count("user_access", {"state": STATE_PENDING})


def taken_seats(db: Any) -> int:
    """정원을 쓰고 있는 사람 수. 신청제로 들어온 allowed 만 센다.

    grandfather 가 적어 둔 기존 계정(CONSENT_GRANDFATHERED)은 빼는 것이 D15134 의 "기존 참여자는
    정원 밖" 이다 — 운영자 본인과 피실험자 1호가 거기 해당한다. 운영자가 손으로 allow 한 사람은
    세어진다: 그것도 자리를 쓰는 것이기 때문이다."""
    return sum(1 for r in db.query("user_access", {"state": STATE_ALLOWED})
               if r["consent_version"] != CONSENT_GRANDFATHERED)


def total_seats(db: Any) -> int:
    """지금 열어 둔 자리 수. 운영자가 돌린 값이 있으면 그것, 없으면 출발값이다.

    확장 130호 — 종전에는 모듈 상수라 숫자를 바꾸려면 배포를 해야 했다. 사용자 2026-09-17:
    "자리 추가도 버튼으로 쉽게 할 수 있게 열어놓으면 좋을 것 같은데."
    값을 _meta 에 두는 이유는 GRANDFATHER_KEY 와 같다 — 코드가 아니라 이 서버의 상태다."""
    v = db.meta(SEATS_KEY)
    if v is None:
        return TESTER_SEATS
    try:
        return max(0, min(SEATS_MAX, int(v)))
    except (TypeError, ValueError):
        return TESTER_SEATS          # 손으로 망가뜨린 값에 서버가 끌려가지 않는다


def set_total_seats(db: Any, n: int) -> int:
    """자리 수를 돌린다. 이미 앉은 사람은 건드리지 않는다 — 줄여도 아무도 쫓겨나지 않는다."""
    n = int(n)
    if not 0 <= n <= SEATS_MAX:
        raise ValueError(f"자리 수는 0 과 {SEATS_MAX} 사이다: {n}")
    db.set_meta(SEATS_KEY, str(n))
    return n


def seats(db: Any) -> dict[str, int]:
    """화면이 "3/5" 를 보이려고 읽는 값."""
    taken, total = taken_seats(db), total_seats(db)
    return {"taken": taken, "total": total, "left": max(0, total - taken)}


def request_access(db: Any, email: str, name: str = "") -> dict[str, Any]:
    """공개 페이지에서 온 신청. 계정이 없으면 만들되 **pending 으로만** 만든다.

    부르는 쪽이 서명을 이미 검증했다는 전제다(mcp_server 의 /join). 여기서는 신원을 다시 묻지 않고,
    같은 사람이 여러 번 눌러도 한 줄만 서게 한다 — 이미 있으면 그 상태를 그대로 돌려준다.
    상한을 넘으면 행을 만들지 않는다."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        raise ValueError("이메일이 없다")
    user = db.get_by("user", "email", email)
    if user is not None:
        r = row(db, user["id"])
        if r is not None:
            return {"state": r["state"], "scope": r["scope"], "new": False, "user_id": user["id"]}
    # 확장 119호 — 자리가 남아 있으면 그 자리에서 연다. 자리가 없을 때만 줄을 세운다.
    # 열려도 기록이 바로 오가지는 않는다: 안내문 동의가 먼저다(확장 115호). 정원은 그 문 앞의
    # 숫자일 뿐 동의를 건너뛰게 하지 않는다.
    opened = taken_seats(db) < total_seats(db)
    if not opened and pending_count(db) >= PENDING_CAP:
        raise QueueFullError(
            "The waiting list is full right now. Ask the operator, or try again later.")
    state = STATE_ALLOWED if opened else STATE_PENDING
    with db.transaction():
        if user is None:
            user = db.add("user", {"name": (name or email.split("@")[0])[:60],
                                   "email": email, "password": None})
        now = db.now_ms()
        db.add("user_access", {"user_id": user["id"], "state": state,
                               "scope": SCOPE_WORKTRAIL, "consent_version": None,
                               "consent_at": None, "updated_at": now})
    return {"state": state, "scope": SCOPE_WORKTRAIL, "new": True, "user_id": user["id"]}


def holders(db: Any) -> list[dict[str, Any]]:
    """자리를 쓰고 있는 사람. taken_seats 가 세는 바로 그 행들이다.

    확장 130호 — 창의 승인 화면이 pending 만 그려서, 이미 들어온 사람은 화면 어디에도 없었다.
    119호로 앞 다섯이 자동으로 열린 뒤로는 pending 이 대개 비어 있어 그 화면이 늘 "기다리는
    신청이 없다" 만 보인다. 운영자가 자리를 비우거나 약속한 삭제를 하려면 서버에 들어가
    CLI 를 쳐야 했다(사용자 2026-09-17 이 실제로 그렇게 했다).

    consent_current 를 같이 낸다 — 안내문 판을 올린 뒤 누가 아직 옛 글에 머물러 있는지가
    이 목록에서 바로 읽혀야 한다(확장 129호)."""
    out = []
    for r in db.query("user_access", {"state": STATE_ALLOWED}):
        if r["consent_version"] == CONSENT_GRANDFATHERED:
            continue                  # 정원 밖이다(D15134) — 세지 않으니 여기도 내지 않는다
        u = db.get("user", r["user_id"])
        out.append({"user_id": r["user_id"], "email": u["email"] if u else "?",
                    "name": u["name"] if u else "", "since": r["updated_at"],
                    "consent": r["consent_version"],
                    "consent_current": notice_current(r["consent_version"])})
    return sorted(out, key=lambda x: x["since"])


def pending(db: Any) -> list[dict[str, Any]]:
    """운영자가 볼 줄. 오래 기다린 사람이 앞이다."""
    out = []
    for r in db.query("user_access", {"state": STATE_PENDING}):
        u = db.get("user", r["user_id"])
        out.append({"user_id": r["user_id"], "email": u["email"] if u else "?",
                    "name": u["name"] if u else "", "asked_at": r["updated_at"]})
    return sorted(out, key=lambda x: x["asked_at"])
