"""서버 이용 허용 — 로그인과 이용 허용은 별개다 (인계서 4절, D15081).

지키는 계약 넷.
(1) 표가 처음 생길 때 있던 계정은 허용으로 남고(D15081), 그 뒤에 생긴 계정은 기본 차단이다.
(2) 차단·미동의 계정은 토큰이 진짜여도 기록을 읽거나 쓰지 못한다 — auth_secret 을 건드리지 않고 개별로 막힌다.
(3) 테스터(worktrail 범위)는 조사·tickets 표면에 닿지 못한다. 화면에서 숨기는 것이 아니라 서버가 막는다.
(4) 해제는 즉시 먹는다 — 해제 전에 발급된 토큰을 그대로 다시 써도 막힌다.
"""
from __future__ import annotations

import os

import pytest

from casebook.core import access, auth
from casebook.core.db import SqliteDB
from casebook.core.errors import AccessDeniedError
from casebook.adapters.http_api import operator_only


def _db_with_users(*emails: str) -> tuple[SqliteDB, list[dict]]:
    db = SqliteDB(":memory:")
    users = [db.add("user", {"name": e.split("@")[0], "email": e, "password": None}) for e in emails]
    return db, users


# ── 경계: 어떤 경로가 운영자 것인가 ─────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/tickets/member", "/tickets/ticket", "/tickets/ticket/7/claim",
    "/app/intake", "/app/case/3/turn", "/app/case/3/turn/9", "/app/case/3/recovery",
    "/app/case/3/draft-query", "/app/case/3/web-lookup", "/app/case/3/record",
])
def test_조사와_tickets_는_운영자_것이다(path):
    assert operator_only(path) is True


@pytest.mark.parametrize("path", [
    "/app/case", "/app/case/3", "/app/case/3/ledger", "/app/case/3/status",
    "/app/thread", "/app/thread/3", "/app/status", "/app/review", "/app/prior",
    "/app/prior/2", "/app/topic",
])
def test_기록_표면은_테스터에게_열린다(path):
    assert operator_only(path) is False


@pytest.mark.parametrize("path", [
    "/openapi.json", "/docs", "/redoc", "/", "/app", "/뭔가새로붙은것", "/app/새표면",
])
def test_모르는_경로는_닫는다(path):
    """fail-open 이면 나중에 붙는 표면과 틀 경로가 그냥 샌다. 모르면 운영자 것으로 본다.
    (/health · /auth/* 는 관문이 아예 건너뛴다 — create_app 의 _OPEN_PATHS)"""
    assert operator_only(path) is True


def test_같은_경로도_읽기와_쓰기가_갈린다():
    """GET /app/case 는 내 스레드 목록이지만 POST 는 조사 케이스를 새로 만든다."""
    assert operator_only("/app/case", "GET") is False
    assert operator_only("/app/case", "POST") is True


# ── D15081: 경계 이전은 허용, 이후는 차단 ────────────────────────────────────

def test_표가_생길_때_있던_계정은_허용으로_남는다():
    db, (a, b) = _db_with_users("a@x.test", "b@x.test")
    n = access.grandfather(db)
    assert n == 2
    for u in (a, b):
        assert access.state(db, u["id"])["state"] == access.STATE_ALLOWED
        access.check(db, u["id"])                       # 동의 표시까지 붙어 통과한다


def test_경계_뒤에_생긴_계정은_기본_차단이다():
    db, _ = _db_with_users("old@x.test")
    access.grandfather(db)
    newbie = db.add("user", {"name": "new", "email": "new@x.test", "password": None})
    s = access.state(db, newbie["id"])
    assert s["state"] == access.STATE_BLOCKED and s["known"] is False
    with pytest.raises(AccessDeniedError) as exc:
        access.check(db, newbie["id"])
    assert "not allowed on this server" in str(exc.value)      # 거절문은 영어다(확장 85호)


def test_grandfather_는_한_번만_돈다():
    """두 번째 호출이 그 사이에 생긴 계정까지 허용으로 만들면 경계가 무너진다."""
    db, _ = _db_with_users("old@x.test")
    assert access.grandfather(db) == 1
    db.add("user", {"name": "new", "email": "new@x.test", "password": None})
    assert access.grandfather(db) == 0
    assert access.state(db, 2)["state"] == access.STATE_BLOCKED


