"""확장 22호 — Tracker 읽기 문. (1) /app/thread 가 저장소별 스레드(open 먼저)와 focus/next/open·마지막 커밋·바인딩을 준다
(2) /app/thread/{id} 는 resume 7칸이고 바인딩된 worktree 가 있으면 live anchor 를 싣는다 (3) 다른 사용자의 스레드는 404 (4) 읽기 전용."""
from __future__ import annotations

import pytest

from tests.drive import USER
from tests.test_threads import _git_repo
from tests.test_git_changes import _commit


def test_list_all_threads_와_thread_state(app, tmp_path):
    a = _git_repo(tmp_path / "a"); _commit(a, "init")
    b = _git_repo(tmp_path / "b", remote="git@github.com:acme/other.git")
    t1 = app.open_thread(USER, "first", a)["case_id"]
    t2 = app.open_thread(USER, "second", a, authority="user")["case_id"]
    t3 = app.open_thread(USER, "elsewhere", b)["case_id"]
    app.declare(USER, t2, "next", "1. ship it", owner="user"); app.constrain(USER, t2, "no schema change", scope="repo", authority="user")
    _commit(a, "work on second"); app.record_commit(USER, a)
    app.close_thread(USER, t1)
    repos = app.list_all_threads(USER)
    assert [r["identity"] for r in repos] == ["github.com/acme/widgets", "github.com/acme/other"]
    wa = repos[0]
    assert wa["open_count"] == 1 and [(t["case_id"], t["state"]) for t in wa["threads"]] == [(t2, "open"), (t1, "closed")]
    sec = wa["threads"][0]
    assert sec["focus"] == "second" and sec["next"] == "1. ship it" and sec["open"] is None
    assert sec["last_commit"]["message"] == "work on second" and sec["bound_worktrees"] == [a]
    assert repos[1]["threads"][0]["case_id"] == t3
    st = app.thread_state(USER, t2)
    assert st["focus"]["statement"] == "second" and st["next"]["statement"] == "1. ship it"
    assert st["anchor"]["branch"] == "main" and len(st["anchor"]["commits"]) == 1          # 바인딩된 worktree 로 live git
    assert [c["statement"] for c in st["repo_state"]["constraints"]] == ["no schema change"]
    assert [e["kind"] for e in st["recent"]] == ["commit", "constraint", "opened"]          # 마지막 발자국, 최신 먼저; 선언(next)은 빠진다
    assert st["recent"][0]["text"] == "work on second" and "recent" not in app.resume(USER, t2)   # MCP resume 에는 없다
    assert app.thread_state(USER, t1)["anchor"] == {"commits": [], "last_checkpoint": None}  # 닫혀 바인딩 없음 → 과거 관찰만


