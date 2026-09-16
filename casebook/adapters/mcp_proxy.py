"""얇은 클라이언트 — 확장 29호 (2026-09-06). 맥의 stdio MCP 가 원격 문의 프록시가 된다.

    CASEBOOK_REMOTE_URL=https://<host>/mcp/<token> python -m casebook.adapters.mcp_proxy     # stdio

왜: 코어와 sqlite 는 서버로 갔고(확장 27호·AWS), 서버는 이 맥의 저장소를 볼 수 없다. 그런데 스레드의 identity·anchor·
커밋 연결은 git 에서 나온다(확장 15·16·17호). 그래서 git 을 읽는 일만 여기 남긴다:
- 도구 목록은 원격 것을 그대로 비춘다(안내문도). `facts` 인자는 프록시가 채우므로 호스트 스키마에서 뺀다. `hook` 은
  훅 스크립트 전용이라 호스트에 보이지 않는다.
- worktree 를 받는 도구(list_threads · open_thread · switch_thread · resume)를 부를 때 threads.local_facts(worktree) 로
  identity·aliases·branch·HEAD·dirty·HEAD 커밋을 읽어 `facts` 로 실어 보낸다. 서버는 그 사실만 기록한다.
- 그 외 도구는 인자 그대로 전달, 결과 그대로 반환. 프록시는 아무것도 해석하지 않는다.
원격이 끊기면 한 번 다시 붙어 재시도하고, 그래도 안 되면 도구 결과를 오류로 돌려준다 — 호스트 세션은 죽지 않는다.
설정: 모드는 core/clientmode.py 가 정한다(~/.casebook/mode). local 이면 로컬 stdio 문을 열고, server 면
CASEBOOK_REMOTE_URL → ~/.casebook/remote-url(600) 로 붙는다. URL 자체가 비밀이다(경로 토큰).
server 인데 주소가 없으면 멈춘다 — 로컬로 내려가면 서버에 쌓이는 줄 알고 빈 DB 에 쌓인다.
"""
from __future__ import annotations

import os
from typing import Any

import anyio

from casebook.core import threads
from contextlib import asynccontextmanager

from casebook.core.clientmode import ModeError, remote_url, resolve   # 모드는 clientmode 가 정한다

FACTS_TOOLS = ("list_threads", "open_thread", "switch_thread", "resume")
HIDDEN_TOOLS = ("hook", "import_prior")   # 훅·백필 스크립트 전용 — 호스트 모델에게 보이지 않는다
TOOL_TIMEOUT = 90.0                       # 확장 103호 — 한 번의 시도에 주는 시간
CALL_DEADLINE = 180.0                     # 확장 103호 — 호스트는 이 안에 반드시 답을 받는다

__all__ = ["FACTS_TOOLS", "HIDDEN_TOOLS", "ModeError", "Proxy", "build_server", "default_worktree",
           "host_schema", "main", "remote_url", "resolve", "serve"]


def default_worktree() -> str:
    return os.environ.get("CASEBOOK_WORKTREE") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


def host_schema(tool: Any) -> dict[str, Any]:
    """원격 스키마에서 facts·client 를 뺀 것 — 둘 다 프록시가 채우므로 호스트 모델은 모른다."""
    schema = dict(tool.input_schema or {})
    props = dict(schema.get("properties") or {})
    props.pop("facts", None)
    props.pop("client", None)      # 확장 40호 — 프록시가 채운다. 호스트가 스스로 대는 칸이 아니다.
    schema["properties"] = props
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in ("facts", "client")]
    return schema


def _oauth_ready() -> bool:
    """로그인해 두었나. 확장 89호 — 로그인하지 않았으면 이 갈래는 없는 것과 같다.

    켜는 것은 사람이 casebook-login 을 돌린 그 행위다. 파일이 없으면 종전대로 경로 토큰으로 붙는다 —
    서버가 아직 oauth 를 켜지 않았을 수도 있고, 그때 헤더로만 붙으면 아무도 못 들어간다."""
    try:
        from casebook.adapters.device_login import load
        return bool(load())
    except Exception:                            # noqa: BLE001 — 못 읽으면 없는 것과 같다
        return False


