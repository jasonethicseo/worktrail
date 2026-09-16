"""설치본이 거는 훅이 다섯 그대로여야 한다 (#495).

2026-09-10 까지 이 저장소의 .claude/settings.json 은 훅 5개, 설치본은 2개였다. 그래서
피실험자는 압축·세션종료 체크포인트도, 에이전트가 만든 커밋 연결도 받지 못했는데 만든
사람은 그 사실을 배포한 뒤에야 알았다. 갈리면 테스터가 쓰는 것이 내가 쓰는 것과 다른
제품이 된다.

그래서 저장소의 훅 설정(.mcp.json · .claude/settings.json)을 지우고 만든 사람도 설치본을
쓰기로 했다(D14289). 이제 갈릴 두 벌이 없으므로, 이 테스트는 설치본이 거는 다섯을
글자 그대로 못박는다 — 줄이거나 늘리려면 여기를 같이 고쳐야 한다.
"""
from __future__ import annotations

import json
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "tools/hooks/install_claude_hooks.sh"
# 걸어야 하는 다섯. (이벤트, matcher, 훅 인자)
EXPECTED = {
    ("SessionStart", "startup|resume|clear|fork", "session-start"),   # 열린 스레드를 보여 준다
    ("SessionStart", "compact", "post-compact"),                       # 압축 직후 먼저 기록하라
    ("PreCompact", "", "checkpoint pre-compact"),                      # 압축 직전 anchor 갱신
    ("SessionEnd", "", "checkpoint session-end"),                      # 닫을 때 anchor 갱신
    ("PostToolUse", "Bash", "post-commit"),                            # HEAD 가 움직였으면 커밋 연결
}


def _key(event: str, matcher: str | None, command: str) -> tuple[str, str, str]:
    """(이벤트, matcher, 훅 인자) — 경로는 빼고 무엇을 언제 부르는지만 남긴다."""
    return (event, matcher or "", command.split("casebook_hook.py", 1)[1].strip())


def _from_settings(path: pathlib.Path) -> set[tuple[str, str, str]]:
    data = json.loads(path.read_text())
    out = set()
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                if "casebook_hook.py" in (hook.get("command") or ""):
                    out.add(_key(event, entry.get("matcher"), hook["command"]))
    return out


def _from_installer() -> set[tuple[str, str, str]]:
    spec = json.loads(subprocess.run(["sh", str(INSTALLER), "--print-spec"],
                                     capture_output=True, text=True, check=True).stdout)
    return {(s["event"], s["matcher"] or "", s["arg"]) for s in spec}


def test_설치본이_다섯을_그대로_건다():
    ships = _from_installer()
    missing = sorted(EXPECTED - ships)
    extra = sorted(ships - EXPECTED)
    assert not missing, f"설치본이 안 거는 훅: {missing}"
    assert not extra, f"목록에 없는데 거는 훅: {extra}"


def test_저장소는_자기_훅_설정을_들고_있지_않는다():
    """만든 사람도 설치본을 쓴다(D14289). 저장소에 훅 설정이 되살아나면 한 이벤트에 두 번 돈다."""
    for stray in (ROOT / ".claude/settings.json", ROOT / ".mcp.json"):
        if not stray.is_file():
            continue
        text = stray.read_text()
        assert "casebook_hook.py" not in text and '"casebook"' not in text, (
            f"{stray.name} 에 casebook 설정이 돌아왔다 — user 범위와 겹쳐 훅이 두 번 돈다")


def test_커밋_훅은_Bash_를_막지_않는다():
    """PostToolUse 는 Bash 마다 돈다. async 가 아니면 매 명령이 파이썬 기동만큼 느려진다."""
    spec = json.loads(subprocess.run(["sh", str(INSTALLER), "--print-spec"],
                                     capture_output=True, text=True, check=True).stdout)
    post = [s for s in spec if s["event"] == "PostToolUse"]
    assert post and all(s.get("async") for s in post), "PostToolUse 훅에 async 가 빠졌다"


def test_설치는_남의_훅을_지우지_않고_여러_번_돌려도_같다(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "other-tool start"}]}]},
        "statusLine": {"type": "command", "command": "keep me"},
    }))
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path),
           "CLAUDE_SETTINGS": str(target), "CASEBOOK_ROOT": str(ROOT)}
    for _ in range(2):
        subprocess.run(["sh", str(INSTALLER)], env=env, capture_output=True, text=True, check=True)

    data = json.loads(target.read_text())
    assert data["statusLine"]["command"] == "keep me"           # 남의 설정은 그대로
    commands = [h["command"] for entries in data["hooks"].values() for e in entries for h in e["hooks"]]
    assert "other-tool start" in commands                        # 남의 훅도 그대로
    assert _from_settings(target) == _from_installer()           # 두 번 돌려도 한 벌