# ── 동의 ────────────────────────────────────────────────────────────────

def test_허용돼도_동의_전에는_기록이_오가지_않는다():
    db, _ = _db_with_users("old@x.test")
    access.grandfather(db)
    u = db.add("user", {"name": "t", "email": "t@x.test", "password": None})
    access.set_state(db, u["id"], access.STATE_ALLOWED)
    with pytest.raises(AccessDeniedError) as exc:
        access.check(db, u["id"])
    assert "has not accepted the data notice" in str(exc.value)
    access.record_consent(db, u["id"], "notice-v1")
    assert access.check(db, u["id"])["consent_version"] == "notice-v1"


def test_동의_판이_바뀌면_다시_받는다():
    db, (a,) = _db_with_users("a@x.test")
    access.record_consent(db, a["id"], "notice-v1")
    access.record_consent(db, a["id"], "notice-v2")
    assert access.state(db, a["id"])["consent_version"] == "notice-v2"


# ── 범위: 테스터는 조사·tickets 에 닿지 못한다 ───────────────────────────────

def test_테스터_범위는_조사와_tickets_를_열지_못한다():
    db, _ = _db_with_users("op@x.test")
    access.grandfather(db)                                   # 운영자는 operator 범위로 남는다
    t = db.add("user", {"name": "t", "email": "t@x.test", "password": None})
    access.set_state(db, t["id"], access.STATE_ALLOWED, access.SCOPE_WORKTRAIL)
    access.record_consent(db, t["id"], "notice-v1")
    access.check(db, t["id"])                                 # 기록은 된다
    with pytest.raises(AccessDeniedError) as exc:
        access.check(db, t["id"], need=access.SCOPE_OPERATOR)
    assert "not open to this account" in str(exc.value)
    access.check(db, 1, need=access.SCOPE_OPERATOR)            # 기존 운영자는 그대로 (회귀)


# ── 개별 회수: auth_secret 을 건드리지 않는다 ────────────────────────────────

def test_한_사람만_끊어도_다른_사람과_비밀은_그대로다():
    """이 스레드를 연 문제다 — 전에는 회수 수단이 auth_secret 교체뿐이라 전원이 끊겼다."""
    db, (a, b) = _db_with_users("a@x.test", "b@x.test")
    access.grandfather(db)
    secret = "s" * 64
    tok_a = auth.create_token(secret, a["id"])
    tok_b = auth.create_token(secret, b["id"])

    access.set_state(db, a["id"], access.STATE_BLOCKED)

    with pytest.raises(AccessDeniedError):
        access.check(db, a["id"])
    access.check(db, b["id"])                                  # 남은 사람은 그대로 쓴다
    assert auth.verify_token(secret, tok_a) == a["id"]         # 서명은 여전히 유효하다 —
    assert auth.verify_token(secret, tok_b) == b["id"]         # 막는 것은 허용 상태이지 비밀 교체가 아니다


def test_해제_전에_받은_토큰을_다시_써도_막힌다():
    """짧은 만료로 때우지 않는다 — 서버가 계정 상태를 보므로 기존 토큰도 그 자리에서 막힌다."""
    db, (a,) = _db_with_users("a@x.test")
    access.grandfather(db)
    secret = "s" * 64
    tok = auth.create_token(secret, a["id"], 10 * 365 * 86400)   # 10년짜리 구형 토큰
    access.check(db, a["id"])
    access.set_state(db, a["id"], access.STATE_BLOCKED)
    assert auth.verify_token(secret, tok) == a["id"]             # 토큰 자체는 살아 있는데
    with pytest.raises(AccessDeniedError):                       # 요청은 막힌다
        access.check(db, a["id"])


def test_다시_켜면_같은_토큰으로_돌아온다():
    db, (a,) = _db_with_users("a@x.test")
    access.grandfather(db)
    access.set_state(db, a["id"], access.STATE_BLOCKED)
    access.set_state(db, a["id"], access.STATE_ALLOWED)
    assert access.check(db, a["id"])["state"] == access.STATE_ALLOWED


