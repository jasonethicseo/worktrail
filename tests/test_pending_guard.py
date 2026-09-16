"""확장 6호 — pending 턴 위에 다음 턴을 얹지 않는다. 레코드 생성의 같은 가드와 짝.

보장할 것: (1) pending 턴이 있으면 submit_turn 은 400 (2) 워커가 끝나면 다시 받는다
(3) recovery 는 가드의 예외다 — pending 원본을 abandoned 로 정리하는 사용자 조치이므로
(4) 멱등 재시도(같은 action_key)는 가드보다 먼저 기존 턴을 돌려준다 (5) 원장 시퀀스 불변.
"""
from __future__ import annotations
import pytest

from casebook.core.errors import InputError
from tests.conftest import PLACEHOLDER_INPUT
from tests.drive import USER, CASE, drive


def test_pending_위에_다음_턴은_400(app):
    r = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a")
    with pytest.raises(InputError, match="still processing"):
        app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="b")
    assert app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a")["turn_id"] == r["turn_id"]  # 멱등
    app.run_workers()
    assert app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="b")["status"] == "pending"
    assert app.db.count("turn", where={"case_id": CASE}) == 2


def test_recovery_는_가드의_예외다(app):
    r = app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="a")   # pending 인 채로
    rec = app.recover(USER, CASE, r["turn_id"], action_key="r")
    assert app.db.get("turn", r["turn_id"])["status"] == "abandoned" and rec["status"] == "pending"


def test_골든_시퀀스는_불변(app, golden):
    for path in ("input_turn", "record", "recovery"):
        expected = next(p["events"] for p in golden if p["path"] == path)
        kind = {"input_turn": dict(kind="input"), "record": dict(kind="input", with_record=True),
                "recovery": dict(kind="recovery")}[path]
        assert drive(app, **kind) == expected


def test_http_400_봉투(app):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    h = {"Authorization": "Bearer " + client.post("/auth/auth/signup", json={"name": "g", "email": "g@x.test", "password": "pw"}).json()["authToken"]}
    cid = client.post("/app/case", json={"title": "c"}, headers=h).json()["case_id"]
    client.post(f"/app/case/{cid}/turn", json={"content": "x", "action_key": "1"}, headers=h)
    r = client.post(f"/app/case/{cid}/turn", json={"content": "y", "action_key": "2"}, headers=h)
    assert r.status_code == 400 and "still processing" in r.json()["message"]
