"""확장 21호 — intake: 받는 문. 보장할 것:
(1) 스레드가 worktree 에 열리고 items 가 각각 byte-exact evidence(external 턴, source.item) 로 들어간다
(2) ask 가 있으면 묶음 턴이 끝난 뒤 입력 턴이 제출되고 워커 뒤 답변이 생긴다 — 모델은 items 를 이전 메시지로 본다
(3) ask 가 없으면 답변 턴이 없다 (4) 항목·묶음 상한을 넘으면 거부하고 아무것도 만들지 않는다
(5) HTTP POST /app/intake 가 같은 모양으로 응답한다 (6) 골든 시퀀스 불변(기존 테스트)
(7) 확장 30호 — topic 이 필수이고("미분류" 거절) 연 스레드는 그 주제에 놓인다
(8) 확장 37호 — ask 가 있으면 next 를 owner=user 로 선언한다(자동 생성 스레드가 차례 없이 열리던 구멍)
(9) 확장 39호 — intake 로 들어온 스레드는 현황에서 origin="intake" 다(에이전트와 한 작업과 갈라 보인다)
(10) 확장 39호 — 그 분류는 여는 순간 굳는다: 랩 판정을 note_turn 으로 기록해도 작업으로 뒤집히지 않는다."""
from __future__ import annotations

import pytest

from casebook.core.errors import InputError
from tests.drive import USER
from tests.test_threads import _git_repo

ITEMS = [
    {"name": "prom error rate", "content": "# prometheus payment error rate\n0.0 0.0 0.5 0.8\t\n"},
    {"name": "logs payment", "content": "# opensearch payment WARN\nInvalid token  \n"},
]


def test_intake_는_스레드와_묶음_evidence_와_첫_턴을_만든다(app, tmp_path):
    wt = _git_repo(tmp_path / "lab")
    r = app.intake(USER, "결제 장애 자동 조사", wt, ITEMS, "What happened? Use only the material above.", action_key="inc-1", topic="장애 랩")
    assert r["status"] == "pending" and r["turn_id"] and len(r["evidence_ids"]) == 2
    assert r["topic"]["name"] == "장애 랩" and r["case_id"] in app.list_topics(USER)[0]["case_ids"]   # (7) 여는 순간 주제에
    nx = app.resume(USER, r["case_id"])["next"]                                                     # (8) 다음 손은 사람
    assert nx["statement"] == "첫 답변을 확인한다." and nx["owner"] == "user"
    assert app.status(USER)["topics"][0]["threads"][0]["phase"] == "mine"
    t = r["case_id"]
    assert app.current_thread(USER, wt)["case_id"] == t
    for eid, it in zip(r["evidence_ids"], ITEMS):
        ev = app.db.get("evidence", eid)
        assert ev["content"] == it["content"] and ev["source"] == {"via": "intake", "item": it["name"], "bundle": "inc-1"}
    # 답변 직전의 모델 입력 — 묶음이 들어가 있고, 첫 답변은 묶음을 근거로 한다
    from casebook.core.workers import _build_context
    core, _lang, prompt = _build_context(app.db, app.db.get("turn", r["turn_id"]))
    app.run_workers()
    turns = app.get_case(USER, t)["turns"]
    assert [x["turn_kind"] for x in turns] == ["external", "external", "input"]
    assert turns[-1]["answer_status"] == "stored" and all(x["status"] == "finalized" for x in turns)
    assert "Invalid token" in prompt and "payment error rate" in prompt and "What happened?" in prompt
    # 묶음은 '지난 Turn의 기록' 이 아니라 이번 입력이다 — 이전 메시지로 넣으면 첫 답변이 "새 자료가 없다" 고 썼다(랩 2·3회차, case 462)
    from casebook.core import prompts
    assert prompts.CTX_HEADER not in prompt and prompts.CTX_TURN_NOTICE not in prompt
    cur = prompt.index(prompts.CTX_CURRENT_INPUT_HEADER)
    assert prompt.index("payment error rate") > cur and prompt.index("Invalid token") > cur
    assert "아래 3건이 이번 Turn에 새로 들어온 입력" in prompt and prompt.index("Invalid token") < prompt.index("What happened?")
    # 같은 action_key 로 다시 부르면 새 스레드가 아니라 같은 턴들 — 멱등은 턴 단위
    again = app.intake(USER, "결제 장애 자동 조사", wt, ITEMS, "again", action_key="inc-1", topic="장애 랩")
    assert again["case_id"] != t                                     # 스레드는 새로 열린다(스레드 멱등 키는 없다)


def test_ask_없으면_묶음만(app, tmp_path):
    wt = _git_repo(tmp_path / "lab")
    r = app.intake(USER, "묶음만", wt, ITEMS, None, action_key="inc-2", topic="장애 랩")
    assert app.resume(USER, r["case_id"])["next"] is None        # (8) 질문이 없으면 다음도 없다 — 미정으로 둔다
    assert r["status"] == "bundle_only" and r["turn_id"] is None
    app.run_workers()
    turns = app.get_case(USER, r["case_id"])["turns"]
    assert [x["turn_kind"] for x in turns] == ["external", "external"] and all(x["answer_status"] == "not_attempted" for x in turns)


