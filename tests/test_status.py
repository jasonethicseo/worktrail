"""확장 31호 — 현황·단계·차례·결과·결론. 보장할 것:
(1) 스레드 단계는 선언된 차례 그대로다(확장 37호): 종료 · user=내 차례 · watch=관찰 중 · 에이전트 이름=에이전트에게 ·
    없으면 미정. 시간 문턱도 worktree 바인딩도 보지 않는다.
(2) 주제 단계: 열린 스레드 없음 → 완료, 하나라도 내 차례·에이전트·미정 → 진행, 전부 관찰 → 휴면
(3) next 는 owner 가 필수이고(36호) 짧은 손잡이만 받는다; owner 는 next 에만 붙고 resume 이 싣는다
(4) close_thread(result) 의 결과 한 줄은 원장(case_status_changed.result)에 남고 현황·회고·스레드 화면에 나온다
(5) 주제 결론은 topic.conclusion 에, 없으면 현황이 마지막으로 닫힌 스레드의 결과를 대신 보인다
(6) GET /app/status 가 주제로 묶은 목록을 주고, POST /app/topic 이 conclusion 을 받는다.
(7) 확장 32호 — 행마다 last_event(마지막 노트·결정·커밋·질문·닫힘), 맨 위 latest(결정·닫힘·열림·제약·배제·질문, 최신 먼저)."""
from __future__ import annotations

import pytest

from casebook.core import phase
from casebook.core.errors import InputError
from tests.drive import USER
from tests.test_threads import _git_repo

H = 3600 * 1000


def test_thread_phase_는_차례_그대로다():
    tp = phase.thread_phase
    assert tp("closed", "user") == "closed" and tp("closed", None) == "closed"
    assert tp("open", "user") == "mine"
    assert tp("open", "watch") == "watch"
    assert tp("open", "codex") == "agent" and tp("open", "claude") == "agent"
    assert tp("open", None) == "unset"      # 선언에 없으면 없다고 말한다 — 추정하지 않는다


def test_topic_phase_규칙():
    assert phase.topic_phase([]) == "done"
    assert phase.topic_phase(["watch", "watch"]) == "quiet"
    assert phase.topic_phase(["watch", "mine"]) == "active"
    assert phase.topic_phase(["unset"]) == "active"


