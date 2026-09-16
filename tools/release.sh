#!/bin/zsh
# 공개 릴리스 한 줄 (확장 126호, D15948) — private 트리에서 allowlist에 든 파일만 공개 저장소에 올린다.
#   tools/release.sh              # 내보내기 → 스캔 → 시험 → 커밋 → 푸시
#   tools/release.sh --dry-run    # 푸시 직전까지만. 무엇이 나가는지 보고 끝낸다.
#
# 왜 이 모양인가: private(이 저장소)가 유일한 작업 저장소다. 공개 저장소는 사람이 손대지 않고 이 스크립트만 쓴다.
# 그래서 공개로 가는 통로가 하나이고, 그 통로에 스캔과 시험이 서 있다. 히스토리는 안 간다 — 254 커밋에
# 실값(인스턴스 id·계정번호)이 남아 있어 트리만 간다(D15935). 공개 히스토리는 릴리스 단위다.
# 무엇을 내보내는지는 deploy/public-include.txt, 그 안에서 다시 빼는 것은 public-exclude.txt 에 있다.
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
PUBLIC_REPO="${CASEBOOK_PUBLIC_REPO:-https://github.com/jasonethicseo/worktrail.git}"
INCLUDE="$HERE/deploy/public-include.txt"
EXCLUDE="$HERE/deploy/public-exclude.txt"
PY="$HERE/.venv/bin/python"
PUBLIC_GIT_NAME="${CASEBOOK_PUBLIC_GIT_NAME:-Worktrail Release}"
PUBLIC_GIT_EMAIL="${CASEBOOK_PUBLIC_GIT_EMAIL:-jasonethicseo@users.noreply.github.com}"
DRY=no; [[ "$1" == "--dry-run" ]] && DRY=yes

# 0) 추적 파일에 미커밋 변경이 있으면 멈춘다 — 무엇이 나갔는지 커밋 해시로 말할 수 있어야 한다(deploy.sh 와 같은 규칙).
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "추적 파일에 미커밋 변경이 있다 — 먼저 커밋한다"; git status --short --untracked-files=no; exit 1
fi
HEAD_SHORT=$(git rev-parse --short HEAD)

# 1) 공개 저장소를 임시 폴더에 받는다 (얕게 — 히스토리는 볼 일이 없다)
WORK=$(mktemp -d -t worktrail-release)
trap 'rm -rf "$WORK"' EXIT
git clone -q --depth 1 "$PUBLIC_REPO" "$WORK/pub"
PUB="$WORK/pub"
LAST_PRIVATE=$(git -C "$PUB" log -1 --format=%B | grep -o -E 'private [0-9a-f]{7,}' | head -1 | cut -d' ' -f2 || true)

# 2) 공개 목록: 추적 파일에서 allowlist를 고른 뒤 예외를 뺀다. 새 파일은 기본 비공개다.
git ls-files > "$WORK/all.txt"
"$PY" "$HERE/tools/public_manifest.py" \
  --tracked "$WORK/all.txt" --include "$INCLUDE" --exclude "$EXCLUDE" > "$WORK/public.txt"
echo "공개 트리: $(wc -l < "$WORK/public.txt" | tr -d ' ') 파일 (추적 $(wc -l < "$WORK/all.txt" | tr -d ' ') 중), private $HEAD_SHORT"

# 3) clone 안을 비우고(.git 빼고) 공개 목록만 복사한다 — 지운 파일도 공개에서 지워진다
find "$PUB" -mindepth 1 -maxdepth 1 -not -name .git -exec rm -rf {} +
while IFS= read -r f; do mkdir -p "$PUB/$(dirname "$f")"; cp "$f" "$PUB/$f"; done < "$WORK/public.txt"

