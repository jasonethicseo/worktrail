"""확장 12호 — 케이스 목록 vitals (HARVEST_V2 B1~B4).

보장할 것: (1) 새 케이스는 turn 0 · record 0 · status None · updated_at=created_at
(2) 턴이 생기면 pending → 워커 뒤 finalized, turn_count 는 sequence 최대치
(3) 답변 실패는 failed (4) recovery 뒤 abandoned 는 마지막 턴 판정에서 빠진다
(5) 레코드는 created 판만 센다 (6) 목록은 updated_at 내림차순
(7) 읽기 전용 — 원장 시퀀스에 아무것도 남기지 않는다.
"""
from __future__ import annotations

import time

from tests.conftest import PLACEHOLDER_INPUT, _build_app, FakeLLM, FakeSearch
from tests.drive import USER, CASE

VITALS = ("turn_count", "updated_at", "record_count", "last_turn_status")


def _row(app, case_id):
    return next(c for c in app.list_cases(USER) if c["id"] == case_id)


def test_새_케이스는_비어_있다(app):
    row = _row(app, CASE)
    assert {k: row[k] for k in VITALS} == {
        "turn_count": 0, "updated_at": row["created_at"], "record_count": 0, "last_turn_status": None,
    }


def test_턴_상태와_개수(app):
    r = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a")
    row = _row(app, CASE)
    assert row["last_turn_status"] == "pending" and row["turn_count"] == 1
    assert row["updated_at"] >= row["created_at"]
    app.run_workers()
    row = _row(app, CASE)
    assert row["last_turn_status"] == "finalized" and row["turn_count"] == 1
    assert row["updated_at"] == app.db.get("turn", r["turn_id"])["finished_at"]


def test_답변_실패와_recovery():
    llm = FakeLLM(); app = _build_app(llm, FakeSearch())
    llm.fail_next.add("answer")
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    assert _row(app, CASE)["last_turn_status"] == "failed"
    # pending 원본을 recovery 가 이어받으면 abandoned 는 판정에서 빠지고 번호는 남는다
    src = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="b")
    app.recover(USER, CASE, src["turn_id"], action_key="r")
    row = _row(app, CASE)
    assert row["last_turn_status"] == "pending" and row["turn_count"] == 3
    app.run_workers()
    assert _row(app, CASE)["last_turn_status"] == "finalized"


def test_web_lookup_실패는_failed(app):
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    app.search.fail_next = True
    app.web_lookup(USER, CASE, "q", "google", action_key="w"); app.run_workers()
    assert _row(app, CASE)["last_turn_status"] == "failed"


def test_레코드는_created_판만_센다():
    llm = FakeLLM(); app = _build_app(llm, FakeSearch())
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    llm.fail_next.add("record")
    app.generate_record(USER, CASE); app.run_workers()
    assert _row(app, CASE)["record_count"] == 0
    app.generate_record(USER, CASE); app.run_workers()
    row = _row(app, CASE)
    assert row["record_count"] == 1
    assert row["updated_at"] == app.db.query("record", where={"case_id": CASE}, order="id", desc=True)[0]["finished_at"]


def test_목록은_마지막_활동_순(app):
    older = app.create_case(USER, "older")["case_id"]
    newer = app.create_case(USER, "newer")["case_id"]
    assert [c["id"] for c in app.list_cases(USER)] == [newer, older, CASE]
    time.sleep(0.002)   # 같은 ms 에 끝나면 id 로 동률을 깬다 — 여기선 시각 차이를 보려는 것
    app.submit_turn(USER, older, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    assert [c["id"] for c in app.list_cases(USER)] == [older, newer, CASE]


def test_읽기_전용(app):
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    before = [e["event_type"] for e in app.list_ledger(USER, CASE)]
    app.list_cases(USER); app.list_cases(USER)
    assert [e["event_type"] for e in app.list_ledger(USER, CASE)] == before
