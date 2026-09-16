"""확장 17호 — git 자동 연결. 보장할 것:
(1) record_commit 은 HEAD 를 현재 스레드의 change(kind=commit) 로 남기고 같은 hash 는 다시 안 남긴다(멱등 — 훅이 매번 불러도 된다)
(2) 바인딩된 스레드가 없으면 아무것도 안 한다 (3) checkpoint 는 branch/HEAD/dirty 를 남기고 pause/switch 가 자동으로 남긴다
(4) resume 의 anchor 는 live git 이 정본이고 마지막 checkpoint 와 비교해 changed_since_checkpoint 를 낸다
(5) change 는 evidence 가 아니다 — evidence 테이블·원장에 아무것도 안 남긴다
(6) 훅 진입점(post-commit · checkpoint)이 서브프로세스로 exit 0."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from tests.drive import USER
from tests.test_hooks import ROOT, SCRIPT
from tests.test_threads import _git_repo

GIT_ID = ["-c", "user.email=t@t", "-c", "user.name=t"]


def _commit(wt: str, msg: str, path: str | None = None):
    if path:
        open(os.path.join(wt, path), "a").write("x\n")
        subprocess.run(["git", "-C", wt, "add", path], check=True)
    subprocess.run(["git", "-C", wt, *GIT_ID, "commit", "-q", "--allow-empty", "-m", msg], check=True)
    return subprocess.run(["git", "-C", wt, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_commit_은_현재_스레드의_change_로_멱등하게(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    _commit(wt, "init")
    assert app.record_commit(USER, wt) is None                     # 스레드 없음 → 아무것도 안 한다
    t = app.open_thread(USER, "goal", wt)["case_id"]
    h = _commit(wt, "add feature", "f.py")
    r = app.record_commit(USER, wt)
    assert r["kind"] == "commit" and r["head"] == h and r["message"] == "add feature" and "1 file changed" in r["stat"]
    assert app.record_commit(USER, wt) is None                     # 같은 HEAD 두 번 → None
    a = app.resume(USER, t, worktree=wt)["anchor"]
    assert [c["head"] for c in a["commits"]] == [h] and a["last_checkpoint"] is None and a["changed_since_checkpoint"] is None
    assert app.db.count("evidence", where={"case_id": t}) == 0     # evidence 가 아니다
    assert not any(e["event_type"].startswith("change") for e in app.list_ledger(USER, t))


def test_checkpoint_와_live_비교(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    _commit(wt, "init")
    t = app.open_thread(USER, "goal", wt)["case_id"]
    open(os.path.join(wt, "dirty.txt"), "w").write("uncommitted")
    cp = app.record_checkpoint(USER, wt, "session-end")
    assert cp["kind"] == "checkpoint" and cp["dirty"] == 1 and json.loads(cp["dirty_files"]) == ["dirty.txt"] and cp["message"] == "session-end"
    a = app.resume(USER, t, worktree=wt)["anchor"]
    assert a["changed_since_checkpoint"] is False and a["last_checkpoint"]["why"] == "session-end"
    subprocess.run(["git", "-C", wt, "add", "dirty.txt"], check=True)
    _commit(wt, "commit it")                                        # 사람이 터미널에서 커밋한 상황
    a = app.resume(USER, t, worktree=wt)["anchor"]
    assert a["changed_since_checkpoint"] is True and a["dirty"] == 0 and a["last_checkpoint"]["dirty"] == 1
    assert app.resume(USER, t)["anchor"]["last_checkpoint"]["head"] == cp["head"]   # worktree 없이는 과거 관찰만


def test_pause_switch_가_checkpoint_를_남긴다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    _commit(wt, "init")
    t1 = app.open_thread(USER, "one", wt)["case_id"]
    t2 = app.open_thread(USER, "two", wt)["case_id"]               # 여는 순간 t1 → t2 로 바뀐다 (open 은 switch 가 아니라 checkpoint 없음)
    app.switch_thread(USER, t1, wt)                                 # t2 를 떠난다 → t2 checkpoint
    app.switch_thread(USER, None, wt)                               # pause → t1 checkpoint
    whys = {c: app.resume(USER, c)["anchor"]["last_checkpoint"]["why"] for c in (t1, t2)}
    assert whys == {t1: "pause", t2: f"switch to {t1}"}


def test_훅_진입점(tmp_path):
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    from tests.conftest import FakeLLM, FakeSearch
    wt = _git_repo(tmp_path / "w"); _commit(wt, "init")
    dbp = str(tmp_path / "hook.db"); db = SqliteDB(dbp)
    db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    t = app.open_thread(USER, "goal", wt)["case_id"]
    env = dict(os.environ, CASEBOOK_DB=dbp, CASEBOOK_MCP_EMAIL="tester@example.test", CLAUDE_PROJECT_DIR=wt)
    for args in (["post-commit"], ["post-commit"], ["checkpoint", "pre-compact"]):
        p = subprocess.run([sys.executable, str(SCRIPT), *args], input="{}", capture_output=True, text=True, env=env, cwd=ROOT)
        assert p.returncode == 0, p.stderr
        out = json.loads(p.stdout); assert out.get("suppressOutput") is True
    a = app.resume(USER, t, worktree=wt)["anchor"]
    assert len(a["commits"]) == 1 and a["last_checkpoint"]["why"] == "pre-compact"


def test_훅은_입력_JSON_의_cwd_를_CLAUDE_PROJECT_DIR_보다_먼저_본다(tmp_path):
    """Claude Code worktree 세션: CLAUDE_PROJECT_DIR 은 원래 checkout, 훅 입력 cwd 는 worktree.
    2026-09-05 ddcbb23 이 worktree 스레드(#452) 대신 원래 checkout 의 #446 에 붙은 오귀속의 재현·방지."""
    from casebook.core.db import SqliteDB
    from casebook.core.app import Casebook
    from tests.conftest import FakeLLM, FakeSearch
    main = _git_repo(tmp_path / "main"); _commit(main, "init main")
    side = _git_repo(tmp_path / "side"); _commit(side, "init side")
    dbp = str(tmp_path / "hook.db"); db = SqliteDB(dbp)
    db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    app = Casebook(db=db, llm=FakeLLM(), search=FakeSearch())
    t_main = app.open_thread(USER, "main work", main)["case_id"]
    t_side = app.open_thread(USER, "side work", side)["case_id"]
    h = _commit(side, "made in the worktree", "w.py")
    env = dict(os.environ, CASEBOOK_DB=dbp, CASEBOOK_MCP_EMAIL="tester@example.test", CLAUDE_PROJECT_DIR=main)
    p = subprocess.run([sys.executable, str(SCRIPT), "post-commit"], input=json.dumps({"cwd": side}), capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0 and f"thread #{t_side}" in json.loads(p.stdout).get("systemMessage", ""), p.stdout
    assert [c["head"] for c in app.resume(USER, t_side)["anchor"]["commits"]] == [h]
    assert app.resume(USER, t_main)["anchor"]["commits"] == []                    # 원래 checkout 의 스레드에는 안 붙는다
    p = subprocess.run([sys.executable, str(SCRIPT), "post-commit"], input="{}", capture_output=True, text=True, env=env, cwd=ROOT)
    assert p.returncode == 0                                                       # stdin 에 cwd 가 없으면 종전 순서(CLAUDE_PROJECT_DIR)


def test_바인딩보다_오래된_커밋은_새_스레드에_붙지_않는다(app, tmp_path):
    """스레드 A 시절의 커밋이 B 로 바꾼 뒤 첫 훅에서 B 에 붙던 오귀속(2026-09-05) 방지."""
    import time
    wt = _git_repo(tmp_path / "w")
    a = app.open_thread(USER, "A", wt)["case_id"]
    _commit(wt, "made under A")
    time.sleep(1.1)                                                  # git 의 초 단위 시각과 바인딩 ms 시각을 확실히 가른다
    b = app.open_thread(USER, "B", wt)["case_id"]                    # B 가 current 가 된다
    assert app.record_commit(USER, wt) is None                       # 훅이 지금 돌아도 A 시절 커밋은 B 에 붙지 않는다
    assert app.resume(USER, b)["anchor"]["commits"] == []
    h = _commit(wt, "made under B")
    assert app.record_commit(USER, wt)["head"] == h
