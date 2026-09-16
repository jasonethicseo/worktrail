"""확장 15호 — 스레드 층. 보장할 것:
(1) repo identity 는 정규화한 remote 이고 branch 와 무관하다 (2) open_thread = 케이스 + repo + focus 선언 + worktree 바인딩
(3) "현재 스레드"는 worktree 별이다 — 같은 repo 의 두 worktree 가 서로 다른 스레드를 잡아도 서로 덮어쓰지 않는다
(4) switch 는 바인딩 변경, case_id 없으면 pause(해제), close 는 resolved + 바인딩 전부 해제
(5) focus 선언은 append-only 이고 새 선언이 직전 것을 supersede 한다 (6) 옛 케이스도 switch 로 스레드가 된다
(7) 다른 repo 의 스레드로는 switch 못 한다 (8) 원장 시퀀스 오라클은 그대로(골든 테스트가 지킨다)."""
from __future__ import annotations

import subprocess

import pytest

from casebook.core import threads
from casebook.core.errors import InputError
from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import USER, CASE


def _git_repo(path, remote: str | None = "git@github.com:acme/widgets.git"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)
    return str(path)


@pytest.mark.parametrize("url,ident", [
    ("git@github.com:acme/widgets.git", "github.com/acme/widgets"),
    ("https://GitHub.com/acme/widgets", "github.com/acme/widgets"),
    ("ssh://git@gitlab.example.com:2222/team/repo.git/", "gitlab.example.com/team/repo"),
    ("https://user@host.io/a/b.git", "host.io/a/b"),
])
def test_remote_정규화(url, ident):
    assert threads.normalize_remote(url) == ident


def test_identity_는_remote_이고_branch_와_무관하다(tmp_path):
    wt = _git_repo(tmp_path / "w")
    a = threads.identify(wt)
    subprocess.run(["git", "-C", wt, "checkout", "-q", "--orphan", "feature"], check=True)
    b = threads.identify(wt + "/")
    assert a["identity"] == b["identity"] == "github.com/acme/widgets" and a["worktree"] == b["worktree"]
    plain = threads.identify(str(tmp_path / "not-git"))
    assert plain["identity"].startswith("path:")
    local = threads.identify(_git_repo(tmp_path / "noremote", remote=None))
    assert local["identity"].startswith("local:")


def test_open_thread_는_케이스_repo_focus_바인딩을_만든다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    r = app.open_thread(USER, "목록에 vitals 열을 붙인다", wt)
    assert r["repo"] == "github.com/acme/widgets" and r["focus"] == "목록에 vitals 열을 붙인다"
    case = app.db.get("case", r["case_id"])
    assert case["title"] == "목록에 vitals 열을 붙인다" and case["status"] == "open"
    cur = app.current_thread(USER, wt)
    assert cur["case_id"] == r["case_id"] and cur["focus"] == r["focus"]
    ev = app.db.query("ledger", where={"case_id": r["case_id"], "event_type": "focus_declared"})
    assert len(ev) == 1 and ev[0]["payload"]["supersedes"] is None
    with pytest.raises(InputError, match="focus is empty"):
        app.open_thread(USER, "  ", wt)


def test_현재_스레드는_worktree_별이다(app, tmp_path):
    a = _git_repo(tmp_path / "a"); b = _git_repo(tmp_path / "b")     # 같은 remote, 다른 worktree(Claude · Codex)
    ta = app.open_thread(USER, "A 작업", a)["case_id"]
    tb = app.open_thread(USER, "B 작업", b)["case_id"]
    assert app.current_thread(USER, a)["case_id"] == ta and app.current_thread(USER, b)["case_id"] == tb
    lt = app.list_threads(USER, a)
    assert lt["current"] == ta and {t["case_id"] for t in lt["threads"]} == {ta, tb}
    assert next(t for t in lt["threads"] if t["case_id"] == tb)["bound_worktrees"] == [threads.identify(b)["worktree"]]


