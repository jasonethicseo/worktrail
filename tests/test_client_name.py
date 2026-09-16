"""확장 40호 (2026-09-08) — 기록에 어느 클라이언트가 남겼는지 붙인다.

원장에 남던 것은 어느 문으로 들어왔는지(`via`)뿐이라, 내가 남긴 노트와 Codex 가 남긴 노트가
구별되지 않았다. MCP 규약의 clientInfo 를 쓴다. 실측(2026-09-08): Claude Code 는 "claude-code",
Codex 는 "codex-mcp-client" 로 자기를 댄다 — 서로 다르고 빈 값이 아니라 통로를 뚫을 값이 있다.

범위(C13509): 기록 도구 셋(note_turn · add_evidence · decide)에 한 칸까지. 못 받으면 안 넣는다.
"""
from __future__ import annotations

import pytest

from casebook.adapters.mcp_server import Tools, normalize_client
from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo


@pytest.fixture()
def wt():
    from casebook.core.db import SqliteDB
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "c@x.test", "pw")
    return w


def test_세_도구가_클라이언트_이름을_남긴다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("이름 붙이기", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    t.note_turn(cid, "$ pytest\n219 passed", "통과했다", kind="verified", client="claude-code", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.add_evidence(cid, "$ git log --oneline -1\nabc1234 x", client="codex-mcp-client")
    t.decide(cid, "이름은 받은 그대로 저장한다", authority="user", client="codex-mcp-client")

    evs = wt.db.query("evidence", where={"case_id": cid}, order="id")
    assert [e["source"]["client"] for e in evs] == ["claude-code", "codex-mcp-client"]
    assert all(e["source"]["via"] == "mcp" for e in evs)

    dec = [e for e in wt.db.query("ledger", where={"case_id": cid}) if e["event_type"] == "decision_recorded"]
    assert dec[0]["payload"]["client"] == "codex-mcp-client"


def test_이름이_없으면_넣지_않는다(wt, tmp_path):
    """미상이라고 꾸미지 않는다 — 칸 자체가 없다."""
    t = Tools(wt, 1)
    cid = t.open_thread("이름 없음", _git_repo(tmp_path / "r2"), topic="검증")["case_id"]
    t.note_turn(cid, "관찰", "결론", kind="thought", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.decide(cid, "결정", authority="agent")
    ev = wt.db.query("evidence", where={"case_id": cid}, order="id")[0]
    assert ev["source"] == {"via": "mcp"}
    dec = [e for e in wt.db.query("ledger", where={"case_id": cid}) if e["event_type"] == "decision_recorded"]
    assert "client" not in dec[0]["payload"]


@pytest.mark.parametrize("bad", ["", "   ", "이름에 공백", "a" * 60, "-시작", None])
def test_이상한_이름은_버린다(bad):
    assert normalize_client(bad) is None


def test_실측한_두_이름은_통과한다():
    assert normalize_client("claude-code") == "claude-code"
    assert normalize_client("codex-mcp-client") == "codex-mcp-client"


def test_호스트는_client_칸을_보지_않는다():
    """프록시가 채우는 칸이다 — 호스트 모델이 스스로 대는 이름이 아니다."""
    from casebook.adapters.mcp_proxy import host_schema

    class T:
        input_schema = {"type": "object",
                       "properties": {"case_id": {}, "observed": {}, "client": {}, "facts": {}},
                       "required": ["case_id", "observed", "client", "facts"]}
    s = host_schema(T())
    assert "client" not in s["properties"] and "facts" not in s["properties"]
    assert s["required"] == ["case_id", "observed"]


def test_프록시가_이름을_실어_원격_기록에_남는다(tmp_path):
    """확장 40호의 통로 전체 — 호스트 이름을 프록시가 듣고, 원격 문이 받아 원장에 남긴다."""
    pytest.importorskip("mcp")
    import anyio, httpx2, json
    from casebook.adapters.mcp_proxy import Proxy
    from casebook.adapters.mcp_server import build_http_app, mint_token
    from tests.conftest import FakeLLM, FakeSearch, _build_app
    from tests.test_mcp_remote import _lifespan, _commit

    app = _build_app(FakeLLM(), FakeSearch())
    asgi = build_http_app(app)
    repo = _git_repo(tmp_path / "w"); _commit(repo, "first")
    tok = mint_token(app, "tester@example.test")

    async def body():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test") as hc:
            p = Proxy(f"http://test/mcp/{tok}", worktree=repo, http_client=hc)
            await p.connect()
            o = json.loads((await p.call("open_thread", {"focus": "통로 확인", "topic": "테스트"})).content[0].text)
            cid = o["case_id"]
            await p.call("note_turn", {"case_id": cid, "observed": "관찰", "conclusion": "결론", "kind": "finding", "next": "다음 할 일\n\n시험이 세운 자리다.", "owner": "user"},
                         client="claude-code")
            await p.call("decide", {"case_id": cid, "statement": "결정", "authority": "user"},
                         client="codex-mcp-client")
            await p.call("note_turn", {"case_id": cid, "observed": "이름 없이", "conclusion": "결론2", "kind": "change", "next": "다음 할 일\n\n시험이 세운 자리다.", "owner": "user"})
            await p.close()

            evs = app.db.query("evidence", where={"case_id": cid}, order="id")
            assert evs[0]["source"] == {"via": "mcp", "client": "claude-code"}
            assert evs[1]["source"] == {"via": "mcp"}                     # 이름을 안 실으면 종전 그대로
            dec = [e for e in app.db.query("ledger", where={"case_id": cid}) if e["event_type"] == "decision_recorded"]
            assert dec[0]["payload"]["client"] == "codex-mcp-client"
            # open_thread 는 범위 밖 — 이름을 실어도 도구가 받지 않는다
            assert p.with_client("open_thread", {}, "claude-code") == {}
    anyio.run(lambda: _lifespan(asgi, body))
