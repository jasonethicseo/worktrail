"""확장 27호 — 원격 MCP 문. 보장할 것:
(1) 가상 worktree "chat"/"chat:*" 는 git 을 묻지 않고 identity "chat" 이다
(2) 가상 worktree 는 어느 저장소의 스레드에나 switch 로 붙는다(저장소 일치 검사 면제), 실제 worktree 는 여전히 막힌다
(3) 가상 worktree 로 resume 하면 mismatch 가 아니라 과거 관찰(anchor_history)이 온다
(4) Tracker 의 bound_worktree_of 는 실제 worktree 를 우선한다
(5) 원격 Tools: worktree 기본값은 "chat", add_evidence_file 은 거부, list_threads 는 열린 작업 전부를 함께 낸다
(6) HTTP 문: /mcp/<token> 이 사용자를 정하고, 틀린 토큰은 401, 남의 토큰으로는 남의 케이스가 안 보인다
(7) 도구 스키마에 ctx 는 나타나지 않는다(stdio 문의 계약 그대로)"""
from __future__ import annotations

import json
import subprocess

import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core import threads
from casebook.core.errors import ApiError, InputError
from tests.conftest import FakeLLM, FakeSearch, _build_app
from tests.drive import USER


def _git_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "git@github.com:acme/widgets.git"], check=True)
    return str(path)


@pytest.fixture
def app():
    return _build_app(FakeLLM(), FakeSearch())


def test_가상_worktree_identity(tmp_path):
    for wt in ("chat", "chat:claude.ai"):
        assert threads.is_virtual(wt)
        i = threads.identify(wt)
        assert i["identity"] == "chat" and i["virtual"] is True and i["worktree"] == wt
    assert not threads.is_virtual(None) and not threads.is_virtual(str(tmp_path)) and not threads.is_virtual("chatter")
    a = threads.anchor("chat")
    assert a["repo"] == "chat" and a["head"] is None and a["virtual"] is True


def test_가상_worktree_는_어느_저장소_스레드에나_붙는다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "저장소 스레드", wt)["case_id"]
    r = app.switch_thread(USER, t, "chat")
    assert r["current"] == t and r["repo"] == "github.com/acme/widgets"          # 스레드는 제 저장소에 남는다
    assert threads.repo_of_case(app.db, t)["identity"] == "github.com/acme/widgets"
    assert app.current_thread(USER, "chat")["case_id"] == t
    assert app.current_thread(USER, wt)["case_id"] == t                         # 실제 worktree 바인딩은 그대로
    # 실제 worktree 는 여전히 다른 저장소 스레드로 못 간다
    other = _git_repo(tmp_path / "other")
    subprocess.run(["git", "-C", other, "remote", "set-url", "origin", "git@github.com:acme/other.git"], check=True)
    with pytest.raises(InputError, match="belongs to"):
        app.switch_thread(USER, t, other)
    # 채팅에서 새로 연 스레드는 "chat" 저장소에
    c = app.open_thread(USER, "채팅에서 연 스레드", "chat")
    assert c["repo"] == "chat" and app.current_thread(USER, "chat")["case_id"] == c["case_id"]


def test_가상_worktree_resume_은_과거_관찰(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "s", wt)["case_id"]
    subprocess.run(["git", "-C", wt, "commit", "-q", "--allow-empty", "-m", "first"], check=True)
    app.record_checkpoint(USER, wt, "test")
    real = app.resume(USER, t, "continuity", wt)["anchor"]
    assert real["branch"] == "main" and "mismatch" not in real
    virt = app.resume(USER, t, "continuity", "chat")["anchor"]
    assert "mismatch" not in virt and virt["last_checkpoint"]["branch"] == "main"   # anchor_history 모양
    assert threads.bound_worktree_of(app.db, USER, t) == wt                         # chat 바인딩이 없을 때
    app.switch_thread(USER, t, "chat")
    assert threads.bound_worktree_of(app.db, USER, t) == wt                         # 최신은 chat 이지만 실제 worktree 우선


