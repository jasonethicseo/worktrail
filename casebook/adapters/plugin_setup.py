"""Claude Code 플러그인의 첫 실행 정리 (#541, D16467).

설치 한 줄로 이미 깐 사람이 플러그인을 더 깔면 MCP 서버와 훅이 두 벌이 된다: 사용자 범위의 `casebook`
(~/.casebook/client/bin/casebook-proxy)과 플러그인의 `plugin:worktrail:casebook` 이 함께 뜨고(증거 #2350),
~/.claude/settings.json 의 casebook_hook.py 훅 다섯과 플러그인 훅 다섯이 같이 돈다. 플러그인 쪽이 세션을
시작할 때 설치기의 흔적을 치운다.

치우는 것: 사용자 범위 MCP 등록 `casebook` 중 설치기의 프록시를 가리키는 것, ~/.claude/settings.json 의
casebook_hook.py 훅. 남기는 것: ~/.casebook 의 토큰·모드·언어·기록 DB(다시 로그인하지 않게), 설치기 파일
(~/.casebook/client — Codex 가 그것을 부른다), Codex 등록, 저장소별 .claude/settings.json.
매 세션 시작에 보되 치울 것이 없으면 아무 말도 하지 않는다. WORKTRAIL_NO_MIGRATE=1 이면 건너뛴다(시험용).

같은 자리에서 새 판 알림도 낸다(D16507): 하루 한 번 공개 저장소의 매니페스트 판 번호만 읽는다.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time

INSTALLER_PROXY = "/.casebook/client/bin/casebook-proxy"
HOOK_SCRIPT = "casebook_hook.py"


def _home() -> pathlib.Path:
    return pathlib.Path(os.environ.get("HOME") or pathlib.Path.home())


def _load(path: pathlib.Path) -> dict | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def installer_mcp(home: pathlib.Path) -> bool:
    """사용자 범위에 설치기 프록시를 가리키는 casebook 등록이 있나."""
    data = _load(home / ".claude.json") or {}
    server = (data.get("mcpServers") or {}).get("casebook")
    if not isinstance(server, dict):
        return False
    line = " ".join([str(server.get("command") or ""), *map(str, server.get("args") or [])])
    return INSTALLER_PROXY in line


def _is_ours(entry: dict) -> bool:
    return any(HOOK_SCRIPT in str(h.get("command") or "") for h in entry.get("hooks") or [] if isinstance(h, dict))


def installer_hooks(home: pathlib.Path) -> int:
    data = _load(home / ".claude" / "settings.json") or {}
    return sum(1 for entries in (data.get("hooks") or {}).values() if isinstance(entries, list)
               for e in entries if isinstance(e, dict) and _is_ours(e))


def remove_installer_hooks(home: pathlib.Path) -> tuple[int, pathlib.Path | None]:
    """~/.claude/settings.json 에서 casebook_hook.py 훅만 걷는다. 남의 훅과 다른 설정은 그대로 둔다. 손대기 전에 백업."""
    target = home / ".claude" / "settings.json"
    data = _load(target)
    if data is None:
        return 0, None
    hooks = data.get("hooks") or {}
    removed = 0
    for event in list(hooks):
        entries = hooks[event]
        if not isinstance(entries, list):
            continue
        keep = [e for e in entries if not (isinstance(e, dict) and _is_ours(e))]
        removed += len(entries) - len(keep)
        if keep:
            hooks[event] = keep
        else:
            del hooks[event]
    if not removed:
        return 0, None
    backup = target.with_name(f"settings.json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(target, backup)
    data["hooks"] = hooks
    if not hooks:
        del data["hooks"]
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return removed, backup


def remove_installer_mcp() -> bool:
    """사용자 범위 등록은 claude 명령으로 뗀다 — ~/.claude.json 은 실행 중인 Claude Code 도 쓰는 파일이다."""
    claude = shutil.which("claude")
    if not claude:
        return False
    try:
        done = subprocess.run([claude, "mcp", "remove", "casebook", "-s", "user"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def migrate(home: pathlib.Path | None = None) -> str | None:
    """설치기 흔적을 치우고, 치운 것이 있으면 사람에게 할 말 한 줄을 돌려준다. 없으면 None."""
    from casebook.core.clientmode import say
    home = home or _home()
    had_mcp = installer_mcp(home)
    removed_mcp = remove_installer_mcp() if had_mcp else False
    removed_hooks, backup = remove_installer_hooks(home)
    if not (had_mcp or removed_hooks):
        return None
    # 문구는 사용자가 썼다(2026-09-17, 한국어). 영어는 그 문장을 옮긴 것이다.
    parts_ko, parts_en = [], []
    if removed_hooks:
        parts_ko.append(f"설치기 훅 {removed_hooks}개를 걷어냈습니다(백업 {backup})")
        parts_en.append(f"removed {removed_hooks} installer hooks (backup {backup})")
    if had_mcp and removed_mcp:
        parts_ko.append("설치기 MCP 등록 casebook 을 뗐다")
        parts_en.append("removed the installer's casebook MCP registration")
    elif had_mcp:
        parts_ko.append("설치기 MCP 등록을 분리하지 못했습니다. — claude mcp remove casebook -s user")
        parts_en.append("could not detach the installer's MCP registration. — claude mcp remove casebook -s user")
    return say("Worktrail 플러그인: " + ", ".join(parts_ko) + ". 로그인과 기록은 유지됩니다. 다음 세션부터 하나로 동작합니다.",
               "Worktrail plugin: " + ", ".join(parts_en) + ". Sign-in and records are kept. From the next session it runs as one.")


# ── 새 판 알림 (D16507) ──────────────────────────────────────────────────────────
# 우리 마켓은 자동 갱신이 기본으로 꺼져 있다(discover-plugins 문서). 알림이 없으면 로컬 사용자에게 고친 판이 닿지 않는다.
# 하루 한 번 공개 저장소의 매니페스트에서 판 번호만 읽는다 — 기록·식별자는 보내지 않는다.
LATEST_URL = "https://raw.githubusercontent.com/jasonethicseo/worktrail/master/.claude-plugin/plugin.json"
CHECK_EVERY = 24 * 3600
PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]


def installed_version(root: pathlib.Path = PLUGIN_ROOT) -> str:
    return str((_load(root / ".claude-plugin" / "plugin.json") or {}).get("version") or "")


def _parts(version: str) -> tuple[int, ...]:
    out = []
    for piece in version.split("-", 1)[0].split("."):
        if not piece.isdigit():
            return ()
        out.append(int(piece))
    return tuple(out)


def is_newer(latest: str, current: str) -> bool:
    a, b = _parts(latest), _parts(current)
    return bool(a and b and a > b)


def _fetch_latest(timeout: float = 3.0) -> str | None:
    import urllib.request
    try:
        with urllib.request.urlopen(LATEST_URL, timeout=timeout) as resp:
            return str(json.loads(resp.read().decode()).get("version") or "") or None
    except Exception:  # noqa: BLE001 — 못 읽으면 알리지 않을 뿐이다
        return None


def update_note(home: pathlib.Path | None = None, root: pathlib.Path = PLUGIN_ROOT,
                now: float | None = None, fetch=_fetch_latest) -> str | None:
    """새 판이 있으면 한 줄. 확인은 하루 한 번이고, 그 사이에는 적어 둔 값으로만 판단한다."""
    from casebook.core.clientmode import say
    if os.environ.get("WORKTRAIL_NO_UPDATE_CHECK"):
        return None
    home = home or _home()
    now = time.time() if now is None else now
    state_file = home / ".casebook" / "plugin-update.json"
    state = _load(state_file) or {}
    latest = state.get("latest")
    checked_at = state.get("checked_at")
    if not isinstance(checked_at, (int, float)) or now - checked_at >= CHECK_EVERY:
        fetched = fetch()
        latest = fetched or latest
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(json.dumps({"checked_at": now, "latest": latest}) + "\n")
        except OSError:
            pass
    current = installed_version(root)
    if not (latest and is_newer(str(latest), current)):
        return None
    # 문구는 사용자가 썼다(2026-09-17, 한국어). 영어는 그 문장을 옮긴 것이다.
    return say(f"새 버전 `{latest}`이 나왔습니다.(지금 `{current}`) · 받는 법: `claude plugin marketplace update worktrail` 뒤 "
               "`claude plugin update worktrail@worktrail`, 적용하려면 재시작 · 자동으로 받으려면 "
               "`/plugin` → Marketplaces → worktrail → Enable auto-update",
               f"New version `{latest}` is out (now `{current}`) · To get it: `claude plugin marketplace update worktrail`, "
               "then `claude plugin update worktrail@worktrail`, and restart to apply · To get updates automatically: "
               "`/plugin` → Marketplaces → worktrail → Enable auto-update")


def on_session_start() -> str | None:
    notes = []
    if not os.environ.get("WORKTRAIL_NO_MIGRATE"):
        try:
            notes.append(migrate())
        except Exception as exc:  # noqa: BLE001 — 정리가 실패해도 세션은 계속된다
            notes.append(f"Worktrail plugin: cleanup skipped ({exc})")
    try:
        notes.append(update_note())
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(n for n in notes if n) or None
