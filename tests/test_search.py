"""확장 9호 — 케이스북 검색. 계약 셋: evidence 만 · 원문 그대로 · 소유자 범위."""
from __future__ import annotations
import pytest

from casebook.core.errors import InputError, NotFoundError
from casebook.adapters.mcp_server import Tools
from tests.conftest import _build_app, FakeLLM, FakeSearch


@pytest.fixture
def two_users():
    app = _build_app(FakeLLM(), FakeSearch())
    app.db.add("user", {"name": "other", "email": "o@x.test", "password": None})
    c1 = app.create_case(1, "mine A")["case_id"]; c2 = app.create_case(1, "mine B")["case_id"]
    c3 = app.create_case(2, "theirs")["case_id"]
    app.external_turn(1, c1, "ERROR nginx: upstream timed out (110) while reading response header", action_key="a", note="n"); app.run_workers()
    app.external_turn(1, c2, "psycopg2.OperationalError: remaining connection slots are reserved", action_key="b"); app.run_workers()
    app.external_turn(2, c3, "upstream timed out (110) — this belongs to another user", action_key="c"); app.run_workers()
    return app, c1, c2, c3


def test_소유자_범위와_evidence_만(two_users):
    app, c1, c2, c3 = two_users
    hits = app.search_evidence(1, "upstream timed out")
    assert [h["case_id"] for h in hits] == [c1]                       # 남의 케이스(c3)는 안 나온다
    h = hits[0]
    assert set(h) == {"evidence_id", "case_id", "case_title", "kind", "created_at", "source", "turn", "excerpt"}
    assert "[upstream] [timed] [out]" in h["excerpt"] and h["turn"] == 1
    assert app.search_evidence(2, "upstream timed out")[0]["case_id"] == c3
    assert app.search_evidence(1, "psycopg2") [0]["case_id"] == c2
    assert app.search_evidence(1, "nothing-like-this") == []
    app.set_status(1, c1, "archived")
    assert app.search_evidence(1, "upstream timed out") == []                       # archived 기본 제외
    assert app.search_evidence(1, "upstream timed out", include_archived=True)[0]["case_id"] == c1


def test_inspect_는_원문_그대로_소유자만(two_users):
    app, c1, c2, c3 = two_users
    ev = app.search_evidence(1, "upstream")[0]["evidence_id"]
    got = app.inspect_evidence(1, ev)
    assert got["content"] == "ERROR nginx: upstream timed out (110) while reading response header"
    assert got["case_id"] == c1 and got["turn"] == 1 and got["source"] == {"via": "mcp"}
    with pytest.raises(NotFoundError):
        app.inspect_evidence(2, ev)                                    # 남의 evidence


def test_fts_문법_주입_불가와_빈_질의(two_users):
    app, *_ = two_users
    assert app.search_evidence(1, 'upstream" OR "psycopg2') == []       # 따옴표는 문자로 취급
    assert app.search_evidence(1, "NOT upstream") == []                  # NOT 은 단어일 뿐
    with pytest.raises(InputError):
        app.search_evidence(1, "   ")


def test_기존_DB_에도_색인이_뒤늦게_붙는다(app):
    from casebook.core import search
    app.submit_turn(1, 1, "FATAL: remaining connection slots are reserved", action_key="k"); app.run_workers()
    # 색인은 첫 검색에서 만들어지고 기존 행을 채운다
    assert app.search_evidence(1, "connection slots")[0]["case_id"] == 1
    app.submit_turn(1, 1, "second: idle in transaction 380", action_key="k2"); app.run_workers()
    assert app.search_evidence(1, "idle in transaction")[0]["turn"] == 2   # 트리거로 동기화


def test_mcp_도구_모양(two_users):
    app, c1, *_ = two_users
    t = Tools(app, 1)
    hits = t.search_evidence("upstream")
    assert hits and "excerpt" in hits[0] and "content" not in hits[0] and "answer" not in hits[0]
    assert t.inspect(hits[0]["evidence_id"])["content"].startswith("ERROR nginx")

