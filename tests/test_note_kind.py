"""확장 60호 — 노트의 갈래(D14720, #508): 바꿈·확인·발견·생각. 보장할 것:
(1) note_turn 은 kind 없이는 거절하고 네 갈래를 말한다 (2) 한글·영문 어느 쪽으로 줘도 영문 키로 저장된다
(3) 갈래는 answer_created 의 payload 에 남고 회고의 note 사건에 note_kind 로 나온다 (4) 옛 노트(external_turn 직접)는 칸이 없다."""
from __future__ import annotations

import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core import review, threads
from casebook.core.errors import InputError
from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo


@pytest.fixture
def wt():
    from casebook.core.db import SqliteDB
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "k@x.test", "pw")
    return w


def test_갈래_없으면_거절하고_넷을_말한다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("갈래", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    with pytest.raises(InputError, match="change.*verified.*finding.*thought"):
        t.note_turn(cid, "관찰", "결론", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    with pytest.raises(InputError, match="needs kind"):
        t.note_turn(cid, "관찰", "결론", kind="decision", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    assert wt.db.count("turn", where={"case_id": cid}) == 0          # 거절은 턴을 남기지 않는다


def test_한글_영문_모두_받아_영문_키로_남긴다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("갈래 저장", _git_repo(tmp_path / "r2"), topic="검증")["case_id"]
    t.note_turn(cid, "$ pytest\n1 passed", "통과했다", kind="확인", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.note_turn(cid, "재 보니 65자", "첫 문장 중앙값은 65자다", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.note_turn(cid, "코드 diff", "라벨을 바꿨다", kind="Change", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.note_turn(cid, "생각", "둘로 갈라야 할 것 같다", kind="생각", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    wt.external_turn(1, cid, "옛 길", action_key="old", note="갈래 없는 옛 노트")
    wt.run_workers()
    threads.ensure_change(wt.db)                                      # 회고는 change 표를 읽는다 — 커밋이 없어도 표는 있어야 한다
    kinds = [e["payload"].get("kind") for e in wt.db.query("ledger", where={"case_id": cid}, order="id")
             if e["event_type"] == "answer_created"]
    assert kinds == ["verified", "finding", "change", "thought", None]
    evs = [e for e in review.events_for_cases(wt.db, 1, [cid])[cid] if e["kind"] == "note"]
    assert [e.get("note_kind") for e in evs] == ["verified", "finding", "change", "thought", None]
    assert "note_kind" not in evs[-1]                                 # 옛 노트는 칸 자체가 없다
