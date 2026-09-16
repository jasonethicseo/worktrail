"""불변식 15개를 실행 가능한 검사로.

지금은 문서(`AGENTS.md`)에만 있다. 이식할 때 조용히 깨지는 걸 막으려면 코드가 지켜야 한다.
번호는 AGENTS.md 와 같다.
"""
from __future__ import annotations
import pytest
from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import drive, USER, CASE

BYTE_EXACT_INPUT = "  들여쓰기와  공백\t탭\n줄바꿈이 그대로  "


# ── 사실과 권위 ────────────────────────────────────────────────────
def test_i2_사용자_원문은_byte_exact_로_보존된다(app):
    r = app.submit_turn(USER, CASE, BYTE_EXACT_INPUT, action_key="k")
    ev = app.get_turn(USER, CASE, r["turn_id"])
    assert ev["evidence"]["content"] == BYTE_EXACT_INPUT


def test_i3_brief_는_모델_컨텍스트에_재투입되지_않는다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k1"); app.run_workers()
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k2"); app.run_workers()
    answer_prompts = [p for role, _, p in llm.calls if role == "answer"]
    assert answer_prompts, "답변 호출이 없다"
    for p in answer_prompts:
        for marker in ("considering", "recent_updates", "next_up"):
            assert marker not in p, f"답변 프롬프트에 brief 필드가 들어갔다: {marker}"


def test_i4_persistent_id_가_모델_컨텍스트에_노출되지_않는다(rec_app):
    app, llm = rec_app
    r = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    for role, _, p in llm.calls:
        assert f'"id": {r["turn_id"]}' not in p and f"turn_id={r['turn_id']}" not in p


# ── 실패 격리 ──────────────────────────────────────────────────────
def test_i5_답변이_저장된_뒤엔_어떤_실패도_철회하지_못한다(app):
    app.llm.fail_next.add("recorder")   # 답변 저장 뒤의 기록자 실패
    r = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    t = app.get_turn(USER, CASE, r["turn_id"])
    assert t["status"] == "finalized" and t["answer_status"] == "stored"
    assert t["assistant_message"] is not None, "기록자 실패가 답변을 철회했다"


def test_i7_역할당_모델_요청은_정확히_1회다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    roles = [role for role, _, _ in llm.calls]
    assert roles.count("answer") == 1, f"답변 호출 {roles.count('answer')}회"
    assert roles.count("recorder") == 1, f"기록자 호출 {roles.count('recorder')}회"


def test_i8_자동_recovery_가_없다(app):
    """워커를 돌려도 사용자가 부르지 않은 recovery 는 시작되지 않는다."""
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k")
    app.run_workers(); app.run_workers()
    assert "recovery_requested" not in [e["event_type"] for e in app.list_ledger(USER, CASE)]


# ── 모델 경계 ──────────────────────────────────────────────────────
def test_i11_brief_는_5field_다(app):
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    brief = app.get_case(USER, CASE)["brief"]
    assert set(brief) == {"focus", "evidence", "considering", "recent_updates", "next_up"}


def test_i12_1턴은_답변_1회_기록자_1회다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    assert len(llm.calls) == 2, f"턴당 모델 호출 {len(llm.calls)}회: {[c[0] for c in llm.calls]}"


# ── 저장 ───────────────────────────────────────────────────────────
def test_i13_message_evidence_ledger_에_update_delete_가_없다():
    """Xano 에서는 '엔드포인트를 안 만들었다'가 보장이었다. 여기서는 코드가 막아야 한다."""
    from casebook.adapters import http_api
    routes = [(m, r) for r, ms in getattr(http_api, "ROUTES", {}).items() for m in ms]
    bad = [(m, r) for m, r in routes
           if m in {"PUT", "PATCH", "DELETE"} and any(t in r for t in ("message", "evidence", "ledger"))]
    assert not bad, f"append-only 를 깨는 라우트: {bad}"


def test_i14_터미널_상태는_write_once_다(app):
    r = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    t = app.get_turn(USER, CASE, r["turn_id"])
    assert t["status"] in {"finalized", "abandoned"}
    # at-least-once 중복 전달 시뮬레이션(인계 §5) — 이미 종결된 턴을 다시 집으면
    # 진입 조건부 클레임이 조용한 no-op 으로 끝내고 터미널 기록은 그대로여야 한다.
    # (스캐폴드 원본은 여기서 예외를 기대했지만, i8 이 빈 큐 재실행을 무예외로 요구해
    #  양립할 수 없었다 — 불변식의 실체인 write-once 를 직접 검사한다.)
    app.enqueue("turn_worker", turn_id=r["turn_id"])
    app.run_workers()
    assert app.get_turn(USER, CASE, r["turn_id"])["finished_at"] == t["finished_at"]


def test_i15_record_는_명시_요청으로만_생성되고_모델에_재투입되지_않는다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    assert not app.list_records(USER, CASE), "요청하지 않았는데 기록이 생겼다"
    app.generate_record(USER, CASE); app.run_workers()
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k2"); app.run_workers()
    later = [p for role, _, p in llm.calls if role == "answer"][-1]
    assert "# fake record" not in later, "기록이 답변 컨텍스트로 되먹여졌다"
