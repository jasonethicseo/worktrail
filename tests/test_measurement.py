"""확장 19호 — 측정 v2. 보장할 것:
(1) SessionStart 훅이 read_log 에 session_start(case_id = 바인딩된 스레드, detail = startup reason) 를 남긴다
(2) handoff_opportunities 는 compact 가 아닌 session_start 중 스레드가 바인딩된 것만 센다
(3) review_handoff 는 사람의 판정을 그대로 저장하고 report 가 sum/answered 로 모은다 (4) 모르는 칸은 NULL
(5) 훅의 기록 실패는 주입을 막지 않는다."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from casebook.core import reads
from casebook.core.app import Casebook
from casebook.core.db import SqliteDB
from tests.conftest import FakeLLM, FakeSearch
from tests.drive import USER
from tests.test_hooks import ROOT, SCRIPT
from tests.test_threads import _git_repo


def _stack(tmp_path):
    wt = _git_repo(tmp_path / "w")
    path = str(tmp_path / "m.db"); db = SqliteDB(path)
    db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    return wt, path, db, app


def _hook(cmd: str, path: str, wt: str, stdin: dict) -> dict:
    env = dict(os.environ, CASEBOOK_DB=path, CASEBOOK_MCP_EMAIL="tester@example.test", CLAUDE_PROJECT_DIR=wt)
    p = subprocess.run([sys.executable, str(SCRIPT), cmd], input=json.dumps(stdin), capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_session_start_가_handoff_기회를_남긴다(tmp_path):
    wt, path, db, app = _stack(tmp_path)
    _hook("session-start", path, wt, {"source": "startup"})                   # 스레드 없음 → case_id NULL
    t = app.open_thread(USER, "goal", wt)["case_id"]
    _hook("session-start", path, wt, {"startup_reason": "resume"})
    _hook("post-compact", path, wt, {})
    rows = db.conn.execute("SELECT kind, case_id, detail FROM read_log ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("session_start", None, "startup"), ("session_start", t, "resume"), ("session_start", t, "compact")]
    r = reads.report(db, USER)
    assert r["session_starts"] == 3 and r["compact_restarts"] == 1 and r["handoff_opportunities"] == 1


def test_review_handoff_와_report(tmp_path):
    wt, path, db, app = _stack(tmp_path)
    t = app.open_thread(USER, "goal", wt)["case_id"]
    r1 = reads.review_handoff(db, USER, t, constraints_retained=True, decisions_retained=False, re_explained=2, note="ok")
    r2 = reads.review_handoff(db, USER, None, wrong_next_step=True)
    assert r1["constraints_retained"] == 1 and r1["decisions_retained"] == 0 and r1["re_explained"] == 2 and r1["retrieval_used"] is None
    assert r2["case_id"] is None and r2["wrong_next_step"] == 1
    rep = reads.report(db, USER)
    assert rep["handoff_reviews"] == 2
    assert rep["review"]["constraints_retained"] == {"sum": 1, "of": 1}
    assert rep["review"]["decisions_retained"] == {"sum": 0, "of": 1}
    assert rep["review"]["re_explained"] == {"sum": 2, "of": 1}
    assert rep["review"]["retrieval_used"] == {"sum": 0, "of": 0}
    with pytest.raises(ValueError, match="unknown review fields"):
        reads.review_handoff(db, USER, t, vibes=True)


def test_기록_실패해도_주입은_된다(tmp_path):
    wt, path, db, app = _stack(tmp_path)
    app.open_thread(USER, "goal", wt)
    out = _hook("session-start", path, wt, {"source": "clear"})
    assert "Current thread" in out["hookSpecificOutput"]["additionalContext"]
    out = _hook("session-start", str(tmp_path / "missing.db"), wt, {"source": "clear"})   # DB 없음 → 주입은 skip 메시지
    assert "skipped" in out["systemMessage"]
