"""HTTP 표면 26개 — 기존 프론트가 config 만 바꿔 붙는지의 오라클.

응답 **모양**(키 집합)이 `.xs` 의 response 와 동일한지 본다. 값 비교는 도메인
테스트의 몫이다. fastapi 미설치 환경에서는 건너뛴다(코어 테스트는 의존성이 없다).
"""
from __future__ import annotations
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from casebook.adapters import http_api  # noqa: E402
from casebook.core.app import Casebook  # noqa: E402
from casebook.core.db import SqliteDB  # noqa: E402
from casebook.core.tickets import Tickets  # noqa: E402
from tests.conftest import FakeLLM, FakeSearch  # noqa: E402


@pytest.fixture
def stack():
    db = SqliteDB(":memory:")
    cb = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    client = TestClient(http_api.create_app(cb, Tickets(db)), raise_server_exceptions=False)
    return client, cb


def _auth(client, email="a@example.test", name="a") -> dict:
    r = client.post("/auth/auth/signup", json={"name": name, "email": email, "password": "pw"})
    assert r.status_code == 200 and set(r.json()) == {"authToken"}
    r = client.post("/auth/auth/login", json={"email": email, "password": "pw"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["authToken"]}


def test_routes_명세와_앱이_일치한다(stack):
    client, _ = stack
    actual: dict[str, set[str]] = {}
    for route in client.app.routes:
        if getattr(route, "methods", None) and route.path.startswith(("/app", "/auth", "/tickets")):
            actual.setdefault(route.path, set()).update(m for m in route.methods if m != "HEAD")
    assert actual == {path: set(methods) for path, methods in http_api.ROUTES.items()}


def test_case_전체_흐름의_응답_모양(stack):
    client, cb = stack
    h = _auth(client)

    me = client.get("/auth/auth/me", headers=h).json()
    assert set(me) == {"id", "created_at", "name", "email"}

    case_id = client.post("/app/case", json={"title": "사건"}, headers=h).json()["case_id"]
    listed = client.get("/app/case", headers=h).json()
    # 확장 12호 — 목록 행에 vitals 4개(turn_count · updated_at · record_count · last_turn_status)
    assert [set(c) for c in listed] == [{"id", "title", "status", "created_at", "brief_focus",
                                         "turn_count", "updated_at", "record_count", "last_turn_status"}]

    r = client.post(f"/app/case/{case_id}/turn",
                    json={"content": "  원문 그대로\t", "action_key": "t1"}, headers=h).json()
    assert r == {"turn_id": r["turn_id"], "status": "pending"}
    cb.run_workers()

    turn = client.get(f"/app/case/{case_id}/turn/{r['turn_id']}", headers=h).json()
    assert set(turn) == {"status", "answer_status", "error", "assistant_message", "brief", "brief_stale"}
    assert turn["status"] == "finalized" and turn["assistant_message"] == "fake answer"
    assert turn["brief_stale"] is False

    detail = client.get(f"/app/case/{case_id}", headers=h).json()
    assert set(detail) == {"case", "conversation", "evidence", "turns"}
    assert set(detail["case"]) == {"id", "title", "status", "created_at",
                                   "brief_cache", "brief_cache_turn_id"}

    q = client.post(f"/app/case/{case_id}/draft-query",
                    json={"candidate": "가설 한 줄"}, headers=h).json()
    assert q == {"query": "fake query"}

    w = client.post(f"/app/case/{case_id}/web-lookup",
                    json={"query": "q", "engine": "google", "action_key": "w1"}, headers=h).json()
    assert set(w) == {"turn_id", "status"}
    cb.run_workers()

    rec = client.post(f"/app/case/{case_id}/record", headers=h).json()
    assert set(rec) == {"record_id", "version", "status"}
    cb.run_workers()
    records = client.get(f"/app/case/{case_id}/record", headers=h).json()
    assert [set(x) for x in records] == [{"id", "version", "status", "content", "turn_id",
                                         "error", "created_at", "finished_at"}]
    assert records[0]["status"] == "created"

    ledger = client.get(f"/app/case/{case_id}/ledger", headers=h).json()
    assert [e["event_type"] for e in ledger][:3] == \
        ["user_message", "canonical_evidence_created", "turn_status_changed"]

    st = client.post(f"/app/case/{case_id}/status", json={"status": "resolved"}, headers=h).json()
    assert st == {"case_id": case_id, "status": "resolved"}


def test_에러_봉투와_상태코드(stack):
    client, _ = stack
    assert client.get("/app/case").status_code == 401
    h = _auth(client)
    assert client.get("/app/case/999", headers=h).status_code == 404
    assert client.get("/app/case/999", headers=h).json()["message"] == "case not found"
    r = client.post("/app/case", json={"title": "  "}, headers=h)
    assert (r.status_code, r.json()["message"]) == (400, "Title must not be empty.")
    # 남의 케이스는 404 — 존재 자체를 흘리지 않는다
    case_id = client.post("/app/case", json={"title": "사건"}, headers=h).json()["case_id"]
    h2 = _auth(client, email="b@example.test", name="b")
    assert client.get(f"/app/case/{case_id}", headers=h2).status_code == 404


def test_ticket_흐름의_응답_모양(stack):
    client, _ = stack
    h = _auth(client)
    members = client.get("/tickets/member", headers=h).json()
    assert [set(m) for m in members["members"]] == [{"id", "name"}]  # 이메일·해시 미노출

    t = client.post("/tickets/ticket", json={
        "title": "티켓", "description": "d", "priority": "high", "action_key": "tk1",
    }, headers=h).json()
    assert t["status"] == "open" and t["version"] == 1

    client.post(f"/tickets/ticket/{t['id']}/case",
                json={"case_id": 11, "action_key": "tk1-link"}, headers=h)
    client.post(f"/tickets/ticket/{t['id']}/snapshot", json={
        "source_case_id": 11, "source_turn_count_before": 1, "source_turn_count_after": 1,
        "brief": None, "evidence_items": [], "record_content": None, "action_key": "tk1-snap",
    }, headers=h)
    client.post(f"/tickets/ticket/{t['id']}/transition", json={
        "to": "needs_help", "expected_version": 2, "snapshot_version": 0, "action_key": "tk1-help",
    }, headers=h)

    queue = client.get("/tickets/ticket", params={"status": "needs_help"}, headers=h).json()
    assert [x["id"] for x in queue["tickets"]] == [t["id"]]

    detail = client.get(f"/tickets/ticket/{t['id']}", headers=h).json()
    assert set(detail) == {"ticket", "cases", "latest_snapshot", "snapshot_count",
                           "events", "comments"}
    assert detail["snapshot_count"] == 1
    assert [e["event_type"] for e in detail["events"]] == \
        ["created", "case_linked", "case_linked", "snapshot_published", "help_requested"]
