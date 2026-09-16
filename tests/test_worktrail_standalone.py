"""확장 39호 3단계 — Worktrail 은 조사 도구 없이 선다.

이 분리가 진짜인지 재는 자리다. 예전 Casebook 은 생성자부터 `Casebook(db, llm, search)` 였고
note_turn 마다 모델을 불렀다. 이제 기록 제품은 db 하나로 서고, 모델 키가 없어도 온전히 돈다.
"""
from __future__ import annotations

import pytest

from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo


@pytest.fixture()
def wt(tmp_path):
    from casebook.core.db import SqliteDB
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "w@x.test", "pw")     # user 1
    return w


def test_모델_없이_한_스레드를_처음부터_끝까지_쓴다(wt, tmp_path):
    repo = _git_repo(tmp_path / "repo")
    uid = 1
    cid = wt.open_thread(uid, "풀 고갈을 확인한다", repo)["case_id"]
    r1 = wt.external_turn(uid, cid, "$ psql -c 'select count(*)'\n380", action_key="a1", note="열린 트랜잭션이 풀을 잡고 있다")
    wt.run_workers()
    ev = r1["evidence_id"]
    wt.decide(uid, cid, "커넥션 풀을 40 으로 올린다", reason="idle 380", authority="user")
    wt.constrain(uid, cid, "운영 DB 는 건드리지 않는다", authority="user")
    wt.rule_out(uid, cid, "네트워크 지연", scope="이 저장소", evidence_ids=[ev])
    wt.declare(uid, cid, "open", "어디서 새는지 아직 모른다")
    wt.declare(uid, cid, "next", "pg_stat_activity 를 다시 뜬다", owner="user")

    r = wt.resume(uid, cid)
    assert r["focus"]["statement"] == "풀 고갈을 확인한다"
    assert r["next"]["owner"] == "user" and r["open"]["statement"].startswith("어디서")
    assert len(r["decisions"]) == 1 and len(r["constraints"]) == 1

    s = wt.status(uid)
    row = next(t for tp in s["topics"] for t in tp["threads"] if t["case_id"] == cid) if s["topics"] else \
          next(t for t in s["unassigned"] if t["case_id"] == cid)
    assert row["phase"] == "mine" and row["origin"] == "agent"

    wt.close_thread(uid, cid, result="풀 크기를 올려 해결했다")
    assert wt.thread_state(uid, cid)["phase"] == "closed"


def test_worktrail_은_조사_코드를_임포트하지_않는다():
    """의존 방향은 한쪽이다 — Casebook → Worktrail. 반대는 없다."""
    import pathlib
    src = pathlib.Path("casebook/core/worktrail.py").read_text()
    for forbidden in ("from .app import", "import app", "openai_llm", "prompts", "tickets", "self.llm", "self.search"):
        assert forbidden not in src, forbidden


def test_생성자가_모델도_검색도_받지_않는다():
    import inspect
    params = list(inspect.signature(Worktrail.__init__).parameters)
    assert params == ["self", "db", "invite_codes"]