def test_경계_표시가_사라져도_다시_풀리지_않는다():
    """적용 전 백업을 되돌리는 절차가 곧 승인 대기자 전원을 운영자로 만들면 안 된다.
    _meta 한 줄이 없어도 user_access 에 줄이 있으면 경계는 이미 그어진 것이다."""
    db, _ = _db_with_users("old@x.test")
    access.grandfather(db)
    waiting = db.add("user", {"name": "w", "email": "w@x.test", "password": None})
    assert access.state(db, waiting["id"])["state"] == access.STATE_BLOCKED

    db.conn.execute("DELETE FROM _meta WHERE key = ?", (access.GRANDFATHER_KEY,))
    assert db.meta(access.GRANDFATHER_KEY) is None
    assert access.grandfather(db) == 0                        # 다시 돌지 않는다
    assert access.state(db, waiting["id"])["state"] == access.STATE_BLOCKED
    assert db.meta(access.GRANDFATHER_KEY) is not None         # 표시는 다시 세운다


def test_허용은_범위를_조용히_강등하지_않는다():
    """block → allow 왕복이 운영자를 테스터로 떨어뜨리면 회수 뒤 복구가 권한 강등이 된다."""
    db, (a,) = _db_with_users("op@x.test")
    access.grandfather(db)
    assert access.state(db, a["id"])["scope"] == access.SCOPE_OPERATOR
    access.set_state(db, a["id"], access.STATE_BLOCKED)
    access.set_state(db, a["id"], access.STATE_ALLOWED)        # scope 를 주지 않았다
    assert access.state(db, a["id"])["scope"] == access.SCOPE_OPERATOR
    access.set_state(db, a["id"], access.STATE_ALLOWED, access.SCOPE_WORKTRAIL)   # 시키면 바뀐다
    assert access.state(db, a["id"])["scope"] == access.SCOPE_WORKTRAIL


def test_모르는_상태나_범위는_거절한다():
    db, (a,) = _db_with_users("a@x.test")
    with pytest.raises(ValueError):
        access.set_state(db, a["id"], "maybe")
    with pytest.raises(ValueError):
        access.set_state(db, a["id"], access.STATE_ALLOWED, "admin")
    with pytest.raises(ValueError):
        access.record_consent(db, a["id"], "  ")


# ── 거절 기록은 접힌다 (인증 전 무제한 DB 쓰기 방지) ─────────────────────────

def test_거절_기록은_창_하나에_한_줄로_접힌다():
    """인증보다 먼저 도는 길이라, 요청마다 한 줄을 쓰면 토큰 없는 누구나 DB 를 불릴 수 있다.
    실측으로 초당 5,411건 · 400건에 WAL 4.1MB 가 늘었다."""
    from casebook.adapters.mcp_server import _refusal_logger
    from casebook.core import reads

    db, _ = _db_with_users("a@x.test")
    log = _refusal_logger(type("CB", (), {"db": db})())
    count = lambda: db.conn.execute("SELECT count(*) FROM read_log").fetchone()[0]

    for _ in range(500):
        log("mcp_unauthorized", 0, "1.2.3.4")
    assert count() == 1                                   # 500건 → 한 줄

    log("mcp_unauthorized", 0, "5.6.7.8")                 # 다른 출처는 따로 센다
    assert count() == 2
    log("mcp_forbidden", 1, "1.2.3.4")                    # 다른 종류도 따로
    assert count() == 3


def test_접기_표는_무한히_자라지_않는다():
    """출처를 바꿔 가며 두드리면 표가 메모리를 먹는다 — 상한을 두고 오래된 것을 버린다."""
    from casebook.adapters import mcp_server

    db, _ = _db_with_users("a@x.test")
    log = mcp_server._refusal_logger(type("CB", (), {"db": db})())
    n = mcp_server._REFUSE_MAX_KEYS + 200
    for i in range(n):
        log("mcp_unauthorized", 0, f"10.0.{i // 256}.{i % 256}")
    rows = db.conn.execute("SELECT count(*) FROM read_log").fetchone()[0]
    assert rows == n                                      # 출처가 다 다르니 줄은 다 남는다


