"""확장 4호 — external 턴(MCP 문). 확장 39호 (2026-09-08) 로 이 길에서 모델 호출이 사라졌다.

기록 도구와 조사 도구를 가르는 첫 단계다. 예전에는 note 마다 기록자(LLM)를 돌려 brief 를
갱신했는데, 기록 화면이 brief 를 읽지 않는다는 것이 확인됐다(hookctx 폴백 한 줄뿐). 그래서
기록은 모델을 부르지 않는다 — brief 는 조사 도구(turn_worker)의 물건으로 남는다.

보장할 것: (1) note 가 있으면 [user_message, canonical_evidence_created, turn_status_changed,
answer_created, turn_status_changed] 이고 model_call 이 하나도 없다 (2) note 가 없으면
not_attempted 로 종결 (3) 원문은 byte-exact (4) **모델이 죽어 있어도 기록은 그대로 된다**
(5) 레코드 dossier 에 external 턴이 들어간다.
"""
from __future__ import annotations

from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import USER, CASE, observed

RAW = "  ERROR  x\t\n\n  y  "


def test_note_있는_external_턴_시퀀스(rec_app):
    app, llm = rec_app
    before = len(app.list_ledger(USER, CASE))
    r = app.external_turn(USER, CASE, RAW, action_key="e1", note="host concluded Z")
    app.run_workers()
    assert observed(app, USER, CASE, before) == [
        "user_message", "canonical_evidence_created", "turn_status_changed",
        "answer_created", "turn_status_changed",
    ]
    assert llm.calls == []                                    # 확장 39호 — 기록은 모델을 부르지 않는다
    t = app.get_turn(USER, CASE, r["turn_id"])
    assert t["status"] == "finalized" and t["answer_status"] == "stored"
    assert t["assistant_message"] == "host concluded Z"
    assert app.db.get("evidence", r["evidence_id"])["content"] == RAW    # byte-exact
    assert app.db.get("evidence", r["evidence_id"])["source"] == {"via": "mcp"}
    assert app.db.get("turn", r["turn_id"])["turn_kind"] == "external"


def test_note_없으면_기록자_없이_not_attempted_로_종결(rec_app):
    app, llm = rec_app
    before = len(app.list_ledger(USER, CASE))
    r = app.external_turn(USER, CASE, RAW, action_key="e2")
    app.run_workers()
    assert observed(app, USER, CASE, before) == [
        "user_message", "canonical_evidence_created", "turn_status_changed", "turn_status_changed",
    ]
    assert llm.calls == []
    t = app.get_turn(USER, CASE, r["turn_id"])
    assert t["status"] == "finalized" and t["answer_status"] == "not_attempted"


def test_모델이_죽어_있어도_기록은_된다(app):
    """확장 39호의 요점 — 기록 도구는 조사 도구에 기대지 않는다."""
    app.llm.fail_next.update({"recorder", "troubleshoot"})
    before = len(app.list_ledger(USER, CASE))
    r = app.external_turn(USER, CASE, RAW, action_key="e3", note="kept")
    app.run_workers()
    assert observed(app, USER, CASE, before) == [
        "user_message", "canonical_evidence_created", "turn_status_changed",
        "answer_created", "turn_status_changed",
    ]
    t = app.get_turn(USER, CASE, r["turn_id"])
    assert t["assistant_message"] == "kept" and t["answer_status"] == "stored"


def test_멱등_action_key(app):
    a = app.external_turn(USER, CASE, RAW, action_key="same", note="n"); app.run_workers()
    b = app.external_turn(USER, CASE, "other", action_key="same", note="n")
    assert a["turn_id"] == b["turn_id"] and app.db.count("turn", where={"case_id": CASE}) == 1


def test_레코드에_external_턴이_들어간다(rec_app):
    app, llm = rec_app
    app.external_turn(USER, CASE, "raw log line", action_key="e4", note="host says A"); app.run_workers()
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="i1"); app.run_workers()
    app.generate_record(USER, CASE); app.run_workers()
    dossier = next(p for (role, _s, p) in llm.calls if role == "record")
    assert "[turn 1] investigator (Evidence #1): raw log line" in dossier
    assert "[turn 1] host interpretation (not evidence): host says A" in dossier
    assert "[turn 2] investigator" in dossier


def test_기존_input_턴_시퀀스는_그대로다(app, golden):
    from tests.drive import drive
    expected = next(p["events"] for p in golden if p["path"] == "input_turn")
    assert drive(app, kind="input") == expected
