#!/usr/bin/env python3
"""Claude Code 훅 — 확장 14호 (2026-09-04). 기록 시점을 사람 기억이 아니라 호스트 이벤트가 정한다.

    python tools/hooks/casebook_hook.py session-start   # SessionStart(startup|resume|clear|fork): 열린 케이스·focus 주입
    python tools/hooks/casebook_hook.py post-compact    # SessionStart(compact): 활성 케이스 + "요약에만 남은 것을 지금 기록하라"
    python tools/hooks/casebook_hook.py post-commit     # PostToolUse(Bash, async) + git post-commit: HEAD 를 현재 스레드의 change 로 (멱등)
    python tools/hooks/casebook_hook.py checkpoint WHY  # PreCompact · SessionEnd: 현재 스레드에 branch/HEAD/dirty 관찰을 남긴다

확장 29호: 본문 렌더링은 casebook/core/hookctx.py 에 있다(원격 `hook` 도구와 공유). 모드가 server 면
sqlite 를 열지 않고 원격 문을 부른다 — git 사실만 여기서 읽어 실어 보낸다. 모드 판정은 core/clientmode.py.
둘 다 SessionStart 훅이다. Claude Code 문서(hooks#sessionstart · hooks-guide "re-inject context after
compaction"): 컨텍스트 주입(additionalContext)은 SessionStart 에서만 되고, PreCompact 는 압축 직전에 모델 턴이
없으며 출력이 버려지고, PostCompact 는 표시 전용이다. 그래서 "압축 전에 기록"은 불가능하고, 압축 직후 첫 턴에
기록하게 하는 것이 닿을 수 있는 가장 이른 시점이다 — SessionStart 의 startup_reason=compact 가 그 자리다.

설정은 .mcp.json 의 casebook 서버 env(CASEBOOK_DB · CASEBOOK_MCP_EMAIL)를 그대로 읽는다 — MCP 와 같은
DB·같은 사용자. 활성 스레드는 현재 worktree 의 열린 바인딩을 우선한다. 바인딩이 없으면 read_log 의
마지막 resume 케이스가 아직 open 일 때만 대체한다. 닫힌 케이스는 활성으로 주입하지 않는다.
출력은 Claude Code 훅 JSON. 어떤 실패도 세션을 막지 않는다 — 예외는 삼키고 빈 JSON 을 낸다.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def _config() -> tuple[str, str]:
    """로컬 모드에서 어느 DB 의 누구로 기록하나. 환경변수 → .mcp.json → 문과 같은 기본값.

    마지막 기본값이 core/clientmode.py 에서 나오는 것이 중요하다. 전에는 여기서 자기 설치 폴더의
    casebook.db 와 빈 이메일로 떨어져, 설치본의 훅 다섯이 MCP 문과 다른 파일을 열고 "no user" 로
    조용히 죽었다 — 커밋이 스레드에 붙지 않는 것이 그 증상이었다."""
    from casebook.core import clientmode
    db = os.environ.get("CASEBOOK_DB"); email = os.environ.get("CASEBOOK_MCP_EMAIL")
    try:
        env = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["casebook"]["env"]
        db = db or env.get("CASEBOOK_DB"); email = email or env.get("CASEBOOK_MCP_EMAIL")
    except Exception:  # noqa: BLE001
        pass
    db = (db or "").strip()
    email = (email or "").strip().lower()
    return (db or clientmode.local_db()), (email or clientmode.DEFAULT_EMAIL)


def _open(db_path: str, email: str):
    from casebook.core.db import SqliteDB
    from casebook.core import reads, vitals
    db = SqliteDB(db_path)
    reads.ensure(db)
    user = db.get_by("user", "email", email)
    if user is None:
        raise RuntimeError(f"no user {email}")
    return db, user["id"], vitals


def _worktree() -> str:
    """훅이 보는 worktree. 훅 입력 JSON 의 cwd(세션의 작업 디렉터리)가 먼저다 — Claude Code 의 worktree 세션
    (.claude/worktrees/<name>)에서는 CLAUDE_PROJECT_DIR 이 원래 저장소를 가리켜, 그 값을 먼저 쓰면 worktree 에서
    만든 커밋이 원래 checkout 에 바인딩된 스레드에 붙는다(2026-09-05 ddcbb23 → #446 오귀속). git post-commit 훅처럼
    stdin 이 없는 경로는 종전 순서(CLAUDE_PROJECT_DIR → CASEBOOK_WORKTREE → cwd)."""
    cwd = HOOK_INPUT.get("cwd") if isinstance(HOOK_INPUT, dict) else None
    if isinstance(cwd, str) and cwd and os.path.isdir(cwd):
        return cwd
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CASEBOOK_WORKTREE") or os.getcwd()


def _app(db_path: str):
    from casebook.core.app import Casebook
    from casebook.adapters.no_search import NoSearch

    class _NoLLM:  # 훅은 모델을 부르지 않는다
        def __getattr__(self, name):
            raise RuntimeError("hook never calls the model")
    db, uid, _v = _open(db_path, _config()[1])
    return Casebook(db=db, llm=_NoLLM(), search=NoSearch()), uid


def _resolve_mode() -> tuple[str, str | None]:
    """확장 29호 — 얇은 클라이언트. server 모드면 훅은 sqlite 를 열지 않고 원격 `hook` 도구를 부른다.
    모드 판정은 core/clientmode.py 한 곳에서 한다 — server 인데 주소가 없으면 ModeError 로 올라와
    로컬 DB 에 조용히 쌓이지 않는다."""
    from casebook.core.clientmode import resolve
    return resolve()


def _remote(url: str, event: str, wt: str, why: str, reason: str) -> dict:
    """git 사실은 여기(맥)서 읽어 실어 보낸다 — 서버는 git 을 모른다. 10초 안에 답이 없으면 실패로(훅 상한 15초)."""
    from casebook.core import threads
    facts = threads.local_facts(wt)
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    # 확장 96호 — 로그인해 두었으면 주소에서 토큰을 떼고 머리로 붙는다. 훅이 여기서 빠지면
    # 구형 경로를 닫는 순간 다섯이 **조용히** 죽는다 — 사람은 기록이 쌓이는 줄 알고 계속 일한다.
    # 확장 103호 — 머리를 한 번 박지 않고 요청마다 다는 인증기를 쓴다(붙는 방법은 한 자리).
    from casebook.adapters.device_login import route_auth
    to, auth = route_auth(url)

    async def go():
        client = None
        if auth is not None:
    # 확장 102호 — SDK 의 create_mcp_http_client 로 만든다. 맨 httpx2.AsyncClient 는 timeout 5초에
    # follow_redirects 가 꺼져 있고, SDK 것은 connect 30초·read 300초에 redirect 를 따라간다.
            from mcp.shared._httpx_utils import create_mcp_http_client
            client = create_mcp_http_client(auth=auth)
        try:
            async with streamable_http_client(to, http_client=client) as (r, w):
                return await _call(r, w, event, facts, why, reason)
        finally:
            if client is not None:
                await client.aclose()

    async def _call(r, w, event, facts, why, reason):
        async with ClientSession(r, w, read_timeout_seconds=10) as sess:
            await sess.initialize()
            res = await sess.call_tool("hook", {"event": event, "facts": facts, "why": why, "reason": reason})
            text = res.content[0].text if res.content else "{}"
            if text.startswith("casebook error:"):
                raise RuntimeError(text)
            return json.loads(text)
    return anyio.run(go)


HOOK_INPUT: dict = {}


def _log_session_start(reason: str) -> None:
    """확장 19호 — handoff 기회의 분모. 실패해도 주입은 계속된다."""
    try:
        db_path, email = _config()
        db, uid, _v = _open(db_path, email)
        from casebook.core import reads, threads
        cur = threads.current_thread(db, uid, _worktree())
        reads.log(db, uid, "session_start", cur["case_id"] if cur else None, reason)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read()
        HOOK_INPUT.update(json.loads(raw) if raw.strip() else {})
    except Exception:  # noqa: BLE001
        pass
    try:
        if cmd not in ("session-start", "post-compact", "post-commit", "checkpoint"):
            raise KeyError(cmd)
        why = sys.argv[2] if (cmd == "checkpoint" and len(sys.argv) > 2) else ""
        reason = ("compact" if cmd == "post-compact" else
                  str(HOOK_INPUT.get("startup_reason") or HOOK_INPUT.get("source") or "startup"))
        wt = _worktree()
        _mode, url = _resolve_mode()
        if url:
            out = _remote(url, cmd, wt, why, reason)
        else:
            from casebook.core import hookctx
            db_path, _email = _config()
            app, uid = _app(db_path)
            out = hookctx.run(app, uid, cmd, wt, why, reason)
    except Exception as exc:  # noqa: BLE001 — 훅 실패가 세션을 막으면 안 된다
        out = {"systemMessage": f"casebook hook ({cmd}) skipped: {exc}"}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