def test_원격_Tools(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    app.open_thread(USER, "저장소 작업", wt)
    t = Tools(app, USER, remote=True)
    assert t._wt(None) == "chat" and t._wt("/x") == "/x"
    with pytest.raises(ApiError, match="remote door"):
        t.add_evidence_file(1, str(tmp_path / "any.log"))
    out = t.list_threads()
    assert out["repo"] == "chat" and out["threads"] == []
    assert [r["identity"] for r in out["other_repositories"]] == ["github.com/acme/widgets"]
    c = t.open_thread("채팅 스레드", topic="테스트")
    assert c["repo"] == "chat"
    assert t.list_threads()["current"] == c["case_id"]
    assert "other_repositories" not in Tools(app, USER).list_threads(wt)     # stdio 문은 그대로


async def _lifespan(app, body):
    """uvicorn 이 하는 lifespan 프로토콜을 같은 이벤트 루프에서 흉내낸다 — SDK 의 세션 매니저는 run() 안에서만 요청을 받는다."""
    import anyio
    started, stopped = anyio.Event(), anyio.Event()
    send_s, recv_s = anyio.create_memory_object_stream(4)

    async def receive():
        return await recv_s.receive()

    async def send(msg):
        if msg["type"] == "lifespan.startup.complete":
            started.set()
        elif msg["type"] == "lifespan.shutdown.complete":
            stopped.set()

    async with anyio.create_task_group() as tg:
        tg.start_soon(app, {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}}, receive, send)
        await send_s.send({"type": "lifespan.startup"})
        await started.wait()
        try:
            return await body()
        finally:
            await send_s.send({"type": "lifespan.shutdown"})
            await stopped.wait()


async def _session(asgi, token, fn):
    """SDK 의 진짜 클라이언트로 붙는다 — Claude.ai·ChatGPT 가 하는 그대로(initialize → tools/*)."""
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test") as hc:
        async with streamable_http_client(f"http://test/mcp/{token}", http_client=hc) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                return await fn(s)


def _text(result) -> str:
    return result.content[0].text


def test_http_문_토큰이_사용자를_정한다():
    pytest.importorskip("mcp")
    import anyio, httpx2
    from casebook.adapters.mcp_server import build_http_app, mint_token
    app = _build_app(FakeLLM(), FakeSearch())
    asgi = build_http_app(app)

    async def body():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test") as hc:
            assert (await hc.get("/health")).json() == {"ok": True, "door": "mcp"}
            assert (await hc.post("/mcp", json={})).status_code == 401
            assert (await hc.post("/mcp/not-a-token", json={})).status_code == 401
            assert (await hc.get("/elsewhere")).status_code == 404
        tok = mint_token(app, "tester@example.test")                    # user 1 (conftest 시드)

        async def mine(s):
            tools = (await s.list_tools()).tools
            names = sorted(t.name for t in tools)
            assert "note_turn" in names and "add_evidence_file" in names and len(names) == 28   # 55호 define, 153호 find·trail
            for t in tools:                                             # (7) ctx 는 스키마에 없다
                assert "ctx" not in (t.input_schema.get("properties") or {})
            refused = _text(await s.call_tool("open_thread", {"focus": "주제 없이", "topic": ""}))
            assert refused.startswith("casebook error: topic is required")              # 빈 topic 도 누락이다
            opened = json.loads(_text(await s.call_tool("open_thread", {"focus": "채팅에서 연 스레드", "topic": "대회 준비"})))
            assert opened["repo"] == "chat" and opened["worktree"] == "chat" and opened["topic"]["name"] == "대회 준비"
            cid = opened["case_id"]
            out = _text(await s.call_tool("note_turn", {"case_id": cid, "observed": "o", "conclusion": "c", "kind": "finding", "next": "다음 할 일\n\n시험이 세운 자리다.", "owner": "user"}))
            assert "Turn 1 recorded" in out
            out = _text(await s.call_tool("add_evidence_file", {"case_id": cid, "path": "/etc/hosts"}))
            assert "remote door" in out
            lt = json.loads(_text(await s.call_tool("list_threads", {})))
            assert lt["current"] == cid and lt["repo"] == "chat" and "other_repositories" in lt
            return cid

        cid = await _session(asgi, tok, mine)

        tok2 = mint_token(app, "other@example.test")                    # 남 — 사용자가 없으면 만들어진다
        other = app.db.get_by("user", "email", "other@example.test")
        assert other is not None

        # 인계서 4절 — mint 뒤에 생긴 계정은 기본 차단이다. 토큰이 진짜여도 기록은 오가지 않는다.
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test") as hc:
            blocked = await hc.post(f"/mcp/{tok2}", json={})
            assert blocked.status_code == 403 and "not allowed on this server" in blocked.json()["error"]

        # 운영자가 켜고 동의를 적어야 비로소 쓴다 — 그때도 남의 기록은 못 본다(기존 격리 계약).
        from casebook.core import access
        access.set_state(app.db, other["id"], access.STATE_ALLOWED)
        access.record_consent(app.db, other["id"], "test-notice-v0")

        async def theirs(s):
            assert "casebook error" in _text(await s.call_tool("resume", {"case_id": cid}))
            assert json.loads(_text(await s.call_tool("list_threads", {})))["threads"] == []
        await _session(asgi, tok2, theirs)

        # Bearer 헤더로도 같은 사용자
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test",
                                      headers={"authorization": f"Bearer {tok}"}) as hc:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            async with streamable_http_client("http://test/mcp", http_client=hc) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    assert json.loads(_text(await s.call_tool("list_threads", {})))["current"] == cid

    anyio.run(lambda: _lifespan(asgi, body))
    from casebook.core import reads
    with app.db._lock:
        n = app.db.conn.execute("SELECT count(*) FROM read_log WHERE kind='mcp_unauthorized'").fetchone()[0]
    # 틀린 토큰 2회 — 실패도 측정된다. 다만 같은 출처의 연속 거절은 창(60초) 하나에 한 줄로 접힌다:
    # 전에는 요청마다 한 줄이라, 토큰 없는 누구나 공개 /mcp 로 DB 를 무제한 불릴 수 있었다.
    assert n == 1