# 4) 스캔 — 걸리면 아무것도 올리지 않는다. 두 겹이다.
#    (a) 꼴로 잡는 것: 키·개인키·JWT·EC2 id·12자리 계정번호·개인 이메일. 시험의 가짜 값은 허용한다.
#    (b) 값으로 잡는 것: 실행하는 사람의 홈 경로·계정명·커밋 이메일, deploy/local.env 와 .env 의 값들.
#        이 스크립트도 공개되므로 실값을 여기 적지 않는다 — 실행 시점의 환경에서 꺼내 고정 문자열로 찾는다.
#    (BSD grep 이라 -P 는 없다 — 계정번호는 앞뒤가 숫자가 아닌 12자리로 잡는다)
PATTERN='AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|BEGIN (RSA|EC|OPENSSH|PRIVATE) |client_01[A-Z0-9]{20,}|eyJ[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}|i-0[0-9a-f]{16}|(^|[^0-9])[0-9]{12}([^0-9]|$)|@(gmail|naver|kakao|daum)\.com'
ALLOW='i-0123456789abcdef0|123456789012|kim@naver\.com'
HITS=$( (cd "$PUB" && grep -rIn -E "$PATTERN" . --exclude-dir=.git --exclude-dir=vendor || true) | grep -v -E "$ALLOW" | cut -c1-140 || true)
{
  echo "$HOME"; id -un; git -C "$HERE" log -1 --format=%ae
  for f in "$HERE/deploy/local.env" "$HERE/.env"; do
    [[ -f "$f" ]] && grep -v -E '^\s*(#|$)' "$f" | sed -e 's/^[A-Za-z_][A-Za-z0-9_]*=//' -e 's/^"//' -e 's/"$//'
  done
} | awk 'length($0) >= 6' | sort -u > "$WORK/real.txt"          # 너무 짧은 값(예: "1")은 어디에나 있어 뺀다
REAL=$( (cd "$PUB" && grep -rIn -F -f "$WORK/real.txt" . --exclude-dir=.git --exclude-dir=vendor || true) | cut -c1-140 || true)
if [[ -n "$HITS$REAL" ]]; then
  echo "스캔에 걸렸다 — 올리지 않는다:"; [[ -n "$HITS" ]] && echo "$HITS"; [[ -n "$REAL" ]] && echo "$REAL" | sed 's/^/[실값] /'; exit 1
fi
echo "스캔: 깨끗함 (꼴 $(echo "$PATTERN" | tr '|' '\n' | wc -l | tr -d ' ')가지 · 실값 $(wc -l < "$WORK/real.txt" | tr -d ' ')개)"

# 5) 시험 — 공개 트리 그대로 돌아야 한다(빠진 파일에 기대는 시험이 있으면 여기서 드러난다).
#    .venv 는 gitignore 라 커밋에 안 들어간다 — 훅 설치 시험이 <저장소>/.venv/bin/python 을 찾으므로 잠깐 빌려 준다.
ln -s "$HERE/.venv" "$PUB/.venv"
echo "시험 (공개 트리에서):"
if ! (cd "$PUB" && PYTHONPATH=. "$PY" -m pytest -q -p no:cacheprovider > "$WORK/pytest.txt" 2>&1); then
  # 순서 의존 시험이 하나 있다(test_git_changes 의 훅 진입점: 전체에서는 가끔 지고 단독은 통과, 2026-09-16 실측).
  # 실패한 것만 한 번 다시 돌려 통과하면 받는다 — 진짜 실패는 두 번 다 진다.
  FAILED=$(grep -E '^FAILED ' "$WORK/pytest.txt" | sed -e 's/^FAILED //' -e 's/ - .*//')
  if [[ -n "$FAILED" ]] && (cd "$PUB" && PYTHONPATH=. "$PY" -m pytest -q -p no:cacheprovider ${(f)FAILED} > "$WORK/pytest2.txt" 2>&1); then
    echo "  처음 실패한 시험을 다시 돌려 통과했다(순서 의존): $(echo "$FAILED" | tr '\n' ' ')"
  else
    tail -4 "$WORK/pytest.txt"; echo "시험 실패 — 올리지 않는다"; exit 1
  fi
fi
tail -1 "$WORK/pytest.txt"
rm "$PUB/.venv"

# 6) 커밋 — 메시지에 private 해시와, 지난 릴리스 이후의 private 커밋 제목을 적는다
cd "$PUB"
find . -name __pycache__ -type d -prune -exec rm -rf {} +     # 시험이 남긴 것
git add -A
if git diff --cached --quiet; then echo "공개 저장소와 같다 — 올릴 것이 없다"; exit 0; fi
CHANGED=$(git diff --cached --stat | tail -1)
{
  echo "릴리스 $(date +%Y-%m-%d) — private $HEAD_SHORT"
  echo
  if [[ -n "$LAST_PRIVATE" ]] && git -C "$HERE" cat-file -e "$LAST_PRIVATE^{commit}" 2>/dev/null; then
    echo "지난 릴리스(private $LAST_PRIVATE) 이후:"
    git -C "$HERE" log --format='- %s' "$LAST_PRIVATE..HEAD"
  else
    echo "이전 릴리스의 private 해시를 못 찾아 목록을 생략한다."
  fi
  echo
  echo "$CHANGED"
  echo
  echo "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
} > "$WORK/msg.txt"
git -c user.name="$PUBLIC_GIT_NAME" -c user.email="$PUBLIC_GIT_EMAIL" commit -q -F "$WORK/msg.txt"
echo "커밋: $(git log -1 --oneline)"
git diff --stat HEAD~1 HEAD | tail -1

# 7) 푸시
if [[ "$DRY" == "yes" ]]; then echo "(dry-run — 푸시하지 않았다)"; exit 0; fi
git push -q origin HEAD:master
echo "올렸다: $PUBLIC_REPO ($(git rev-parse --short HEAD))"