def test_상한을_넘으면_거부하고_아무것도_안_만든다(app, tmp_path):
    wt = _git_repo(tmp_path / "lab")
    before = app.db.count("case")
    with pytest.raises(InputError, match="cap is 100000"):
        app.intake(USER, "big", wt, [{"name": "huge", "content": "x" * 100_001}], None, action_key="inc-3", topic="장애 랩")
    with pytest.raises(InputError, match="bundle is"):
        app.intake(USER, "big", wt, [{"name": f"p{i}", "content": "x" * 90_000} for i in range(5)], None, action_key="inc-4", topic="장애 랩")
    with pytest.raises(InputError, match="non-empty"):
        app.intake(USER, "empty", wt, [], None, action_key="inc-5", topic="장애 랩")
    assert app.db.count("case") == before


def test_intake_는_topic_이_필수다(app, tmp_path):
    """(7) 확장 30호 — 누락도 "미분류" 도 거절, 스레드는 안 열린다. 거절 문구에 기존 주제 목록."""
    wt = _git_repo(tmp_path / "lab")
    before = app.db.count("case")
    with pytest.raises(InputError, match=r"topic is required.*Existing topics: \(none yet\)"):
        app.intake(USER, "주제 없이", wt, ITEMS, None, action_key="inc-6")
    with pytest.raises(InputError, match=r"\"미분류\" is not accepted"):
        app.intake(USER, "미분류도 누락", wt, ITEMS, None, action_key="inc-7", topic="미분류")
    assert app.db.count("case") == before and app.list_topics(USER) == []


def test_http_intake(app, tmp_path):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    tok = client.post("/auth/auth/signup", json={"name": "i", "email": "i@x.test", "password": "pw"}).json()["authToken"]
    h = {"Authorization": "Bearer " + tok}
    wt = _git_repo(tmp_path / "lab")
    r = client.post("/app/intake", json={"focus": "f", "worktree": wt, "items": ITEMS, "ask": "why?", "action_key": "inc-http", "topic": "장애 랩"}, headers=h)
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"case_id", "repo", "worktree", "topic", "evidence_ids", "turn_id", "status"} and r.json()["status"] == "pending"
    assert r.json()["topic"]["name"] == "장애 랩"
    no_topic = client.post("/app/intake", json={"focus": "f", "worktree": wt, "items": ITEMS, "action_key": "inc-http-2"}, headers=h)
    assert no_topic.status_code == 400 and "topic is required" in no_topic.text
    bad = client.post("/app/intake", json={"focus": "f", "worktree": wt, "items": [], "action_key": "x"}, headers=h)
    assert bad.status_code == 400


def test_intake_스레드는_현황에서_조사로_갈린다(app, tmp_path):
    """확장 39호 — 기록 도구와 조사 도구를 가른다. 사람이 물은 적 없는 스레드가 현황에 섞이지 않는다."""
    from casebook.core import status
    wt = _git_repo(tmp_path / "lab")
    lab = app.intake(USER, "랩 회차", wt, ITEMS, "무슨 일이 있었나?", action_key="inc-origin", topic="장애 랩")["case_id"]
    app.run_workers()
    mine = app.open_thread(USER, "내가 에이전트와 한 작업", wt)["case_id"]
    app.external_turn(USER, mine, "$ pytest -q\n209 passed", action_key="o1", note="전부 통과했다")
    app.run_workers()

    org = status.origins(app.db, USER, [lab, mine])
    assert org[lab] == "intake" and org[mine] == "agent"

    s = app.status(USER)
    rows = {t["case_id"]: t for tp in s["topics"] for t in tp["threads"]}
    rows.update({t["case_id"]: t for t in s["unassigned"]})
    assert rows[lab]["origin"] == "intake" and rows[mine]["origin"] == "agent"
    assert s["counts"]["intake"] == 1


def test_랩_판정을_기록해도_분류가_뒤집히지_않는다(app, tmp_path):
    """확장 39호 — origin 을 증거로 도출하면 랩의 정상 흐름(사람 판정 → 기록 → 닫는다)이 분류를 뒤집는다.
    #455·#460·#463 이 전부 그렇게 끝났으므로, 여는 순간 저장해 굳힌다."""
    from casebook.core import threads
    wt = _git_repo(tmp_path / "lab")
    lab = app.intake(USER, "랩 회차", wt, ITEMS, "무슨 일이 있었나?", action_key="inc-freeze", topic="장애 랩")["case_id"]
    app.run_workers()
    assert threads.origins(app.db, USER, [lab])[lab] == "intake"

    app.external_turn(USER, lab, "사용자: payment 가 맞다", action_key="judge", note="첫 답변이 맞다고 판정했다")
    app.run_workers()
    app.declare(USER, lab, "next", "닫는다", owner="user")
    assert threads.origins(app.db, USER, [lab])[lab] == "intake"      # 뒤집히지 않는다
    assert app.status(USER)["counts"]["intake"] == 1