def test_open_thread_은_topic_이_필수다(app, tmp_path):
    """확장 28호 — D11098 을 도구가 강제한다. 누락은 거절(기존 주제 목록 동봉). 확장 30호 — "미분류" 도 거절."""
    wt = _git_repo(tmp_path / "w")
    t = Tools(app, USER)
    with pytest.raises(ApiError, match=r"topic is required.*Existing topics: \(none yet\)"):
        t.open_thread("주제 없이", wt)
    with pytest.raises(ApiError, match="topic is required"):
        t.open_thread("빈 문자열도 누락", wt, topic="   ")
    assert app.db.count("case") == 1                                              # 거절이면 스레드도 안 열린다(시드 1개뿐)
    a = t.open_thread("첫 스레드", wt, topic="MCP 실효성")
    assert a["topic"]["name"] == "MCP 실효성"
    b = t.open_thread("같은 주제 둘째", wt, topic="MCP 실효성")                        # 이름으로 찾는다 — 주제가 늘지 않는다
    assert b["topic"]["topic_id"] == a["topic"]["topic_id"]
    with pytest.raises(ApiError, match=r"\"미분류\" is not accepted"):              # 30호 — 도망길도 닫혔다
        t.open_thread("일부러 미분류", wt, topic="미분류")
    # 확장 118호 — 처음 보는 이름은 한 번 거절한다. 위의 "MCP 실효성" 둘이 그대로 통과한 것은
    # 첫 주제일 때(목록이 비었을 때)와 이미 있는 이름일 때라서다.
    with pytest.raises(ApiError, match=r"is a new topic.*Existing topics: MCP 실효성"):
        t.open_thread("비슷한 이름으로 새로 파기", wt, topic="MCP 실효성 검증")
    assert app.db.count("case") == 3                                              # 시드 + a + b
    topics = app.list_topics(USER)
    assert [(x["name"], sorted(x["case_ids"])) for x in topics] == [("MCP 실효성", sorted([a["case_id"], b["case_id"]]))]
    with pytest.raises(ApiError, match="Existing topics: MCP 실효성"):              # 거절 문구가 기존 주제를 보여 준다
        t.open_thread("또 주제 없이", wt)


