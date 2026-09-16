#!/bin/sh
# casebook — Claude Code 훅 설치 (~/.claude/settings.json 에 병합).
#   sh tools/hooks/install_claude_hooks.sh
#   sh tools/hooks/install_claude_hooks.sh --print-spec   # 넣는 훅 목록만 JSON 으로
#
# 다섯을 넣는다. 이 목록이 이 저장소의 .claude/settings.json 과 같아야 한다 —
# 만든 사람 맥과 설치본이 갈리면 테스터가 겪는 것이 내가 겪는 것과 달라진다(#495).
#   SessionStart(startup|resume|clear|fork)  열린 스레드를 보여 준다
#   SessionStart(compact)                    압축 직후 "요약에만 남은 것을 먼저 기록하라"
#   PreCompact                               압축 직전 체크포인트(anchor 갱신)
#   SessionEnd                               세션을 닫을 때 체크포인트
#   PostToolUse(Bash)                        HEAD 가 움직였으면 커밋을 붙인다(async — Bash 를 막지 않는다)
# 저장소마다 거는 git post-commit 훅(install_git_hook.sh)은 터미널에서 손으로 친 커밋용이라 별개다.
# 기존 설정은 병합한다(덮어쓰지 않는다). 같은 훅이 이미 있으면 갈아 끼운다 — 여러 번 돌려도 안전하다.
set -e

SPEC='[
 {"event":"SessionStart","matcher":"startup|resume|clear|fork","arg":"session-start","status":"casebook: open threads"},
 {"event":"SessionStart","matcher":"compact","arg":"post-compact","status":"casebook: record what the summary holds"},
 {"event":"PreCompact","matcher":null,"arg":"checkpoint pre-compact","status":null},
 {"event":"SessionEnd","matcher":null,"arg":"checkpoint session-end","status":null},
 {"event":"PostToolUse","matcher":"Bash","arg":"post-commit","status":null,"async":true}
]'

if [ "$1" = "--print-spec" ]; then printf '%s\n' "$SPEC"; exit 0; fi

CASEBOOK="${CASEBOOK_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="$CASEBOOK/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at $PY — 먼저: cd $CASEBOOK && python3 -m venv .venv && .venv/bin/pip install -e \".[mcp]\"" >&2; exit 1; }
TARGET="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
mkdir -p "$(dirname "$TARGET")"

"$PY" - "$TARGET" "$CASEBOOK" "$SPEC" <<'PYEOF'
import json, pathlib, shutil, sys, time

target, casebook, spec = pathlib.Path(sys.argv[1]), sys.argv[2], json.loads(sys.argv[3])
hook = f"{casebook}/.venv/bin/python {casebook}/tools/hooks/casebook_hook.py"

data = {}
if target.is_file() and target.read_text().strip():
    try:
        data = json.loads(target.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{target} 를 읽을 수 없다 (JSON 오류: {e}) — 고친 뒤 다시 실행한다.")
    backup = target.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(target, backup)
    print(f"backup: {backup}")

def mine(entry):
    return any("casebook_hook.py" in (h.get("command") or "") for h in entry.get("hooks", []))

hooks = data.setdefault("hooks", {})
for ev in {s["event"] for s in spec}:                      # 이전 casebook 훅은 걷어낸다
    hooks[ev] = [e for e in hooks.get(ev, []) if not mine(e)]

for s in spec:
    one = {"type": "command", "command": f"{hook} {s['arg']}", "timeout": 15}
    if s.get("status"):
        one["statusMessage"] = s["status"]
    if s.get("async"):
        one["async"] = True                                # Bash 를 막지 않는다
    entry = {"hooks": [one]}
    if s.get("matcher"):
        entry = {"matcher": s["matcher"], **entry}
    hooks.setdefault(s["event"], []).append(entry)

target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(f"installed: {target} — 훅 {len(spec)} (casebook @ {casebook})")
PYEOF

echo "확인: 아무 저장소에서 claude 를 열면 첫 화면에 casebook 블록이 뜬다."
