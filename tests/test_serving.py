"""서빙 하드닝 — 오라클 밖의 보장. HTTP 표면 26개·원장 시퀀스는 건드리지 않는다.

(1) 드레이너 여러 스레드가 같은 큐를 집어도 잡 하나는 정확히 한 번 실행된다.
(2) 워커 예외는 run_one 밖으로 나온다(드레이너가 격리한다) — 큐의 다음 잡은 산다.
(3) /health 는 인증 없이 열려 있고 그룹 프리픽스 밖이다.
(4) CORS 허용목록이 실제 응답 헤더에 반영된다.
"""
from __future__ import annotations
import threading
import pytest

from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import USER, CASE


def test_여러_스레드가_큐를_집어도_잡은_한_번만_돈다(app):
    # 확장 6호 뒤로 한 케이스에 pending 턴을 겹쳐 놓을 수 없으므로 케이스 20개에 하나씩
    cids = [CASE] + [app.create_case(USER, f"c{i}")["case_id"] for i in range(19)]
    for i, cid in enumerate(cids):
        app.submit_turn(USER, cid, PLACEHOLDER_INPUT, action_key=f"k{i}")
    assert app.pending_jobs() == 20
    ran = []

    def drain():
        while True:
            try:
                if not app.run_one():
                    return
                ran.append(1)
            except Exception:  # noqa: BLE001
                return

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(ran) == 20 and app.pending_jobs() == 0
    statuses = [t["status"] for cid in cids for t in app.get_case(USER, cid)["turns"]]
    assert statuses == ["finalized"] * 20
    # 답변 호출은 턴당 정확히 1회 — 중복 실행이 없었다는 증거(불변식 7)
    calls = [e for cid in cids for e in app.list_ledger(USER, cid)
             if e["event_type"] == "model_call" and e["payload"].get("role") == "troubleshoot"]
    assert len(calls) == 20


def test_워커_예외는_다음_잡을_막지_않는다(app):
    c2 = app.create_case(USER, "c2")["case_id"]
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a")
    app.submit_turn(USER, c2, PLACEHOLDER_INPUT, action_key="b")
    first = app._queue[0]
    app._workers["turn_worker"]  # 존재 확인
    boom = {"n": 0}
    real = app._workers["turn_worker"]

    def flaky(**kw):
        if kw["turn_id"] == first[1]["turn_id"]:
            boom["n"] += 1
            raise RuntimeError("db exploded")
        return real(**kw)

    app._workers["turn_worker"] = flaky
    with pytest.raises(RuntimeError):
        app.run_one()
    assert app.run_one() is True and app.pending_jobs() == 0
    statuses = [t["status"] for cid in (CASE, c2) for t in app.get_case(USER, cid)["turns"]]
    assert sorted(statuses) == ["finalized", "pending"]  # 죽은 잡의 턴은 pending 으로 남는다


def test_health_와_cors_허용목록():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.app import Casebook
    from casebook.core.db import SqliteDB
    from casebook.core.tickets import Tickets
    from tests.conftest import FakeLLM, FakeSearch

    db = SqliteDB(":memory:")
    cb = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    client = TestClient(http_api.create_app(cb, Tickets(db), cors_origins=["https://ok.example"]))

    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True, "pending_jobs": 0}

    r = client.options("/app/case", headers={
        "Origin": "https://ok.example", "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "https://ok.example"
    r = client.options("/app/case", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers
