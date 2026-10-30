"""확장 157호 — 사용자 발화 원문과 source_ref (스레드 595). 보장할 것:
(1) UserPromptSubmit 훅은 원문을 spool 에 쓰고 같은 턴에 호스트의 프롬프트 id 를 source_ref 로 알린다 — 네트워크 없이.
    Claude Code 는 prompt_id, Codex 는 turn_id 다
(2) 훅이 띄운 업로더가 spool 을 저장 위치로 비운다. 실패하면 파일이 남고, 다시 보내도 한 번만 저장된다
(3) 어느 도구에서 오든 같은 규칙: 사람 권한 결정·제약·focus 와 사람 말에서 나온 노트는 ref 없이 거절된다.
    노트는 관찰의 출처(machine | user)를 늘 밝힌다
(4) 훅이 없는 채팅 클라이언트는 add_utterance 로 원문을 붙여 ref 를 받는다(origin=model_pasted). 요약 꼴은 거절하고,
    붙이지 않은(없는) ref 는 거절한다. 훅 호스트는 add_utterance 를 쓰지 않는다
(5) inspect 가 기록 옆에 원문과 출처를 붙인다(훅 경로에서 아직 안 올라왔으면 pending)
(6) 프록시는 호출한 호스트의 capture 만 보고 표시를 채우고, 호스트 모델이 댄 값은 버린다. 옛 서버에는 싣지 않는다."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from casebook.adapters import capture
from casebook.adapters.mcp_server import Tools
from casebook.core import utterances
from casebook.core.db import SqliteDB
from casebook.core.errors import InputError
from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "hooks" / "casebook_hook.py"
PID = "5b0e2c1a-7f3d-4e8a-9c21-0d6f4b7a9e10"
TURN = "019a3f7e-codex-turn-0001"
NEXT = "다음 할 일\n\n시험이 세운 자리다."


@pytest.fixture
def wt():
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "k@x.test", "pw")
    return w


@pytest.fixture
def cap_dir(tmp_path, monkeypatch):
    d = tmp_path / "capture"
    monkeypatch.setenv("CASEBOOK_CAPTURE_DIR", str(d))
    return d


def _env(tmp_path, cap_dir):
    env = dict(os.environ, CASEBOOK_DB=str(tmp_path / "local.db"), CASEBOOK_MCP_EMAIL="me@local.test",
               CASEBOOK_MODE="local", CASEBOOK_CAPTURE_DIR=str(cap_dir))
    env.pop("CASEBOOK_REMOTE_URL", None)
    return env


def _did(msg: str) -> int:
    return int(msg.split("Decision D", 1)[1].split()[0])


def _uid(msg: str) -> str:
    return msg.split("Utterance ", 1)[1].split()[0]


def _hook(args: list[str], payload: dict, env: dict) -> dict:
    p = subprocess.run([sys.executable, str(SCRIPT), *args], input=json.dumps(payload), capture_output=True,
                       text=True, env=env, cwd=ROOT, timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _stored(db_path: pathlib.Path) -> list[dict]:
    if not db_path.exists():
        return []
    db = SqliteDB(str(db_path))
    utterances.ensure(db)
    return [dict(r) for r in db.conn.execute(
        "SELECT prompt_id, content, session_id, worktree, client, origin FROM utterance ORDER BY id")]


def _drained(tmp_path, cap_dir, n: int) -> list[dict]:
    deadline = time.time() + 15                                     # 훅이 띄운 업로더가 비운다 — 훅은 기다리지 않는다
    while time.time() < deadline:
        rows = _stored(tmp_path / "local.db")
        if len(rows) >= n and not list((cap_dir / "spool").glob("*.json")):
            return rows
        time.sleep(0.2)
    return _stored(tmp_path / "local.db")


# ── (1)(2) 훅과 업로더 ─────────────────────────────────────────────────────────────

def test_프롬프트_훅은_원문을_남기고_같은_턴에_ref_를_알리고_업로더가_비운다(tmp_path, cap_dir):
    env = _env(tmp_path, cap_dir)
    said = "결정은 턴마다 남기고,\n원문은 ref 로 잇는다. 줄바꿈과  공백도  그대로."
    out = _hook(["prompt"], {"prompt": said, "prompt_id": PID, "session_id": "s-1", "cwd": str(tmp_path)}, env)
    ctx = out["hookSpecificOutput"]
    assert ctx["hookEventName"] == "UserPromptSubmit"
    assert f"casebook source_ref: {PID}" in ctx["additionalContext"]
    assert "systemMessage" in out                                   # 처음 켜질 때 한 번 알린다(D18010)
    assert capture.active("claude-code") and not capture.active("codex")

    rows = _drained(tmp_path, cap_dir, 1)
    assert rows == [{"prompt_id": PID, "content": said, "session_id": "s-1", "worktree": str(tmp_path),
                     "client": "claude-code", "origin": "hook"}]
    assert not list((cap_dir / "spool").glob("*.json"))

    again = _hook(["prompt"], {"prompt": "두 번째", "prompt_id": "a" * 32}, env)
    assert "systemMessage" not in again                             # 안내는 한 번뿐
    assert _hook(["prompt"], {"prompt": "   "}, env) == {}           # 빈 입력은 남길 것이 없다


def test_Codex_훅은_turn_id_를_ref_로_쓰고_Codex_만_켠다(tmp_path, cap_dir):
    env = _env(tmp_path, cap_dir)
    out = _hook(["prompt", "codex"], {"prompt": "Codex 에서 친 말", "turn_id": TURN, "session_id": "cx",
                                      "cwd": str(tmp_path), "model": "gpt-5.5-codex"}, env)
    assert f"casebook source_ref: {TURN}" in out["hookSpecificOutput"]["additionalContext"]
    assert capture.active("codex") and not capture.active("claude-code")
    assert capture.client_active("codex-mcp-client") and not capture.client_active("claude-code")
    rows = _drained(tmp_path, cap_dir, 1)
    assert rows[0]["prompt_id"] == TURN and rows[0]["client"] == "codex" and rows[0]["origin"] == "hook"


def test_id_가_없는_옛_판에서도_ref_를_만든다(cap_dir):
    out = capture.capture_prompt({"prompt": "옛 판"})
    pid = out["hookSpecificOutput"]["additionalContext"].splitlines()[0].split(": ", 1)[1]
    assert utterances.REF_RE.match(pid)
    assert json.loads((cap_dir / "spool" / f"{pid}.json").read_text())["content"] == "옛 판"


def test_보내기가_실패하면_남기고_다시_보내도_한_번만_저장된다(wt, cap_dir):
    capture.capture_prompt({"prompt": "첫째", "prompt_id": PID})
    capture.capture_prompt({"prompt": "둘째", "prompt_id": "b" * 32})

    def down(items):
        raise ConnectionError("server down")
    assert capture.upload_once(down) == {"sent": 0, "left": 2}

    got: list[dict] = []

    def send(items):
        got.extend(items)
        utterances.record(wt.db, 1, items)
    assert capture.upload_once(send) == {"sent": 2, "left": 0}
    assert utterances.record(wt.db, 1, got) == {"stored": 0, "known": 2}     # 재전송은 무시된다
    assert [s["content"] for s in utterances.lookup(wt.db, 1, [PID, "b" * 32])] == ["첫째", "둘째"]


def test_큰_붙여넣기는_잘라_남기고_원래_크기를_적는다(wt):
    big = "가" * (utterances.CONTENT_CAP // 3 + 10)                   # 한 글자 3바이트
    utterances.record(wt.db, 1, [{"prompt_id": PID, "content": big}])
    s = utterances.lookup(wt.db, 1, [PID])[0]
    assert s["clipped_from_bytes"] == len(big.encode()) and len(s["content"].encode()) <= utterances.CONTENT_CAP


def test_끄면_남기지_않고_표시도_서지_않는다(cap_dir):
    capture.capture_prompt({"prompt": "켠다", "prompt_id": PID})
    (cap_dir / "off").write_text("")
    assert capture.capture_prompt({"prompt": "꺼져 있다", "prompt_id": "c" * 32}) == {}
    assert not capture.client_active("claude-code")


# ── (3)(4) 어느 도구든 같은 규칙 ───────────────────────────────────────────────────

def test_사람_권한_결정_제약_focus_는_어느_도구에서든_ref_가_필수다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("원문 연결", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    before = wt.db.count("ledger", where={"case_id": cid})
    for capture_flag in (True, False):                              # 훅 호스트든 채팅이든 같다
        with pytest.raises(InputError, match="needs source_refs") as e:
            t.decide(cid, "턴마다 원문을 남긴다", authority="user", capture=capture_flag, next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
        assert "add_utterance" in str(e.value) and "Nothing was recorded" in str(e.value)
        with pytest.raises(InputError, match="needs source_refs"):
            t.constrain(cid, "원문은 복제하지 않는다", authority="user", capture=capture_flag)
        with pytest.raises(InputError, match="needs source_refs"):
            t.declare(cid, "focus", "원문 연결을 만든다", authority="user", capture=capture_flag, next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    assert wt.db.count("ledger", where={"case_id": cid}) == before                   # 거절은 기록하지 않는다

    did = _did(t.decide(cid, "턴마다 원문을 남긴다", authority="user", capture=True, source_refs=[PID], next="다음 할 일\n\n시험이 세운 자리다.", owner="user"))
    assert wt.db.get("ledger", did)["payload"]["source_refs"] == [PID]               # 훅 경로는 아직 없는 ref 도 받는다
    t.decide(cid, "구현 중 에이전트가 고른 것", authority="agent", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")                      # 에이전트 권한은 요구하지 않는다
    t.declare(cid, "next", NEXT, owner="user")                                        # next·open 은 선택
    with pytest.raises(InputError, match="is not a prompt id"):
        t.decide(cid, "형식이 틀린 ref", authority="user", source_refs=["D17975"], next="다음 할 일\n\n시험이 세운 자리다.", owner="user")


def test_채팅은_원문을_붙여_ref_를_받고_없는_ref_와_요약은_거절된다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("채팅 원문", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    with pytest.raises(InputError, match="are not stored utterances"):              # 지어낸 ref 는 못 쓴다
        t.decide(cid, "채팅에서 온 사람 결정", authority="user", source_refs=[PID], next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    for summary in ("사용자는 원문을 보존하자고 했다", "The user said keep every prompt", "요약: 원문을 남긴다"):
        with pytest.raises(InputError, match="not their words"):
            t.add_utterance(summary)
    said = "모델이 내 글을 paste해서 원문이라고 하면 되잖아. 그 방법은 안됨?"
    out = t.add_utterance(said, client="chatgpt")
    assert "origin: model_pasted" in out
    ref = _uid(out)
    did = _did(t.decide(cid, "채팅도 원문을 붙여 ref 를 단다", authority="user", source_refs=[ref], next="다음 할 일\n\n시험이 세운 자리다.", owner="user"))
    src = t.inspect(ref=f"D{did}")["sources"][0]
    assert src["content"] == said and src["origin"] == "model_pasted"
    with pytest.raises(InputError, match="already records the engineer's prompts"):  # 훅 호스트는 붙이지 않는다
        t.add_utterance(said, capture=True)


def test_노트는_출처를_늘_밝히고_사람_말이면_ref_가_필수다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("노트 출처", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    n = wt.db.count("turn", where={"case_id": cid})
    with pytest.raises(InputError, match="needs observed_from"):
        t.note_turn(cid, "테스트 694개 통과", "그대로다", kind="verified", next=NEXT, owner="user")
    with pytest.raises(InputError, match="needs source_refs"):
        t.note_turn(cid, "노트는 조건부로 하자고 했다", "조건부", kind="thought", next=NEXT, owner="user",
                    observed_from="user")
    with pytest.raises(InputError, match='must be "machine"'):
        t.note_turn(cid, "관찰", "결론", kind="thought", next=NEXT, owner="user", observed_from="agent")
    assert wt.db.count("turn", where={"case_id": cid}) == n
    t.note_turn(cid, "테스트 694개 통과", "그대로다", kind="verified", next=NEXT, owner="user", observed_from="machine")
    t.note_turn(cid, "노트가 원문 ref 를 필요로 한다면", "조건부 강제", kind="thought", next=NEXT, owner="user",
                capture=True, observed_from="user", source_refs=[PID])
    wt.run_workers()
    notes = [e["payload"] for e in wt.db.query("ledger", where={"case_id": cid}, order="id")
             if e["event_type"] == "answer_created"]
    assert notes[0]["observed_from"] == "machine" and "source_refs" not in notes[0]
    assert notes[1]["observed_from"] == "user" and notes[1]["source_refs"] == [PID]


# ── (5) 원문 병기 ───────────────────────────────────────────────────────────────

def test_inspect_는_기록_옆에_원문을_붙이고_아직_없으면_pending(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("원문 병기", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    did = _did(t.decide(cid, "노트는 조건부로 ref 를 요구한다", authority="user", capture=True, source_refs=[PID], next="다음 할 일\n\n시험이 세운 자리다.", owner="user"))
    t.note_turn(cid, "사람이 말했다", "해석", kind="thought", next=NEXT, owner="user", capture=True,
                observed_from="user", source_refs=[PID])
    assert t.inspect(ref=f"D{did}")["sources"] == [{"source_ref": PID, "pending": True}]   # ref 가 먼저 와도 받는다(D17990)
    said = "아 아니네. 노트가 원문 ref를 필요로 한다면 - 이라는 전제하에 쓰는게 맞아보인다."
    t.record_utterances([{"prompt_id": PID, "content": said, "said_at": 1}])
    d = t.inspect(ref=f"D{did}")
    assert d["source_refs"] == [PID]
    assert d["sources"] == [{"source_ref": PID, "origin": "hook", "said_at": 1, "content": said}]
    note = t.inspect(ref=f"{cid}:1")
    assert note["observed_from"] == "user" and note["sources"][0]["content"] == said
    assert "source_refs" in t.resume(cid)["decisions"][0]


def test_남의_원문은_붙지_않는다(wt, tmp_path):
    wt.signup("남", "o@x.test", "pw")
    Tools(wt, 2).record_utterances([{"prompt_id": PID, "content": "남의 말"}])
    t = Tools(wt, 1)
    cid = t.open_thread("격리", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    with pytest.raises(InputError, match="are not stored utterances"):              # 채팅에서는 남의 ref 를 못 단다
        t.decide(cid, "내 결정", authority="user", source_refs=[PID], next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    did = _did(t.decide(cid, "내 결정", authority="user", capture=True, source_refs=[PID], next="다음 할 일\n\n시험이 세운 자리다.", owner="user"))
    assert t.inspect(ref=f"D{did}")["sources"] == [{"source_ref": PID, "pending": True}]   # 훅 경로여도 남의 원문은 안 붙는다


# ── (6) 프록시 ──────────────────────────────────────────────────────────────────

def test_프록시는_호출한_호스트의_capture_만_보고_호스트_값은_버린다(cap_dir):
    from casebook.adapters.mcp_proxy import Proxy
    p = Proxy("http://unused/mcp/x", worktree="/tmp")
    assert "capture" not in p.with_client("decide", {"capture": True}, "claude-code")     # 아직 수집이 안 돌았다
    capture.capture_prompt({"prompt": "켠다", "prompt_id": PID})
    assert p.with_client("decide", {}, "claude-code")["capture"] is True
    assert p.with_client("add_utterance", {}, "claude-code")["capture"] is True
    assert "capture" not in p.with_client("decide", {"capture": True}, "codex-mcp-client")  # Codex 훅은 아직 없다
    assert "capture" not in p.with_client("add_evidence", {}, "claude-code")
    capture.capture_prompt({"prompt": "Codex 도 켠다", "turn_id": TURN}, host="codex")
    assert p.with_client("note_turn", {}, "codex-mcp-client")["capture"] is True


def test_옛_서버가_capture_를_모르면_표시를_빼고_보낸다(cap_dir):
    """플러그인이 서버보다 먼저 갱신되는 동안 기록이 통째로 거절되지 않게 한다."""
    from types import SimpleNamespace
    from casebook.adapters.mcp_proxy import Proxy
    capture.capture_prompt({"prompt": "켠다", "prompt_id": PID})
    p = Proxy("http://unused/mcp/x", worktree="/tmp")
    p.tools = [SimpleNamespace(name="decide", input_schema={"properties": {"case_id": {}, "statement": {}}})]
    assert "capture" not in p.with_client("decide", {}, "claude-code")
    p.tools = [SimpleNamespace(name="decide", input_schema={"properties": {"case_id": {}, "capture": {}}})]
    assert p.with_client("decide", {}, "claude-code")["capture"] is True


def test_원격_문으로도_같은_규칙이_돌고_업로더_도구가_원문을_받는다(tmp_path, cap_dir):
    """서버 모드의 실제 경로: 프록시(맥)가 capture 를 싣고 → 원격 문이 거절하거나 받고 → 숨은 도구로 원문이 올라간다."""
    pytest.importorskip("mcp")
    import anyio, httpx2
    from casebook.adapters.mcp_proxy import Proxy
    from casebook.adapters.mcp_server import build_http_app, mint_token
    from tests.conftest import _build_app, FakeLLM, FakeSearch
    from tests.test_mcp_remote import _lifespan
    app = _build_app(FakeLLM(), FakeSearch())
    asgi = build_http_app(app)
    wt = _git_repo(tmp_path / "w")
    tok = mint_token(app, "tester@example.test")
    capture.capture_prompt({"prompt": "원격에서도 ref 를 요구한다", "prompt_id": PID})

    async def body():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test") as hc:
            p = Proxy(f"http://test/mcp/{tok}", worktree=wt, http_client=hc)
            await p.connect()
            o = json.loads((await p.call("open_thread", {"focus": "원격 원문", "topic": "테스트"})).content[0].text)
            cid = o["case_id"]
            user = {"case_id": cid, "statement": "사람 결정", "authority": "user", "next": "다음 할 일\n\n시험이 세운 자리다.", "owner": "user"}
            res = await p.call("decide", user, client="claude-code")
            assert "needs source_refs" in res.content[0].text
            res = await p.call("decide", dict(user), client="codex-mcp-client")      # 훅 없는 호스트도 같은 규칙
            assert "needs source_refs" in res.content[0].text
            res = await p.call("add_utterance", {"text": "Codex 에서 붙인 내 말"}, client="codex-mcp-client")
            pasted = _uid(res.content[0].text)
            res = await p.call("decide", {**user, "source_refs": [pasted]}, client="codex-mcp-client")
            assert "Decision D" in res.content[0].text
            res = await p.call("add_utterance", {"text": "훅 호스트가 붙이려 한다"}, client="claude-code")
            assert "already records" in res.content[0].text
            did = _did((await p.call("decide", {**user, "source_refs": [PID]}, client="claude-code")).content[0].text)
            sent = []

            async def send():
                items = [json.loads(f.read_text()) for f in (cap_dir / "spool").glob("*.json")]
                r = await p.session.call_tool("record_utterances", {"items": items})
                sent.append(json.loads(r.content[0].text))
            await send()
            await send()
            assert sent == [{"stored": 1, "known": 0}, {"stored": 0, "known": 1}]
            d = json.loads((await p.call("inspect", {"ref": f"D{did}"})).content[0].text)
            assert d["sources"][0]["content"] == "원격에서도 ref 를 요구한다" and d["sources"][0]["origin"] == "hook"
            await p.close()
    anyio.run(lambda: _lifespan(asgi, body))


def test_Codex_훅_설치는_남의_훅을_두고_여러_번_돌려도_하나다(tmp_path):
    target = tmp_path / "hooks.json"
    other = {"hooks": [{"type": "command", "command": "python3 ~/.codex/hooks/mine.py"}]}
    target.write_text(json.dumps({"hooks": {"UserPromptSubmit": [other], "Stop": [other]}}))
    env = dict(os.environ, CODEX_HOOKS=str(target), CASEBOOK_ROOT=str(ROOT))
    for _ in range(2):
        subprocess.run(["sh", str(ROOT / "tools/hooks/install_codex_hooks.sh")], env=env, check=True,
                       capture_output=True, text=True)
    hooks = json.loads(target.read_text())["hooks"]
    assert hooks["Stop"] == [other]
    ups = hooks["UserPromptSubmit"]
    assert ups[0] == other and len(ups) == 2
    cmd = ups[1]["hooks"][0]["command"]
    assert cmd.endswith("tools/hooks/casebook_hook.py prompt codex") and ups[1]["hooks"][0]["timeout"] == 5
