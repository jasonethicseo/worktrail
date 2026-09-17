"""#541 — Claude Code 플러그인. 저장소 루트가 곧 플러그인이자 마켓이다(.claude-plugin/).

지킬 것: (1) 매니페스트가 가리키는 실행기가 있다 (2) 플러그인 훅은 설치기와 같은 명세에서 나온다 — 두 곳에
적지 않는다(#495) (3) 실행기의 의존성 범위가 pyproject 와 같다 (4) 공개 트리에 플러그인 파일이 실린다
(5) 첫 실행 정리는 설치기가 깐 MCP 등록과 casebook 훅만 치우고 남의 설정은 건드리지 않는다 (D16467)
(6) 실행기는 준비된 환경의 python 으로 모듈을 돌리고, 준비 전에는 세션을 막지 않는다
(7) 로컬 모드 첫 세션에 훅이 문보다 먼저 와도 "no user" 로 비지 않는다.
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import sys
import tomllib

import pytest

from casebook.adapters import client_dist, plugin_setup

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
MARKET = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
RUN = ROOT / "bin/worktrail-run"
HOOK = ROOT / "bin/worktrail-hook"


def _root_path(command: str) -> pathlib.Path:
    return ROOT / command.replace("${CLAUDE_PLUGIN_ROOT}/", "")


def test_매니페스트가_가리키는_실행기가_있다():
    assert PLUGIN["name"] == "worktrail"
    server = PLUGIN["mcpServers"]["casebook"]           # 도구 이름은 mcp__plugin_worktrail_casebook__* (증거 #2350)
    run = _root_path(server["command"])
    assert run == RUN and os.access(run, os.X_OK)
    module = server["args"][0]
    assert (ROOT / (module.replace(".", "/") + ".py")).is_file()
    assert [p["name"] for p in MARKET["plugins"]] == ["worktrail"] and MARKET["plugins"][0]["source"] == "./"


def test_플러그인_훅은_설치기_명세와_같다():
    shipped = json.loads((ROOT / "hooks/hooks.json").read_text())
    assert shipped == client_dist.plugin_hooks(), (
        "hooks/hooks.json 이 설치기 명세와 갈렸다 — client_dist.plugin_hooks 의 docstring 명령으로 다시 쓴다")
    commands = [h["command"] for entries in shipped["hooks"].values() for e in entries for h in e["hooks"]]
    assert len(commands) == 5 and all(c.startswith(client_dist.PLUGIN_HOOK + " ") for c in commands)
    assert os.access(_root_path(client_dist.PLUGIN_HOOK), os.X_OK)


def test_명령_실행기가_있는_모듈을_부른다():
    for name in ("casebook-login", "casebook-ui", "casebook-backfill"):
        text = (ROOT / "bin" / name).read_text()
        assert os.access(ROOT / "bin" / name, os.X_OK)
        module = text.split("worktrail-run\" ", 1)[1].split()[0]
        assert (ROOT / (module.replace(".", "/") + ".py")).is_file(), name


def test_실행기의_의존성은_pyproject_와_같다():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    wanted = sorted(project["dependencies"] + project["optional-dependencies"]["mcp"])
    line = next(l for l in RUN.read_text().splitlines() if l.startswith("DEPS="))
    assert sorted(line.split("'")[1].split()) == wanted


def test_공개_트리에_플러그인이_실린다():
    rules = (ROOT / "deploy/public-include.txt").read_text().split()
    for rule in (".claude-plugin/", "hooks/", "bin/"):
        assert rule in rules, f"{rule} 가 공개 목록에 없다 — 마켓에서 받은 플러그인이 빈다"


# ── 첫 실행 정리 (D16467) ────────────────────────────────────────────────────────

INSTALLER_HOOK = "/Users/x/.casebook/client/.venv/bin/python /Users/x/.casebook/client/tools/hooks/casebook_hook.py"


def _home(tmp_path: pathlib.Path, *, mcp_command: str | None) -> pathlib.Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    servers = {"other": {"type": "stdio", "command": "other-server"}}
    if mcp_command:
        servers["casebook"] = {"type": "stdio", "command": mcp_command, "args": [], "env": {}}
    (home / ".claude.json").write_text(json.dumps({"mcpServers": servers, "numStartups": 3}))
    (home / ".claude/settings.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [
                {"matcher": "startup|resume|clear|fork",
                 "hooks": [{"type": "command", "command": f"{INSTALLER_HOOK} session-start"}]},
                {"hooks": [{"type": "command", "command": "other-tool start"}]},
            ],
            "SessionEnd": [{"hooks": [{"type": "command", "command": f"{INSTALLER_HOOK} checkpoint session-end"}]}],
        },
        "statusLine": {"type": "command", "command": "keep me"},
    }))
    return home


def test_설치기_훅만_걷고_남의_설정은_둔다(tmp_path):
    home = _home(tmp_path, mcp_command=None)
    removed, backup = plugin_setup.remove_installer_hooks(home)
    assert removed == 2 and backup is not None and backup.is_file()
    data = json.loads((home / ".claude/settings.json").read_text())
    assert data["statusLine"]["command"] == "keep me"
    assert data["hooks"] == {"SessionStart": [{"hooks": [{"type": "command", "command": "other-tool start"}]}]}
    assert plugin_setup.remove_installer_hooks(home) == (0, None)        # 두 번째는 할 일이 없다


def test_설치기_프록시를_가리키는_등록만_설치기_것으로_본다(tmp_path):
    assert plugin_setup.installer_mcp(_home(tmp_path / "a", mcp_command="/Users/x/.casebook/client/bin/casebook-proxy"))
    assert not plugin_setup.installer_mcp(_home(tmp_path / "b", mcp_command="/opt/dev/casebook/.venv/bin/python"))
    assert not plugin_setup.installer_mcp(_home(tmp_path / "c", mcp_command=None))


def _fake_claude(tmp_path: pathlib.Path, rc: int = 0) -> pathlib.Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    claude = bindir / "claude"
    claude.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/claude-calls"\nexit {rc}\n')
    claude.chmod(claude.stat().st_mode | stat.S_IEXEC)
    return bindir


def test_정리는_claude_명령으로_등록을_떼고_한_줄로_알린다(tmp_path, monkeypatch):
    home = _home(tmp_path, mcp_command="/Users/x/.casebook/client/bin/casebook-proxy")
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path)))
    note = plugin_setup.migrate(home)
    assert (tmp_path / "claude-calls").read_text().split() == ["mcp", "remove", "casebook", "-s", "user"]
    assert note and "2" in note
    assert plugin_setup.installer_hooks(home) == 0


def test_치울_것이_없으면_아무_말도_없다(tmp_path, monkeypatch):
    home = tmp_path / "empty"
    home.mkdir()
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path)))
    assert plugin_setup.migrate(home) is None
    assert not (tmp_path / "claude-calls").exists()


def test_등록을_못_떼면_손으로_뗄_명령을_말한다(tmp_path, monkeypatch):
    home = _home(tmp_path, mcp_command="/Users/x/.casebook/client/bin/casebook-proxy")
    monkeypatch.setenv("PATH", str(_fake_claude(tmp_path, rc=1)))
    assert "claude mcp remove casebook -s user" in plugin_setup.migrate(home)


# ── 실행기 ──────────────────────────────────────────────────────────────────────

def _ready_venv(tmp_path: pathlib.Path) -> pathlib.Path:
    """의존성 도장이 찍힌 가짜 환경. python 은 받은 인자와 WORKTRAIL_PLUGIN 을 적는다."""
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    py = venv / "bin/python"
    py.write_text(f'#!/bin/sh\nprintf "%s\\n" "plugin=$WORKTRAIL_PLUGIN" "$@" > "{tmp_path}/python-args"\necho "{{}}"\n')
    py.chmod(py.stat().st_mode | stat.S_IEXEC)
    deps = next(l for l in RUN.read_text().splitlines() if l.startswith("DEPS=")).split("'")[1]
    (venv / ".worktrail-deps").write_text(deps + "\n")
    return venv


def _env(tmp_path: pathlib.Path, venv: pathlib.Path) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "WORKTRAIL_VENV": str(venv), "LANG": "en_US.UTF-8"}


def test_준비된_환경이면_그_python_으로_모듈을_돌린다(tmp_path):
    venv = _ready_venv(tmp_path)
    env = _env(tmp_path, venv)
    assert subprocess.run([str(RUN), "--ready"], env=env).returncode == 0
    r = subprocess.run([str(RUN), "casebook.adapters.mcp_proxy", "--flag"], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    args = (tmp_path / "python-args").read_text().splitlines()
    assert args[-3:] == [str(ROOT), "casebook.adapters.mcp_proxy", "--flag"] and "runpy" in args[2]


def test_의존성_목록이_바뀌면_준비되지_않은_것으로_본다(tmp_path):
    venv = _ready_venv(tmp_path)
    (venv / ".worktrail-deps").write_text("mcp\n")
    assert subprocess.run([str(RUN), "--ready"], env=_env(tmp_path, venv)).returncode == 1


def test_훅은_준비된_환경에서_casebook_hook_을_플러그인_표시와_함께_부른다(tmp_path):
    venv = _ready_venv(tmp_path)
    r = subprocess.run([str(HOOK), "post-commit"], input="{}", env=_env(tmp_path, venv), capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "{}"
    args = (tmp_path / "python-args").read_text().splitlines()
    assert args == ["plugin=1", str(ROOT / "tools/hooks/casebook_hook.py"), "post-commit"]


def test_준비_전의_훅은_세션을_막지_않는다(tmp_path):
    """session-start 가 아닌 훅은 환경을 만들러 가지 않고 빈 답을 낸다(만드는 것은 session-start 의 몫)."""
    venv = tmp_path / "not-yet"
    for event in ("post-commit", "post-compact", "checkpoint"):
        r = subprocess.run([str(HOOK), event], input="{}", env=_env(tmp_path, venv), capture_output=True, text=True, timeout=10)
        assert r.returncode == 0 and json.loads(r.stdout) == {}
    assert not venv.exists()


# ── 훅 본체 ────────────────────────────────────────────────────────────────────

def _hook(tmp_path: pathlib.Path, extra: dict[str, str]) -> dict:
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "CASEBOOK_MODE": "local",
           "CASEBOOK_DB": str(tmp_path / "fresh.db"), "WORKTRAIL_NO_UPDATE_CHECK": "1", **extra}   # 시험은 GitHub 에 닿지 않는다
    r = subprocess.run([sys.executable, str(ROOT / "tools/hooks/casebook_hook.py"), "session-start"],
                       input=json.dumps({"cwd": str(tmp_path)}), env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_로컬_첫_세션에_훅이_먼저_와도_비지_않는다(tmp_path):
    out = _hook(tmp_path, {})
    assert "no user" not in (out.get("systemMessage") or ""), out


def test_플러그인으로_돌면_세션_시작에_설치기_흔적을_치운다(tmp_path):
    home = _home(tmp_path, mcp_command=None)
    out = _hook(home, {"WORKTRAIL_PLUGIN": "1"})
    assert "Worktrail" in (out.get("systemMessage") or ""), out
    assert plugin_setup.installer_hooks(home) == 0


def test_플러그인이_아니면_정리하지_않는다(tmp_path):
    home = _home(tmp_path, mcp_command=None)
    _hook(home, {})
    _hook(home, {"WORKTRAIL_PLUGIN": "1", "WORKTRAIL_NO_MIGRATE": "1"})
    assert plugin_setup.installer_hooks(home) == 2


# ── 새 판 알림 (D16507) ─────────────────────────────────────────────────────────

def _root_with(tmp_path: pathlib.Path, version: str) -> pathlib.Path:
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "worktrail", "version": version}))
    return root


def test_새_판이_있으면_한_줄로_알리고_하루는_다시_묻지_않는다(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKTRAIL_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setenv("CASEBOOK_LANG", "en")
    calls = []
    fetch = lambda: calls.append(1) or "0.2.0"
    root = _root_with(tmp_path, "0.1.0")
    note = plugin_setup.update_note(tmp_path, root, now=1000.0, fetch=fetch)
    assert note and "0.2.0" in note and "0.1.0" in note and "claude plugin update worktrail@worktrail" in note
    assert plugin_setup.update_note(tmp_path, root, now=1000.0 + 3600, fetch=fetch) == note   # 적어 둔 값으로
    assert calls == [1]
    plugin_setup.update_note(tmp_path, root, now=1000.0 + plugin_setup.CHECK_EVERY, fetch=fetch)
    assert calls == [1, 1]                                                                    # 하루가 지나면 다시


def test_같은_판이거나_못_읽으면_말하지_않는다(tmp_path, monkeypatch):
    monkeypatch.delenv("WORKTRAIL_NO_UPDATE_CHECK", raising=False)
    root = _root_with(tmp_path, "0.2.0")
    assert plugin_setup.update_note(tmp_path, root, now=1.0, fetch=lambda: "0.2.0") is None
    assert plugin_setup.update_note(tmp_path / "b", root, now=1.0, fetch=lambda: None) is None
    assert plugin_setup.update_note(tmp_path / "c", root, now=1.0, fetch=lambda: "0.10.0") is not None   # 글자가 아니라 숫자로 견준다
    assert not plugin_setup.is_newer("0.2.0-rc1", "0.2.0") and not plugin_setup.is_newer("x", "0.1.0")


def test_끄면_묻지도_않는다(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTRAIL_NO_UPDATE_CHECK", "1")
    root = _root_with(tmp_path, "0.1.0")
    assert plugin_setup.update_note(tmp_path, root, now=1.0, fetch=lambda: pytest.fail("fetched")) is None


def test_알림_주소는_공개_저장소의_이_매니페스트다():
    assert plugin_setup.LATEST_URL.endswith("/jasonethicseo/worktrail/master/.claude-plugin/plugin.json")
    assert plugin_setup.installed_version() == PLUGIN["version"]