# ── 삭제 (확장 116호, D15637 의 "삭제") ──────────────────────────────────────
# 안내문이 "말씀하시면 그 계정의 기록을 전부 지웁니다, 7일 안에 처리합니다" 라고 약속하는데
# 그것을 할 길이 없었다(CLI 는 stdio·http·mint·allow·block·access·consent 일곱뿐이었다).
# C15448 — 아래 넷은 access.forget 이 없던 코드에서 AttributeError 로 실패한다.

def _seeded(db, user_id: int, n: int = 2) -> list[int]:
    """이 사람 앞으로 스레드를 만들고 증거를 붙인다. case_id 로만 달린 표를 걸기 위해서다."""
    from casebook.core import threads
    ids = []
    for i in range(n):
        c = db.add("case", {"user_id": user_id, "title": f"t{i}", "status": "open", "schema_version": 1})
        db.add("evidence", {"case_id": c["id"], "content": "raw", "kind": "user_paste"})
        db.add("ledger", {"case_id": c["id"], "turn_id": None, "event_type": "x", "payload": {}})
        ids.append(c["id"])
    threads.ensure(db)
    return ids


def test_세어_보이는_것이_기본이다_아무것도_안_지운다():
    db, (a,) = _db_with_users("a@x.test")
    _seeded(db, a["id"])
    plan = access.forget_plan(db, a["id"])
    assert plan["case"] == 2 and plan["evidence"] == 2 and plan["user"] == 1
    # 세기만 했으니 그대로 있어야 한다
    assert len(db.query("case", where={"user_id": a["id"]})) == 2
    assert db.get("user", a["id"]) is not None


def test_지우면_그_계정의_것만_사라진다():
    db, (a, b) = _db_with_users("a@x.test", "b@x.test")
    _seeded(db, a["id"], 2)
    keep = _seeded(db, b["id"], 3)
    access.set_state(db, b["id"], access.STATE_ALLOWED)

    access.forget(db, a["id"])

    assert db.get("user", a["id"]) is None
    assert db.query("case", where={"user_id": a["id"]}) == []
    assert access.row(db, a["id"]) is None
    # 남의 것은 한 줄도 건드리지 않았다
    assert len(db.query("case", where={"user_id": b["id"]})) == 3
    assert db.get("user", b["id"]) is not None
    assert access.state(db, b["id"])["state"] == access.STATE_ALLOWED
    for cid in keep:
        assert len(db.query("evidence", where={"case_id": cid})) == 1


def test_저장소는_남긴다_남과_공유하는_이름이다():
    """repo.identity 는 저장소를 가리키는 값이라 여러 사람이 같은 행을 쓴다."""
    from casebook.core import threads
    db, (a,) = _db_with_users("a@x.test")
    _seeded(db, a["id"])
    threads.ensure(db)
    with db._lock:
        db.conn.execute("INSERT INTO repo(created_at, identity, hint) VALUES(?, ?, ?)",
                        (db.now_ms(), "github.com/x/y", "y"))
        db.conn.commit()

    access.forget(db, a["id"])

    with db._lock:
        assert db.conn.execute("SELECT COUNT(*) FROM repo").fetchone()[0] == 1


def test_지운_사실은_남고_주소는_남지_않는다():
    """언제 지웠는지는 답할 수 있어야 하고, 주소가 남으면 지운 것이 아니다."""
    import json
    db, (a,) = _db_with_users("gone@x.test")
    _seeded(db, a["id"])

    access.forget(db, a["id"])

    log = json.loads(db.meta(access.FORGOTTEN_KEY) or "[]")
    assert len(log) == 1 and log[0]["rows"] > 0 and log[0]["at"] > 0
    assert "gone@x.test" not in json.dumps(log), "지운 주소가 그대로 남았다"
    # 같은 주소를 다시 해시하면 그 줄을 찾을 수 있다 — 되찾을 수는 없다
    import hashlib
    assert log[0]["who"] == hashlib.sha256(b"gone@x.test").hexdigest()[:12]


