"""확장 158호 — 인계문을 읽는 순서대로 다시 짠다 (스레드 596, D18078). 보장할 것:
(1) '한 일' 이 있다 — 커밋과 바꿈·확인 노트의 제목이 시간순으로. 생각·발견 노트는 해석이라 싣지 않는다.
    전의 인계문에는 이 칸이 없어 어디까지 했는지를 답하지 못했다(증상)
(2) 결정·제약은 ADR 형식: 상태(무엇을 대체했나) · 정한 사람 · 맥락(이유와 사람 원문) · 결정 · 근거.
    원문은 출처(훅·모델)와 함께 인용하고, 길면 자르고, 아직 없으면 그렇다고 쓴다. Consequences 칸은 없다
(3) 저장소 공통 결정·제약은 제목 한 줄씩만 — 이유 본문은 싣지 않는다
(4) 인용된 증거는 결정·배제가 기대는 것만 (5) 한 일이 많으면 최근 것만 싣고 나머지는 trail 로 보낸다."""
from __future__ import annotations

import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core import handoff, utterances
from casebook.core.db import SqliteDB
from casebook.core.worktrail import Worktrail
from tests.test_mcp_remote import _commit
from tests.test_threads import _git_repo

NEXT = "배포한다\n\n서버에 올리고 두 스레드로 뽑아 읽는다."


@pytest.fixture
def wt():
    w = Worktrail(SqliteDB(":memory:"))
    w.signup("사람", "k@x.test", "pw")
    return w


def _did(msg: str) -> int:
    return int(msg.split("Decision D", 1)[1].split()[0])


def _eid(msg: str) -> int:
    return int(msg.split("#", 1)[1].split()[0])


def test_한_일에_커밋과_바꿈_확인_노트가_시간순으로_실린다(wt, tmp_path):
    t = Tools(wt, 1)
    repo = _git_repo(tmp_path / "r")
    cid = t.open_thread("인계문을 다시 짠다", repo, topic="검증")["case_id"]
    t.note_turn(cid, "handoff 를 뽑았다", "한 일이 없다", kind="finding", next=NEXT, owner="claude", observed_from="machine")
    t.note_turn(cid, "handoff.py 를 새로 썼다", "구조를 바꿨다\n\n세부.", kind="change", next=NEXT, owner="claude",
                observed_from="machine")
    _commit(repo, "확장 158호 — 인계문을 다시 짠다")
    assert wt.record_commit(1, repo)
    t.note_turn(cid, "pytest 709 passed", "시험이 통과했다", kind="verified", next=NEXT, owner="claude", observed_from="machine")
    t.note_turn(cid, "이렇게 하면 어떨까", "생각은 싣지 않는다", kind="thought", next=NEXT, owner="claude", observed_from="machine")
    doc = t.handoff(cid, engineer_asked=True)
    done = doc.split("## 한 일", 1)[1].split("\n## ", 1)[0]
    rows = [l for l in done.splitlines() if l.startswith("- ")]
    assert [r.split(" · ", 1)[1] for r in rows] == [
        "바꿈 turn 2 · 구조를 바꿨다", rows[1].split(" · ", 1)[1], "확인 turn 3 · 시험이 통과했다"]
    assert rows[1].split(" · ", 1)[1].startswith("커밋 ") and rows[1].endswith("· 확장 158호 — 인계문을 다시 짠다")
    assert "한 일이 없다" not in done and "생각은 싣지 않는다" not in done
    assert "세부." not in done                                           # 제목만


