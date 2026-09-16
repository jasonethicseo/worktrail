"""effort 선택지 — 로컬 제품 확장(Xano 시대 .xs 는 none 고정).

보장할 것: (1) 요청값 > 서버 기본값 > "none" 우선순위로 답변 호출에만 흐른다
(2) troubleshoot model_call 원장 payload 에 남는다 (3) 원장 시퀀스는 effort 와
무관하게 동일하다 — 재현 보장 (4) 선택지 밖 값은 거부된다.
"""
from __future__ import annotations
import pytest

from casebook.core.errors import InputError
from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import USER, CASE, drive


def _troubleshoot_efforts(app):
    return [e["payload"]["effort"] for e in app.list_ledger(USER, CASE)
            if e["event_type"] == "model_call" and e["payload"].get("role") == "troubleshoot"]


def test_기본은_xs_고정값_none_이다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    assert llm.answer_efforts == ["none"]
    assert _troubleshoot_efforts(app) == ["none"]


def test_요청_effort_가_답변_호출과_원장에_흐른다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k", effort="low"); app.run_workers()
    assert llm.answer_efforts == ["low"]
    assert _troubleshoot_efforts(app) == ["low"]


def test_recovery_도_effort_를_받는다(rec_app):
    app, llm = rec_app
    app.llm.fail_next.add("answer")
    src = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="src"); app.run_workers()
    app.recover(USER, CASE, src["turn_id"], action_key="rec", effort="medium"); app.run_workers()
    assert llm.answer_efforts == ["none", "medium"]   # 실패한 원본은 기본값, 복구는 medium


def test_서버_기본값은_요청_미지정일_때만_적용된다(rec_app):
    app, llm = rec_app
    app.default_effort = "low"
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k1"); app.run_workers()
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k2", effort="none"); app.run_workers()
    assert llm.answer_efforts == ["low", "none"]      # 명시한 none 이 기본값 low 를 이긴다


def test_선택지_밖_값은_거부된다(app):
    with pytest.raises(InputError, match="effort"):
        app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k", effort="high")
    from casebook.core.app import Casebook
    with pytest.raises(ValueError, match="default_effort"):
        Casebook(db=app.db, llm=app.llm, search=app.search, default_effort="max")


def test_effort_는_원장_시퀀스를_바꾸지_않는다(app, golden):
    expected = next(p["events"] for p in golden if p["path"] == "input_turn")
    app.default_effort = "medium"
    assert drive(app, kind="input") == expected       # 골든과 동일 — 재현 보장