def test_http_tracker(app, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    tok = client.post("/auth/auth/signup", json={"name": "t", "email": "t@x.test", "password": "pw"}).json()["authToken"]
    h = {"Authorization": "Bearer " + tok}
    wt = _git_repo(tmp_path / "w")
    me = client.get("/auth/auth/me", headers=h).json()["id"]
    t = app.open_thread(me, "web thread", wt)["case_id"]
    r = client.get("/app/thread", headers=h).json()
    assert [x["case_id"] for x in r["repos"][0]["threads"]] == [t]
    st = client.get(f"/app/thread/{t}", headers=h).json()
    assert st["case_id"] == t and set(st) >= {"focus", "anchor", "constraints", "decisions", "open", "next", "evidence_refs", "repo_state"}
    other = app.open_thread(USER, "not mine", wt)["case_id"]
    assert client.get(f"/app/thread/{other}", headers=h).status_code == 404
    assert client.get("/app/thread").status_code == 401


def test_원격_화면은_없는_checkout_대신_기록된_커밋을_읽는다(app, tmp_path, monkeypatch):
    from casebook.core import threads
    wt = _git_repo(tmp_path / "client")
    _commit(wt, "init")
    cid = app.open_thread(USER, "원격 기록 확인", wt)["case_id"]
    _commit(wt, "기록 제품을 분리했다")
    app.record_commit(USER, wt)
    app.record_checkpoint(USER, wt, "세션 종료")
    monkeypatch.setattr(threads, "anchor", lambda path: {"worktree": path, "repo": "path:" + path})
    # 에이전트가 명시적으로 다른 worktree를 요청하면 불일치가 그대로 보인다.
    assert "mismatch" in app.resume(USER, cid, worktree=wt)["anchor"]
    anchor = app.thread_state(USER, cid)["anchor"]
    assert "mismatch" not in anchor
    assert anchor["commits"][0]["message"] == "기록 제품을 분리했다"
    assert anchor["last_checkpoint"]["branch"] == "main"
    assert "branch" not in anchor  # 과거 관찰을 live git으로 위장하지 않는다.


def test_http_화면에서_닫기_다시_열기(app, tmp_path):
    """확장 48호 — POST /app/case/{id}/status: 닫으면 바인딩도 풀린다(MCP close_thread 와 같은 뜻) ·
    결과는 나중에 붙일 수 있다 · 다시 열린다."""
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    tok = client.post("/auth/auth/signup", json={"name": "t", "email": "t48@x.test", "password": "pw"}).json()["authToken"]
    h = {"Authorization": f"Bearer {tok}"}
    me = client.get("/auth/auth/me", headers=h).json()["id"]
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(me, "화면에서 닫는다", wt)["case_id"]
    r = client.post(f"/app/case/{t}/status", json={"status": "resolved"}, headers=h)
    assert r.status_code == 200 and r.json() == {"case_id": t, "status": "resolved"}
    assert app.current_thread(me, wt) is None                                                   # 바인딩이 풀렸다
    assert client.get(f"/app/thread/{t}", headers=h).json()["result"] is None
    assert client.post(f"/app/case/{t}/status", json={"status": "resolved", "result": "나중에 채운 결과"}, headers=h).status_code == 200
    assert client.get(f"/app/thread/{t}", headers=h).json()["result"] == "나중에 채운 결과"
    assert client.post(f"/app/case/{t}/status", json={"status": "open"}, headers=h).json()["status"] == "open"
    assert client.get(f"/app/thread/{t}", headers=h).json()["status"] == "open"


def test_대체된_결정과_제약은_화면_상태에만_접혀서_실린다(app, tmp_path):
    """확장 56호 — 찾기에서 누른 "대체됨" 기록이 갈 자리. resume(에이전트)에는 없고 thread_state(화면)에만 있다.
    갈음한 것이 다른 스레드(저장소 규칙)에 있어도 찾는다."""
    a = _git_repo(tmp_path / "a")
    t1 = app.open_thread(USER, "first", a)["case_id"]
    t2 = app.open_thread(USER, "second", a)["case_id"]
    d1 = app.decide(USER, t1, "1판", authority="user")["decision_id"]
    d2 = app.decide(USER, t1, "2판", authority="user", supersedes=d1)["decision_id"]
    c1 = app.constrain(USER, t1, "규칙", authority="user", scope="repo")["constraint_id"]
    c2 = app.constrain(USER, t2, "고친 규칙", authority="user", scope="repo", supersedes=c1)["constraint_id"]
    st = app.thread_state(USER, t1)
    assert [(d["decision_id"], d["superseded_by"]) for d in st["superseded"]["decisions"]] == [(d1, d2)]
    assert [(c["constraint_id"], c["superseded_by"]) for c in st["superseded"]["constraints"]] == [(c1, c2)]
    assert [d["decision_id"] for d in st["decisions"]] == [d2]                      # 살아 있는 것은 그대로
    assert app.thread_state(USER, t2)["superseded"] == {"decisions": [], "constraints": []}
    assert "superseded" not in app.resume(USER, t1)