def test_owner_는_next_에만_짧은_손잡이로(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(USER, "goal", wt)["case_id"]
    r = app.declare(USER, t, "next", "커넥터를 등록한다", owner=" User ")
    assert r["owner"] == "user" and app.resume(USER, t)["next"]["owner"] == "user"
    with pytest.raises(InputError, match="owner belongs to next"):
        app.declare(USER, t, "open", "질문", owner="user")
    with pytest.raises(InputError, match="short handle"):
        app.declare(USER, t, "next", "x", owner="Codex 차례")
    assert app.declare(USER, t, "next", "지켜본다", owner="watch")["owner"] == "watch"
    with pytest.raises(InputError, match="next needs an owner"):        # 36호 — 빠지면 거절, 이어 선언해도 안 물려받는다
        app.declare(USER, t, "next", "아무나")


def test_status_는_주제로_묶고_단계와_결과를_보인다(app, tmp_path):
    a = _git_repo(tmp_path / "a"); b = _git_repo(tmp_path / "b", remote="git@github.com:acme/other.git")
    t1 = app.open_thread(USER, "first", a)["case_id"]                      # next 없음 → 미정
    t2 = app.open_thread(USER, "second", a)["case_id"]                     # 곧 닫힘(결과 있음)
    t3 = app.open_thread(USER, "third", b)["case_id"]                      # watch → 관찰 중
    t4 = app.open_thread(USER, "fourth", b)["case_id"]                     # user → 내 차례
    t5 = app.open_thread(USER, "loose", b)["case_id"]                      # 어느 주제에도 없음
    app.declare(USER, t3, "next", "며칠 지켜본다", owner="watch")
    app.declare(USER, t4, "next", "결정을 내린다", owner="user")
    app.close_thread(USER, t2, result="서버 DB 가 진실이 됐다")
    app.create_topic(USER, "대회", "출품", case_ids=[t1, t2])
    app.create_topic(USER, "랩", case_ids=[t3, t4])
    s = app.status(USER)
    assert [t["name"] for t in s["topics"]] == ["랩", "대회"] or [t["name"] for t in s["topics"]] == ["대회", "랩"]
    by = {t["name"]: t for t in s["topics"]}
    assert by["대회"]["phase"] == "active" and by["랩"]["phase"] == "active"
    rows = {t["case_id"]: t for t in by["대회"]["threads"]}
    assert rows[t1]["phase"] == "unset" and rows[t1]["state"] == "open"      # next 를 선언하지 않았다
    assert rows[t2]["phase"] == "closed" and rows[t2]["result"] == "서버 DB 가 진실이 됐다" and rows[t2]["closed_at"]
    assert by["대회"]["open_count"] == 1 and by["대회"]["closed_count"] == 1
    assert by["대회"]["conclusion"] is None and by["대회"]["last_result"] == "서버 DB 가 진실이 됐다"   # 결론 없으면 마지막 결과
    lab = {t["case_id"]: t for t in by["랩"]["threads"]}
    assert lab[t3]["phase"] == "watch" and lab[t3]["owner"] == "watch"
    assert lab[t4]["phase"] == "mine" and lab[t4]["owner"] == "user"
    assert [t["case_id"] for t in by["랩"]["threads"]] == [t4, t3]              # 내 차례가 관찰보다 먼저
    assert [t["case_id"] for t in s["unassigned"]] == [t5] and s["unassigned"][0]["repo"] == "github.com/acme/other"
    assert s["counts"] == {"open": 4, "mine": 1, "agent": 0, "watching": 1, "unset": 2, "closed_recent": 1,
                           "topics": 2, "repos": 2, "intake": 0}
    assert all(t["origin"] == "agent" for t in rows.values())   # 확장 39호 — MCP 로 들어온 것은 전부 작업 스레드
    # (7) 사실: 마지막으로 한 일과 최근 일어난 일 — 선언(next)은 사실이 아니라 약속이라 빠진다
    assert rows[t2]["last_event"]["kind"] == "closed" and rows[t2]["last_event"]["text"] == "서버 DB 가 진실이 됐다"
    assert lab[t4]["last_event"]["kind"] == "opened" and lab[t4]["last_event"]["text"] == "fourth"
    assert [e["kind"] for e in s["latest"]][:1] == ["closed"] and s["latest"][0]["case_id"] == t2 and s["latest"][0]["title"] == "second"
    assert {e["kind"] for e in s["latest"]} == {"closed", "opened"} and len(s["latest"]) == 6
    # 주제가 마무리되면 done, 결론을 붙이면 그 줄
    app.close_thread(USER, t1, result="끝")
    s2 = app.status(USER); done = next(t for t in s2["topics"] if t["name"] == "대회")
    assert done["phase"] == "done" and done["last_result"] == "끝" and s2["topics"][-1]["name"] == "대회"   # 마무리된 주제는 맨 아래
    app.topic_by_name(USER, "대회", conclusion="원격 문으로 제출했다")
    done = next(t for t in app.status(USER)["topics"] if t["name"] == "대회")
    assert done["conclusion"] == "원격 문으로 제출했다" and done["concluded_at"]
    # 스레드 화면과 회고에도 같은 사실
    st = app.thread_state(USER, t4)
    assert st["phase"] == "mine" and st["owner"] == "user" and [x["name"] for x in st["topics"]] == ["랩"]
    assert st["repo"] == "github.com/acme/other"                                              # 두 축의 교차점 — 저장소도 싣는다
    rv = app.review(USER)
    assert rv["cases"][t2]["result"] == "서버 DB 가 진실이 됐다" and rv["cases"][t2]["phase"] == "closed"
    closed_ev = [e for e in rv["cases"][t2]["events"] if e["kind"] == "closed"][0]
    assert closed_ev["text"] == "서버 DB 가 진실이 됐다" and closed_ev["result"] == "서버 DB 가 진실이 됐다"
    assert {t["name"]: t["phase"] for t in rv["topics"]} == {"대회": "done", "랩": "active"}
    assert next(t for t in rv["topics"] if t["name"] == "대회")["conclusion"] == "원격 문으로 제출했다"


def test_http_status_와_topic_conclusion(app, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    tok = client.post("/auth/auth/signup", json={"name": "s", "email": "s@x.test", "password": "pw"}).json()["authToken"]
    h = {"Authorization": "Bearer " + tok}
    me = client.get("/auth/auth/me", headers=h).json()["id"]
    wt = _git_repo(tmp_path / "w")
    t = app.open_thread(me, "web", wt)["case_id"]
    top = client.post("/app/topic", json={"name": "축", "case_ids": [t]}, headers=h).json()
    s = client.get("/app/status", headers=h).json()
    assert set(s) == {"generated_at", "topics", "unassigned", "counts", "latest", "stream", "prior"} and s["topics"][0]["threads"][0]["case_id"] == t
    assert s["prior"] is None                      # 확장 42호 — 설치 전 기록이 없으면 한 줄도 없다
    assert s["topics"][0]["phase"] == "active" and s["topics"][0]["threads"][0]["phase"] == "unset"
    r = client.post("/app/topic", json={"topic_id": top["topic_id"], "conclusion": "다 됐다"}, headers=h).json()
    assert r["conclusion"] == "다 됐다" and client.get("/app/status", headers=h).json()["topics"][0]["conclusion"] == "다 됐다"
    assert client.get("/app/status").status_code == 401


def test_stream_은_종류를_가리지_않고_최근_40개다(app, tmp_path):
    """확장 64호 (#512) — "지금" 띠. latest(결정·닫힘·열림…)와 달리 노트·커밋·선언까지 전부 흐르고, 노트는 갈래를 싣는다.
    증거는 흐르지 않는다(스레드의 증거 칸에 있다). 최신이 먼저, 40개까지."""
    from casebook.adapters.mcp_server import Tools
    wt = _git_repo(tmp_path / "w")
    t = Tools(app, USER)
    a = t.open_thread("띠를 검증한다", wt, topic="검증")["case_id"]
    t.add_evidence(a, "$ pytest\n9 passed")
    t.note_turn(a, "9 passed", "테스트가 통과했다", kind="verified", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    t.decide(a, "폴링으로 한다", reason="SSE 는 길이 하나 더 든다")
    t.declare(a, "next", "브라우저에서 본다", owner="user")
    s = app.status(USER)
    kinds = [(e["kind"], e.get("note_kind")) for e in s["stream"]]
    assert {("next", None), ("decision", None), ("note", "verified"), ("opened", None)} <= set(kinds)   # 노트는 워커가 적어 순서는 안 본다
    assert all(k != "evidence" for k, _ in kinds)
    assert all(e["case_id"] == a and e["title"] == "띠를 검증한다" for e in s["stream"])
    assert [e["at"] for e in s["stream"]] == sorted((e["at"] for e in s["stream"]), reverse=True)
    for i in range(45):
        t.note_turn(a, f"관찰 {i}", f"결론 {i}", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user") if i % 15 else t.declare(a, "focus", f"다시 {i}")   # 46호 상한을 넘지 않게
    assert len(app.status(USER)["stream"]) == 40
