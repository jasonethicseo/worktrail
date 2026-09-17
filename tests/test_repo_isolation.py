"""Repository identity upgrades must not change another user's records."""

import pytest

from casebook.core import threads
from tests.conftest import FakeLLM, FakeSearch, _build_app


@pytest.mark.parametrize("target_exists", [False, True])
def test_another_user_cannot_promote_my_repository(target_exists):
    app = _build_app(FakeLLM(), FakeSearch())
    mine = 1
    other = app.db.add("user", {"name": "other", "email": "other@example.test", "password": "x"})["id"]
    path = "/shared/project"
    old_ident = {"worktree": path, "identity": f"path:{path}", "hint": path, "aliases": []}
    new_ident = {"worktree": "/other/project", "identity": "github.com/shared/project",
                 "hint": "shared/project", "aliases": [f"path:{path}"]}

    with threads.provided(old_ident):
        case_id = app.open_thread(mine, "내 작업", path)["case_id"]
    old_repo = threads.repo_of_case(app.db, case_id)
    assert old_repo is not None

    if target_exists:
        with threads.provided({**new_ident, "aliases": []}):
            app.open_thread(other, "다른 사람의 작업", new_ident["worktree"])

    with threads.provided(new_ident):
        app.open_thread(other, "다른 사람의 다음 작업", new_ident["worktree"])

    with threads.provided(old_ident):
        listed = app.list_threads(mine, path)
        assert listed["current"] == case_id
        assert [t["case_id"] for t in listed["threads"]] == [case_id]
    assert threads.repo_of_case(app.db, case_id)["id"] == old_repo["id"]
    assert app.db.conn.execute("SELECT repo_id FROM worktree_binding WHERE user_id=? AND worktree=?",
                               (mine, path)).fetchone()[0] == old_repo["id"]
