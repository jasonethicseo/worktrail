"""확장 46호 — 스레드가 주제에서 미끄러지는 것을 도구가 잡는다(#484). 보장할 것:
(1) 마지막 focus 선언 뒤 15턴까지는 note_turn 이 들어가고, 16번째는 거절된다 — 거절은 아무것도 기록하지 않는다
(2) 거절 문구는 두 길만 적는다: declare(focus) 로 확인하고 계속, 아니면 open_thread. 우회 인자는 없다
(3) focus 를 다시 선언하면 그 시각부터 다시 센다 — 같은 일은 계속된다
(4) add_evidence 로 쌓인 턴도 센다(턴은 턴이다)
(5) focus 가 한 번도 없는 케이스(open_case)는 전부 세고, 문구가 그것을 말한다"""
from __future__ import annotations

import inspect

import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core import state
from casebook.core.errors import ApiError
from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo

CAP = Tools.FOCUS_TURN_CAP


@pytest.fixture()
def wt():
    from casebook.core.db import SqliteDB
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "c@x.test", "pw")
    return w


def _fill(t: Tools, cid: int, n: int) -> None:
    for i in range(n):
        t.note_turn(cid, f"관찰 {i}", f"결론 {i}", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")


def test_문턱은_15_이고_16번째는_거절된다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("풀 고갈을 확인한다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    _fill(t, cid, CAP)                                   # 15 는 들어간다 — 건강한 스레드(4~20턴)는 안 건드린다
    assert wt.db.count("turn", where={"case_id": cid}) == CAP
    with pytest.raises(ApiError, match=rf"thread {cid} has {CAP} turns since its focus was declared") as e:
        t.note_turn(cid, "관찰 16", "결론 16", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    msg = str(e.value)
    assert "the cap is 15" in msg and "D11098" in msg
    assert 'declare(case_id, "focus"' in msg and "open_thread(focus, topic)" in msg   # (2) 두 길
    assert "Nothing was recorded" in msg
    assert wt.db.count("turn", where={"case_id": cid}) == CAP                          # (1) 거절은 기록하지 않는다
    assert wt.db.count("evidence", where={"case_id": cid}) == CAP


def test_우회_인자는_없다():
    """(2) 30호의 교훈 — 도망길이 있으면 그리로 간다. note_turn 의 인자는 종전 넷 + kind(60호, 갈래)
    + next·owner(110호, D15573 — 턴마다 다시 말한다) + evidence_ids(123호, D15872 — 근거는 글이 아니라
    번호로)다. 우회 인자는 하나도 없다."""
    assert list(inspect.signature(Tools.note_turn).parameters) == [
        "self", "case_id", "observed", "conclusion", "kind", "next", "owner", "client", "evidence_ids"]


def test_focus_를_다시_선언하면_다시_센다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("풀 고갈을 확인한다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    _fill(t, cid, CAP)
    with pytest.raises(ApiError):
        t.note_turn(cid, "관찰", "결론", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.declare(cid, "focus", "풀 고갈을 확인한다 (아직 같은 일이다)")   # (3) 확인은 append-only 선언으로 남는다
    assert state.turns_since_focus(wt.db, cid)["turns"] == 0
    assert "Turn 16 recorded" in t.note_turn(cid, "관찰 16", "결론 16", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    _fill(t, cid, CAP - 1)                               # 다시 15 까지
    with pytest.raises(ApiError, match=rf"thread {cid} has {CAP} turns since its focus was declared"):
        t.note_turn(cid, "관찰", "결론", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    # 다른 주제면 새 스레드 — 그쪽은 0 부터다
    other = t.open_thread("설치 모드 선택", _git_repo(tmp_path / "r2"), topic="검증")["case_id"]
    assert "Turn 1 recorded" in t.note_turn(other, "관찰", "결론", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")


def test_증거_턴도_센다(wt, tmp_path):
    """(4) add_evidence 도 턴을 만든다 — 그것으로 채워도 note_turn 은 같은 문턱에 걸린다."""
    t = Tools(wt, 1)
    cid = t.open_thread("증거로 채운다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    for i in range(CAP):
        t.add_evidence(cid, f"$ cmd {i}\nout")
    assert state.turns_since_focus(wt.db, cid)["turns"] == CAP
    with pytest.raises(ApiError, match="the cap is 15"):
        t.note_turn(cid, "관찰", "결론", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")


def test_focus_없는_케이스는_전부_센다(wt):
    """(5) open_case 로 연 케이스에는 focus 선언이 없다 — 열린 뒤 턴 전부를 세고, 문구가 그것을 말한다."""
    t = Tools(wt, 1)
    cid = t.open_case("옛 사건")["case_id"]
    _fill(t, cid, CAP)
    with pytest.raises(ApiError, match=rf"thread {cid} has {CAP} turns and no focus has ever been declared"):
        t.note_turn(cid, "관찰", "결론", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.declare(cid, "focus", "옛 사건을 이어서 본다")
    assert "Turn 16 recorded" in t.note_turn(cid, "관찰 16", "결론 16", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")


# ── 확장 110호 (D15573) — 턴마다 next 를 다시 말한다 ────────────────────────
def test_next_없이는_턴을_기록하지_못한다(wt, tmp_path):
    """스레드 520 에서 next 가 마지막 노트보다 7시간·턴 10개만큼 낡은 채로 이미 끝난 일을
    가리키고 있었다. focus 에는 15턴 상한이 있는데 사람이 실제로 읽고 움직이는 줄에는 아무
    강제가 없었다 — 거절해서 매 턴 다시 말하게 한다."""
    from casebook.core.errors import InputError
    t = Tools(wt, 1)
    cid = t.open_thread("강제를 본다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    with pytest.raises(InputError, match="note_turn needs next"):
        t.note_turn(cid, "관찰", "결론", kind="finding")
    with pytest.raises(InputError, match="needs an owner"):
        t.note_turn(cid, "관찰", "결론", kind="finding", next="다음 할 일\n\n본문")
    assert wt.db.count("turn", where={"case_id": cid}) == 0          # 거절된 턴은 쌓이지 않는다


def test_같은_next_는_새_줄을_쓰지_않는다(wt, tmp_path):
    """매 턴 다시 말하게 했으므로 같은 글이 줄줄이 들어온다. 그대로면 원장에 새 줄을 쓰지 않는다 —
    안 그러면 회고와 타임라인이 거의 같은 next 로 시끄러워진다. 바뀌면 그때 새 줄이다."""
    t = Tools(wt, 1)
    cid = t.open_thread("겹침을 본다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    same = "판정을 기다린다\n\n같은 줄이 세 번 들어온다."
    n = lambda: wt.db.count("ledger", where={"case_id": cid, "event_type": "next_declared"})
    before = n()
    out1 = t.note_turn(cid, "관찰 1", "결론 1", kind="finding", next=same, owner="user")
    assert "next #" in out1 and "declared" in out1
    after_first = n()
    assert after_first == before + 1

    out2 = t.note_turn(cid, "관찰 2", "결론 2", kind="finding", next=same, owner="user")
    assert "unchanged" in out2
    assert n() == after_first                                        # 같은 글 — 새 줄이 없다

    out3 = t.note_turn(cid, "관찰 3", "결론 3", kind="finding", next=same, owner="claude")
    assert "declared" in out3 and n() == after_first + 1              # 차례가 바뀌면 새 줄이다

    out4 = t.note_turn(cid, "관찰 4", "결론 4", kind="finding",
                       next="이제 다른 일이다\n\n글이 바뀌었다.", owner="claude")
    assert "declared" in out4 and n() == after_first + 2
    assert wt.db.count("turn", where={"case_id": cid}) == 4          # 턴은 넷 다 쌓였다


def test_next_는_그_턴_뒤에_선다(wt, tmp_path):
    """원장 순서에서 next 가 노트보다 앞서면 화면이 "이 next 는 마지막 노트보다 낡았다" 로 읽는다.
    턴을 먼저 쌓고 그 다음에 선언한다."""
    t = Tools(wt, 1)
    cid = t.open_thread("순서를 본다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    t.note_turn(cid, "관찰", "결론", kind="finding", next="다음\n\n본문", owner="user")
    rows = wt.db.query("ledger", where={"case_id": cid}, order="id")
    note = max(e["id"] for e in rows if e["event_type"] == "user_message")
    nxt = max(e["id"] for e in rows if e["event_type"] == "next_declared")
    assert nxt > note


# ── 확장 114호 — focus 는 확인과 변경을 가른다 ──────────────────────────────
def test_같은_말로_다시_선언하면_확인이다(wt, tmp_path):
    """상한을 빠져나갈 길이 재선언뿐이라, 방향이 안 바뀌었는데도 에이전트가 진척을 focus 본문에
    적어 넣었다(실측: 스레드 517 의 focus 가 "구현은 다 섰다 — … 확장 104·108·109·111호" 로
    자라 SO FAR 와 겹쳤다). 글자 그대로 다시 말하면 확인으로 남기고, 그때는 회고·타임라인이
    그것을 변경으로 세지 않는다."""
    t = Tools(wt, 1)
    same = "확인과 변경을 가른다\n\n같은 글을 두 번 말한다."
    cid = t.open_thread(same, _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    n = lambda: wt.db.count("ledger", where={"case_id": cid, "event_type": "focus_declared"})
    first = n()

    out = t.declare(cid, "focus", same)                       # 글자 그대로
    assert n() == first + 1, "줄을 남기지 않으면 15턴 상한이 다시 세지 못한다"
    rows = wt.db.query("ledger", where={"case_id": cid, "event_type": "focus_declared"}, order="id")
    assert rows[-1]["payload"].get("confirmed") is True
    assert rows[0]["payload"].get("confirmed") is not True     # 처음 것은 확인이 아니다

    t.declare(cid, "focus", "이제 다른 일이다\n\n방향이 바뀌었다.")
    rows = wt.db.query("ledger", where={"case_id": cid, "event_type": "focus_declared"}, order="id")
    assert rows[-1]["payload"].get("confirmed") is not True    # 글이 바뀌면 변경이다


def test_확인은_상한을_다시_세게_한다(wt, tmp_path):
    """확인이 상한을 풀지 못하면 빠져나갈 길이 '글을 바꾸는 것' 뿐이라 원래 문제로 돌아간다."""
    from casebook.core.errors import ApiError
    t = Tools(wt, 1)
    same = "상한을 본다\n\n같은 글로 확인한다."
    cid = t.open_thread(same, _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    for i in range(Tools.FOCUS_TURN_CAP):
        t.note_turn(cid, f"관찰 {i}", f"결론 {i}", kind="finding", next="다음\n\n본문", owner="user")
    with pytest.raises(ApiError, match="the cap is"):
        t.note_turn(cid, "관찰 넘침", "결론", kind="finding", next="다음\n\n본문", owner="user")
    t.declare(cid, "focus", same)                              # 글자 그대로 확인
    out = t.note_turn(cid, "관찰 다시", "결론", kind="finding", next="다음\n\n본문", owner="user")
    assert "recorded in case" in out


def test_확인은_회고에_초점_줄을_만들지_않는다(wt, tmp_path):
    """바뀐 적 없는 초점이 줄로 쌓이면 읽는 사람이 무엇이 바뀐 줄 안다."""
    from casebook.core import review, threads
    review.ensure(wt.db); threads.ensure_change(wt.db)          # 회고가 읽는 표는 쓰는 쪽이 만든다
    t = Tools(wt, 1)
    same = "회고를 본다\n\n같은 글로 확인한다."
    cid = t.open_thread(same, _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    t.note_turn(cid, "관찰", "결론", kind="finding", next="다음\n\n본문", owner="user")
    t.declare(cid, "focus", same)                              # 확인
    focus_events = lambda: [e for e in review.events_for_cases(wt.db, 1, [cid]).get(cid, []) if e["kind"] == "focus"]
    assert focus_events() == [], "확인이 회고에 초점 줄로 나온다"
    t.declare(cid, "focus", "방향이 바뀌었다\n\n이제 다른 일이다.")
    assert len(focus_events()) == 1, "진짜 변경은 나와야 한다"


def test_안내문이_진척을_넣지_말라고_말한다():
    """원인은 상한이 아니라 빠져나가는 길이 하나뿐이라는 것이었다 — 그러니 그 길에서 말해야 한다."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "casebook/adapters/mcp_server.py").read_text(encoding="utf-8")
    assert "restate the focus WORD FOR WORD" in src
    assert "Do NOT fold" in src and "SO FAR" in src
    assert "not how far along you are" in src                   # declare 설명에도 있다
