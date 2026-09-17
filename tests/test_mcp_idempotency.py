"""A proxy retry after an uncertain response must not duplicate a record."""

import anyio
from concurrent.futures import ThreadPoolExecutor

from casebook.adapters.mcp_proxy import Proxy
from casebook.adapters.mcp_server import Tools, _request_context
from casebook.core.db import SqliteDB
from casebook.core.worktrail import Worktrail
from tests.conftest import FakeLLM, FakeSearch, _build_app
from tests.drive import USER


def test_proxy_retry_reuses_one_request_id():
    app = _build_app(FakeLLM(), FakeSearch())
    case_id = app.open_thread(USER, "멱등 시험", "chat")["case_id"]
    tools = Tools(app, USER, remote=True)

    class SlowReply:
        def __init__(self):
            self.calls = 0

        async def call_tool(self, name, args, meta=None):
            self.calls += 1
            assert "request_id" not in args               # 열쇠는 입력칸이 아니라 _meta 로 온다
            with _request_context((meta or {}).get("casebook/request_id")):
                result = tools.add_evidence(args["case_id"], args["text"])
            if self.calls == 1:
                raise RuntimeError("response was lost after commit")
            return result

    async def run():
        proxy = Proxy("http://unused")
        proxy.session = SlowReply()

        async def keep_session(_stale):
            return None

        proxy._reconnect = keep_session
        result = await proxy.call("add_evidence", {"case_id": case_id, "text": "한 번만"})
        assert "Evidence #" in result
        assert proxy.session.calls == 2

    anyio.run(run)
    assert app.db.count("turn", {"case_id": case_id}) == 1
    assert app.db.count("evidence", {"case_id": case_id}) == 1


def test_same_request_arriving_together_creates_one_turn(tmp_path):
    path = tmp_path / "retry.db"
    setup = Worktrail(SqliteDB(str(path)))
    user = setup.db.add("user", {"name": "tester", "email": "t@example.test", "password": "x"})
    case = setup.db.add("case", {"user_id": user["id"], "title": "retry", "status": "open", "schema_version": 1})
    one, two = Worktrail(SqliteDB(str(path))), Worktrail(SqliteDB(str(path)))

    def write(app):
        return app.external_turn(user["id"], case["id"], "same", "request-1")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (one, two)))

    assert results[0]["turn_id"] == results[1]["turn_id"]
    assert setup.db.count("turn", {"case_id": case["id"]}) == 1
    assert setup.db.count("evidence", {"case_id": case["id"]}) == 1
