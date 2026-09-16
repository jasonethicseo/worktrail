"""확장 11호 — 읽기 기록. MCP 읽기 도구 4종이 read_log 에 남고 report 가 지표를 낸다."""
from casebook.adapters.mcp_server import Tools
from casebook.core import reads
from tests.conftest import _build_app, FakeLLM, FakeSearch


def test_읽기가_기록되고_지표가_나온다():
    app = _build_app(FakeLLM(), FakeSearch()); t = Tools(app, 1)
    c1 = t.open_case("A")["case_id"]; c2 = t.open_case("B")["case_id"]
    t.add_evidence(c1, "ERROR foo bar")
    r0 = reads.report(app.db, 1)
    assert r0["cases_created"] == 3 and r0["cases_never_read"] == 3   # replay case + A + B
    hits = t.search_evidence("foo"); t.search_evidence("zzz-none")
    t.inspect(hits[0]["evidence_id"]); t.resume(c1); t.resume(c1, "fresh"); t.handoff(c1); t.handoff(c1, engineer_asked=True)
    r = reads.report(app.db, 1)
    assert r["cross_case_searches"] == 2 and r["searches_with_hit"] == 1
    assert r["cases_resumed"] == 1 and r["resumes"] == 2 and r["evidence_inspected"] == 1
    assert r["handoffs"] == 1 and r["handoffs_refused"] == 1
    assert r["cases_never_read"] == 2                                   # replay case 와 B 는 안 읽힘
    rows = app.db.conn.execute("select kind, case_id, detail, hits from read_log order by id").fetchall()
    assert [tuple(x) for x in rows][:2] == [("search", None, "foo", 1), ("search", None, "zzz-none", 0)]
    # 원장에는 섞이지 않는다
    assert not any(e["event_type"].startswith("read") for e in app.list_ledger(1, c1))