def test_forget_는_CLI_에서_깃발까지_지나간다(tmp_path):
    """access.forget 만 시험하면 놓친다 — main() 의 인자 검사가 --yes 를 통째로 거절하고 있었다.
    도구가 아니라 사람이 치는 줄이 동작하는지는 그 줄을 쳐 봐야 안다."""
    import subprocess
    import sys
    db_path = tmp_path / "t.db"
    db = SqliteDB(str(db_path))
    for e in ("keep@x.test", "gone@x.test"):
        u = db.add("user", {"name": e.split("@")[0], "email": e, "password": None})
        db.add("case", {"user_id": u["id"], "title": e, "status": "open", "schema_version": 1})

    def cli(*args):
        return subprocess.run([sys.executable, "-m", "casebook.adapters.mcp_server", *args],
                              capture_output=True, text=True,
                              env={**os.environ, "CASEBOOK_DB": str(db_path)})

    r = cli("forget", "gone@x.test")                     # 깃발 없이 — 세기만 한다
    assert r.returncode == 0, r.stderr
    assert "--yes" in r.stdout and "지웠다" not in r.stdout

    r = cli("forget", "gone@x.test", "--yes")
    assert r.returncode == 0, r.stderr
    assert "지웠다" in r.stdout

    after = SqliteDB(str(db_path))
    assert after.get_by("user", "email", "gone@x.test") is None
    assert after.get_by("user", "email", "keep@x.test") is not None


# ── 확장 119호 — 앞 다섯은 신청하면 그 자리에서 열린다 ──────────────────────

def _join(db, email: str) -> dict:
    return access.request_access(db, email)


def test_앞_다섯은_신청하면_그_자리에서_열린다():
    """운영자가 승인을 누를 때까지 아무도 못 쓰던 것이 병목이었다. 밤에 신청하면 아침까지 기다린다."""
    db = SqliteDB(":memory:")
    for i in range(access.TESTER_SEATS):
        out = _join(db, f"t{i}@x.test")
        assert out["state"] == access.STATE_ALLOWED, f"{i}번째가 안 열렸다"
    assert access.taken_seats(db) == access.TESTER_SEATS
    # 여섯째부터는 줄을 선다
    sixth = _join(db, "late@x.test")
    assert sixth["state"] == access.STATE_PENDING
    assert access.seats(db) == {"taken": 5, "total": 5, "left": 0}


def test_열려도_동의_전에는_기록이_오가지_않는다():
    """자동 승인이 안내문을 건너뛰게 하면 안 된다 — 정원은 그 문 앞의 숫자일 뿐이다."""
    db = SqliteDB(":memory:")
    out = _join(db, "new@x.test")
    assert out["state"] == access.STATE_ALLOWED
    with pytest.raises(AccessDeniedError, match="data notice"):
        access.check(db, out["user_id"])
    access.record_consent(db, out["user_id"], access.NOTICE_VERSION)
    access.check(db, out["user_id"])                      # 동의 뒤에야 지난다


def test_기존_계정은_정원을_먹지_않는다():
    """D15134 — 기존 참여자는 정원 밖이다. 운영자 본인과 피실험자 1호가 여기 해당한다."""
    db, users = _db_with_users(*[f"old{i}@x.test" for i in range(8)])
    access.grandfather(db)                                 # 여덟 명이 전부 allowed·grandfathered 가 된다
    assert access.taken_seats(db) == 0, "기존 계정이 정원을 먹었다"
    assert _join(db, "tester@x.test")["state"] == access.STATE_ALLOWED


def test_자리가_나면_다음_사람이_들어온다():
    """운영자가 한 명을 지우거나 막으면 그 자리는 다시 열린다."""
    db = SqliteDB(":memory:")
    first = _join(db, "a@x.test")
    for i in range(access.TESTER_SEATS - 1):
        _join(db, f"f{i}@x.test")
    assert _join(db, "waiting@x.test")["state"] == access.STATE_PENDING

    access.forget(db, first["user_id"])                    # 한 자리 비었다
    assert access.seats(db)["left"] == 1
    assert _join(db, "next@x.test")["state"] == access.STATE_ALLOWED


def test_막은_사람은_자리를_돌려준다():
    db = SqliteDB(":memory:")
    ids = [_join(db, f"s{i}@x.test")["user_id"] for i in range(access.TESTER_SEATS)]
    assert access.seats(db)["left"] == 0
    access.set_state(db, ids[0], access.STATE_BLOCKED)
    assert access.seats(db)["left"] == 1
