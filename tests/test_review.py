"""확장 24호 — Review(회고). 보장할 것:
(1) topic 은 스레드를 여러 주제에 넣을 수 있고(다대다) 멱등이며, 어느 주제에도 없는 스레드는 unassigned 로 남는다
(2) 케이스별 사건은 저장하지 않고 도출한다 — opened(focus 합침) · decision · constraint · next/open · note(결론) ·
    evidence(결론 없는 외부 턴) · asked(입력 턴) · commit(change) · closed — 시간순
(3) topic.repos 의 저장소에서 어느 스레드에도 안 붙은 git 커밋이 그 주제의 사건으로 들어온다(스레드 이전 시절)
(4) 남의 케이스는 넣을 수 없다(404) (5) HTTP GET /app/review · GET/POST /app/topic (6) MCP assign_topic 은 이름으로 찾거나 만든다."""
from __future__ import annotations

import time

import pytest

from casebook.core.errors import NotFoundError
from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import USER
from tests.test_git_changes import _commit
from tests.test_mcp_server import tools  # noqa: F401  — MCP 도구 픽스처
from tests.test_threads import _git_repo


def _tick():
    time.sleep(0.002)   # 원장·턴·change 는 다른 표이므로 같은 ms 안에서는 순서를 정할 수 없다 — 실사용에선 안 생기는 동률


def test_topic_assign_은_다대다_멱등_이고_미분류가_남는다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    a = app.open_thread(USER, "A", wt)["case_id"]; b = app.open_thread(USER, "B", wt)["case_id"]; c = app.open_thread(USER, "C", wt)["case_id"]
    t1 = app.create_topic(USER, "mcp", "agents", case_ids=[a, b])
    t2 = app.create_topic(USER, "lab", case_ids=[b])
    assert t1["case_ids"] == sorted([a, b]) and t2["case_ids"] == [b]
    assert app.assign_topic(USER, t1["topic_id"], [a])["case_ids"] == sorted([a, b])        # 멱등
    r = app.review(USER)
    assert [t["name"] for t in r["topics"]] == ["mcp", "lab"] and c in r["unassigned"] and a not in r["unassigned"]
    assert app.topic_by_name(USER, "lab", case_ids=[c])["case_ids"] == sorted([b, c])          # 이름으로 찾아 넣는다
    assert app.assign_topic(USER, t2["topic_id"], [c], remove=True)["case_ids"] == [b]
    with pytest.raises(NotFoundError):
        app.assign_topic(USER, 999, [a])


def test_사건은_도출된다_시간순(app, tmp_path):
    wt = _git_repo(tmp_path / "w"); _commit(wt, "init")
    t = app.open_thread(USER, "make the list faster", wt, authority="user")["case_id"]; _tick()
    app.decide(USER, t, "cache the vitals", "one query", [], authority="user"); _tick()
    app.constrain(USER, t, "no schema change", "hackathon freeze", authority="user", scope="repo"); _tick()
    app.external_turn(USER, t, "$ pytest\n3 passed", action_key="k1", note="tests pass: the cache is correct"); _tick()
    app.external_turn(USER, t, "raw log line", action_key="k2"); _tick()                       # 결론 없는 evidence
    app.run_workers()
    app.declare(USER, t, "next", "1. ship it", owner="user"); _tick()
    h = _commit(wt, "add vitals cache", "v.py"); app.record_commit(USER, wt); _tick()
    app.submit_turn(USER, t, PLACEHOLDER_INPUT, action_key="k3"); app.run_workers(); _tick()  # 사람이 물었다
    app.close_thread(USER, t)
    ev = app.review(USER)["cases"][t]["events"]
    kinds = [e["kind"] for e in ev]
    assert kinds == ["opened", "decision", "constraint", "note", "evidence", "next", "commit", "asked", "closed"]
    assert ev[0]["text"] == "make the list faster" and ev[0]["authority"] == "user"          # 여는 순간의 focus 는 opened 줄에 합쳐진다
    assert ev[0]["focus"] == "make the list faster" and "focus" not in kinds                  # 줄에는 제목, focus 는 펼침용 필드
    assert ev[1]["reason"] == "one query" and ev[2]["scope"] == "repo"
    assert ev[3]["text"].startswith("tests pass") and ev[3]["observed"].startswith("$ pytest")
    assert ev[4]["text"] == "raw log line" and ev[6]["head"] == h and ev[6]["via"] == "thread"
    assert ev[8]["text"] == "resolved"
    assert all(ev[i]["at"] <= ev[i + 1]["at"] for i in range(len(ev) - 1))
    case = app.review(USER)["cases"][t]
    assert case["state"] == "closed" and case["next"] == "1. ship it" and case["repo"] == "github.com/acme/widgets"


def test_주제의_저장소_git_커밋은_스레드_이전_시절도_들어온다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    h0 = _commit(wt, "before any thread")                                                    # 스레드 층 이전
    t = app.open_thread(USER, "later", wt)["case_id"]
    h1 = _commit(wt, "under thread", "f.py"); app.record_commit(USER, wt)
    top = app.create_topic(USER, "port", repos=["github.com/acme/widgets"], case_ids=[t])
    r = app.review(USER)
    git = r["topics"][0]["git"]
    assert [g["head"] for g in git] == [h0] and git[0]["via"] == "git" and git[0]["case_id"] is None   # h1 은 스레드에 붙어 있어 제외
    assert [e["head"] for e in r["cases"][t]["events"] if e["kind"] == "commit"] == [h1]
    other = app.create_topic(USER, "ghost", repos=["github.com/none/none"])
    assert app.review(USER)["topics"][1]["git"] == []                                        # 디스크에 없는 저장소 → 빈 목록


