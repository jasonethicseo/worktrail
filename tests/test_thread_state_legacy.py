"""스레드 층 이전 케이스도 화면이 열 수 있어야 한다 (2026-09-08).

확장 42호의 찾기가 모든 케이스를 누를 수 있게 만들면서 드러났다: #442 는 thread 행도 worktree
바인딩도 없어 resume 의 anchor 가 None 인데, thread_state 가 `out.get("anchor", {})` 로 읽어
터졌다(키는 있고 값이 None 이면 기본값을 쓰지 않는다). 그 전에는 현황에서 갈 길이 없어 안 터졌다.
"""
from __future__ import annotations

import pytest

from casebook.core.worktrail import Worktrail


@pytest.fixture()
def wt():
    from casebook.core.db import SqliteDB
    from casebook.core import threads
    w = Worktrail(SqliteDB(":memory:"))
    threads.ensure_change(w.db)
    w.signup("사람", "l@x.test", "pw")
    return w


def test_스레드가_아닌_케이스도_화면_상태를_준다(wt):
    cid = wt.create_case(1, "스레드 층 이전 케이스")["case_id"]
    wt.external_turn(1, cid, "$ 옛 기록\n한 줄", action_key="a1", note="그때의 결론")
    wt.run_workers()

    st = wt.thread_state(1, cid)
    assert st["case_id"] == cid and st["title"] == "스레드 층 이전 케이스"
    assert st["anchor"] is None and st["repo"] is None     # git 도 저장소도 없다 — 없다고 말한다
    assert st["phase"] is None and st["owner"] is None     # 스레드가 아니므로 단계도 없다
    assert len(st["recent"]) >= 1                          # 기록은 그대로 읽힌다
    assert st["topics"] == []


def test_결정도_읽힌다(wt):
    """찾기에서 옛 결정을 눌러 들어오는 길 — 그 케이스의 durable state 가 보여야 한다."""
    cid = wt.create_case(1, "옛 케이스")["case_id"]
    d = wt.decide(1, cid, "그때 이렇게 정했다", authority="user")["decision_id"]
    st = wt.thread_state(1, cid)
    assert [x["decision_id"] for x in st["decisions"]] == [d]
