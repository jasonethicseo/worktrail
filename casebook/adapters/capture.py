"""사용자 발화 원문 수집 — 확장 157호 (2026-09-25, 스레드 595). 맥에서 도는 쪽이다. 서버 쪽은 core/utterances.py.

    capture_prompt(hook_input)   UserPromptSubmit 훅: 원문을 spool 에 쓰고, 같은 턴 에이전트에게 source_ref 를 알린다
    upload_once(send)            one-shot 업로더: spool 을 비워 저장 위치로 보낸다. 실패분은 남겨 다음 기회에 다시

입력 경로와 업로드 경로를 가른다(D17990 · D18009). 훅은 사람이 엔터를 친 순간 동기로 돌기 때문에 네트워크를 타지
않는다 — 디스크에 한 파일을 쓰고(fsync) ref 를 돌려주면 끝이다. 올리는 일은 따로 띄운 한 번짜리 프로세스가 하고,
실패하면 파일이 그대로 남아 다음 프롬프트나 다음 SessionStart 가 다시 띄운다. 서버는 prompt_id 로 중복을 버린다.

ref 는 서버가 발급하지 않는다(D17988). 호스트가 UserPromptSubmit 입력에 주는 식별자를 쓴다 — Claude Code 는 prompt_id,
Codex 는 turn_id. 옛 판이라 없으면 여기서 uuid 를 만든다. 어느 쪽이든 같은 턴에 에이전트가 안다.

호스트마다 따로 켜진다. 표지는 그 호스트의 훅이 처음 원문을 쓸 때 생긴다(active-<호스트>). 프록시는 호출한 호스트의
표지만 본다 — Claude Code 훅만 깔린 맥에서 Codex 가 ref 없는 결정을 쓰다 막히면 안 되기 때문이다(그쪽은 채팅처럼
add_utterance 로 원문을 붙인다).

저장 위치는 사람이 설치 때 고른 것을 따른다(D18010): local 이면 이 맥의 DB, server 면 서버. 원문 저장이 처음 켜질 때
한 번 알린다 — 그 문장은 사용자가 쓴다(NOTICE, 아래).
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import uuid
from typing import Any, Callable

# MCP 호스트 이름(clientInfo.name) → 훅이 쓰는 호스트 열쇠. 여기 있는 호스트만 원문을 턴마다 스스로 남길 수 있다.
HOSTS = {"claude-code": "claude-code", "codex-mcp-client": "codex"}
DEFAULT_HOST = "claude-code"
DEFAULT_DIR = "~/.casebook/capture"
BATCH_BYTES = 700_000                   # 원격 문의 요청 상한(1 MiB) 안쪽
BATCH_ITEMS = 50
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")

# 원문 저장이 처음 켜질 때 사람에게 한 번 보이는 안내. 문장은 사용자가 쓴다(자리표시 — 받으면 바꾸고 판을 올린다).
# 판이 바뀌면 한 번 더 보인다.
NOTICE_VERSION = "1"   # 자리표시(draft-1)를 본 사람에게도 실제 문구를 한 번 보인다
NOTICE = {
    "ko": "Worktrail: 이제부터 입력한 프롬프트 원문이 Worktrail 저장 위치(이 맥 또는 연결한 서버)에 함께 저장됩니다.",
    "en": "Worktrail: From now on, your prompts are saved verbatim to Worktrail's storage (this Mac or the server you connected).",
}


def base_dir() -> pathlib.Path:
    return pathlib.Path(os.environ.get("CASEBOOK_CAPTURE_DIR") or DEFAULT_DIR).expanduser()


def spool_dir() -> pathlib.Path:
    return base_dir() / "spool"


def _mark(host: str) -> pathlib.Path:
    return base_dir() / f"active-{host}"


def active(host: str = DEFAULT_HOST) -> bool:
    """이 맥에서 그 호스트의 원문 수집이 돌고 있나 — 그 훅이 한 번이라도 원문을 썼으면 표지가 있다."""
    return not disabled() and _mark(host).is_file()


def client_active(mcp_client: str | None) -> bool:
    """MCP 호출을 한 호스트가 원문을 스스로 남기는가 — 프록시와 로컬 문이 capture 표시를 정할 때 쓴다."""
    host = HOSTS.get(mcp_client or "")
    return bool(host) and active(host)


def disabled() -> bool:
    """끄는 길: ~/.casebook/capture/off 파일. 있으면 훅은 아무것도 남기지 않고 capture 표시도 서지 않는다."""
    return (base_dir() / "off").exists()


def _write_atomic(path: pathlib.Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def ref_line(prompt_id: str) -> str:
    """에이전트가 같은 턴에 받는 줄. 기록 도구의 source_refs 설명과 같은 말을 쓴다.

    셋째 줄은 기록 지시다(#626, D18838). 세션 시작 규칙만으로는 결정이 뒤집히는 턴을 통째로 건너뛰었다 —
    v3 실험에서 다섯 중 둘. 발화마다 그 턴 바로 앞에 놓아, 전역 지시 없이도 설치된 훅으로 모두에게 들어간다."""
    return (f"casebook source_ref: {prompt_id}\n"
            "If a decision, constraint, focus or note you record comes from what the engineer said in this prompt, "
            f"pass source_refs=[\"{prompt_id}\"] (and observed_from=\"user\" on note_turn).\n"
            "Before you end this turn, record in Worktrail what it settled: a decision made or reversed → decide "
            "(a reversal supersedes the old one), a constraint → constrain, a hypothesis checked and dropped → rule_out, "
            "what the machine showed → note_turn. Skip only a turn that settled nothing.")


def _notice(lang: str) -> str | None:
    mark = base_dir() / "notice"
    try:
        if mark.read_text().strip() == NOTICE_VERSION:
            return None
    except OSError:
        pass
    _write_atomic(mark, NOTICE_VERSION.encode())
    return NOTICE.get(lang) or NOTICE["en"]


def capture_prompt(hook_input: dict[str, Any], lang: str = "en", now: Callable[[], float] = time.time,
                   host: str = DEFAULT_HOST) -> dict[str, Any]:
    """UserPromptSubmit 한 번. 원문을 spool 에 쓰고 훅 JSON 을 돌려준다. 쓸 게 없으면 {}."""
    if disabled():
        return {}
    prompt = hook_input.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {}
    pid = next((v for v in (hook_input.get("prompt_id"), hook_input.get("turn_id"))
                if isinstance(v, str) and _ID_RE.match(v)), None) or uuid.uuid4().hex
    item = {"prompt_id": pid, "content": prompt, "said_at": int(now() * 1000), "client": host,
            "session_id": hook_input.get("session_id") if isinstance(hook_input.get("session_id"), str) else None,
            "worktree": hook_input.get("cwd") if isinstance(hook_input.get("cwd"), str) else None}
    spool = spool_dir()
    spool.mkdir(parents=True, exist_ok=True)
    _write_atomic(spool / f"{pid}.json", json.dumps(item, ensure_ascii=False).encode())
    if not active(host):
        _write_atomic(_mark(host), str(int(now())).encode())
    out: dict[str, Any] = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                  "additionalContext": ref_line(pid)}}
    note = _notice(lang)
    if note:
        out["systemMessage"] = note
    return out


def pending() -> list[pathlib.Path]:
    try:
        return sorted((p for p in spool_dir().glob("*.json")), key=lambda p: p.stat().st_mtime)
    except OSError:
        return []


def _batches(paths: list[pathlib.Path]):
    batch: list[tuple[pathlib.Path, dict]] = []
    size = 0
    for p in paths:
        try:
            item = json.loads(p.read_text())
        except (OSError, ValueError):
            continue                     # 쓰다 만 파일은 원자적 쓰기라 없다 — 읽을 수 없으면 건너뛰고 남긴다
        n = len(json.dumps(item, ensure_ascii=False).encode())
        if batch and (size + n > BATCH_BYTES or len(batch) >= BATCH_ITEMS):
            yield batch
            batch, size = [], 0
        batch.append((p, item))
        size += n
    if batch:
        yield batch


def upload_once(send: Callable[[list[dict]], Any]) -> dict[str, int]:
    """spool 을 한 번 비운다. 다른 업로더가 돌고 있으면 아무것도 안 한다. 보내다 실패하면 거기서 멈추고 남긴다."""
    import fcntl
    spool = spool_dir()
    spool.mkdir(parents=True, exist_ok=True)
    with open(spool / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"sent": 0, "left": len(pending()), "busy": 1}
        sent = 0
        for batch in _batches(pending()):
            try:
                send([item for _p, item in batch])
            except Exception:            # noqa: BLE001 — 남겨 두고 다음 기회에 다시 보낸다
                break
            for p, _item in batch:
                try:
                    p.unlink()
                except OSError:
                    pass
            sent += len(batch)
        return {"sent": sent, "left": len(pending())}


def spawn_upload(script: str) -> None:
    """업로더를 띄우고 기다리지 않는다. 출력을 훅의 파이프에 물리지 않는다 — 물리면 호스트가 끝날 때까지 기다린다."""
    if not pending():
        return
    try:
        subprocess.Popen([sys.executable, script, "upload"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError:
        pass