# ── 확장 29호 — 얇은 클라이언트: git 사실 전달 · hook 도구 · 프록시 ─────────────────────────────
def _commit(wt: str, msg: str) -> None:
    subprocess.run(["git", "-C", wt, "commit", "-q", "--allow-empty", "-m", msg], check=True,
                   env={**__import__("os").environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"})


def test_local_facts_와_provided(tmp_path):
    wt = _git_repo(tmp_path / "w"); _commit(wt, "first")
    f = threads.local_facts(wt)
    assert f["identity"] == "github.com/acme/widgets" and f["branch"] == "main" and f["dirty"] == 0
    assert f["commit"]["message"] == "first" and len(f["commit"]["head"]) == 40 and f["commit"]["committed_at"] > 0
    # provided 로 걸면 git 을 안 부른다 — 존재하지 않는 경로로도 같은 답
    ghost = dict(f, worktree="/nowhere/ghost")
    with threads.provided(ghost):
        i = threads.identify("/nowhere/ghost"); a = threads.anchor("/nowhere/ghost")
        assert i["identity"] == "github.com/acme/widgets" and i["provided"] is True
        assert a["head"] == f["head"] and a["provided"] is True
        assert threads.identify(wt)["provided" if False else "identity"] == "github.com/acme/widgets"   # 다른 worktree 는 git 그대로
    assert threads.identify("/nowhere/ghost")["identity"].startswith("path:")                       # 블록 밖은 원래대로


def test_원격_도구가_facts_로_스레드를_열고_커밋을_붙인다(app, tmp_path):
    """서버가 볼 수 없는 경로("/srv/nowhere")도 프록시가 실어 보낸 사실로 identity·anchor·commit 이 선다."""
    wt = _git_repo(tmp_path / "w"); _commit(wt, "init")
    facts = dict(threads.local_facts(wt), worktree="/srv/nowhere")
    t = Tools(app, USER, remote=True)
    o = t.open_thread("원격에서 연 저장소 스레드", topic="테스트", facts=facts)
    assert o["repo"] == "github.com/acme/widgets" and o["worktree"] == "/srv/nowhere"
    # 커밋은 스레드를 연 뒤에 — 바인딩보다 오래된 커밋은 붙지 않는다(확장 17호). 사실도 다시 읽는다.
    _commit(wt, "first")
    facts = dict(threads.local_facts(wt), worktree="/srv/nowhere")
    r = t.resume(o["case_id"], facts=facts)
    assert r["anchor"]["provided"] is True and r["anchor"]["branch"] == "main" and "mismatch" not in r["anchor"]
    h = t.hook("post-commit", facts)                                    # HEAD 커밋을 스레드에 (멱등)
    assert h["systemMessage"].startswith("casebook: commit ") and f"#{o['case_id']}" in h["systemMessage"]
    assert t.hook("post-commit", facts) == {"suppressOutput": True}
    c = t.hook("checkpoint", facts, why="test")
    assert "checkpoint (test)" in c["systemMessage"]
    hist = threads.anchor_history(app.db, o["case_id"])
    assert hist["commits"][0]["message"] == "first" and hist["last_checkpoint"]["branch"] == "main"
    s = t.hook("session-start", facts, reason="startup")
    ctx = s["hookSpecificOutput"]["additionalContext"]
    assert f"Current thread (bound to this worktree): #{o['case_id']}" in ctx and "anchor: main @" in ctx
    lt = t.list_threads(facts=facts)
    assert lt["repo"] == "github.com/acme/widgets" and lt["current"] == o["case_id"]
    with pytest.raises(ApiError, match="hook needs facts"):
        t.hook("post-commit", {})


def test_원격_문은_git_사실을_지어내지_않는다(app, tmp_path):
    """확장 48호 (#492·#493) — facts 없이 경로만 오면: 이미 묶인 경로는 바인딩의 정체성으로, 처음 보는 경로는 거절.
    어느 쪽이든 서버는 git 을 돌리지 않고 path: 저장소 행을 만들지 않는다."""
    wt = _git_repo(tmp_path / "w"); _commit(wt, "init")
    facts = dict(threads.local_facts(wt), worktree="/Users/me/work")          # 프록시가 실어 보낸 것처럼
    t = Tools(app, USER, remote=True)
    o = t.open_thread("프록시로 연 스레드", topic="테스트", facts=facts)
    assert o["repo"] == "github.com/acme/widgets"
    # 같은 경로를 facts 없이(커넥터처럼) — 바인딩이 있으니 같은 저장소로 해소된다
    lt = t.list_threads(worktree="/Users/me/work")
    assert lt["repo"] == "github.com/acme/widgets" and lt["current"] == o["case_id"]
    r = t.resume(o["case_id"], worktree="/Users/me/work")
    assert r["anchor"]["repo"] == "github.com/acme/widgets" and r["anchor"]["from_binding"] is True
    assert r["anchor"]["head"] is None and "mismatch" not in r["anchor"]  # live git 은 없다 — 지어내지 않는다
    assert t.switch_thread(o["case_id"], worktree="/Users/me/work")["repo"] == "github.com/acme/widgets"
    # 처음 보는 경로는 거절 — 종전에는 path:/Users/nowhere 저장소가 생겼다
    with pytest.raises(ApiError, match="without git facts"):
        t.list_threads(worktree="/Users/nowhere")
    with pytest.raises(ApiError, match="without git facts"):
        t.open_thread("길 잃은 스레드", topic="테스트", worktree="/Users/nowhere")
    assert app.db.get_by("repo", "identity", "path:/Users/nowhere") is None
    assert app.db.get_by("repo", "identity", "path:/Users/me/work") is None
    # 가상 작업 공간과 stdio 문은 종전 그대로
    assert t.list_threads()["repo"] == "chat"
    assert Tools(app, USER).list_threads(wt)["repo"] == "github.com/acme/widgets"


def test_프록시는_facts_를_채우고_hook_을_숨긴다(tmp_path):
    pytest.importorskip("mcp")
    import anyio, httpx2
    from casebook.adapters.mcp_proxy import Proxy, host_schema
    from casebook.adapters.mcp_server import build_http_app, mint_token
    app = _build_app(FakeLLM(), FakeSearch())
    asgi = build_http_app(app)
    wt = _git_repo(tmp_path / "w"); _commit(wt, "first")
    tok = mint_token(app, "tester@example.test")

    async def body():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test") as hc:
            p = Proxy(f"http://test/mcp/{tok}", worktree=wt, http_client=hc)
            await p.connect()
            from casebook.adapters.mcp_server import INSTRUCTIONS
            assert p.instructions == INSTRUCTIONS
            names = [t.name for t in p.host_tools()]
            assert "hook" not in names and "import_prior" not in names and len(names) == 26   # 호스트가 보는 것은 26개 (hook·import_prior 는 스크립트 전용; 55호 define)
            for t in p.host_tools():
                assert "facts" not in t.input_schema.get("properties", {})
            assert "facts" in {t.name: t for t in p.tools}["open_thread"].input_schema["properties"]   # 원격에는 있다
            res = await p.call("open_thread", {"focus": "프록시로 연 스레드", "topic": "테스트"})
            o = json.loads(res.content[0].text)
            assert o["repo"] == "github.com/acme/widgets" and o["worktree"] == wt                 # 프록시가 facts 를 채웠다
            res = await p.call("resume", {"case_id": o["case_id"]})
            assert json.loads(res.content[0].text)["anchor"]["provided"] is True
            res = await p.call("note_turn", {"case_id": o["case_id"], "observed": "o", "conclusion": "c", "kind": "finding", "next": "다음 할 일\n\n시험이 세운 자리다.", "owner": "user"})   # facts 없는 도구는 그대로
            assert "Turn 1 recorded" in res.content[0].text
            await p.close()
            # 끊긴 뒤 호출 → 다시 붙어서 성공
            res = await p.call("list_threads", {})
            assert json.loads(res.content[0].text)["current"] == o["case_id"]
            await p.close()
    anyio.run(lambda: _lifespan(asgi, body))
    assert host_schema(type("T", (), {"input_schema": {"properties": {"a": {}, "facts": {}}, "required": ["a", "facts"]}})()) == \
        {"properties": {"a": {}}, "required": ["a"]}


def test_프록시는_원격_URL_이_없으면_로컬_문으로_폴백한다(monkeypatch, tmp_path):
    pytest.importorskip("mcp")
    from casebook.adapters import mcp_proxy, mcp_server
    monkeypatch.delenv("CASEBOOK_REMOTE_URL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))                       # ~/.casebook/remote-url 없음
    assert mcp_proxy.remote_url() is None
    called = []
    monkeypatch.setattr(mcp_server, "main", lambda argv=None: called.append(argv))
    mcp_proxy.main()
    assert called == [[]]                                            # 로컬 stdio 문(cmd 기본 stdio)
    (tmp_path / ".casebook").mkdir(); (tmp_path / ".casebook" / "remote-url").write_text("https://x.test/mcp/t\n")
    assert mcp_proxy.remote_url() == "https://x.test/mcp/t"          # 파일 하나가 스위치
    monkeypatch.setenv("CASEBOOK_REMOTE_URL", "https://env.test/mcp/t")
    assert mcp_proxy.remote_url() == "https://env.test/mcp/t"        # 환경변수가 우선


# ── 확장 103호 — 재접속은 연결을 소유한 태스크가 한다 ────────────────────────
#
# 실측 2026-09-14: lowlevel Server 는 도구 호출마다 새 태스크를 연다. 그 태스크가 재접속하려고
# serve() 에서 열린 AsyncExitStack 을 닫으면 streamable_http_client 의 anyio 태스크그룹이
# "Attempted to exit cancel scope in a different task than it was entered in" 으로 터지고,
# 호출 태스크의 취소 범위가 망가져 **응답 없이** 죽는다. 호스트는 영원히 기다린다
# (add_evidence 가 1317초 running 뒤 stdio 끊김). 아래 둘이 그 자리를 붙잡는다.
def _remote_proxy_case(tmp_path, body_with):
    """serve() 와 같은 모양으로 프록시를 세우고 body_with(proxy) 를 돌린다."""
    pytest.importorskip("mcp")
    import anyio, httpx2
    from casebook.adapters.mcp_proxy import Proxy
    from casebook.adapters.mcp_server import build_http_app, mint_token
    app = _build_app(FakeLLM(), FakeSearch())
    asgi = build_http_app(app)
    wt = _git_repo(tmp_path / "w"); _commit(wt, "first")
    tok = mint_token(app, "tester@example.test")

    async def body():
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=asgi), base_url="http://test") as hc:
            p = Proxy(f"http://test/mcp/{tok}", worktree=wt, http_client=hc)
            async with anyio.create_task_group() as tg:
                await tg.start(p.own)          # 연결은 이 태스크가 소유한다 (serve() 와 같다)
                try:
                    await body_with(p)
                finally:
                    tg.cancel_scope.cancel()
    anyio.run(lambda: _lifespan(asgi, body))


def test_호출_태스크에서_재접속해도_매달리지_않는다(tmp_path):
    """도구 호출 태스크에서 첫 시도가 실패하면(만료된 토큰이 하던 일) 다시 붙어 성공해야 한다.
    확장 103호 전에는 여기서 취소 범위가 터지고 호스트가 답을 영영 못 받았다."""
    import anyio
    done = []

    async def body_with(p):
        real, n = p.session.call_tool, {"i": 0}

        async def flaky(name, args):                 # 첫 호출만 401 처럼 실패한다
            n["i"] += 1
            if n["i"] == 1:
                raise RuntimeError("simulated 401 — 만료된 토큰")
            return await real(name, args)
        p.session.call_tool = flaky
        stale = p.session

        async def handler():                         # lowlevel Server 가 호출마다 여는 자리
            with anyio.fail_after(20):               # 매달리면 여기서 잡힌다
                res = await p.call("list_threads", {})
            assert not getattr(res, "is_error", False), res.content[0].text
            done.append(json.loads(res.content[0].text))

        async with anyio.create_task_group() as tg:
            tg.start_soon(handler)
        assert p.session is not stale                # 실제로 다시 붙었다
    _remote_proxy_case(tmp_path, body_with)
    assert done and "threads" in done[0]


def test_호스트는_반드시_답을_받는다(tmp_path, monkeypatch):
    """서버가 영영 답하지 않아도 도구 호출은 오류로라도 돌아온다. SDK 의 read_timeout 은 요청을
    **쓴 뒤에야** 무장하므로(jsonrpc_dispatcher), 쓰는 쪽이 막히면 아무 시계도 돌지 않는다."""
    import anyio
    from casebook.adapters import mcp_proxy
    monkeypatch.setattr(mcp_proxy, "TOOL_TIMEOUT", 0.3)

    async def body_with(p):
        async def hang(name, args):
            await anyio.sleep_forever()
        p.session.call_tool = hang

        async def no_reconnect(stale):               # 다시 붙는 것도 안 되는 최악
            raise RuntimeError("재접속도 안 된다")
        p._reconnect = no_reconnect

        with anyio.fail_after(10):                   # 답이 없으면 여기서 잡힌다
            res = await p.call("list_threads", {})
        assert res.is_error and "casebook remote error" in res.content[0].text
    _remote_proxy_case(tmp_path, body_with)


def test_붙다_실패해도_다음_기회에_회복한다(tmp_path):
    """connect() 가 도중에 실패하면 반쯤 연 것을 닫아야 한다. 두고 가면 그 태스크그룹이 살아남아
    **그 다음** 재접속이 매달린다 — 실측 2026-09-14: 진짜 서버에 붙여 토큰을 못 쓰게 했다가
    되돌리자 그 다음 호출이 60초 무응답이었다. 붙기가 실패하는 것은 흔하다(만료·회수·서버 재시작).
    한 번 실패한 뒤 다시는 못 붙는 것이 진짜 고장이다."""
    import anyio, httpx2

    async def body_with(p):
        good = p.http_client
        p.http_client = httpx2.AsyncClient(                # 무엇을 물어도 401 인 서버
            transport=httpx2.MockTransport(lambda _r: httpx2.Response(401, json={"error": "nope"})),
            base_url="http://test")

        async def broken(name, args):
            raise RuntimeError("simulated 401 — 만료된 토큰")
        p.session.call_tool = broken

        with anyio.fail_after(20):
            res = await p.call("list_threads", {})         # 재접속도 실패한다
        assert res.is_error, res.content[0].text

        await p.http_client.aclose()
        p.http_client = good                               # 다시 붙을 수 있게 됐다
        with anyio.fail_after(20):                         # 매달리면 여기서 잡힌다
            res = await p.call("list_threads", {})
        assert not res.is_error, res.content[0].text       # 스스로 회복한다
    _remote_proxy_case(tmp_path, body_with)


def test_재접속이_매달려도_답은_온다(tmp_path, monkeypatch):
    """다시 붙는 일 자체가 안 돌아오면(망이 멎었다·서버가 응답만 안 한다) 그때도 답이 가야 한다.
    벽시계를 호출 한 번이 아니라 시도·재접속·재시도 **전체**에 두는 이유다."""
    import anyio
    from casebook.adapters import mcp_proxy
    monkeypatch.setattr(mcp_proxy, "CALL_DEADLINE", 0.5)

    async def body_with(p):
        async def broken(name, args):
            raise RuntimeError("simulated 401")
        p.session.call_tool = broken

        async def deaf(*_a):                          # 소유 태스크가 영영 안 돌아온다
            await anyio.sleep_forever()
        p.connect = deaf

        with anyio.fail_after(10):                    # 매달리면 여기서 잡힌다
            res = await p.call("list_threads", {})
        assert res.is_error and "after reconnect" in res.content[0].text
    _remote_proxy_case(tmp_path, body_with)


# ── 확장 118호 — 기록이 조용히 흩어지는 두 자리 ──────────────────────────────

def test_새_주제는_한_번_거절하고_목록을_보인다(app, tmp_path):
    """종전에는 빈 값과 "미분류" 만 걸러서, 그럴듯한 새 이름을 대면 목록을 한 번도 안 보고
    주제가 하나 더 생겼다. 실측: "원격 MCP 문"(7스레드)과 "원격 MCP 연결"(2스레드)이 같은 일로 갈렸다."""
    wt = _git_repo(tmp_path / "w")
    t = Tools(app, USER)
    first = t.open_thread("첫 주제는 지난다", wt, topic="원격 MCP 문")      # 목록이 비면 거르지 않는다
    assert first["topic"]["name"] == "원격 MCP 문"

    with pytest.raises(ApiError, match=r"is a new topic.*new_topic=true"):
        t.open_thread("같은 일인데 새 이름", wt, topic="원격 MCP 연결")
    assert len(app.list_topics(USER)) == 1, "거절했는데 주제가 생겼다"

    # 그러고도 정말 새 일이면 대놓고 만든다 — 막는 것이 아니라 보게 하는 것이 목적이다
    made = t.open_thread("이건 진짜 다른 일", wt, topic="영어권 지원", new_topic=True)
    assert made["topic"]["name"] == "영어권 지원"
    assert len(app.list_topics(USER)) == 2


def test_띄어쓰기만_다른_이름은_같은_주제로_본다(app, tmp_path):
    """"기록 규칙" 과 "기록규칙" 이 두 주제가 되면 그 둘을 나중에 사람이 합쳐야 한다."""
    wt = _git_repo(tmp_path / "w")
    t = Tools(app, USER)
    a = t.open_thread("원본", wt, topic="기록 규칙")
    b = t.open_thread("띄어쓰기 뺀 이름", wt, topic="기록규칙")             # 거절되지 않고 같은 주제로 간다
    assert b["topic"]["topic_id"] == a["topic"]["topic_id"]
    assert len(app.list_topics(USER)) == 1


def test_스크래치_작업공간은_저장소가_아니라_대화_맥락이다(app, tmp_path):
    """데스크톱 앱이 프로젝트 없이 띄우면 cwd 가 임시 폴더다. 그것을 저장소로 삼으면 세션마다
    새 저장소가 서고 거기 연 스레드가 다음 세션에서 사라진다 — 이어짐이 제품의 전부인데."""
    scratch = (tmp_path / "Library" / "Application Support" / "Claude" / "scratch-workspaces"
               / "u1" / "u2" / "scratch-2026-09-13-e2cfb4")
    scratch.mkdir(parents=True)
    assert threads.is_scratch(str(scratch))
    assert not threads.is_scratch(str(tmp_path / "myproject"))

    ident = threads.identify(str(scratch))
    assert ident["identity"] == threads.VIRTUAL_IDENTITY
    assert ident["worktree"] == threads.VIRTUAL_IDENTITY, "경로가 남으면 세션마다 바인딩이 갈린다"

    # 날짜가 다른 두 세션이 같은 곳에 모인다
    later = str(scratch.parent / "scratch-2026-09-14-99aaaa")
    t = Tools(app, USER)
    a = t.open_thread("어제 세션", str(scratch), topic="기록 규칙")
    b = t.open_thread("오늘 세션", later, topic="기록 규칙")
    for cid in (a["case_id"], b["case_id"]):
        assert threads.repo_of_case(app.db, cid)["identity"] == threads.VIRTUAL_IDENTITY
    assert [r["identity"] for r in app.overview(USER)] == [threads.VIRTUAL_IDENTITY]


def test_스크래치에서_온_git_사실은_버린다(app, tmp_path):
    """프록시는 사실대로 보낸다 — 거르는 것은 서버 몫이다. 그 사실을 받으면 path: 저장소가 선다."""
    scratch = str(tmp_path / "Library" / "Application Support" / "Claude"
                  / "scratch-workspaces" / "u" / "v" / "scratch-2026-09-15-abc123")
    t = Tools(app, USER)
    facts = {"worktree": scratch, "identity": f"path:{scratch}", "hint": scratch, "aliases": []}
    assert t._wt(None, facts) == threads.VIRTUAL_IDENTITY
    assert t._facts(None, facts) is None


def test_스레드가_많아도_목록은_한도_안이고_정리할_것은_보인다(app, tmp_path):
    """확장 151호 — 스레드가 90개를 넘자 list_threads 가 56,958자가 되어 도구 한도를 넘었다(실측 2026-09-21).
    초점 전문과 결과 전문을 스레드마다 실었기 때문이다. 이 시험은 증상을 본다: 닫힌 스레드 80개(긴 초점·긴 결과)와
    열린 스레드 5개에서 목록이 몇 천 자 안에 든다. 결과 없이 화면에서 닫힌 것(needs_result)은 개수로 보이고,
    include_closed=true 로 부르면 그 스레드를 찾을 수 있어야 한다."""
    wt = _git_repo(tmp_path / "w")
    t = Tools(app, USER)
    body = "\n\n" + "초점의 긴 본문이다. " * 60
    for i in range(80):
        c = t.open_thread(f"닫힌 일 {i}{body}", worktree=wt, topic="테스트")
        t.close_thread(c["case_id"], f"끝냈다 {i}\n\n" + "결과의 긴 본문이다. " * 40)
    silent = t.open_thread(f"화면에서 결과 없이 닫힌 일{body}", worktree=wt, topic="테스트")["case_id"]
    app.set_status(USER, silent, "resolved")
    opened = [t.open_thread(f"열린 일 {i}{body}", worktree=wt, topic="테스트")["case_id"] for i in range(5)]
    t.declare(opened[-1], "next", "배포를 승인한다\n\n긴 설명", owner="user")

    out = t.list_threads(wt)
    size = len(json.dumps(out, ensure_ascii=False))
    assert size < 6000, f"목록이 {size}자다 — 한도 안에 들어야 한다"
    rows = {r["case_id"]: r for r in out["threads"]}
    assert all(rows[c]["state"] == "open" for c in opened), "열린 스레드가 빠졌다"
    assert rows[opened[-1]]["next"] == "배포를 승인한다" and rows[opened[-1]]["owner"] == "user"
    assert rows[opened[0]]["title"] == "열린 일 0", "제목은 초점의 첫 줄이어야 한다"
    assert out["closed"] == {"count": 81, "needs_result": 1, "listed": False}, "정리할 스레드의 개수가 안 보인다"
    assert len(out["threads"]) == 5, "닫힌 스레드가 기본으로 딸려 나온다"

    everything = t.list_threads(wt, include_closed=True)
    assert len(everything["threads"]) == 86 and everything["closed"]["listed"] is True
    assert {r["case_id"]: r for r in everything["threads"]}[silent].get("needs_result") is True, "정리할 스레드를 찾을 길이 없다"
    one = next(r for r in everything["threads"] if r["title"] == "닫힌 일 7")
    assert one["result"] == "끝냈다 7", "닫힌 스레드는 결과 첫 줄만 싣는다"
