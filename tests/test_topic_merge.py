"""확장 47호 — 주제 정리를 사람이 싸게 한다(#485). 보장할 것:
(1) merge: src 의 스레드가 dst 로 가고(added_at 유지, 이미 있던 것은 중복 없이) src 는 사라진다. 저장소는 합집합
(2) 설명·결론은 dst 것을 지키고, dst 에 없을 때만 src 것을 가져온다
(3) 자기 자신에 합치기·남의 주제·없는 주제는 거절
(4) move: from 에서 빠지고 to 에 들어간다. 다른 주제에 같이 속해 있던 것은 그대로다
(5) HTTP POST /app/topic — merge_into · from_topic_id 두 모양
(6) 원장에는 아무것도 남지 않는다 — 주제는 부속이다
(7) MCP merge_topic 은 engineer_asked=true 없이는 거절(아무것도 안 바뀜), 없는 이름은 기존 목록을 실어 거절, list_topics 는 읽기 전용(D13981)"""
from __future__ import annotations

import pytest

from casebook.core.errors import InputError, NotFoundError
from tests.drive import USER
from tests.test_mcp_server import tools  # noqa: F401  — MCP 도구 픽스처
from tests.test_threads import _git_repo


def _threads(app, tmp_path, n):
    return [app.open_thread(USER, f"t{i}", _git_repo(tmp_path / f"r{i}"))["case_id"] for i in range(n)]


def test_merge_는_스레드와_저장소를_합치고_src_를_지운다(app, tmp_path):
    a, b, c = _threads(app, tmp_path, 3)
    dst = app.create_topic(USER, "코드 에이전트의 기록·인계 — MCP와 Tracker", "설명", repos=["github.com/x/one"], case_ids=[a, b])
    src = app.create_topic(USER, "코드 에이전트의 기록·인계", repos=["github.com/x/two", "github.com/x/one"], case_ids=[b, c])
    before = {r["case_id"]: r["added_at"] for r in app.db.conn.execute("SELECT case_id, added_at FROM topic_case WHERE topic_id=?", (src["topic_id"],))}
    ledger_n = app.db.count("ledger")
    out = app.merge_topic(USER, src["topic_id"], dst["topic_id"])
    assert out["topic_id"] == dst["topic_id"] and out["case_ids"] == sorted([a, b, c])
    assert out["merged"] == {"topic_id": src["topic_id"], "name": "코드 에이전트의 기록·인계", "moved": 2}
    assert out["repos"] == ["github.com/x/one", "github.com/x/two"]
    names = [t["name"] for t in app.list_topics(USER)]
    assert names == ["코드 에이전트의 기록·인계 — MCP와 Tracker"]                 # (1) src 는 사라졌다
    after = {r["case_id"]: r["added_at"] for r in app.db.conn.execute("SELECT case_id, added_at FROM topic_case WHERE topic_id=?", (dst["topic_id"],))}
    assert after[c] == before[c]                                                 # (1) 분류된 시각은 사실 — 그대로 간다
    assert app.db.count("ledger") == ledger_n                                    # (6)


def test_merge_는_dst_의_설명과_결론을_지킨다(app, tmp_path):
    a, b = _threads(app, tmp_path, 2)
    dst = app.create_topic(USER, "dst", "", case_ids=[a])
    src = app.create_topic(USER, "src", "src 설명", case_ids=[b], conclusion="src 결론")
    r = app.merge_topic(USER, src["topic_id"], dst["topic_id"])
    t = app.list_topics(USER)[0]
    assert t["summary"] == "src 설명" and t["conclusion"] == "src 결론" and t["concluded_at"]   # (2) dst 에 없으니 가져온다
    dst2 = app.create_topic(USER, "dst2", "dst2 설명", case_ids=[a], conclusion="dst2 결론")
    app.merge_topic(USER, r["topic_id"], dst2["topic_id"])
    t = app.list_topics(USER)[0]
    assert t["name"] == "dst2" and t["summary"] == "dst2 설명" and t["conclusion"] == "dst2 결론"    # (2) dst 것을 지킨다


def test_merge_거절(app, tmp_path):
    (a,) = _threads(app, tmp_path, 1)
    t = app.create_topic(USER, "one", case_ids=[a])
    with pytest.raises(InputError, match="into itself"):
        app.merge_topic(USER, t["topic_id"], t["topic_id"])
    with pytest.raises(NotFoundError):
        app.merge_topic(USER, t["topic_id"], 999)
    other = app.db.add("user", {"name": "o", "email": "o@x.test", "password": None})["id"]
    theirs = app.create_topic(other, "theirs")
    with pytest.raises(NotFoundError):                                           # (3) 남의 주제는 없는 것과 같다
        app.merge_topic(USER, t["topic_id"], theirs["topic_id"])
    assert [x["name"] for x in app.list_topics(USER)] == ["one"]