@asynccontextmanager
async def _logged_in_client(url: str):
    """device_login.route_auth 가 정한 대로 붙는다 (확장 96호부터 그 한 자리가 정한다).

    토큰이 주소에 있던 것을 머리로 옮기는 것이 이 갈래의 요점이다 — 주소는 프록시 로그·셸 기록·
    오류 메시지에 남지만 머리는 그러지 않는다. 갱신까지 실패하면 옛 경로로 되돌아간다 —
    로그인 전에 쓰던 길이 아직 살아 있기 때문이다.

    확장 103호 — 머리를 박지 않고 인증기를 단다. 박아 두면 300초 뒤부터 전부 401 이다.
    확장 102호 — 클라이언트는 SDK 의 create_mcp_http_client 로 만든다. 맨 httpx2.AsyncClient 는
    timeout 5초에 follow_redirects 가 꺼져 있고, SDK 것은 connect 30초·read 300초다."""
    from mcp.shared._httpx_utils import create_mcp_http_client
    from casebook.adapters.device_login import route_auth
    bare, auth = route_auth(url)
    if auth is None:
        yield url, None
        return
    async with create_mcp_http_client(auth=auth) as client:
        yield bare, client


class Proxy:
    def __init__(self, url: str, worktree: str | None = None, http_client: Any = None) -> None:
        self.url = url
        self.worktree = worktree or default_worktree()
        self.http_client = http_client          # 테스트가 ASGI 전송을 넣는다
        self._stack = None
        self.session = None
        self.instructions: str | None = None
        self.tools: list[Any] = []
        self._opened_in: int | None = None      # 연결을 연 태스크 — 닫는 것도 거기서만 한다
        self._owner_tx = None                   # own() 이 돌고 있으면 재접속을 그쪽에 부탁한다
        self._recon_lock = anyio.Lock()         # 재접속은 한 번에 하나

    async def connect(self) -> None:
        from contextlib import AsyncExitStack
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        await self.close()
        stack = AsyncExitStack()
        try:
            url, client = self.url, self.http_client
            if client is None:                   # 시험이 전송을 넣어 줬으면 그대로 둔다
                url, client = await stack.enter_async_context(_logged_in_client(self.url)) \
                    if _oauth_ready() else (self.url, None)
            r, w = await stack.enter_async_context(streamable_http_client(url, http_client=client))
            session = await stack.enter_async_context(ClientSession(r, w, read_timeout_seconds=60))
            init = await session.initialize()
            self.instructions = init.instructions
            self.tools = list((await session.list_tools()).tools)
        except BaseException:
            # 붙다 실패했으면 반쯤 연 것을 여기서 닫는다 (확장 103호). 두고 가면 그 태스크그룹이
            # 살아남아 다음 재접속이 매달린다 — 실측 2026-09-14: 진짜 서버에 붙여 토큰을 못 쓰게
            # 했다가 되돌리자 그 다음 호출이 60초 무응답이었다. 연 곳이 여기이므로 닫는 것도 여기다.
            try:
                await stack.aclose()
            except Exception:                    # noqa: BLE001 — 닫다 실패해도 원래 오류를 낸다
                pass
            raise
        self._stack, self.session = stack, session
        self._opened_in = anyio.get_current_task().id

    async def close(self) -> None:
        """연 태스크에서만 닫는다 (확장 103호).

        streamable_http_client 는 anyio 태스크그룹을 연다. 그것을 연 태스크가 아닌 곳에서 닫으면
        'Attempted to exit cancel scope in a different task' 로 터지고, 그 순간 닫으려던 태스크의
        취소 범위가 망가진다 — lowlevel Server 의 호출 태스크가 그러면 응답 없이 죽고 호스트는
        영원히 기다린다(실측 2026-09-14: add_evidence 1317초 매달림). 그래서 남의 태스크에서는
        놓기만 한다. 실제로 닫는 일은 own() 이 제자리에서 한다."""
        if self._stack is None:
            return
        stack, mine = self._stack, self._opened_in
        self._stack, self.session, self._opened_in = None, None, None
        if mine is not None and mine != anyio.get_current_task().id:
            return
        try:
            await stack.aclose()
        except Exception:  # noqa: BLE001 — 끊긴 연결을 닫다 실패해도 상관없다
            pass

    async def own(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        """연결을 소유하는 태스크 (확장 103호). 여닫는 일은 전부 여기서 일어난다.

        호출 태스크는 재접속이 필요하면 여기에 부탁만 한다. 이렇게 두지 않으면 lowlevel Server 가
        호출마다 여는 태스크가 남의 태스크그룹을 닫으려 들고, 위 close() 의 실측 고장이 난다."""
        tx, rx = anyio.create_memory_object_stream(0)
        await self.connect()                     # 못 붙으면 여기서 죽는다 — 호스트가 "서버 없음"으로 본다
        self._owner_tx = tx
        task_status.started()
        try:
            async with rx:
                async for reply_tx in rx:
                    err = None
                    try:
                        await self.connect()
                    except Exception as exc:     # noqa: BLE001 — 부탁한 쪽에 그대로 넘긴다
                        err = exc
                    async with reply_tx:
                        await reply_tx.send(err)
        finally:
            # 닫는 것은 여기, 열린 그대로의 취소 범위 안에서 한다. 새 CancelScope 로 감싸면
            # 안에서 빠져나오는 태스크그룹의 범위와 순서가 어긋나 그 자체가 터진다(실측).
            self._owner_tx = None
            await self.close()

    async def _reconnect(self, stale: Any) -> None:
        """다시 붙는다. 소유 태스크가 있으면 그쪽에 부탁한다 — 남의 태스크에서 열고 닫지 않는다."""
        if self._owner_tx is None:
            await self.connect()                 # 소유 태스크가 없다(시험·직접 사용): 여기가 연 곳이다
            return
        async with self._recon_lock:
            if self.session is not None and self.session is not stale:
                return                           # 기다리는 사이 남이 이미 다시 붙였다
            reply_tx, reply_rx = anyio.create_memory_object_stream(1)
            # 소유 태스크가 안 돌아오면(망이 멎었다·서버가 응답만 안 한다) 여기서 끊는다.
            # 여기는 기다리기만 하므로 임시 범위를 씌워도 안전하다 — 여는 일은 저쪽에서 한다.
            with anyio.fail_after(CALL_DEADLINE):
                await self._owner_tx.send(reply_tx)
                async with reply_rx:
                    err = await reply_rx.receive()
            if err is not None:
                raise err

    def host_tools(self) -> list[Any]:
        return [t.model_copy(update={"input_schema": host_schema(t)}) for t in self.tools if t.name not in HIDDEN_TOOLS]

    def with_client(self, name: str, args: dict[str, Any], client: str | None) -> dict[str, Any]:
        if client and name in CLIENT_TOOLS:
            args["client"] = client
        return args

    def with_facts(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in FACTS_TOOLS:
            args = dict(args)
            args["facts"] = threads.local_facts(args.get("worktree") or self.worktree)
        return args

    async def _send(self, name: str, args: dict[str, Any]) -> Any:
        """한 번의 시도에 세운 벽시계 (확장 103호).

        SDK 의 read_timeout 은 요청을 **쓴 뒤에야** 무장한다(jsonrpc_dispatcher: "fail_after arms
        only after the write"). 쓰는 쪽이 막히면 아무 시계도 돌지 않아 영영 매달린다."""
        with anyio.fail_after(TOOL_TIMEOUT):
            return await self.session.call_tool(name, args)

    async def call(self, name: str, args: dict[str, Any] | None, client: str | None = None) -> Any:
        """호스트는 반드시 답을 받는다 (확장 103호).

        답이 없는 것이 제일 나쁜 실패다 — 오류는 사람이 보고 다음 수를 두지만, 침묵은 호스트가
        영원히 기다린다(실측 2026-09-14: add_evidence 가 1317초 뒤 stdio 째로 끊겼다).

        시계를 호출 전체에 두르지는 않는다 — connect() 가 그 안에서 돌면 오래 사는 태스크그룹을
        임시 취소 범위 안에서 열게 되고, 나중에 그 범위 밖에서 닫을 때 순서가 어긋나 터진다
        (기존 시험 test_프록시는_facts_를_채우고_hook_을_숨긴다 가 이것을 잡았다). 그래서 매달릴 수
        있는 자리 둘에 각각 세운다: 보내기(_send)와 재접속 기다리기(_reconnect)."""
        from mcp import types
        args = self.with_client(name, self.with_facts(name, args or {}), client)
        try:
            if self.session is None:
                await self._reconnect(None)
            return await self._send(name, args)
        except Exception as first:  # noqa: BLE001 — 한 번은 다시 붙어 본다
            try:
                await self._reconnect(self.session)
                return await self._send(name, args)
            except Exception as exc:  # noqa: BLE001 — 답 없이 끝나지 않는다
                return types.CallToolResult(
                    content=[types.TextContent(type="text", text=f"casebook remote error: {exc} (after reconnect; first: {first})")],
                    is_error=True)


# 확장 40호 (2026-09-08) — 기록에 어느 클라이언트가 남겼는지를 붙인다. 프록시만 진짜 호스트를 본다:
# 원격 문이 보는 것은 프록시이므로, 여기서 들은 이름을 기록 도구 셋에 실어 보낸다.
# 실측: Claude Code "claude-code" (2.1.261) · Codex "codex-mcp-client" (0.147.0).
CLIENT_TOOLS = ("note_turn", "add_evidence", "decide")


def client_name(ctx: Any) -> str | None:
    """호스트가 initialize 에서 댄 자기 이름(clientInfo.name). 못 읽으면 None — 꾸미지 않는다."""
    try:
        p = ctx.session.client_params
        ci = p.client_info if p else None
        n = (ci.name or "").strip() if ci else ""
        return n or None
    except Exception:      # noqa: BLE001 — 이름을 못 읽어도 도구 호출은 그대로 간다
        return None


def build_server(proxy: Proxy):
    """lowlevel Server — 목록과 호출을 그대로 넘긴다. MCPServer 의 데코레이터는 스키마를 다시 만들어 쓰지 않는다."""
    from mcp import types
    from mcp.server.lowlevel import Server

    async def on_list_tools(_ctx, _params):
        return types.ListToolsResult(tools=proxy.host_tools())

    async def on_call_tool(ctx, params):
        return await proxy.call(params.name, params.arguments, client=client_name(ctx))

    return Server("casebook", instructions=proxy.instructions, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def serve(url: str) -> None:
    """확장 103호 — 연결은 own() 태스크가 소유한다. 호출 태스크는 열지도 닫지도 않는다."""
    from mcp.server.stdio import stdio_server
    proxy = Proxy(url)
    async with anyio.create_task_group() as tg:
        await tg.start(proxy.own)               # 원격이 안 붙으면 여기서 죽는다 — 호스트가 "서버 없음" 으로 본다
        try:
            server = build_server(proxy)
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())
        finally:
            tg.cancel_scope.cancel()            # own() 이 제자리에서 닫는다


def main() -> None:
    """모드가 local 이면 로컬 stdio 문(mcp_server)을 연다. server 면 원격에 붙는다 — 붙을 수 없으면 멈춘다.
    예전처럼 주소가 없다고 로컬로 내려가지 않는다: 서버에 쌓이는 줄 알고 빈 DB 에 쌓이는 일을 막는다."""
    import sys
    try:
        mode, url = resolve()
    except ModeError as exc:
        print(f"casebook proxy: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    if mode == "local":
        print("casebook proxy: local mode — serving the local stdio door", file=sys.stderr)
        from casebook.adapters.mcp_server import main as local_main
        local_main([])
        return
    import anyio
    anyio.run(serve, url)


if __name__ == "__main__":
    main()