def test_MCP_문이_Worktrail_만으로도_돈다(wt, tmp_path):
    """기록 도구의 문은 조사 도구 없이 열린다 — handoff 는 선언된 상태로 답한다."""
    from casebook.adapters.mcp_server import Tools
    repo = _git_repo(tmp_path / "repo2")
    t = Tools(wt, user_id=1)
    cid = t.open_thread("설치 한 줄을 고친다", repo, topic="온보딩")["case_id"]
    assert "Turn 1 recorded" in t.note_turn(cid, "$ sh install.sh\n됐다", "Codex 만 있어도 문구가 맞다", kind="verified", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.declare(cid, "next", "피실험자에게 전달한다", owner="user")

    doc = t.handoff(cid, engineer_asked=True)
    assert "선언된 상태와 근거로 구성한 인계" in doc and "## Declared state" in doc
    assert "- next: 피실험자에게 전달한다  (차례: user)" in doc
    assert t.resume(cid)["focus"]["statement"] == "설치 한 줄을 고친다"


def test_실제_MCP_진입점도_조사_의존성_없이_인계한다(tmp_path):
    """별도 인터프리터에서 실행하여 conftest의 Casebook import가 경계 검사를 가리지 않게 한다."""
    import os
    import subprocess
    import sys
    script = r'''
import sys
from casebook.adapters.mcp_server import build_from_env, Tools
from casebook.core.worktrail import Worktrail
cb, uid = build_from_env()
assert type(cb) is Worktrail
assert not any(m in sys.modules for m in (
    "casebook.core.app", "casebook.core.workers", "casebook.core.prompts",
    "casebook.core.prompt_ext", "casebook.core.tickets", "casebook.adapters.openai_llm"))
t = Tools(cb, uid)
cid = t.open_thread("기록 제품 검증", "chat", topic="독립 실행")["case_id"]
t.note_turn(cid, "  원문\\t<log>\\n", "기록이 저장됐다", kind="verified", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
t.decide(cid, "공유 원장을 유지한다", reason="과거 근거를 보존한다", authority="user")
t.constrain(cid, "운영 자료를 바꾸지 않는다", authority="user")
t.declare(cid, "next", "근거를 읽는다", owner="user")
doc = t.handoff(cid, engineer_asked=True)
for text in ("공유 원장을 유지한다", "과거 근거를 보존한다", "운영 자료를 바꾸지 않는다", "증거 #", "근거를 읽는다"):
    assert text in doc, doc
assert not cb.db.query("record")
assert cb.pending_jobs() == 0
print("MCP entry: Worktrail; model imports: 0; handoff: durable state + evidence; records: 0")
'''
    env = {**os.environ, "CASEBOOK_DB": str(tmp_path / "standalone.db"), "CASEBOOK_MCP_EMAIL": "smoke@local"}
    # 환경에 키가 있어도 기록 제품은 모델을 로드하지 않는다.
    for key in ("", "deliberately-unusable-model-key"):
        result = subprocess.run([sys.executable, "-c", script], env={**env, "OPENAI_API_KEY": key}, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


def test_증거_읽기는_Worktrail_소유자만_원문을_읽는다(wt):
    from fastapi.testclient import TestClient
    from casebook.adapters.http_api import create_app
    from casebook.adapters.mcp_server import Tools
    t = Tools(wt, 1)
    cid = t.open_thread("원문 확인", "chat", topic="검증")["case_id"]
    raw = "  <script>alert('원문')</script>\t\n두 번째 줄  "
    t.note_turn(cid, raw, "원문을 그대로 남겼다", kind="verified", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    owner = wt.login("w@x.test", "pw")["authToken"]
    other = wt.signup("다른 사람", "other@x.test", "pw")["authToken"]
    with TestClient(create_app(wt, None)) as client:
        result = client.get(f"/app/case/{cid}", headers={"Authorization": f"Bearer {owner}"})
        assert result.status_code == 200
        assert result.json()["evidence"][0]["content"] == raw
        assert result.json()["conversation"][-1]["content"] == "원문을 그대로 남겼다"
        assert client.get(f"/app/case/{cid}", headers={"Authorization": f"Bearer {other}"}).status_code == 404
        assert client.get(f"/app/case/{cid}").status_code == 401


def test_실제_MCP_프로토콜로_기록_검색_인계_종료(wt):
    import anyio
    import json
    from casebook.adapters.mcp_server import build_http_app, mint_token
    from tests.test_mcp_remote import _lifespan, _session, _text
    asgi = build_http_app(wt)
    token = mint_token(wt, "w@x.test")

    async def body():
        async def first(session):
            opened = json.loads(_text(await session.call_tool("open_thread", {"focus": "독립 기록 흐름", "topic": "실사용 검증"})))
            cid = opened["case_id"]
            await session.call_tool("note_turn", {"case_id": cid, "observed": "standalone-proof: 저장 성공", "conclusion": "모델 없이 남겼다", "kind": "verified", "next": "다음 할 일\n\n시험이 세운 자리다.", "owner": "user"})
            await session.call_tool("declare", {"case_id": cid, "kind": "next", "statement": "원문을 읽는다", "owner": "user"})
            assert "standalone-proof" in _text(await session.call_tool("search_evidence", {"query": "standalone-proof"}))
            assert "원문을 읽는다" in _text(await session.call_tool("handoff", {"case_id": cid, "engineer_asked": True}))
            return cid
        cid = await _session(asgi, token, first)

        async def resumed(session):
            assert "독립 기록 흐름" in _text(await session.call_tool("resume", {"case_id": cid}))
            await session.call_tool("close_thread", {"case_id": cid, "result": "독립 기록 흐름을 검증했다"})
            assert wt.thread_state(1, cid)["result"] == "독립 기록 흐름을 검증했다"
        await _session(asgi, token, resumed)
    anyio.run(lambda: _lifespan(asgi, body))
    assert wt.db.query("record") == []
