"""확장 160호 (D18197) — 다음 할 일을 바꾸는 기록은 next 를 같이 말한다.

전에는 next 를 노트(note_turn)에만 걸어, 결정으로 끝난 턴 뒤에 next 가 이미 정한 일을 가리켰다
(스레드 595: D18184 "커밋 연결은 보류" 뒤에도 N18180 "커밋 연결 방식을 정한다"). 보장할 것:
(1) 결정 · 가설 기각 · 다른 말의 focus 는 next 없이 거절되고, 거절되면 아무것도 쌓이지 않는다
(2) 받은 next 는 그 기록 뒤에 선다 — 원장 순서에서 next 가 기록보다 앞서면 낡은 next 다
(3) 같은 말의 focus(확인)와 제약은 next 를 요구하지 않는다 (4) open 에는 next 를 싣지 않는다."""
from __future__ import annotations

import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core.db import SqliteDB
from casebook.core.errors import InputError
from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo

NEXT = "다음 남은 일을 볼지 정한다\n\n보류했으니 이 일은 끝났다."


@pytest.fixture
def t(tmp_path):
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "n@x.test", "pw")
    return Tools(w, 1)


def _rows(t, cid):
    return t.cb.db.query("ledger", where={"case_id": cid}, order="id")


def _current_next(t, cid):
    return t.resume(cid)["next"]["statement"]


def test_결정은_next_없이_거절되고_아무것도_쌓이지_않는다(t, tmp_path):
    cid = t.open_thread("커밋 연결을 본다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    t.declare(cid, "next", "커밋과 스레드를 잇는 방식을 정한다", owner="user")
    before = len(_rows(t, cid))
    with pytest.raises(InputError, match="decide needs next"):
        t.decide(cid, "커밋 연결 방식은 보류한다", authority="agent")
    assert len(_rows(t, cid)) == before
    out = t.decide(cid, "커밋 연결 방식은 보류한다", authority="agent", next=NEXT, owner="user")
    assert "declared (owner: user)" in out
    assert _current_next(t, cid) == NEXT                                  # 이미 정한 일을 가리키지 않는다
    rows = _rows(t, cid)
    dec = max(r["id"] for r in rows if r["event_type"] == "decision_recorded")
    nxt = max(r["id"] for r in rows if r["event_type"] == "next_declared")
    assert nxt > dec


def test_가설_기각도_next_를_같이_말한다(t, tmp_path):
    cid = t.open_thread("풀 고갈을 본다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    ev = int(t.add_evidence(cid, "$ ping db\n0.4 ms").split("#", 1)[1].split()[0])
    with pytest.raises(InputError, match="rule_out needs next"):
        t.rule_out(cid, "네트워크 지연", "이 저장소", [ev])
    assert "Ruled out R" in t.rule_out(cid, "네트워크 지연", "이 저장소", [ev], next="열린 트랜잭션을 센다", owner="claude")
    assert _current_next(t, cid) == "열린 트랜잭션을 센다"


def test_다른_말의_focus_는_next_를_요구하고_같은_말의_확인은_요구하지_않는다(t, tmp_path):
    same = "원문을 남긴다\n\n턴마다 남긴다."
    cid = t.open_thread(same, _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    assert "declared" in t.declare(cid, "focus", same)                   # 확인
    with pytest.raises(InputError, match="needs next"):
        t.declare(cid, "focus", "이제 인계문을 짠다\n\n방향이 바뀌었다.")
    out = t.declare(cid, "focus", "이제 인계문을 짠다\n\n방향이 바뀌었다.", next="인계문을 뽑아 읽는다", owner="user")
    assert "focus #" in out and "next #" in out
    assert _current_next(t, cid) == "인계문을 뽑아 읽는다"


def test_제약은_next_를_요구하지_않고_open_에는_next_를_싣지_않는다(t, tmp_path):
    cid = t.open_thread("경계를 본다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    assert "Constraint C" in t.constrain(cid, "운영 DB 는 건드리지 않는다", authority="agent")
    with pytest.raises(InputError, match="next goes with a focus"):
        t.declare(cid, "open", "원인을 아직 모른다", next="로그를 본다", owner="claude")
