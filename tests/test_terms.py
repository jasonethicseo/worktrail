"""확장 55호 — 지은 이름에 뜻 한 줄(#502). 보장할 것:
(1) define 은 스레드 범위로 resume.terms 에, 저장소 범위로 형제 스레드의 repo_state.terms 에 보인다
(2) 대체하면 옛 것은 사라지고 새 것만 남는다 (3) 뜻은 제목 규칙(첫 줄 표시폭 120칸)을 따르고 이름은 40자 한 줄
(4) 회고 사건에 "term" 으로 나오고 대체된 것은 superseded_by 를 얻는다 (5) fresh 모드에서도 terms 는 준다 — 어휘라서."""
from __future__ import annotations

import pytest

from casebook.core import review
from casebook.core.errors import InputError
from tests.drive import USER
from tests.test_threads import _git_repo


def test_define_투영_대체_범위(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    a = app.open_thread(USER, "랩 실험", wt)["case_id"]
    b = app.open_thread(USER, "다른 스레드", wt)["case_id"]
    t1 = app.define(USER, a, "3회차", "장애 시뮬 랩의 세 번째 실험\n\nrecommendationCacheFailure 로 장애를 켜고 첫 답변을 채점한 한 바퀴.", authority="user")
    assert t1["term"] == "3회차" and t1["scope"] == "thread"
    r = app.resume(USER, a, "fresh")
    assert [x["term"] for x in r["terms"]] == ["3회차"] and r["terms"][0]["meaning"].startswith("장애 시뮬 랩의")
    assert app.resume(USER, b)["terms"] == []                                          # 스레드 범위는 남에게 안 보인다
    t2 = app.define(USER, a, "조리법", "첫 답변 전에 자동으로 모으는 텔레메트리 묶음의 규칙", authority="user", scope="repo")
    assert [x["term"] for x in app.resume(USER, b)["repo_state"]["terms"]] == ["조리법"]  # 저장소 범위는 형제에게 보인다
    t3 = app.define(USER, a, "3회차", "장애 시뮬 랩의 세 번째 실험 (recommendationCacheFailure)", authority="user", supersedes=t1["term_id"])
    assert [x["term_id"] for x in app.resume(USER, a)["terms"]] == [t3["term_id"]]       # 대체: 옛 것은 사라진다
    with pytest.raises(InputError, match="meaning: the first line is a title"):
        app.define(USER, a, "1층", "첫 묶음 조리법 — 시간창 앞뒤 5분, 대상은 알림 서비스와 실패 trace 가 지나간 서비스, 상한 항목당 100KB", authority="user")
    with pytest.raises(InputError, match="term: the name"):
        app.define(USER, a, "이름이 너무 길어서 마흔 자를 넘기면 그것은 이름이 아니라 문장이다 그렇지 않은가", "뜻")
    ev = {e["id"]: e for e in review.events_for_cases(app.db, USER, [a])[a] if e["kind"] == "term"}
    assert ev[t1["term_id"]]["superseded_by"] == t3["term_id"] and ev[t1["term_id"]]["superseded_by_head"] == "3회차"
    assert ev[t3["term_id"]]["text"].startswith("3회차 = 장애 시뮬 랩의 세 번째 실험")
