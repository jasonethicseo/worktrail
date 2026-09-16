"""확장 2호 — 초대 코드 가입. 표면은 signup body 에 선택 필드 invite_code 하나가 는다.

보장할 것: (1) 코드 목록이 비면 .xs 그대로 열린 가입 (2) 코드가 있으면 없거나 틀린 요청은
403 이고 계정이 만들어지지 않는다 (3) 맞으면 그대로 authToken (4) 로그인은 코드와 무관.
"""
from __future__ import annotations
import pytest

from casebook.core.app import Casebook
from casebook.core.db import SqliteDB
from casebook.core.errors import AccessDeniedError
from tests.conftest import FakeLLM, FakeSearch


def _app(codes=()):
    return Casebook(db=SqliteDB(":memory:"), llm=FakeLLM(), search=FakeSearch(), invite_codes=frozenset(codes))


def test_코드_목록이_비면_열린_가입이다():
    app = _app()
    assert set(app.signup("a", "a@x.test", "pw")) == {"authToken"}
    assert set(app.signup("b", "b@x.test", "pw", invite_code="whatever")) == {"authToken"}


def test_코드가_있으면_없거나_틀리면_403_이고_계정은_안_생긴다():
    app = _app(["alpha-1", " beta-2 "])
    for bad in (None, "", "gamma", "ALPHA-1"):
        with pytest.raises(AccessDeniedError):
            app.signup("a", "a@x.test", "pw", invite_code=bad)
    assert app.db.count("user") == 0


def test_맞는_코드면_가입되고_로그인은_코드와_무관하다():
    app = _app(["alpha-1", " beta-2 "])
    assert set(app.signup("a", "a@x.test", "pw", invite_code="beta-2")) == {"authToken"}
    assert set(app.login("a@x.test", "pw")) == {"authToken"}


def test_http_표면_invite_code_는_선택_필드다():
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from casebook.adapters import http_api
    from casebook.core.tickets import Tickets
    app = _app(["k1"])
    client = TestClient(http_api.create_app(app, Tickets(app.db)), raise_server_exceptions=False)
    r = client.post("/auth/auth/signup", json={"name": "a", "email": "a@x.test", "password": "pw"})
    assert r.status_code == 403 and r.json() == {"message": "A valid invite code is required to sign up."}
    r = client.post("/auth/auth/signup", json={"name": "a", "email": "a@x.test", "password": "pw", "invite_code": "k1"})
    assert r.status_code == 200 and set(r.json()) == {"authToken"}