def test_결정은_ADR_형식으로_원문과_함께_나온다(wt, tmp_path):
    t = Tools(wt, 1)
    cid = t.open_thread("인계문을 다시 짠다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    said = "응. 이제 인계문도 슬슬 건드릴 때가 된거 같아."
    pasted = t.add_utterance(said).split()[1]
    utterances.record(wt.db, 1, [{"prompt_id": "hook-prompt-0001", "content": "가" * (handoff.SOURCE_CLIP + 50),
                                  "said_at": 1}])
    e = _eid(t.add_evidence(cid, "$ handoff\n# Handoff — case 595"))
    old = _did(t.decide(cid, "ADR은 코어에 넣지 않는다\n\n향후 인계 산출물 후보로 둔다.", reason="지금은 출처 관리가 문제다",
                        authority="user", source_refs=[pasted], next="다음 할 일\n\n시험이 세운 자리다.", owner="user"))
    new = _did(t.decide(cid, "인계문의 결정 부분을 ADR 형식으로 낸다\n\n저장소에 파일로 내보내지 않는다.",
                        reason="읽는 순서대로 짠다\n\nhandoff 를 뽑아 읽은 결과다.", authority="user",
                        source_refs=[pasted, "hook-prompt-0001", "not-yet-arrived-01"], capture=True,
                        evidence_ids=[e], supersedes=old, next="다음 할 일\n\n시험이 세운 자리다.", owner="user"))
    t.constrain(cid, "Consequences 는 지어 쓰지 않는다", authority="agent")
    doc = t.handoff(cid, engineer_asked=True)
    sec = doc.split(f"### D{new} · 인계문의 결정 부분을 ADR 형식으로 낸다", 1)[1].split("\n### ", 1)[0]
    assert f"- 상태: 유효 · D{old} (ADR은 코어에 넣지 않는다) 을 대체함" in sec
    assert "- 정한 사람: user" in sec
    assert "- 맥락: 읽는 순서대로 짠다\n  handoff 를 뽑아 읽은 결과다." in sec
    assert "  - 원문 (모델이 옮겨 붙임 · " in sec and f"    > {said}" in sec
    assert "  - 원문 (훅이 잡음 · " in sec and f"_전문은 inspect(D{new})_" in sec and "가" * (handoff.SOURCE_CLIP + 1) not in sec
    assert "_원문이 아직 서버에 도착하지 않았다_ (not-yet-arrived-01)" in sec
    assert "- 결정:\n  저장소에 파일로 내보내지 않는다." in sec
    assert f"- 근거: 증거 #{e}" in sec
    assert f"### D{old}" not in doc                                      # 대체된 결정은 따로 나오지 않는다
    assert "Consequences" not in doc.replace("Consequences 는 지어 쓰지 않는다", "")
    assert "## 제약" in doc and "### C" in doc
    assert doc.split("## 인용된 증거", 1)[1].strip().startswith(f"- #{e} · $ handoff")


def test_저장소_규칙은_제목만_인용된_증거는_기댄_것만(wt, tmp_path):
    t = Tools(wt, 1)
    repo = _git_repo(tmp_path / "r")
    other = t.open_thread("다른 스레드", repo, topic="검증")["case_id"]
    t.decide(other, "저장소 공통 결정 제목\n\n본문은 인계문에 나오지 않는다.", reason="이유도 나오지 않는다", scope="repo", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    cid = t.open_thread("인계문", repo, topic="검증")["case_id"]
    loose = _eid(t.add_evidence(cid, "기대지 않은 증거"))
    doc = t.handoff(cid, engineer_asked=True)
    assert "## 저장소 공통 규칙" in doc and " · 저장소 공통 결정 제목" in doc
    assert "본문은 인계문에 나오지 않는다" not in doc and "이유도 나오지 않는다" not in doc
    assert f"#{loose}" not in doc and "## 인용된 증거" not in doc


def test_한_일이_많으면_최근_것만_싣고_trail_로_보낸다(wt, tmp_path, monkeypatch):
    monkeypatch.setattr(handoff, "DONE_LIMIT", 3)
    t = Tools(wt, 1)
    cid = t.open_thread("많이 한 스레드", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    t.declare(cid, "focus", "많이 한 스레드")                            # 15턴 상한 전에 다시 센다
    for i in range(5):
        t.note_turn(cid, f"관찰 {i}", f"바꿈 {i}", kind="change", next=NEXT, owner="claude", observed_from="machine")
    doc = t.handoff(cid, engineer_asked=True)
    done = doc.split("## 한 일", 1)[1].split("\n## ", 1)[0]
    assert f"_앞선 2건은 trail({cid}) 로 본다._" in done
    assert "바꿈 0" not in done and "바꿈 4" in done and done.count("\n- ") == 3


def test_라벨_표는_두_언어가_같은_열쇠를_갖는다():
    assert set(handoff.LABELS["ko"]) == set(handoff.LABELS["en"])