def test_switch_pause_close(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t1 = app.open_thread(USER, "첫 스레드", wt)["case_id"]
    t2 = app.open_thread(USER, "둘째 스레드", wt)["case_id"]
    assert app.current_thread(USER, wt)["case_id"] == t2                     # 열면 그게 current
    assert app.switch_thread(USER, t1, wt)["current"] == t1
    assert app.switch_thread(USER, None, wt)["paused"] is True and app.current_thread(USER, wt) is None
    app.switch_thread(USER, t1, wt)
    r = app.close_thread(USER, t1)
    assert r["unbound_worktrees"] == 1 and app.db.get("case", t1)["status"] == "resolved"
    assert app.current_thread(USER, wt) is None
    lt = app.list_threads(USER, wt)
    assert [(t["case_id"], t["state"]) for t in lt["threads"]] == [(t2, "open"), (t1, "closed")]
    with pytest.raises(InputError, match="is resolved"):
        app.switch_thread(USER, t1, wt)


def test_focus_선언은_append_only(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "처음 목표", wt)["case_id"]
    d = app.declare(USER, t, "focus", "바뀐 목표", authority="user")
    ev = app.db.query("ledger", where={"case_id": t, "event_type": "focus_declared"}, order="id")
    assert [e["payload"]["statement"] for e in ev] == ["처음 목표", "바뀐 목표"]
    assert d["supersedes"] == ev[0]["id"] and app.current_thread(USER, wt)["focus"] == "바뀐 목표"
    with pytest.raises(InputError, match="kind must be"):
        app.declare(USER, t, "mood", "x")


def test_옛_케이스도_switch_로_스레드가_된다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a"); app.run_workers()
    assert app.switch_thread(USER, CASE, wt)["current"] == CASE
    lt = app.list_threads(USER, wt)
    assert lt["current"] == CASE and lt["threads"][0]["turn_count"] == 1 and lt["threads"][0]["focus"] is None


def test_다른_repo_의_스레드로는_switch_못_한다(app, tmp_path):
    a = _git_repo(tmp_path / "a"); other = _git_repo(tmp_path / "o", remote="git@github.com:acme/other.git")
    t = app.open_thread(USER, "A", a)["case_id"]
    with pytest.raises(InputError, match="belongs to github.com/acme/widgets"):
        app.switch_thread(USER, t, other)


def test_identity_승격_path_에서_git_init_뒤에도_같은_저장소(app, tmp_path):
    """Codex Phase 0(case 447): 폴더에서 스레드를 연 뒤 git init → identity 가 path: → local: 로 바뀌어도 스레드·바인딩 유지."""
    d = tmp_path / "lab"; d.mkdir(); wt = str(d)
    t = app.open_thread(USER, "lab work", wt)["case_id"]
    assert app.list_threads(USER, wt)["repo"].startswith("path:")
    subprocess.run(["git", "init", "-q", "-b", "main", wt], check=True)                   # 폴더가 저장소가 된다
    lt = app.list_threads(USER, wt)
    assert lt["repo"].startswith("local:") and lt["current"] == t and [x["case_id"] for x in lt["threads"]] == [t]
    assert app.switch_thread(USER, t, wt)["current"] == t                                  # 447 에서 거절됐던 호출
    assert app.resume(USER, t, worktree=wt)["anchor"].get("mismatch") is None
    subprocess.run(["git", "-C", wt, "remote", "add", "origin", "git@github.com:acme/lab.git"], check=True)
    lt = app.list_threads(USER, wt)                                                        # remote 가 붙어도 한 번 더 승격
    assert lt["repo"] == "github.com/acme/lab" and lt["current"] == t
    assert app.db.count("repo") == 1                                                       # 행은 하나가 제자리에서 바뀌었다


def test_identity_병합_현재_identity_행이_이미_있으면_옛_행을_흡수(app, tmp_path):
    d = tmp_path / "lab"; d.mkdir(); wt = str(d)
    t_old = app.open_thread(USER, "before init", wt)["case_id"]
    subprocess.run(["git", "init", "-q", "-b", "main", wt], check=True)
    ident = threads.identify(wt)
    app.db.add("repo", {"identity": ident["identity"], "hint": ident["hint"]})           # 누군가 새 identity 로 먼저 행을 만든 상황
    assert app.db.count("repo") == 2
    lt = app.list_threads(USER, wt)
    assert app.db.count("repo") == 1 and lt["current"] == t_old and [x["case_id"] for x in lt["threads"]] == [t_old]


def test_화면에서_닫은_뒤_결과를_나중에_붙인다(app, tmp_path):
    """확장 48호 — 사람은 화면에서 결과 없이 닫고, 에이전트가 뒤에 close_thread(result) 로 채운다.
    보장할 것: 같은 상태 + 결과 없음이면 아무것도 안 남는다 · 결과는 새 원장 이벤트로 붙는다 · 닫힌 시각은 처음 닫은 때 ·
    다시 열었다 닫으면 옛 결과를 물려받지 않는다 · list_threads 가 needs_result 로 알려 준다."""
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "화면에서 닫을 스레드", wt)["case_id"]
    ev = lambda: app.db.query("ledger", where={"case_id": t, "event_type": "case_status_changed"}, order="id")
    row = lambda: next(x for x in app.list_threads(USER, wt)["threads"] if x["case_id"] == t)
    tracker = lambda: next(x for rep in threads.all_threads(app.db, USER, {}) for x in rep["threads"] if x["case_id"] == t)
    app.set_status(USER, t, "resolved")                                                        # 화면 길 — 결과 없음
    assert row()["state"] == "closed" and row()["result"] is None and row()["needs_result"] is True
    assert app.set_status(USER, t, "resolved")["status"] == "resolved" and len(ev()) == 1        # 같은 상태 + 결과 없음 → 그대로
    r = app.close_thread(USER, t, "고쳤다: 화면에서 닫고 나중에 채움")
    e = ev()
    assert len(e) == 2 and e[1]["payload"] == {"from": "resolved", "to": "resolved", "result": r["result"]}
    assert row()["result"] == r["result"] and row()["needs_result"] is False
    assert tracker()["result"] == r["result"] and tracker()["closed_at"] == e[0]["created_at"]   # 닫힌 시각은 처음 닫은 때
    app.set_status(USER, t, "open"); app.set_status(USER, t, "resolved")
    assert tracker()["result"] is None and tracker()["closed_at"] == ev()[-1]["created_at"]
