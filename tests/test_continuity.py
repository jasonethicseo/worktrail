"""확장 16호 — durable continuity. 보장할 것:
(1) decide/constrain 의 scope 기본은 thread 이고 repo scope 는 스레드가 아닌 케이스에서는 거부된다
(2) repo scope 항목은 같은 저장소의 다른 스레드 resume 에 repo_state 로 보이고, 다른 저장소에는 안 보인다
(3) repo scope 의 supersede 는 다른 스레드에서도 되고, thread scope 는 자기 스레드 안에서만 된다
(4) thread 와 repo 가 충돌해도 둘 다 그대로 보인다 — 우선순위를 정해 주지 않는다
(5) open · next 선언이 resume 에 provenance 와 함께 실린다
(6) anchor 는 live git 이고, 다른 저장소의 worktree 를 주면 mismatch 로 표시된다
(7) evidence_refs 는 결정·배제가 가리키는 evidence 만."""
from __future__ import annotations

import subprocess

import pytest

from casebook.core.errors import InputError, NotFoundError
from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import USER, CASE
from tests.test_threads import _git_repo


def test_scope_기본은_thread_이고_repo_는_스레드에서만(app, tmp_path):
    d = app.decide(USER, CASE, "x")
    assert d["scope"] == "thread"
    with pytest.raises(InputError, match="scope=repo needs a thread"):
        app.constrain(USER, CASE, "y", scope="repo")
    with pytest.raises(InputError, match="scope must be"):
        app.decide(USER, CASE, "z", scope="global")
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "t", wt)["case_id"]
    assert app.constrain(USER, t, "y", scope="repo")["scope"] == "repo"


def test_repo_scope_는_저장소의_모든_스레드에_보인다(app, tmp_path):
    a = _git_repo(tmp_path / "a"); other = _git_repo(tmp_path / "o", remote="git@github.com:acme/other.git")
    t1 = app.open_thread(USER, "first", a)["case_id"]
    t2 = app.open_thread(USER, "second", a)["case_id"]
    t3 = app.open_thread(USER, "elsewhere", other)["case_id"]
    c = app.constrain(USER, t1, "public API keeps offset pagination", authority="user", scope="repo")["constraint_id"]
    app.decide(USER, t1, "this screen uses cursor pagination", scope="thread")
    r2 = app.resume(USER, t2)
    assert [x["constraint_id"] for x in r2["repo_state"]["constraints"]] == [c]
    assert r2["repo_state"]["constraints"][0]["case_id"] == t1 and r2["repo_state"]["threads"] == sorted({t1, t2})
    assert r2["constraints"] == [] and r2["decisions"] == []                    # thread scope 는 t1 의 것
    r1 = app.resume(USER, t1)
    assert [x["statement"] for x in r1["decisions"]] == ["this screen uses cursor pagination"]
    assert [x["constraint_id"] for x in r1["repo_state"]["constraints"]] == [c] and r1["constraints"] == []
    assert app.resume(USER, t3)["repo_state"]["constraints"] == []
    assert "repo_state" not in app.resume(USER, CASE)                            # 스레드가 아닌 케이스


def test_repo_scope_의_supersede_는_스레드를_넘는다(app, tmp_path):
    a = _git_repo(tmp_path / "a")
    t1 = app.open_thread(USER, "first", a)["case_id"]
    t2 = app.open_thread(USER, "second", a)["case_id"]
    c1 = app.constrain(USER, t1, "no schema migrations", scope="repo")["constraint_id"]
    c2 = app.constrain(USER, t2, "schema migrations must be backwards compatible", scope="repo", supersedes=c1)["constraint_id"]
    assert [x["constraint_id"] for x in app.resume(USER, t1)["repo_state"]["constraints"]] == [c2]
    d1 = app.decide(USER, t1, "thread-local choice")["decision_id"]
    with pytest.raises(NotFoundError, match="not found in this thread"):
        app.decide(USER, t2, "override from elsewhere", supersedes=d1)         # thread scope 는 자기 스레드 안에서만


def test_충돌은_그대로_보인다(app, tmp_path):
    a = _git_repo(tmp_path / "a")
    t1 = app.open_thread(USER, "first", a)["case_id"]
    t2 = app.open_thread(USER, "second", a)["case_id"]
    app.decide(USER, t1, "offset pagination everywhere", scope="repo")
    app.decide(USER, t2, "cursor pagination on this screen", scope="thread")
    r = app.resume(USER, t2)
    assert [x["statement"] for x in r["decisions"]] == ["cursor pagination on this screen"]
    assert [x["statement"] for x in r["repo_state"]["decisions"]] == ["offset pagination everywhere"]


def test_open_next_선언이_resume_에_실린다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "goal", wt, authority="user")["case_id"]
    r = app.resume(USER, t)
    assert r["focus"]["statement"] == "goal" and r["focus"]["authority"] == "user" and r["open"] is None and r["next"] is None
    app.declare(USER, t, "open", "does the CSP block the font?")
    n1 = app.declare(USER, t, "next", "1. run the smoke test", owner="user")["declaration_id"]
    n2 = app.declare(USER, t, "next", "1. fix the CSP  2. rerun", owner="user")["declaration_id"]
    r = app.resume(USER, t)
    assert r["open"]["statement"] == "does the CSP block the font?"
    assert r["next"] == {"id": n2, "statement": "1. fix the CSP  2. rerun", "authority": "agent",
                         "declared_at": r["next"]["declared_at"], "supersedes": n1, "owner": "user"}   # owner: 확장 31호(36호에서 필수)


def test_anchor_는_live_git(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    subprocess.run(["git", "-C", wt, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init"], check=True)
    t = app.open_thread(USER, "goal", wt)["case_id"]
    a = app.resume(USER, t, worktree=wt)["anchor"]
    assert a["branch"] == "main" and len(a["head"]) == 12 and a["dirty"] == 0 and a["repo"] == "github.com/acme/widgets"
    (tmp_path / "w" / "x.txt").write_text("hi")
    a = app.resume(USER, t, worktree=wt)["anchor"]
    assert a["dirty"] == 1 and a["dirty_files"] == ["x.txt"]
    other = _git_repo(tmp_path / "o", remote="git@github.com:acme/other.git")
    a = app.resume(USER, t, worktree=other)["anchor"]
    assert "mismatch" in a and "acme/other" in a["mismatch"]
    assert app.resume(USER, t)["anchor"] == {"commits": [], "last_checkpoint": None}   # worktree 없이는 과거 관찰만(17호)


def test_evidence_refs_는_결정과_배제가_가리키는_것만(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "goal", wt)["case_id"]
    app.submit_turn(USER, t, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    e1 = app.db.add("evidence", {"case_id": t, "kind": "user_paste", "content": "ERR 1", "source": {"via": "test"}})["id"]
    e2 = app.db.add("evidence", {"case_id": t, "kind": "user_paste", "content": "ERR 2", "source": {"via": "test"}})["id"]
    app.decide(USER, t, "d", evidence_ids=[e2])
    r = app.resume(USER, t)
    assert r["evidence_refs"] == [e2] and r["evidence_count"] == 3 and e1 not in r["evidence_refs"]