def test_남의_케이스는_못_넣는다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "mine", wt)["case_id"]
    other = app.db.add("user", {"name": "o", "email": "o@x.test", "password": "x"})["id"]
    top = app.create_topic(other, "theirs")
    with pytest.raises(NotFoundError):
        app.assign_topic(other, top["topic_id"], [t])


def test_http_review_and_topic(app, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    tok = client.post("/auth/auth/signup", json={"name": "r", "email": "r@x.test", "password": "pw"}).json()["authToken"]
    h = {"Authorization": "Bearer " + tok}
    uid = app.db.get_by("user", "email", "r@x.test")["id"]
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(uid, "http thread", wt)["case_id"]
    r = client.post("/app/topic", json={"name": "axis 1", "summary": "s", "case_ids": [t], "repos": ["github.com/acme/widgets"]}, headers=h)
    assert r.status_code == 200 and r.json()["case_ids"] == [t]
    r2 = client.post("/app/topic", json={"topic_id": r.json()["topic_id"], "case_ids": [t], "remove": True}, headers=h)
    assert r2.json()["case_ids"] == []
    rv = client.get("/app/review", headers=h).json()
    assert set(rv) == {"generated_at", "topics", "cases", "unassigned", "note"}
    assert rv["topics"][0]["name"] == "axis 1" and rv["unassigned"] == [t] and rv["cases"][str(t)]["events"][0]["kind"] == "opened"
    assert client.get("/app/topic", headers=h).json()["topics"][0]["repos"] == ["github.com/acme/widgets"]
    assert client.post("/app/topic", json={"name": "", "case_ids": []}, headers=h).status_code == 400


def test_mcp_assign_topic(tools, tmp_path):
    t, app, _ = tools
    wt = _git_repo(tmp_path / "w")
    cid = t.open_thread("mcp thread", wt, topic="테스트")["case_id"]
    out = t.assign_topic("MCP for code agents", [cid], summary="axis 1")
    assert out.startswith("topic #") and f"[{cid}]" in out
    assert t.assign_topic("MCP for code agents", [cid]) == out                               # 같은 이름 → 같은 주제, 멱등


def test_답변_실패_턴과_recovery_턴은_같은_질문에_상태가_붙는다(tmp_path):
    # 2026-09-06 case 450: 자리표시자 키로 첫 답변이 failed → recovery 로 다시 답변. 같은 문장이 두 줄로 보여 "기록을 두번한건가".
    from tests.conftest import FakeLLM, FakeSearch, _build_app
    llm = FakeLLM(); app = _build_app(llm, FakeSearch())
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "why 500", wt)["case_id"]; _tick()
    llm.fail_next.add("answer")
    src = app.submit_turn(USER, t, PLACEHOLDER_INPUT, action_key="a"); app.run_workers(); _tick()
    app.recover(USER, t, src["turn_id"], action_key="r"); app.run_workers(); _tick()
    asked = [e for e in app.review(USER)["cases"][t]["events"] if e["kind"] == "asked"]
    assert len(asked) == 2 and asked[0]["text"] == asked[1]["text"]                      # 원장은 append-only — 둘 다 남는다
    assert asked[0]["answer_status"] == "failed" and asked[0]["turn_kind"] == "input" and asked[0]["error"]
    assert asked[1]["turn_kind"] == "recovery" and asked[1]["answer_status"] == "stored" and asked[1]["source_turn"] == asked[0]["turn"]


def test_닫은_뒤_붙인_결과는_결과_줄이다(app, tmp_path):
    """확장 48호 — 화면에서 결과 없이 닫은 줄과, 뒤에 에이전트가 채운 결과 줄이 따로 보인다(later)."""
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "closed from screen", wt)["case_id"]; _tick()
    app.set_status(USER, t, "resolved"); _tick()
    app.close_thread(USER, t, "filled later"); _tick()
    ev = app.review(USER)["cases"][t]["events"]
    assert [(e["kind"], e["text"], e.get("later", False)) for e in ev[-2:]] == [("closed", "resolved", False), ("closed", "filled later", True)]
    assert app.review(USER)["cases"][t]["result"] == "filled later"


def test_대체된_제약도_대체됨으로_읽힌다(app, tmp_path):
    """확장 52호 — 결정처럼 제약도 supersedes 포인터를 거꾸로 읽어 superseded_by 를 준다."""
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "rule thread", wt)["case_id"]; _tick()
    c1 = app.constrain(USER, t, "first rule", "r", authority="user")["constraint_id"]; _tick()
    c2 = app.constrain(USER, t, "first rule, reworded", "r", authority="user", supersedes=c1)["constraint_id"]; _tick()
    ev = {e["id"]: e for e in app.review(USER)["cases"][t]["events"] if e["kind"] == "constraint"}
    assert ev[c1]["superseded_by"] == c2 and ev[c1]["superseded_by_head"] == "first rule, reworded"
    assert ev[c2]["supersedes"] == c1 and "superseded_by" not in ev[c2] or ev[c2]["superseded_by"] is None
