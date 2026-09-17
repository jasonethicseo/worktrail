"""확장 14호 — Claude Code 훅. 스크립트가 (1) 훅 JSON 모양을 내고 (2) 열린 케이스를 마지막 활동 순으로 싣고
(3) 마지막 resume 를 활성 케이스로 알고 (4) 주입 본문이 상한(3,000자) 안이며 (5) 어떤 실패에도 exit 0 이다.
서브프로세스로 실제 명령을 돌린다 — settings.json 의 command 가 그대로 쓰는 진입점이기 때문."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

import pytest

from casebook.adapters.mcp_server import Tools
from tests.conftest import _build_app, FakeLLM, FakeSearch, PLACEHOLDER_INPUT
from tests.drive import USER

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "hooks" / "casebook_hook.py"
CAP = 3000


def _run(cmd: str, db_path: str, email: str = "tester@example.test", worktree: str | None = None) -> dict:
    env = dict(os.environ, CASEBOOK_DB=db_path, CASEBOOK_MCP_EMAIL=email)
    env.pop("CLAUDE_PROJECT_DIR", None)
    env.pop("CASEBOOK_WORKTREE", None)
    if worktree:
        env["CLAUDE_PROJECT_DIR"] = worktree
    p = subprocess.run([sys.executable, str(SCRIPT), cmd], input="{}", capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def _seed(tmp_path):
    """파일 DB 에 케이스 둘 + 한 케이스에 턴, 그 케이스를 resume."""
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    path = str(tmp_path / "hook.db")
    db = SqliteDB(path)
    db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    quiet = app.create_case(USER, "quiet case")["case_id"]
    busy = app.create_case(USER, "busy case")["case_id"]
    app.submit_turn(USER, busy, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    Tools(app, USER).resume(busy)
    return path, quiet, busy


def test_session_start_는_열린_케이스와_활성_케이스를_싣는다(tmp_path):
    path, quiet, busy = _seed(tmp_path)
    out = _run("session-start", path)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert ctx.index(f"#{busy} · busy case") < ctx.index(f"#{quiet} · quiet case")   # 마지막 활동 순
    assert "1 turns · last finalized" in ctx and f"Last resumed: #{busy}" in ctx
    assert "resume(case_id)" in ctx and "add_evidence" in ctx                          # 규칙 한 줄
    assert len(ctx) <= CAP


def test_session_start_cross_repo_open_threads_and_next_age(tmp_path):
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    path, _, _ = _seed(tmp_path)
    db = SqliteDB(path)
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    here = str(tmp_path / "here")
    away = str(tmp_path / "away")
    cur = app.open_thread(USER, "here focus", here)["case_id"]
    old_clock = db.now_ms
    db.now_ms = lambda: old_clock() - 2 * 86400_000
    remote = app.open_thread(USER, "remote focus\nsecond line", away)["case_id"]
    app.declare(USER, remote, "next", "run smoke", owner="user")
    db.now_ms = old_clock
    missing = app.open_thread(USER, "no next yet", away)["case_id"]
    closed = app.open_thread(USER, "closed secret", away)["case_id"]
    app.close_thread(USER, closed)
    archived = app.open_thread(USER, "archived secret", away)["case_id"]
    app.set_status(USER, archived, "archived")
    other_uid = db.add("user", {"name": "other", "email": "other@test", "password": "x"})["id"]
    app.open_thread(other_uid, "other user secret", away)
    ctx = _run("session-start", path, worktree=here)["hookSpecificOutput"]["additionalContext"]
    assert f"Current thread (bound to this worktree): #{cur}" in ctx
    cross = ctx.split("Open threads in other repositories", 1)[1]
    assert f"path:{away} · #{remote} · remote focus · next: 2d ago" in cross      # 확장 52호 — 첫 줄이 제목, 둘째 줄은 본문
    assert f"#{missing} · no next yet · next: not declared" in cross
    assert "here focus" not in cross
    assert "closed secret" not in ctx and "archived secret" not in ctx and "other user secret" not in ctx
    # 현재 저장소에 스레드가 없어도 교차 저장소 목록은 나온다.
    empty = _run("session-start", path, worktree=str(tmp_path / "empty"))["hookSpecificOutput"]["additionalContext"]
    assert "Open threads in other repositories" in empty and f"#{remote}" in empty


def test_session_start_cross_repo_cap_keeps_rules_and_complete_lines(tmp_path):
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    path, _, _ = _seed(tmp_path)
    db = SqliteDB(path)
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    here = str(tmp_path / "here")
    app.open_thread(USER, "current focus", here)
    for i in range(90):
        app.open_thread(USER, f"{i}: 긴 제목\n\n" + "긴 문장" * 80, str(tmp_path / "away"))   # 확장 52호 — 제목 + 본문
    ctx = _run("session-start", path, worktree=here)["hookSpecificOutput"]["additionalContext"]
    assert len(ctx) <= 10_000
    assert "current focus" in ctx and "more open threads omitted" in ctx
    assert ctx.endswith("Nothing is saved for you at compaction — record as you go.")
    remote_lines = [line for line in ctx.splitlines() if " · next: " in line]
    assert remote_lines and all(line.endswith("next: not declared") for line in remote_lines)


def test_post_compact_는_활성_케이스의_마지막_기록_시점을_말한다(tmp_path):
    path, _quiet, busy = _seed(tmp_path)
    out = _run("post-compact", path)
    ctx = out["hookSpecificOutput"]["additionalContext"]
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"   # SessionStart(compact) 로 붙는다
    assert f"#{busy} · busy case" in ctx and "turn 1 at" in ctx and "note_turn" in ctx
    assert len(ctx) <= CAP


@pytest.mark.parametrize("status", ["resolved", "archived"])
def test_post_compact_closed_last_resume_is_not_active(tmp_path, status):
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    path, quiet, busy = _seed(tmp_path)
    db = SqliteDB(path)
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    t = Tools(app, USER)
    t.resume(quiet)
    t.resume(busy)
    app.set_status(USER, busy, status)
    ctx = _run("post-compact", path, worktree=str(tmp_path / "empty"))["hookSpecificOutput"]["additionalContext"]
    assert "No thread is bound here" in ctx and "Active thread" not in ctx
    assert f"#{busy}" not in ctx and f"#{quiet}" not in ctx  # 과거 open resume로도 재탐색하지 않는다.
    assert "note_turn" in ctx and "resume(case_id)" in ctx


def test_post_compact_bound_thread_wins_over_closed_resume(tmp_path):
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    path, _, busy = _seed(tmp_path)
    db = SqliteDB(path)
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    wt = str(tmp_path / "bound")
    bound = app.open_thread(USER, "bound work", wt)["case_id"]
    app.declare(USER, bound, "next", "continue bound work", owner="user")
    Tools(app, USER).resume(busy)
    app.set_status(USER, busy, "resolved")
    ctx = _run("post-compact", path, worktree=wt)["hookSpecificOutput"]["additionalContext"]
    assert f"Active thread: #{bound} · bound work" in ctx
    assert "next:  continue bound work" in ctx and f"#{busy}" not in ctx


def test_post_compact_without_resume_has_no_active_thread(tmp_path):
    from casebook.core.db import SqliteDB
    db_path = str(tmp_path / "empty.db")
    db = SqliteDB(db_path)
    db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    ctx = _run("post-compact", db_path, worktree=str(tmp_path / "empty"))["hookSpecificOutput"]["additionalContext"]
    assert "No thread is bound here" in ctx and "Active thread" not in ctx


def test_실패해도_세션을_막지_않는다(tmp_path):
    # 열 수 없는 DB(폴더)로 실패를 만든다. 없는 DB 는 이제 실패가 아니다 — 로컬 첫 세션처럼 사용자를 만든다(#541).
    out = _run("session-start", str(tmp_path), email="nobody@example.test")
    assert "skipped" in out["systemMessage"] and "hookSpecificOutput" not in out
    out = _run("bogus", str(tmp_path / "missing.db"))
    assert "skipped" in out["systemMessage"]


def test_session_start_스레드_경로는_현재_스레드의_작은_resume_와_repo_state(tmp_path):
    """확장 18호 — current thread(focus·open·next·anchor) + repo-scope durable state + 열린 스레드. closed 는 0개."""
    from tests.test_threads import _git_repo
    from tests.test_git_changes import _commit
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    wt = _git_repo(tmp_path / "w"); _commit(wt, "init")
    path = str(tmp_path / "hook.db"); db = SqliteDB(path)
    db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    done = app.open_thread(USER, "finished work", wt)["case_id"]; app.close_thread(USER, done)
    other = app.open_thread(USER, "parallel work", wt)["case_id"]
    cur = app.open_thread(USER, "current work", wt)["case_id"]
    app.constrain(USER, other, "never touch the schema", authority="user", scope="repo")
    app.declare(USER, cur, "open", "is the CSP the blocker?"); app.declare(USER, cur, "next", "1. run smoke  2. commit", owner="user")
    env = dict(os.environ, CASEBOOK_DB=path, CASEBOOK_MCP_EMAIL="tester@example.test", CLAUDE_PROJECT_DIR=wt)
    p = subprocess.run([sys.executable, str(SCRIPT), "session-start"], input="{}", capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, p.stderr
    ctx = json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]
    assert f"Current thread (bound to this worktree): #{cur} · current work" in ctx
    assert "open:  is the CSP the blocker?" in ctx and "next:  1. run smoke  2. commit" in ctx
    assert "anchor: main @" in ctx and "no checkpoint yet" in ctx
    assert "Repository durable state" in ctx and "never touch the schema (user)" in ctx
    assert f"#{other} · parallel work" in ctx and "finished work" not in ctx          # closed 는 주입하지 않는다
    assert len(ctx) <= CAP


def test_원격_모드_훅은_sqlite_를_열지_않고_원격_hook_도구를_부른다(tmp_path):
    """확장 29호 — CASEBOOK_REMOTE_URL 이 있으면 스크립트는 git 사실만 읽어 원격 문으로 보낸다. 로컬 DB 경로는 일부러 없는 곳."""
    pytest.importorskip("mcp"); pytest.importorskip("uvicorn")
    import socket, threading, time
    import uvicorn
    from tests.test_threads import _git_repo
    from tests.test_git_changes import _commit
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    from casebook.adapters.mcp_server import build_http_app, mint_token
    wt = _git_repo(tmp_path / "w"); _commit(wt, "init")
    db = SqliteDB(str(tmp_path / "server.db"))
    db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    cur = app.open_thread(USER, "server-side thread", wt)["case_id"]      # 서버가 같은 기계라 wt 로 열 수 있다
    _commit(wt, "after binding")                                          # 바인딩보다 오래된 커밋은 붙지 않는다(17호) — 연 뒤에 커밋
    tok = mint_token(app, "tester@example.test")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_http_app(app), host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started
    try:
        env = dict(os.environ, CASEBOOK_REMOTE_URL=f"http://127.0.0.1:{port}/mcp/{tok}",
                   CASEBOOK_DB=str(tmp_path / "does-not-exist" / "x.db"), CASEBOOK_MCP_EMAIL="nobody@example.test",
                   CLAUDE_PROJECT_DIR=wt)
        env.pop("CASEBOOK_WORKTREE", None)
        def run(cmd, *extra):
            p = subprocess.run([sys.executable, str(SCRIPT), cmd, *extra], input="{}", capture_output=True, text=True, env=env, cwd=ROOT)
            assert p.returncode == 0, p.stderr
            return json.loads(p.stdout)
        out = run("post-commit")
        assert out["systemMessage"].startswith("casebook: commit ") and f"#{cur}" in out["systemMessage"]
        assert run("post-commit") == {"suppressOutput": True}                                   # 멱등
        out = run("checkpoint", "test")
        assert "checkpoint (test)" in out["systemMessage"]
        ctx = run("session-start")["hookSpecificOutput"]["additionalContext"]
        assert f"Current thread (bound to this worktree): #{cur}" in ctx and "anchor: main @" in ctx
        assert "last checkpoint" in ctx
        ctx = run("post-compact")["hookSpecificOutput"]["additionalContext"]
        assert "context was just compacted" in ctx and f"Active thread: #{cur}" in ctx
        with db._lock:
            kinds = [r["kind"] for r in db.conn.execute("SELECT kind FROM change WHERE case_id=? ORDER BY id", (cur,))]
            logged = db.conn.execute("SELECT count(*) FROM read_log WHERE kind='session_start'").fetchone()[0]
        assert kinds == ["commit", "checkpoint"] and logged == 2
        # 원격이 죽으면 훅은 세션을 막지 않는다
        env["CASEBOOK_REMOTE_URL"] = f"http://127.0.0.1:{port}/mcp/wrong-token"
        assert run("post-commit")["systemMessage"].startswith("casebook hook (post-commit) skipped:")
    finally:
        server.should_exit = True; th.join(timeout=5)
