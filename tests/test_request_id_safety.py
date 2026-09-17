"""재시도 열쇠가 기록을 삼키지 않는다 — 35c11b1 뒤따름.

35c11b1 은 request_id 를 도구 입력칸으로 열었다. 프록시를 거치지 않는 채팅 커넥터와 옛 프록시에서는
모델이 그 칸을 채울 수 있고, 같은 값을 두 번 쓰면 내용이 다른 두 번째 기록이 버려진 채 "stored" 로 답했다.
열쇠는 요청의 _meta 로만 오고, 같은 열쇠라도 내용이 다르면 재시도가 아니라 새 기록이다.
"""

import json

import anyio
import pytest

from tests.conftest import FakeLLM, FakeSearch, _build_app
from tests.drive import USER


def test_같은_열쇠라도_내용이_다르면_새로_기록한다():
    app = _build_app(FakeLLM(), FakeSearch())
    case_id = app.open_thread(USER, "열쇠 재사용", "chat")["case_id"]
    before = app.db.count("evidence", {"case_id": case_id})
    one = app.external_turn(USER, case_id, "첫 번째 로그", "1")
    two = app.external_turn(USER, case_id, "전혀 다른 두 번째 로그", "1")
    again = app.external_turn(USER, case_id, "첫 번째 로그", "1")
    assert one["evidence_id"] != two["evidence_id"]
    assert app.db.count("evidence", {"case_id": case_id}) == before + 2
    assert again["evidence_id"] == one["evidence_id"]          # 같은 열쇠·같은 내용의 재전송은 여전히 한 번


def test_같은_관찰이라도_결론이_다르면_새로_기록한다():
    app = _build_app(FakeLLM(), FakeSearch())
    case_id = app.open_thread(USER, "결론 다른 노트", "chat")["case_id"]
    one = app.external_turn(USER, case_id, "같은 관찰", "k", note="첫 결론")
    two = app.external_turn(USER, case_id, "같은 관찰", "k", note="다른 결론")
    assert one["turn_id"] != two["turn_id"]


def test_원격_도구_입력칸에_request_id_가_없다():
    pytest.importorskip("mcp")
    from casebook.adapters.mcp_server import build_server
    app = _build_app(FakeLLM(), FakeSearch())
    server = build_server(app, USER)

    async def names():
        return {t.name: t for t in await server.list_tools()}

    tools = anyio.run(names)
    for name in ("add_evidence", "note_turn"):
        assert "request_id" not in (tools[name].input_schema.get("properties") or {}), name


def test_프록시_재시도는_meta_의_같은_열쇠로_한_번만_기록한다(tmp_path):
    from tests.test_mcp_remote import _remote_proxy_case
    seen: dict = {}

    async def body_with(p):
        await anyio.sleep(0)
        res = await p.call("open_thread", {"focus": "재시도 열쇠", "topic": "테스트"})
        case_id = json.loads(res.content[0].text)["case_id"]
        real = p.session.call_tool
        state = {"n": 0}

        async def lose_first_reply(name, args, **kw):
            seen.setdefault("args", []).append(dict(args or {}))
            seen.setdefault("meta", []).append(kw.get("meta"))
            out = await real(name, args, **kw)
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("response was lost after commit")
            return out

        p.session.call_tool = lose_first_reply

        async def keep(_stale):
            return None

        p._reconnect = keep
        res = await p.call("add_evidence", {"case_id": case_id, "text": "한 번만"})
        assert "Evidence #" in res.content[0].text
        p.session.call_tool = real
        res = await p.call("resume", {"case_id": case_id})
        seen["evidence_count"] = json.loads(res.content[0].text)["evidence_count"]

    _remote_proxy_case(tmp_path, body_with)
    assert len(seen["args"]) == 2
    assert all("request_id" not in a for a in seen["args"])
    keys = [(m or {}).get("casebook/request_id") for m in seen["meta"]]
    assert keys[0] and keys[0] == keys[1]
    assert seen["evidence_count"] == 1


def test_재접속을_기다리던_호출이_포기해도_프록시는_살아_있다(monkeypatch):
    from casebook.adapters import mcp_proxy
    from casebook.adapters.mcp_proxy import Proxy
    monkeypatch.setattr(mcp_proxy, "CALL_DEADLINE", 0.2)
    p = Proxy("http://unused")
    calls = {"n": 0}

    async def slow_connect():
        calls["n"] += 1
        if calls["n"] > 1:
            await anyio.sleep(0.5)              # 부탁한 쪽이 먼저 포기할 만큼 늦게 끝난다
        raise RuntimeError("server down")

    p.connect = slow_connect

    async def body():
        async with anyio.create_task_group() as tg:
            await tg.start(p.own)
            with pytest.raises(TimeoutError):
                await p._reconnect(None)
            await anyio.sleep(0.7)              # 소유 태스크가 connect 를 끝내고 답을 보내려 한다
            assert p._owner_tx is not None      # 소유 태스크가 살아 있다
            tg.cancel_scope.cancel()

    anyio.run(body)


def test_연결이_살아나면_호스트에_도구_목록이_바뀌었다고_알린다():
    from mcp import types
    from casebook.adapters.mcp_proxy import Proxy, call_and_announce
    p = Proxy("http://unused")
    tool = types.Tool(name="list_threads", description="d", inputSchema={"type": "object", "properties": {}})

    async def recover(_stale):
        p.session = object()
        p.tools = [tool]

    p._reconnect = recover
    sent = []

    class Session:
        async def send_tool_list_changed(self):
            sent.append(True)

    ctx = type("Ctx", (), {"session": Session()})()
    assert [t.name for t in p.host_tools()] == ["connection_status"]
    result = anyio.run(lambda: call_and_announce(p, ctx, "connection_status", {}))
    assert not result.is_error
    assert sent == [True]
