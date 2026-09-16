"""MCP 문 (확장 4호) — 도구 5개의 계약. 프레임워크 없이 Tools 를 직접 부른다."""
from __future__ import annotations
import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core.errors import InputError
from tests.conftest import _build_app, FakeLLM, FakeSearch, RecordingLLM


@pytest.fixture
def tools():
    llm = RecordingLLM()
    app = _build_app(llm, FakeSearch())
    return Tools(app, user_id=1), app, llm


def test_open_list_add_note_handoff(tools):
    t, app, llm = tools
    c = t.open_case("Checkout 500s after deploy")
    cid = c["case_id"]
    assert any(x["case_id"] == cid for x in t.list_cases())

    out = t.add_evidence(cid, "  raw\tlog  ")
    assert out.startswith("Evidence #") and "byte-exact" in out and llm.calls == []

    out = t.note_turn(cid, "pg_stat_activity: 380 idle in transaction", "pool exhausted by open transactions", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    assert "Turn 2 recorded" in out
    assert llm.calls == []                                  # 확장 39호 — 기록은 모델을 부르지 않는다

    refused = t.handoff(cid)                                             # 단언 없이 부르면 거부
    assert refused.startswith("handoff is for the engineer") and llm.calls == []
    doc = t.handoff(cid, engineer_asked=True)
    # 확장 39호 — 꼬리는 모델이 만든 brief 가 아니라 선언된 상태다(모델이 만든 ## Brief 는 없다).
    # 확장 110호(D15573) — note_turn 이 턴마다 next 를 세우므로, 노트를 남긴 스레드에는 늘 선언된
    # 상태가 있다. 전에는 이 자리가 "선언이 없으니 꼬리도 없다" 였다.
    assert doc.startswith(f"# Handoff — case {cid}")
    from tests.conftest import investigation_available
    if investigation_available():                    # 레코드는 조사 도구의 것 — 공개 트리에는 없다(확장 130호)
        assert "# fake record" in doc
    assert "## Brief" not in doc
    assert "## Declared state" in doc and "다음 할 일" in doc
    assert [r for (r, _, _) in llm.calls] == (["record"] if investigation_available() else [])   # 기록자는 안 돌고, 레코드 한 번만(조사 도구가 있을 때)

    # 도구는 브리프를 되돌려주지 않는다 — handoff 만 사람 앞으로 낸다
    assert "focus" not in out


def test_handoff_꼬리는_선언된_상태다(tools, tmp_path):
    """확장 39호 — 모델이 만든 요약이 아니라 스레드가 선언한 것으로 답한다."""
    t, app, llm = tools
    cid = t.open_thread("풀 고갈을 확인한다", str(tmp_path), topic="테스트")["case_id"]
    t.note_turn(cid, "pg_stat_activity: 380 idle in transaction", "열린 트랜잭션이 풀을 잡고 있다", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.declare(cid, "open", "커넥션이 어디서 새는지 아직 모른다")
    t.declare(cid, "next", "pg_stat_activity 를 다시 뜬다", owner="user")
    doc = t.handoff(cid, engineer_asked=True)
    assert "## Declared state" in doc
    assert "- focus: 풀 고갈을 확인한다" in doc
    assert "- open: 커넥션이 어디서 새는지 아직 모른다" in doc
    assert "- next: pg_stat_activity 를 다시 뜬다  (차례: user)" in doc


def test_남의_케이스는_404_문구로_막힌다(tools):
    t, app, _ = tools
    other = Tools(app, user_id=2)
    app.db.add("user", {"name": "o", "email": "o@x.test", "password": None})
    cid = t.open_case("mine")["case_id"]
    from casebook.core.errors import NotFoundError
    with pytest.raises(NotFoundError):
        other.add_evidence(cid, "x")


def test_모델이_죽어_있어도_note_turn_은_된다(tools):
    """확장 39호 — 기록 도구는 모델 키 없이 돈다."""
    t, app, llm = tools
    cid = t.open_case("c")["case_id"]
    llm.fail_next.update({"recorder", "troubleshoot"})
    out = t.note_turn(cid, "obs", "concl", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    assert "Turn 1 recorded" in out
    assert app.get_case(1, cid)["conversation"][-1]["content"] == "concl"


def test_evidence_요약은_거부된다(tools):
    t, app, llm = tools
    cid = t.open_case("c")["case_id"]
    n = app.db.count("evidence")
    for bad in ("Evidence #1164: 본체 저장소는 clean, 테스트 104 passed", "E12 shows the pool was exhausted",
                "요약: 두 WAR 의 MD5 가 다르다", "Summary of the logs above"):
        assert t.add_evidence(cid, bad).startswith("rejected:")
    assert app.db.count("evidence") == n
    from casebook.core import reads
    assert reads.report(app.db, 1)["evidence_refused"] == 4                 # 거부도 측정된다
    assert t.add_evidence(cid, "ERROR checkout.charge psycopg2.OperationalError: FATAL").startswith("Evidence #")
    assert t.add_evidence(cid, "evidence of a leak in the logs:\n  waiters=64").startswith("Evidence #")  # 단어 'evidence' 자체는 허용


def test_build_server_registers_twelve_tools(tools):
    mcp = pytest.importorskip("mcp")
    t, app, _ = tools
    from casebook.adapters.mcp_server import build_server
    import asyncio
    server = build_server(app, 1)
    names = sorted(x.name for x in asyncio.run(server.list_tools()))
    assert names == ["add_evidence", "add_evidence_file", "assign_topic", "close_thread", "constrain", "decide", "declare", "define", "handoff",
                     "hook", "import_prior", "inspect", "list_cases", "list_threads", "list_topics", "merge_topic", "note_turn", "open_case",
                     "open_thread", "overview", "rate_handoff", "resume", "rule_out", "search_evidence", "search_prior", "switch_thread"]
    # 확장 15·16호 스레드 4개 + declare · 24호 assign_topic · 26호 overview · 42호 import_prior(스크립트 전용)·search_prior


def test_add_evidence_file_은_서버가_읽고_출처를_남긴다(tools, tmp_path):
    t, app, llm = tools
    cid = t.open_case("c")["case_id"]
    f = tmp_path / "app.log"; f.write_bytes(b"line1\n\xff\xfe raw bytes\n")
    out = t.add_evidence_file(cid, str(f))
    assert out.startswith("Evidence #") and "app.log" in out and llm.calls == []
    ev = app.get_case(1, cid)["evidence"][-1]
    assert ev["content"].startswith(f"[file] {f}") and "line1" in ev["content"]
    assert ev["source"]["file"] == str(f) and ev["source"]["clipped"] is False
    with pytest.raises(Exception):
        t.add_evidence_file(cid, str(tmp_path / "missing.log"))


def test_worktree_기본값_우선순위(monkeypatch):
    """CASEBOOK_WORKTREE(래퍼가 cd 전에 잡은 호스트 PWD) → CLAUDE_PROJECT_DIR → cwd. 인자가 있으면 인자."""
    from casebook.adapters.mcp_server import Tools
    monkeypatch.delenv("CASEBOOK_WORKTREE", raising=False); monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    import os
    assert Tools._worktree(None) == os.getcwd()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/tmp/claude-proj"); assert Tools._worktree(None) == "/tmp/claude-proj"
    monkeypatch.setenv("CASEBOOK_WORKTREE", "/tmp/lab"); assert Tools._worktree(None) == "/tmp/lab"
    assert Tools._worktree("/explicit") == "/explicit"


def test_overview_cross_repo_open_work_is_owned_and_read_only(tools, tmp_path):
    t, app, llm = tools
    from casebook.core import threads
    assert t.overview() == []
    a = str(tmp_path / "a"); b = str(tmp_path / "b")
    clock = [1000]
    app.db.now_ms = lambda: clock[0]
    older = t.open_thread("older focus", a, topic="테스트")["case_id"]
    t.declare(older, "next", "  next one\nnext two  ", owner="user")
    t.switch_thread(worktree=a)  # pause: 스레드는 열린 채로 바인딩만 없어진다.
    clock[0] = 2000
    newer = t.open_thread("newer focus", b, topic="테스트")["case_id"]
    clock[0] = 3000
    peer = t.open_thread("peer focus", b, topic="테스트")["case_id"]
    clock[0] = 4000
    closed = t.open_thread("closed focus", a, topic="테스트")["case_id"]
    t.close_thread(closed)
    archived = t.open_thread("archived focus", str(tmp_path / "closed-only"), topic="테스트")["case_id"]
    app.set_status(1, archived, "archived")
    uid = app.db.add("user", {"name": "other", "email": "other@test", "password": "x"})["id"]
    Tools(app, uid).open_thread("private focus", b, topic="테스트")
    # 읽기 전후 정본·바인딩에 변경이 없고, 모델도 호출하지 않는다.
    tables = ("case", "ledger", "message", "evidence", "turn")
    before = {name: app.db.query(name) for name in tables}
    bindings = app.db.conn.execute("SELECT * FROM worktree_binding ORDER BY user_id, worktree").fetchall()
    calls = list(llm.calls)
    out = t.overview()
    assert [r["identity"] for r in out] == [f"path:{b}", f"path:{a}"]
    assert [r["open_count"] for r in out] == [2, 1]
    assert [r["updated_at"] for r in out] == [3000, 1000]  # closed의 최근 활동은 정렬에 끼지 않는다.
    assert [x["case_id"] for x in out[0]["threads"]] == [peer, newer]
    assert out[1]["threads"] == [{"case_id": older, "title": "older focus", "focus": "older focus",
                                   "next": "next one\nnext two", "updated_at": 1000, "bound_worktrees": []}]
    assert out[0]["threads"][0]["next"] is None
    assert out[0]["threads"][0]["bound_worktrees"] == [threads.identify(b)["worktree"]]
    assert {name: app.db.query(name) for name in tables} == before
    assert app.db.conn.execute("SELECT * FROM worktree_binding ORDER BY user_id, worktree").fetchall() == bindings
    assert llm.calls == calls


def test_overview_is_callable_through_mcp(tools, tmp_path):
    pytest.importorskip("mcp")
    import asyncio
    from casebook.adapters.mcp_server import build_server
    t, app, llm = tools
    t.open_thread("cross repo work", str(tmp_path / "repo"), topic="테스트")
    server = build_server(app, 1)
    result = asyncio.run(server.call_tool("overview", {}))
    assert "cross repo work" in str(result)
    assert llm.calls == []


def test_확장_31호_도구_인자_셋(tools, tmp_path):
    """declare(owner) · close_thread(result) · assign_topic(conclusion) — 도구 수는 그대로, 인자만 는다."""
    from tests.test_threads import _git_repo
    t, app, _ = tools
    wt = _git_repo(tmp_path / "w")
    cid = t.open_thread("단계 실험", wt, topic="테스트")["case_id"]
    out = t.declare(cid, "next", "다음을 정한다", owner="user")
    assert "owner: user" in out and app.resume(1, cid)["next"]["owner"] == "user"
    assert "owner: watch" in t.declare(cid, "next", "지켜본다", owner="watch")
    with pytest.raises(InputError, match="owner belongs to next"):          # 프레임워크 밖이라 _guard 문구 대신 예외
        t.declare(cid, "open", "질문", owner="user")
    topic = t.assign_topic("테스트", [cid], conclusion="한 줄 결론")
    assert topic.endswith("Conclusion: 한 줄 결론") and app.list_topics(1)[0]["conclusion"] == "한 줄 결론"
    closed = t.close_thread(cid, result="잘 끝났다")
    assert closed["result"] == "잘 끝났다" and app.status(1)["topics"][0]["threads"][0]["result"] == "잘 끝났다"
