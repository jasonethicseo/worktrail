"""확장 123호 — 노트가 기대는 증거는 글이 아니라 evidence_ids 로 (D15872). 보장할 것:
(1) note_turn(evidence_ids=…) 는 answer_created 의 payload.refs 에 남고 회고의 note 사건에 evidence_ids 로 나온다
(2) 남의 증거·없는 증거는 거절하고 턴을 남기지 않는다 (3) 없으면 칸 자체가 없다 (4) 결과 문장이 무엇을 달았는지 말한다."""
from __future__ import annotations

import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core import review, threads
from casebook.core.errors import NotFoundError
from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo

NEXT = "다음 할 일\n\n시험이 세운 자리다."


@pytest.fixture
def wt():
    from casebook.core.db import SqliteDB
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "k@x.test", "pw")
    w.signup("남", "other@x.test", "pw")
    return w


def _eid(msg: str) -> int:
    return int(msg.split("#")[1].split()[0])


def test_근거는_evidence_ids_로_남고_회고가_읽는다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("근거 링크", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    e1 = _eid(t.add_evidence(cid, "$ curl /app/status\nHTTP 200"))
    e2 = _eid(t.add_evidence(cid, "$ curl /mcp\nHTTP 401"))
    out = t.note_turn(cid, "두 문을 쳤다", "API 문만 열려 있다", kind="finding", next=NEXT, owner="user", evidence_ids=[e1, e2])
    assert f"Cites evidence #{e1}, #{e2}." in out
    t.note_turn(cid, "다시 봤다", "근거 없는 노트", kind="thought", next=NEXT, owner="user")
    wt.run_workers()
    threads.ensure_change(wt.db)
    payloads = [e["payload"] for e in wt.db.query("ledger", where={"case_id": cid}, order="id") if e["event_type"] == "answer_created"]
    assert payloads[0]["refs"] == [e1, e2] and "refs" not in payloads[1]
    notes = [e for e in review.events_for_cases(wt.db, 1, [cid])[cid] if e["kind"] == "note"]
    assert notes[0]["evidence_ids"] == [e1, e2]
    assert "evidence_ids" not in notes[1]                                          # 없으면 칸 자체가 없다
    assert wt.thread_state(1, cid)["notes"][-1]["evidence_ids"] == [e1, e2]         # 스레드 화면의 노트 서랍도 같은 것을 본다


def test_남의_증거와_없는_증거는_거절하고_턴을_남기지_않는다(wt, tmp_path):
    mine, theirs = Tools(wt, 1), Tools(wt, 2)
    cid = mine.open_thread("내 것", _git_repo(tmp_path / "a"), topic="검증")["case_id"]
    other = theirs.open_thread("남의 것", _git_repo(tmp_path / "b"), topic="검증")["case_id"]
    foreign = _eid(theirs.add_evidence(other, "남의 관찰"))
    n = wt.db.count("turn", where={"case_id": cid})
    with pytest.raises(NotFoundError, match="evidence not found"):
        mine.note_turn(cid, "관찰", "결론", kind="finding", next=NEXT, owner="user", evidence_ids=[foreign])
    with pytest.raises(NotFoundError, match="evidence not found"):
        mine.note_turn(cid, "관찰", "결론", kind="finding", next=NEXT, owner="user", evidence_ids=[999999])
    assert wt.db.count("turn", where={"case_id": cid}) == n
