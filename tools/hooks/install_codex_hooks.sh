#!/bin/sh
# casebook — Codex 훅 설치 (~/.codex/hooks.json 에 병합). 확장 157호.
#   sh tools/hooks/install_codex_hooks.sh
#
# 하나를 넣는다: UserPromptSubmit → casebook_hook.py prompt codex
#   사람이 친 원문을 맥에 남기고 같은 턴에 source_ref(turn_id)를 알린다. 네트워크를 타지 않는다.
# Codex 는 사람이 검토해 신뢰한 훅만 돌린다 — 이 스크립트는 신뢰까지 대신하지 않는다(설치기가 그 사실을 알린다).
# 기존 파일은 병합한다(덮어쓰지 않는다). 같은 훅이 이미 있으면 갈아 끼운다 — 여러 번 돌려도 안전하다.
set -e

CASEBOOK="${CASEBOOK_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
PY="$CASEBOOK/.venv/bin/python"
[ -x "$PY" ] || { echo "no venv at $PY" >&2; exit 1; }
TARGET="${CODEX_HOOKS:-$HOME/.codex/hooks.json}"
mkdir -p "$(dirname "$TARGET")"

"$PY" - "$TARGET" "$CASEBOOK" <<'PYEOF'
import json, pathlib, shutil, sys, time

target, casebook = pathlib.Path(sys.argv[1]), sys.argv[2]
command = f"{casebook}/.venv/bin/python {casebook}/tools/hooks/casebook_hook.py prompt codex"

data = {}
if target.is_file() and target.read_text().strip():
    try:
        data = json.loads(target.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"{target} 를 읽을 수 없다 (JSON 오류: {e}) — 고친 뒤 다시 실행한다.")
    shutil.copy2(target, target.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}"))

def mine(entry):
    return any("casebook_hook.py" in (h.get("command") or "") for h in entry.get("hooks", []))

hooks = data.setdefault("hooks", {})
hooks["UserPromptSubmit"] = [e for e in hooks.get("UserPromptSubmit", []) if not mine(e)]
hooks["UserPromptSubmit"].append({"hooks": [{"type": "command", "command": command, "timeout": 5}]})
target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(f"installed: {target} — UserPromptSubmit (casebook @ {casebook})")
PYEOF
