"""확장 41호 (2026-09-08) — 대체된 결정이 화면에서 유효한 것처럼 읽히지 않게.

저장은 이미 됐다(state.record_decision 의 payload.supersedes). 끊긴 곳은 회고였다: decision 줄이
그 필드를 싣지 않아, 두 번 갈아엎힌 조리법(D10599)이 유효한 결정처럼 한 줄로 남아 있었다.

관계는 사람이 지정한다(C13533). 여기서 더하는 것은 **저장된 포인터를 거꾸로 읽는 것**뿐이다 —
supersedes 는 "내가 무엇을 대체했나", superseded_by 는 "나를 무엇이 대체했나". 판단은 없다.
"""
from __future__ import annotations

import pytest

from casebook.core import review
from casebook.core.worktrail import Worktrail
from tests.test_threads import _git_repo


@pytest.fixture()
def wt():
    from casebook.core.db import SqliteDB
    from casebook.core import threads
    w = Worktrail(SqliteDB(":memory:"))
    threads.ensure_change(w.db)      # 커밋이 한 번도 없으면 change 테이블이 없다(회고가 그걸 읽는다)
    w.signup("사람", "s@x.test", "pw")
    return w


def _dec(evs):
    return {e["id"]: e for e in evs if e["kind"] == "decision"}


def test_대체된_결정과_대체한_결정이_양쪽에서_보인다(wt, tmp_path):
    cid = wt.open_thread(1, "조리법", _git_repo(tmp_path / "r"))["case_id"]
    a = wt.decide(1, cid, "시간창은 앞뒤 5분", authority="user")["decision_id"]
    b = wt.decide(1, cid, "시간창을 앞뒤 10분으로 넓힌다", authority="user", supersedes=a)["decision_id"]

    d = _dec(review.events_for_cases(wt.db, 1, [cid])[cid])
    assert d[a]["superseded_by"] == b and d[a]["supersedes"] is None      # 나를 대체한 것
    assert d[b]["supersedes"] == a and d[b]["superseded_by"] is None      # 내가 대체한 것


def test_사슬은_가운데도_대체된_것으로_보인다(wt, tmp_path):
    """실데이터의 D10599 → D11721 → D11968 꼴. 가운데 것도 이미 대체됐다."""
    cid = wt.open_thread(1, "사슬", _git_repo(tmp_path / "r2"))["case_id"]
    a = wt.decide(1, cid, "1판", authority="user")["decision_id"]
    b = wt.decide(1, cid, "2판", authority="user", supersedes=a)["decision_id"]
    c = wt.decide(1, cid, "3판", authority="user", supersedes=b)["decision_id"]

    d = _dec(review.events_for_cases(wt.db, 1, [cid])[cid])
    assert [d[a]["superseded_by"], d[b]["superseded_by"], d[c]["superseded_by"]] == [b, c, None]


def test_대체가_없으면_칸도_비어_있다(wt, tmp_path):
    cid = wt.open_thread(1, "홑", _git_repo(tmp_path / "r3"))["case_id"]
    a = wt.decide(1, cid, "그대로 간다", authority="user")["decision_id"]
    d = _dec(review.events_for_cases(wt.db, 1, [cid])[cid])
    assert d[a]["supersedes"] is None and d[a]["superseded_by"] is None


def test_다른_스레드에서_대체해도_보인다(wt, tmp_path):
    """repo 범위 결정은 형제 스레드에서 대체된다. 스레드 화면은 그 스레드 하나만 넘겨 부르므로
    역방향 지도는 원장 전체에서 만든다 — 아니면 대체된 줄이 자기 화면에서 멀쩡해 보인다."""
    repo = _git_repo(tmp_path / "r4")
    one = wt.open_thread(1, "먼저", repo)["case_id"]
    a = wt.decide(1, one, "저장소 공통 규칙", authority="user", scope="repo")["decision_id"]
    two = wt.open_thread(1, "나중", repo)["case_id"]
    b = wt.decide(1, two, "규칙을 고친다", authority="user", scope="repo", supersedes=a)["decision_id"]

    only_one = _dec(review.events_for_cases(wt.db, 1, [one])[one])       # 대체한 결정은 이 목록에 없다
    assert only_one[a]["superseded_by"] == b

    recent = review.recent_events(wt.db, 1, one)
    assert [e for e in recent if e["kind"] == "decision"][0]["superseded_by"] == b


def test_제약도_대체됨을_읽고_선언은_그대로(wt, tmp_path):
    """41호의 범위 제약(C13532)은 결정만이었다. 확장 52호(2026-09-11)에서 제약으로 넓혔다 — 사용자가 스레드
    화면에서 같은 문장의 제약 둘(하나는 대체된 것)이 같은 무게로 보이는 것을 지적했다. 선언(focus·open·next)은
    supersedes 사슬을 화면이 따로 보이므로 그대로다."""
    cid = wt.open_thread(1, "범위", _git_repo(tmp_path / "r5"))["case_id"]
    a = wt.constrain(1, cid, "운영 DB 는 안 건드린다", authority="user")["constraint_id"]
    b = wt.constrain(1, cid, "운영 DB 도 읽기는 된다", authority="user", supersedes=a)["constraint_id"]
    evs = review.events_for_cases(wt.db, 1, [cid])[cid]
    con = {e["id"]: e for e in evs if e["kind"] == "constraint"}
    assert con[a]["superseded_by"] == b and con[a]["superseded_by_head"] == "운영 DB 도 읽기는 된다"
    assert con[b]["supersedes"] == a and con[b].get("superseded_by") is None
    assert all("superseded_by" not in e for e in evs if e["kind"] in ("focus", "open", "next"))


def test_대체한_결정의_문장과_스레드가_함께_온다(wt, tmp_path):
    """확장 43호 — 번호만으로는 무엇으로 바뀌었는지 그 자리에서 모른다. 화면이 문장 머리를 보이고
    그 결정이 사는 스레드로 데려갈 수 있어야 한다."""
    repo = _git_repo(tmp_path / "r6")
    one = wt.open_thread(1, "먼저", repo)["case_id"]
    a = wt.decide(1, one, "시간창은 앞뒤 5분", authority="user", scope="repo")["decision_id"]
    two = wt.open_thread(1, "나중", repo)["case_id"]                      # 다른 스레드에서 대체한다
    b = wt.decide(1, two, "시간창을 앞뒤 10분으로 넓힌다.\n둘째 줄은 머리에 안 들어간다.",
                  authority="user", scope="repo", supersedes=a)["decision_id"]

    d = _dec(review.events_for_cases(wt.db, 1, [one])[one])
    assert d[a]["superseded_by"] == b                                     # 번호는 그대로
    assert d[a]["superseded_by_head"] == "시간창을 앞뒤 10분으로 넓힌다."    # 첫 줄만
    assert d[a]["superseded_by_case"] == two                              # 다른 스레드여도 데려갈 수 있다


def test_대체가_없으면_옆_칸도_비어_있다(wt, tmp_path):
    cid = wt.open_thread(1, "홑2", _git_repo(tmp_path / "r7"))["case_id"]
    a = wt.decide(1, cid, "그대로 간다", authority="user")["decision_id"]
    d = _dec(review.events_for_cases(wt.db, 1, [cid])[cid])
    assert d[a]["superseded_by_head"] is None and d[a]["superseded_by_case"] is None
