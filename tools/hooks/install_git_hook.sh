#!/bin/sh
# casebook git post-commit 훅 — 확장 17호. 터미널에서 한 커밋도 현재 스레드의 change 로 남긴다.
#   sh tools/hooks/install_git_hook.sh            # 이 저장소에 설치
# 훅은 best-effort 다: casebook 이 죽어 있어도 commit 은 성공한다(exit 0 고정, 백그라운드).
set -e
ROOT="$(git rev-parse --show-toplevel)"
HOOKS="$(git rev-parse --git-path hooks)"
CASEBOOK="${CASEBOOK_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
mkdir -p "$HOOKS"
# worktree 는 실행 시점에 잡는다 — .git/hooks 는 모든 worktree 가 공유하므로 설치 시점의 ROOT 를 박으면
# .claude/worktrees/<name> 에서 만든 커밋이 원래 checkout 의 스레드에 붙는다(2026-09-05: ddcbb23 → #446, 2008eaa 가 #452·#453 에 이중).
cat > "$HOOKS/post-commit" <<HOOK
#!/bin/sh
# installed by casebook (tools/hooks/install_git_hook.sh)
CLAUDE_PROJECT_DIR="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)" "$CASEBOOK/.venv/bin/python" "$CASEBOOK/tools/hooks/casebook_hook.py" post-commit </dev/null >/dev/null 2>&1 &
exit 0
HOOK
chmod +x "$HOOKS/post-commit"
echo "installed: $HOOKS/post-commit → $CASEBOOK (worktree $ROOT)"
