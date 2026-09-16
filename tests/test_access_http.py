"""HTTP 문에서의 이용 허용 — 화면이 아니라 서버가 막는다 (인계서 4절).

test_access.py 가 판정 함수를 본다면 여기는 **실제 요청**을 본다: 차단된 사람이 유효한 토큰으로
직접 불러도 거절되는가, 테스터가 조사·tickets 경로를 직접 쳐도 막히는가, 그리고 허용 검사를 켜지
않은 로컬 창에서는 종전대로 도는가.
"""
from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from casebook.adapters import http_api  # noqa: E402
from casebook.core import access  # noqa: E402
from casebook.core.app import Casebook  # noqa: E402
from casebook.core.db import SqliteDB  # noqa: E402
from casebook.core.tickets import Tickets  # noqa: E402
from tests.conftest import FakeLLM, FakeSearch  # noqa: E402


def _stack(enforce: bool):
    db = SqliteDB(":memory:")
    cb = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    client = TestClient(http_api.create_app(cb, Tickets(db), enforce_access=enforce),
                        raise_server_exceptions=False)
    return client, cb, db


def _signup(client, email, name="u") -> dict:
    client.post("/auth/auth/signup", json={"name": name, "email": email, "password": "pw"})
    r = client.post("/auth/auth/login", json={"email": email, "password": "pw"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["authToken"]}


def test_검사를_끄면_종전_그대로다():
    """로컬 창(ui_server)이 같은 앱을 127.0.0.1 에 띄운다 — 거기엔 허용이라는 개념이 없다."""
    client, _, _ = _stack(enforce=False)
    h = _signup(client, "solo@x.test")
    assert client.get("/app/status", headers=h).status_code == 200
    assert client.get("/app/case", headers=h).status_code == 200


def test_차단된_계정은_유효한_토큰으로도_기록을_못_읽는다():
    client, _, db = _stack(enforce=True)
    h = _signup(client, "newbie@x.test")            # 가입은 되지만 허용은 따로다
    r = client.get("/app/status", headers=h)
    assert r.status_code == 403 and "not allowed on this server" in r.json()["message"]
    assert client.get("/app/case", headers=h).status_code == 403
    assert client.post("/app/case", headers=h, json={"title": "몰래"}).status_code == 403


def test_허용해도_동의_전에는_막힌다():
    client, _, db = _stack(enforce=True)
    h = _signup(client, "newbie@x.test")
    uid = db.get_by("user", "email", "newbie@x.test")["id"]
    access.set_state(db, uid, access.STATE_ALLOWED)
    r = client.get("/app/status", headers=h)
    assert r.status_code == 403 and "has not accepted the data notice" in r.json()["message"]
    access.record_consent(db, uid, "notice-v1")
    assert client.get("/app/status", headers=h).status_code == 200


def test_테스터는_조사와_tickets_경로를_직접_쳐도_막힌다():
    client, _, db = _stack(enforce=True)
    h = _signup(client, "tester@x.test")
    uid = db.get_by("user", "email", "tester@x.test")["id"]
    access.set_state(db, uid, access.STATE_ALLOWED, access.SCOPE_WORKTRAIL)
    access.record_consent(db, uid, "notice-v1")

    assert client.get("/app/status", headers=h).status_code == 200        # 기록은 연다
    assert client.get("/app/review", headers=h).status_code == 200

    for path in ("/tickets/member", "/tickets/ticket"):                    # tickets 는 닫는다
        r = client.get(path, headers=h)
        assert r.status_code == 403 and "not open to this account" in r.json()["message"]
    r = client.post("/app/intake", headers=h, json={})                      # 조사도 닫는다
    assert r.status_code == 403
    r = client.post("/app/case/1/turn", headers=h, json={"content": "x"})
    assert r.status_code == 403


def test_운영자_범위는_조사와_tickets_가_그대로_열린다():
    """기존 운영 계정 기능 유지 — 회귀 확인."""
    client, _, db = _stack(enforce=True)
    h = _signup(client, "op@x.test")
    uid = db.get_by("user", "email", "op@x.test")["id"]
    access.set_state(db, uid, access.STATE_ALLOWED, access.SCOPE_OPERATOR)
    access.record_consent(db, uid, "notice-v1")
    assert client.get("/tickets/member", headers=h).status_code == 200
    assert client.get("/app/status", headers=h).status_code == 200


def test_로그인과_자기_확인은_차단돼도_지난다():
    """차단된 사람도 자기가 차단됐다는 것은 알 수 있어야 한다. 그 셋은 작업 기록이 아니다."""
    client, _, _ = _stack(enforce=True)
    h = _signup(client, "blocked@x.test")                # signup·login 이 이미 통과한 셈
    assert client.get("/auth/auth/me", headers=h).status_code == 200
    assert client.get("/health").status_code == 200


def test_토큰이_없거나_틀리면_401_이지_403_이_아니다():
    """인증 실패와 허용 실패를 구별한다 — 왜 막혔는지가 달라진다."""
    client, _, _ = _stack(enforce=True)
    assert client.get("/app/status").status_code == 401
    assert client.get("/app/status", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_차단하면_그_자리에서_막힌다():
    """해제 전에 받은 토큰을 그대로 다시 써도 막힌다 — 만료를 기다리지 않는다."""
    client, _, db = _stack(enforce=True)
    h = _signup(client, "t@x.test")
    uid = db.get_by("user", "email", "t@x.test")["id"]
    access.set_state(db, uid, access.STATE_ALLOWED)
    access.record_consent(db, uid, "notice-v1")
    assert client.get("/app/status", headers=h).status_code == 200

    access.set_state(db, uid, access.STATE_BLOCKED)
    assert client.get("/app/status", headers=h).status_code == 403        # 같은 토큰, 이제 막힌다

    access.set_state(db, uid, access.STATE_ALLOWED)
    assert client.get("/app/status", headers=h).status_code == 200        # 다시 켜면 돌아온다


def test_거절_응답에도_CORS_헤더가_붙는다():
    """관문이 CORS 보다 안쪽에 있어야 한다 — 아니면 브라우저가 403 사유를 읽지 못하고,
    access.py 가 구별해 만든 세 문장(차단·미동의·범위 밖)이 화면에서 통째로 사라진다."""
    client, _, _ = _stack(enforce=True)
    h = _signup(client, "newbie@x.test")
    origin = {"Origin": "http://127.0.0.1"}
    r = client.get("/app/status", headers={**h, **origin})
    assert r.status_code == 403
    assert r.headers.get("access-control-allow-origin") is not None
    r = client.get("/app/status", headers=origin)              # 무토큰 401 도 마찬가지
    assert r.status_code == 401 and r.headers.get("access-control-allow-origin") is not None


def test_틀_경로는_테스터에게_열리지_않는다():
    """모르는 경로를 열어 두면 /openapi.json 같은 것이 그냥 샌다."""
    client, _, db = _stack(enforce=True)
    h = _signup(client, "t@x.test")
    uid = db.get_by("user", "email", "t@x.test")["id"]
    access.set_state(db, uid, access.STATE_ALLOWED, access.SCOPE_WORKTRAIL)
    access.record_consent(db, uid, "notice-v1")
    assert client.get("/app/status", headers=h).status_code == 200
    assert client.get("/openapi.json", headers=h).status_code == 403


def test_테스터는_조사_케이스를_새로_만들지_못한다():
    """POST /app/case 는 조사 케이스 생성이다 — 같은 경로라도 GET 과 갈린다."""
    client, _, db = _stack(enforce=True)
    h = _signup(client, "t@x.test")
    uid = db.get_by("user", "email", "t@x.test")["id"]
    access.set_state(db, uid, access.STATE_ALLOWED, access.SCOPE_WORKTRAIL)
    access.record_consent(db, uid, "notice-v1")
    assert client.get("/app/case", headers=h).status_code == 200
    assert client.post("/app/case", headers=h, json={"title": "몰래 여는 조사"}).status_code == 403


def test_한_사람을_끊어도_다른_사람은_그대로다():
    client, _, db = _stack(enforce=True)
    ha = _signup(client, "a@x.test", "a")
    hb = _signup(client, "b@x.test", "b")
    for email in ("a@x.test", "b@x.test"):
        uid = db.get_by("user", "email", email)["id"]
        access.set_state(db, uid, access.STATE_ALLOWED)
        access.record_consent(db, uid, "notice-v1")

    access.set_state(db, db.get_by("user", "email", "a@x.test")["id"], access.STATE_BLOCKED)
    assert client.get("/app/status", headers=ha).status_code == 403
    assert client.get("/app/status", headers=hb).status_code == 200       # 전원이 끊기지 않는다


# ── 확장 108호 — 가입 신청을 창에서 고른다 (D15414) ─────────────────────────
def test_가입_신청_길은_운영자만_연다():
    """화면에서 메뉴를 숨기는 것으로는 직접 요청을 못 막는다. 경로가 기록 표면 목록에 없으므로
    operator_only() 가 기본값(닫힘)으로 판정해야 한다 — 테스터가 남의 신청을 보거나 스스로를
    승인하는 일이 없어야 한다."""
    assert http_api.operator_only("/app/join") is True
    assert http_api.operator_only("/app/join", "POST") is True

    client, _, db = _stack(enforce=True)
    h = _signup(client, "tester@x.test")
    uid = db.get_by("user", "email", "tester@x.test")["id"]
    access.set_state(db, uid, access.STATE_ALLOWED, access.SCOPE_WORKTRAIL)
    assert client.get("/app/join", headers=h).status_code == 403
    assert client.post("/app/join", headers=h, json={"user_id": uid, "action": "allow"}).status_code == 403
    assert access.state(db, uid)["state"] == access.STATE_ALLOWED       # 스스로를 올리지 못했다


def test_운영자는_줄을_보고_승인하거나_거절한다(monkeypatch):
    # 확장 119호 — 자리가 남으면 신청이 그 자리에서 열려 줄이 서지 않는다. 줄을 보는 화면의
    # 시험이므로 정원을 0 으로 두어 줄이 서는 상황을 만든다.
    monkeypatch.setattr(access, "TESTER_SEATS", 0)
    client, _, db = _stack(enforce=False)          # 검사를 끈 로컬 창이 운영자의 자리다
    h = _signup(client, "boss@x.test")
    access.request_access(db, "a@x.test", "가")
    access.request_access(db, "b@x.test", "나")

    r = client.get("/app/join", headers=h)
    assert r.status_code == 200
    q = r.json()
    assert [p["email"] for p in q["pending"]] == ["a@x.test", "b@x.test"]   # 오래 기다린 사람이 앞
    assert q["cap"] == access.PENDING_CAP

    a = db.get_by("user", "email", "a@x.test")["id"]
    r = client.post("/app/join", headers=h, json={"user_id": a, "action": "allow"})
    assert r.status_code == 200 and r.json()["state"] == access.STATE_ALLOWED
    assert [p["email"] for p in r.json()["pending"]] == ["b@x.test"]        # 줄에서 빠진다

    b = db.get_by("user", "email", "b@x.test")["id"]
    r = client.post("/app/join", headers=h, json={"user_id": b, "action": "block"})
    assert r.status_code == 200 and r.json()["state"] == access.STATE_BLOCKED
    assert r.json()["pending"] == []
    # 거절은 되돌릴 수 있다 — 잘못 눌러도 끝이 아니다
    assert client.post("/app/join", headers=h, json={"user_id": b, "action": "allow"}).json()["state"] == access.STATE_ALLOWED


def test_모르는_동작이나_없는_계정은_거절한다():
    client, _, db = _stack(enforce=False)
    h = _signup(client, "boss2@x.test")
    assert client.post("/app/join", headers=h, json={"user_id": 1, "action": "delete"}).status_code == 400
    assert client.post("/app/join", headers=h, json={"user_id": 9999, "action": "allow"}).status_code == 400


def test_혼자_쓰는_로컬_창에는_자리를_내지_않는다():
    """로컬 모드(허용 검사 없음)는 사람이 하나다 — 승인할 사람이라는 것이 없다. 빈 메뉴를
    내지 않도록 화면이 이 값으로 자리를 정한다. 검사를 켠 서버에서는 줄이 비어도 낸다."""
    client, _, _ = _stack(enforce=False)
    h = _signup(client, "solo@x.test")
    assert client.get("/app/join", headers=h).json()["enforced"] is False

    client2, _, db2 = _stack(enforce=True)
    h2 = _signup(client2, "op@x.test")
    uid = db2.get_by("user", "email", "op@x.test")["id"]
    access.set_state(db2, uid, access.STATE_ALLOWED, access.SCOPE_OPERATOR)
    access.record_consent(db2, uid, "notice-v1")
    r = client2.get("/app/join", headers=h2)
    assert r.status_code == 200 and r.json()["enforced"] is True and r.json()["pending"] == []


def test_화면이_가입_신청_자리를_스스로_숨긴다():
    """상단바 자리는 기본이 숨김이고, 길이 열린 뒤에만 뜬다 — 테스터의 창에서는 403 이라 그대로
    숨어 있다. 데모(스냅샷)에서는 주소로 쳐도 열리지 않는다."""
    import pathlib
    page = (pathlib.Path(__file__).resolve().parent.parent / "web/worktrail/index.html").read_text(encoding="utf-8")
    assert 'id="navJoin" hidden' in page, "자리가 기본으로 떠 있다"
    assert 'a.hidden = !(enforced || n);' in page, "혼자 쓰는 창에서 빈 메뉴가 뜬다"
    assert 'if (h === "#/join" && !SNAP)' in page, "데모에서 주소로 열린다"
    assert "joinProbe();" in page, "길을 확인하지 않는다 — 자리가 영영 안 뜬다"
    for key in ("가입 신청", "승인", "거절", "기다리는 신청이 없다."):
        assert f'"{key}":' in page, f"사전에 {key} 가 없다"


def test_화면은_APP_BASE_에_app_을_또_붙이지_않는다():
    """창의 APP_BASE 는 이미 "<prefix>/app" 이다(ui_server). 화면이 api("/app/join") 을 부르면
    /api/app/app/join 이 되어 404 다 — 실측 2026-09-15: 그래서 가입 신청 자리가 끝내 안 떴다.
    내 시험 하네스가 APP_BASE 를 "/app" 없이 만들어 두어 이것을 못 잡았다."""
    import pathlib, re
    root = pathlib.Path(__file__).resolve().parent.parent
    page = (root / "web/worktrail/index.html").read_text(encoding="utf-8")
    ui = (root / "casebook/adapters/ui_server.py").read_text(encoding="utf-8")
    assert 'APP_BASE:"{p}/app"' in ui, "이 시험이 서 있는 전제가 바뀌었다"
    bad = sorted(set(re.findall(r'(?:api|post)\("(/app/[^"]*)"', page)))
    assert not bad, f"APP_BASE 에 /app 이 또 붙는다: {bad}"
