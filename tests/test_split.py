"""확장 130호 — 조사·티켓은 private 에만 (D16054). 보장할 것:
(1) Worktrail 만으로 세운 API 에는 조사·티켓 길이 없고, 그 표면은 http_api.ROUTES 와 정확히 같다
(2) 로컬 설치 묶음은 조사 모듈(app·tickets·workers·prompts·prompt_ext·ledger·openai_llm·no_search)과
    private 전용 길 모듈을 싣지 않는다 — 로컬 모드 테스터가 남의 비법을 받지 않는다
(3) 서버 진입점은 기본으로 Worktrail 만 띄우고 OpenAI 키를 요구하지 않는다; 조사 모드는 환경변수로만
(4) 조사 객체를 주면 옛 길이 그대로 붙는다 — private 에서는 아무것도 잃지 않는다"""
from __future__ import annotations

import pathlib

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from casebook.adapters import client_dist, http_api  # noqa: E402
from casebook.core.db import SqliteDB  # noqa: E402
from casebook.core.worktrail import Worktrail  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
INVESTIGATION = ("casebook/core/app.py", "casebook/core/tickets.py", "casebook/core/workers.py",
                 "casebook/core/prompts.py", "casebook/core/prompt_ext.py", "casebook/core/ledger.py",
                 "casebook/adapters/openai_llm.py", "casebook/adapters/no_search.py",
                 "casebook/adapters/http_api_legacy.py")


def _surface(app) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for r in app.routes:
        if getattr(r, "path", "").startswith(("/app", "/tickets", "/auth", "/health")):
            out.setdefault(r.path, set()).update(m for m in r.methods if m not in ("HEAD", "OPTIONS"))
    return out


def test_기록만_세운_API_에는_조사와_티켓_길이_없다():
    wt = Worktrail(SqliteDB(":memory:"))
    app = http_api.create_app(wt, None)
    actual = _surface(app)
    assert not any(p.startswith("/tickets") for p in actual)
    assert "/app/intake" not in actual and "/app/case/{case_id}/turn" not in actual
    expected = {p: set(m) for p, m in http_api.ROUTES.items()} | {"/health": {"GET"}}
    assert actual == expected
    # 그 앱은 실제로 돈다 — 조사 모듈 없이
    wt.signup("사람", "s@x.test", "pw")
    tok = wt.login("s@x.test", "pw")["authToken"]
    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/app/status", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_로컬_설치_묶음은_조사_모듈을_싣지_않는다():
    files = client_dist.local_files(ROOT)
    assert "casebook/core/worktrail.py" in files and "main.py" in files       # 기록과 진입점은 간다
    for f in INVESTIGATION:
        assert f not in files, f"{f} 가 로컬 묶음에 들어 있다"
    for f in INVESTIGATION:
        assert f in client_dist.LOCAL_EXCLUDE


def test_서버_진입점은_기본으로_기록만_띄우고_키를_요구하지_않는다(tmp_path, monkeypatch):
    import main
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("CASEBOOK_INVESTIGATION", raising=False)
    cb, tickets = main.build(str(tmp_path / "w.db"))
    assert isinstance(cb, Worktrail) and type(cb) is Worktrail and tickets is None
    assert cb.run_one() is False                                            # 드레이너가 그대로 돈다


def test_조사_모드는_환경변수로만_켜고_키가_없으면_멈춘다(tmp_path, monkeypatch):
    import main
    pytest.importorskip("casebook.core.app")                                 # 공개 트리에는 없다 — 그때는 건너뛴다
    monkeypatch.setenv("CASEBOOK_INVESTIGATION", "1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        main.build(str(tmp_path / "w.db"))


def test_조사_객체를_주면_옛_길이_그대로_붙는다():
    Casebook = pytest.importorskip("casebook.core.app").Casebook
    from casebook.adapters import http_api_legacy
    from casebook.core.tickets import Tickets
    from tests.conftest import FakeLLM, FakeSearch
    db = SqliteDB(":memory:")
    app = http_api.create_app(Casebook(db=db, llm=FakeLLM(), search=FakeSearch()), Tickets(db))
    actual = _surface(app)
    for p in http_api_legacy.ROUTES:
        assert p in actual, f"{p} 가 붙지 않았다"