def test_move_는_from_에서_빠지고_to_에_들어간다(app, tmp_path):
    a, b = _threads(app, tmp_path, 2)
    src = app.create_topic(USER, "src", case_ids=[a, b])
    dst = app.create_topic(USER, "dst")
    keep = app.create_topic(USER, "keep", case_ids=[a])
    out = app.move_topic(USER, [a], src["topic_id"], dst["topic_id"])
    assert out["case_ids"] == [a] and out["from"] == {"topic_id": src["topic_id"], "name": "src", "case_ids": [b]}
    by = {t["name"]: t["case_ids"] for t in app.list_topics(USER)}
    assert by == {"src": [b], "dst": [a], "keep": [a]}                           # (4) 다른 주제 소속은 그대로
    with pytest.raises(InputError, match="case_ids is empty"):
        app.move_topic(USER, [], src["topic_id"], dst["topic_id"])
    with pytest.raises(NotFoundError):
        app.move_topic(USER, [a], 999, dst["topic_id"])


def test_http_merge_and_move(app, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    tok = client.post("/auth/auth/signup", json={"name": "r", "email": "r@x.test", "password": "pw"}).json()["authToken"]
    h = {"Authorization": "Bearer " + tok}
    uid = app.db.get_by("user", "email", "r@x.test")["id"]
    a = app.open_thread(uid, "a", _git_repo(tmp_path / "a"))["case_id"]
    b = app.open_thread(uid, "b", _git_repo(tmp_path / "b"))["case_id"]
    long = client.post("/app/topic", json={"name": "긴 이름 — 갈래", "case_ids": [a]}, headers=h).json()
    short = client.post("/app/topic", json={"name": "긴 이름", "case_ids": [b]}, headers=h).json()
    r = client.post("/app/topic", json={"topic_id": short["topic_id"], "merge_into": long["topic_id"]}, headers=h)
    assert r.status_code == 200 and r.json()["case_ids"] == sorted([a, b]) and r.json()["merged"]["moved"] == 1
    assert [t["name"] for t in client.get("/app/topic", headers=h).json()["topics"]] == ["긴 이름 — 갈래"]
    other = client.post("/app/topic", json={"name": "다른 주제"}, headers=h).json()
    r = client.post("/app/topic", json={"topic_id": other["topic_id"], "case_ids": [b], "from_topic_id": long["topic_id"]}, headers=h)
    assert r.status_code == 200 and r.json()["case_ids"] == [b] and r.json()["from"]["case_ids"] == [a]
    assert client.post("/app/topic", json={"topic_id": long["topic_id"], "merge_into": long["topic_id"]}, headers=h).status_code == 400
    assert client.post("/app/topic", json={"topic_id": long["topic_id"], "merge_into": 999}, headers=h).status_code == 404


def test_mcp_merge_topic_은_사람이_시킬_때만(tools, tmp_path):
    from casebook.core.errors import ApiError
    t, app, _ = tools
    a = t.open_thread("a", _git_repo(tmp_path / "a"), topic="긴 이름 — 갈래")["case_id"]
    # 118호 — "긴 이름" 은 "긴 이름 — 갈래" 와 다른 이름이므로 새 주제다. 합치기 시험의 재료라 일부러 만든다.
    b = t.open_thread("b", _git_repo(tmp_path / "b"), topic="긴 이름", new_topic=True)["case_id"]
    assert t.list_topics() == [{"topic_id": 1, "name": "긴 이름 — 갈래", "summary": "", "threads": [a]},
                               {"topic_id": 2, "name": "긴 이름", "summary": "", "threads": [b]}]
    refused = t.merge_topic("긴 이름", "긴 이름 — 갈래")
    assert refused.startswith("merge_topic is for the engineer's explicit request") and len(t.list_topics()) == 2   # (7) 아무것도 안 바뀜
    with pytest.raises(ApiError, match=r"topic not found: '없는 것' — pass an existing name exactly. Existing topics: 긴 이름 — 갈래; 긴 이름"):
        t.merge_topic("없는 것", "긴 이름 — 갈래", engineer_asked=True)
    out = t.merge_topic("긴 이름", "긴 이름 — 갈래", engineer_asked=True)
    assert out == f"topic #2 '긴 이름' merged into #1 '긴 이름 — 갈래': 1 thread(s) moved, it now holds {sorted([a, b])}. '긴 이름' no longer exists."
    assert [x["name"] for x in t.list_topics()] == ["긴 이름 — 갈래"]
