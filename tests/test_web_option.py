"""확장 3호 — 답변 호출의 내장 web_search 옵션 (Xano 시대 .xs 는 검색 없음).

보장할 것: (1) 요청값 > 서버 기본값 > False 우선순위로 답변 호출에만 흐른다 (2) 켠 턴의
troubleshoot model_call 원장 payload 에 web·질의·인용이 남는다 (3) 원장 시퀀스는 web 과
무관하게 동일하다 (4) bool 이 아닌 값은 거부된다 (5) 인용 URL 의 utm_source=openai 는 떼어진다.
"""
from __future__ import annotations
import pytest

from casebook.core.app import Casebook
from casebook.core.db import SqliteDB
from casebook.core.errors import InputError
from tests.conftest import PLACEHOLDER_INPUT, RecordingLLM, FakeSearch
from tests.drive import USER, CASE, drive


def _troubleshoot(app):
    return [e["payload"] for e in app.list_ledger(USER, CASE)
            if e["event_type"] == "model_call" and e["payload"].get("role") == "troubleshoot"]


def test_기본은_off_이고_원장에도_남는다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    assert llm.answer_webs == [False]
    p = _troubleshoot(app)[0]
    assert p["web"] is False and "web_queries" not in p


def test_요청_web_이_답변_호출과_원장에_흐른다(rec_app):
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k", web=True); app.run_workers()
    assert llm.answer_webs == [True]
    p = _troubleshoot(app)[0]
    assert p["web"] is True and p["web_queries"] == ["fake query"]
    assert p["web_citations"] == [{"url": "https://example.test/doc", "title": "Doc"}]
    # 기록자 호출은 영향 없음 — 답변 호출 1회·기록자 1회 그대로 (불변식 12)
    assert [c[0] for c in llm.calls] == ["answer", "recorder"]


def test_서버_기본값과_recovery_에도_흐른다():
    llm = RecordingLLM()
    db = SqliteDB(":memory:")
    db.add("user", {"name": "t", "email": "t@x.test", "password": "x"})
    db.add("case", {"user_id": 1, "title": "c", "status": "open", "schema_version": 1})
    app = Casebook(db=db, llm=llm, search=FakeSearch(), default_web=True)
    llm.fail_next.add("answer")
    r = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    app.recover(USER, CASE, r["turn_id"], action_key="r", web=False); app.run_workers()
    assert llm.answer_webs == [True, False]


def test_원장_시퀀스는_web_과_무관하다(app):
    on = drive(app, "input")  # 기본 off
    app.default_web = True
    off = drive(app, "input")
    assert on == off


def test_bool_이_아니면_거부(app):
    with pytest.raises(InputError):
        app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k", web="yes")


def test_어댑터가_질의와_인용을_뽑고_utm_을_뗀다():
    from casebook.adapters.openai_llm import OpenAILLM
    result = {"output": [
        {"type": "web_search_call", "action": {"type": "search", "queries": ["q1", "q2"]}},
        {"type": "web_search_call", "action": {"type": "search", "query": "q2"}},
        {"type": "message", "content": [{"type": "output_text", "text": "see https://a.test/x?utm_source=openai and https://b.test/?p=1&utm_source=openai",
            "annotations": [
                {"type": "url_citation", "url": "https://a.test/x?utm_source=openai", "title": "A"},
                {"type": "url_citation", "url": "https://a.test/x?utm_source=openai", "title": "A again"},
                {"type": "url_citation", "url": "https://b.test/?p=1&utm_source=openai", "title": "B"},
            ]}]},
    ]}
    info = OpenAILLM._web_info(result)
    assert info["queries"] == ["q1", "q2"]
    assert info["citations"] == [{"url": "https://a.test/x", "title": "A"}, {"url": "https://b.test/?p=1", "title": "B"}]
    assert OpenAILLM._clean_url("https://c.test/?utm_source=openai&k=v") == "https://c.test/?k=v"
    # 본문 안의 URL — ")" 가 뒤따르는 경우 (실측: luna 는 "(https://…?utm_source=openai)" 로 인용한다)
    assert OpenAILLM._strip_utm("see (https://a.test/x?utm_source=openai) and https://b.test/?p=1&utm_source=openai.") \
        == "see (https://a.test/x) and https://b.test/?p=1."
