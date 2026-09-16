"""클라이언트 배포 — 확장 36호. 원격 문이 토큰 뒤에서 얇은 클라이언트를 직접 내준다.

    GET /mcp/<token>/install.sh      # 자기 URL 이 박힌 설치 스크립트 (한 줄 설치)
    GET /mcp/<token>/client.tar.gz   # 얇은 클라이언트 파일 묶음

왜: 사용자가 clone·venv·pip·설정 파일 편집을 하지 않게 한다(C13017). 토큰이 이미 문지기이므로 배포도 같은 문을
쓴다 — 저장소는 private 이고 초대도 tarball 전달도 필요 없다. 설치 URL 이 곧 커넥터 URL 이라 `remote-url` 도
스크립트가 알아서 쓴다.

화면(확장 38호): `GET /mcp/<token>/ui` 가 Tracker HTML 을 낸다 — 받는 쪽은 브라우저가 아니라 `casebook-ui`
(로컬 창)다. 그래서 토큰이 브라우저 주소에 실리지 않고, UI 를 고쳐도 클라이언트는 그대로다.

무엇을 보내나: 프록시와 훅이 실제로 import 하는 것만 — `core/{threads,phase,state,errors}` 와 `adapters/mcp_proxy`,
훅 스크립트 셋. app·db·prompts 등 서버 코어는 보내지 않는다(원격 모드에서 쓰이지 않고, 보낼 이유도 없다).
`tests/test_client_dist.py` 가 이 목록만으로 import 가 서는지 지킨다 — 목록이 낡으면 거기서 깨진다.
"""
from __future__ import annotations

import hashlib
import io
import os
import pathlib
import tarfile
import time

# 클라이언트가 실제로 쓰는 파일. 여기 없는 것은 원격 모드에서 import 되지 않는다.
CLIENT_FILES = (
    "casebook/__init__.py",
    "casebook/core/__init__.py",
    "casebook/core/clientmode.py",  # 모드 판정 — 프록시·훅·창·backfill 이 같이 쓴다
    "casebook/core/errors.py",
    "casebook/core/headline.py",   # 확장 52호 — state.py 가 임포트한다
    "casebook/core/phase.py",
    "casebook/core/state.py",
    "casebook/core/threads.py",
    "casebook/adapters/__init__.py",
    "casebook/adapters/mcp_proxy.py",
    "casebook/adapters/ui_server.py",
    "casebook/adapters/backfill.py",
    "casebook/adapters/device_login.py",   # 확장 89호 — bin/casebook-login 이 부르는 것
    "tools/hooks/casebook_hook.py",
    "tools/hooks/install_claude_hooks.sh",
    "tools/hooks/install_git_hook.sh",
    # 확장 112호 (D15638) — 받은 사람이 무엇을 해도 되는지 알 수 있어야 한다.
    # 전에는 묶음에도 저장소에도 없어 법적 기본값(모든 권리 유보)에 말없이 기대고 있었다.
    "LICENSE",
)

# 실행기 — 설치 위치를 스스로 찾아 sys.path 를 잡는다. 사용자는 이 파일 하나만 부르면 된다.
# 확장 95호 — "$@" 와 argv 정리가 둘 다 있어야 인자가 모듈까지 간다.
# 없을 때 실제로 일어난 일: casebook-login --status 가 $DIR 을 서버 주소로 읽었고,
# casebook-backfill --dry-run 은 --dry-run 이 사라져 진짜로 올릴 뻔했다(2026-09-14).
_LAUNCHER = """#!/bin/sh
# {what} (서버가 만들어 보낸다)
DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$DIR/.venv/bin/python" -c 'import sys; sys.path.insert(0, sys.argv[1]); m = sys.argv[2]; sys.argv[:] = [m, *sys.argv[3:]]; import runpy; runpy.run_module(m, run_name="__main__")' "$DIR" {module} "$@"
"""

# 갱신 실행기 — 로컬 모드는 화면·코드가 받은 시점에 얼어 있다(아무것도 안 보내는 대가).
# 사람이 이걸 부를 때만 서버에 닿는다. 저절로 도는 것은 없다.
_UPDATE = r"""#!/bin/sh
# casebook 갱신 — 새 판이 있으면 받아서 바꾼다. 기록(~/.casebook/casebook.db)은 건드리지 않는다.
set -e
# 설치할 때 고른 언어로 말한다. 영어 설치의 마지막 안내가 바로 이 명령을 가리키는데,
# 여기만 한국어면 영어 테스터는 설치를 영어로 마치고 갱신에서 한국어를 만난다.
L="$(cat "$HOME/.casebook/lang" 2>/dev/null || true)"
[ "$L" = ko ] || [ "$L" = en ] || { case "${LC_ALL:-$LANG}" in ko*) L=ko ;; *) L=en ;; esac; }
if [ "$L" = ko ]; then
  U_NOURL="갱신 주소가 없다 — 받은 설치 한 줄을 다시 실행한다."
  U_NOSRV="서버에 닿지 못했다 — 잠시 뒤 다시 해 본다."
  U_NOTSH="받은 것이 설치 스크립트가 아니다 — 주소를 확인한다."
  U_BADMODE="모드가 이상하다 — ~/.casebook/mode 를 local 또는 server 로 고친다."
  U_LATEST="이미 최신이다"; U_NEW="새 판"; U_NOW="지금은"; U_GET="받는다."
else
  U_NOURL="No update address — run the install line you were given again."
  U_NOSRV="Could not reach the server — try again in a moment."
  U_NOTSH="What came back is not the install script — check the address."
  U_BADMODE="The mode looks wrong — set ~/.casebook/mode to local or server."
  U_LATEST="Already up to date"; U_NEW="New version"; U_NOW="now on"; U_GET="Downloading."
fi
U="$HOME/.casebook/update-url"
[ -r "$U" ] || { echo "$U_NOURL" >&2; exit 1; }
URL="$(cat "$U")"
NOW="$(cat "$HOME/.casebook/client/VERSION" 2>/dev/null || echo unknown)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$URL/install.sh" -o "$TMP" || { echo "$U_NOSRV" >&2; exit 1; }
# 고를 때 적어 둔 모드를 그대로 쓴다. 옛 설치본에는 mode 파일이 없다 — 그때만 주소 유무로 본다.
# 판을 읽기 전에 정해야 한다: 판이 모드마다 다르기 때문이다.
if [ -r "$HOME/.casebook/mode" ]; then
  M="--$(cat "$HOME/.casebook/mode")"
elif [ -r "$HOME/.casebook/remote-url" ]; then M=--server; else M=--local; fi
case "$M" in --local|--server) ;; *) echo "$U_BADMODE ($M)" >&2; exit 1 ;; esac
case "$M" in --local) KEY=VERSION_LOCAL ;; *) KEY=VERSION_THIN ;; esac
NEW="$(sed -n "s/^$KEY='\(.*\)'\$/\1/p" "$TMP" | head -1)"
[ -n "$NEW" ] || { echo "$U_NOTSH" >&2; exit 1; }
if [ "$NOW" = "$NEW" ]; then
  echo "$U_LATEST ($NOW)."
  exit 0
fi
echo "$U_NEW $NEW — $U_NOW $NOW. $U_GET"
# 고른 언어를 다시 넘긴다 — 넘기지 않으면 재설치가 언어를 다시 묻거나 로케일로 되돌린다.
sh "$TMP" "$M" "--$L"
"""

# 확장 116호 — 깐 것을 걷어낸다. 안내문이 "말하면 지운다"고 약속하는데(D15637) 서버 쪽만 지우면
# 이 맥에는 실행기·훅·토큰이 그대로 남는다. 지울 것을 세어 보이는 것이 기본이고, 실제로 지우려면
# --yes 를 붙인다. 기록 DB(로컬 모드의 ~/.casebook/casebook.db)는 --records 를 따로 줘야 지운다 —
# 그것이 그 사람의 기록 전부이고, 프로그램을 지우는 것과 기록을 버리는 것은 다른 결심이다.
_UNINSTALL = r"""#!/bin/sh
# casebook 삭제 — 이 맥에 깐 것을 걷어낸다.
#   casebook-uninstall                 무엇을 지울지 보여만 준다
#   casebook-uninstall --yes           실제로 지운다 (기록 DB 는 남긴다)
#   casebook-uninstall --yes --records 기록 DB 까지 지운다
set -e
H="$HOME/.casebook"
L="$(cat "$H/lang" 2>/dev/null || true)"
[ -n "$L" ] || case "${LANG:-}${LC_ALL:-}" in ko*|*ko_KR*) L=ko ;; *) L=en ;; esac
YES=no; RECORDS=no
for a in "$@"; do
  case "$a" in --yes) YES=yes ;; --records) RECORDS=yes ;;
    *) echo "모르는 인자: $a — 받는 것은 --yes 와 --records 뿐이다" >&2; exit 1 ;; esac
done

if [ "$L" = ko ]; then
  T_HEAD="지울 것"; T_KEEP="남기는 것"; T_ASK="지우려면 --yes 를 붙인다."
  T_DB="기록 DB — 로컬 모드면 이것이 당신 기록 전부다. 지우려면 --yes --records"
  T_GONE="지웠다."; T_NONE="깐 흔적이 없다."; T_SRV="서버에 둔 기록은 여기서 지워지지 않는다 — 운영자에게 말한다."
else
  T_HEAD="Will remove"; T_KEEP="Kept"; T_ASK="Add --yes to actually remove."
  T_DB="your records — in local mode this file is all of them. Add --yes --records to delete it"
  T_GONE="Removed."; T_NONE="Nothing is installed."; T_SRV="Records kept on the server are not touched here — ask the operator."
fi

FOUND=no
echo "$T_HEAD:"
[ -d "$H/client" ]      && { echo "  $H/client"; FOUND=yes; }
[ -d "$H/client.old" ]  && { echo "  $H/client.old"; FOUND=yes; }
for f in mode lang update-url remote-url oauth.json; do
  [ -e "$H/$f" ] && { echo "  $H/$f"; FOUND=yes; }
done
command -v claude >/dev/null 2>&1 && claude mcp get casebook >/dev/null 2>&1 && { echo "  claude mcp: casebook"; FOUND=yes; }
command -v codex  >/dev/null 2>&1 && grep -q '\[mcp_servers.casebook\]' "$HOME/.codex/config.toml" 2>/dev/null && { echo "  codex mcp: casebook"; FOUND=yes; }
grep -q casebook_hook.py "$HOME/.claude/settings.json" 2>/dev/null && { echo "  ~/.claude/settings.json 의 casebook 훅"; FOUND=yes; }
[ "$FOUND" = yes ] || { echo "  -"; echo "$T_NONE"; exit 0; }

echo ""
echo "$T_KEEP:"
[ -e "$H/casebook.db" ] && echo "  $H/casebook.db  ($T_DB)"
echo "  $T_SRV"

if [ "$YES" != yes ]; then echo ""; echo "$T_ASK"; exit 0; fi

# 에이전트 등록부터 뗀다 — 파일을 먼저 지우면 등록만 남아 claude 가 없는 것을 부른다.
command -v claude >/dev/null 2>&1 && claude mcp remove casebook -s user >/dev/null 2>&1 || true
command -v codex  >/dev/null 2>&1 && codex mcp remove casebook >/dev/null 2>&1 || true

# 훅은 우리 것만 걷어낸다 — 남의 훅이 같은 파일에 있다. 손대기 전에 백업을 둔다.
python3 - "$HOME/.claude/settings.json" <<'PYEOF' || true
import json, pathlib, shutil, sys, time
t = pathlib.Path(sys.argv[1])
if not (t.is_file() and t.read_text().strip()):
    raise SystemExit(0)
try:
    data = json.loads(t.read_text())
except json.JSONDecodeError:
    print("settings.json 을 읽을 수 없어 훅은 그대로 둔다", file=sys.stderr); raise SystemExit(0)
hooks = data.get("hooks") or {}
def mine(e):
    return any("casebook_hook.py" in (h.get("command") or "") for h in e.get("hooks", []))
hit = 0
for ev in list(hooks):
    keep = [e for e in hooks[ev] if not mine(e)]
    hit += len(hooks[ev]) - len(keep)
    if keep: hooks[ev] = keep
    else: del hooks[ev]
if not hit:
    raise SystemExit(0)
shutil.copy2(t, t.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}"))
data["hooks"] = hooks
t.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
print(f"훅 {hit} 걷어냄 (백업 남김)")
PYEOF

[ "$RECORDS" = yes ] && rm -f "$H/casebook.db" "$H/casebook.db-wal" "$H/casebook.db-shm"
rm -f "$H/mode" "$H/lang" "$H/update-url" "$H/remote-url" "$H/oauth.json"
rm -rf "$H/client.old"
# 마지막이다 — 이 스크립트가 그 안에 있다.
rm -rf "$H/client"
rmdir "$H" 2>/dev/null || true
echo "$T_GONE"
"""

LAUNCHERS = {
    "bin/casebook-proxy": _LAUNCHER.format(
        what="casebook 프록시 — claude mcp add casebook -s user -- <이 파일>",
        module="casebook.adapters.mcp_proxy"),
    "bin/casebook-ui": _LAUNCHER.format(
        what="casebook 창 — 브라우저로 현황·회고를 본다. 토큰은 브라우저에 가지 않는다",
        module="casebook.adapters.ui_server"),
    "bin/casebook-update": _UPDATE,
    "bin/casebook-backfill": _LAUNCHER.format(
        what="설치 전 기록 끌어오기 — 이 폴더에서 한 과거 세션의 사람 지시만 서버로",
        module="casebook.adapters.backfill"),
    # 확장 89호 — 서버가 oauth 를 켰을 때만 쓸 것이 있다. 켜지 않았으면 "이 서버는 로그인을
    # 받지 않는다" 고 말하고 끝난다 — 있어서 해로울 것이 없고, 켠 날 따로 배포하지 않아도 된다.
    "bin/casebook-login": _LAUNCHER.format(
        what="터미널에서 로그인 — 코드 한 조각을 브라우저에 넣으면 주소 대신 토큰으로 붙는다",
        module="casebook.adapters.device_login"),
    "bin/casebook-uninstall": _UNINSTALL,
}

LAUNCHER = LAUNCHERS["bin/casebook-proxy"]      # 이름 유지 (기존 테스트·문서)


def source_root() -> pathlib.Path:
    """클라이언트 파일을 읽어 올 곳. 컨테이너에서는 마운트된 체크아웃(/src), 아니면 이 저장소."""
    return pathlib.Path(os.environ.get("CASEBOOK_CLIENT_SRC") or
                        pathlib.Path(__file__).resolve().parents[2])


# 로컬 모드 — 기록이 사용자 맥에만 있을 때. 서버 코어까지 전부 실어야 문과 화면이 거기서 선다.
LOCAL_GLOBS = ("casebook/*.py", "casebook/core/*.py", "casebook/adapters/*.py",
               "tools/hooks/*", "web/worktrail/index.html", "main.py", "LICENSE")
# 확장 130호 (D16054) — 조사·티켓은 private 에만: 로컬 묶음에도 싣지 않는다. 로컬 창은 Worktrail 만 띄우고
# main.py 는 그 모듈 없이 돈다(조사 모드는 환경변수로만 켜고, 없으면 멈춘다).
LOCAL_EXCLUDE = frozenset({
    "casebook/core/app.py", "casebook/core/tickets.py", "casebook/core/workers.py", "casebook/core/prompts.py",
    "casebook/core/prompt_ext.py", "casebook/core/ledger.py", "casebook/adapters/openai_llm.py",
    "casebook/adapters/no_search.py", "casebook/adapters/http_api_legacy.py",
})


def local_files(root: pathlib.Path | None = None) -> tuple[str, ...]:
    root = root or source_root()
    out: list[str] = []
    for g in LOCAL_GLOBS:
        out += sorted(str(p.relative_to(root)) for p in root.glob(g)
                      if p.is_file() and "__pycache__" not in p.parts
                      and str(p.relative_to(root)) not in LOCAL_EXCLUDE)
    return tuple(dict.fromkeys(out))


def build_tarball(root: pathlib.Path | None = None, mode: str = "thin") -> bytes:
    """실행기 + 파일 묶음. thin = 원격용 최소, local = 기록까지 이 맥에 두는 전체."""
    root = root or source_root()
    files = local_files(root) if mode == "local" else CLIENT_FILES
    missing = [f for f in files if not (root / f).is_file()]
    if missing:
        raise FileNotFoundError(f"client files missing under {root}: {', '.join(missing)}")
    if mode == "local" and not any(f.endswith("core/worktrail.py") for f in files):
        raise FileNotFoundError("local bundle needs casebook/core — nothing to serve records from")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in files:
            info = tar.gettarinfo(str(root / name), arcname=f"casebook-client/{name}")
            info.uid = info.gid = 0
            info.uname = info.gname = "casebook"
            with open(root / name, "rb") as fh:
                tar.addfile(info, fh)
        for name, script in LAUNCHERS.items():
            data = script.encode()
            info = tarfile.TarInfo(f"casebook-client/{name}")
            info.size, info.mode, info.mtime = len(data), 0o755, int(time.time())
            info.uname = info.gname = "casebook"
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def version(root: pathlib.Path | None = None, mode: str = "thin") -> str:
    """그 모드가 실제로 받는 파일들의 해시 12자. 설치본이 서버와 같은지 사람이 눈으로 맞출 수 있게.

    모드마다 따로 세는 이유: 로컬 묶음은 LOCAL_GLOBS 로 뽑히는데(서버 코어·화면까지) 전에는 판을
    CLIENT_FILES 15개로만 셌다. 그래서 기록 엔진이나 화면을 고쳐도 로컬 모드 설치본에는
    "이미 최신이다" 가 나왔고, 얇은 파일 하나가 우연히 같이 바뀔 때만 편승해 내려갔다."""
    root = root or source_root()
    files = local_files(root) if mode == "local" else CLIENT_FILES
    h = hashlib.sha256()
    for name in files:
        p = root / name
        h.update(name.encode())
        h.update(p.read_bytes() if p.is_file() else b"")
    for name, script in sorted(LAUNCHERS.items()):
        h.update(name.encode()); h.update(script.encode())
    return h.hexdigest()[:12]


INSTALL_SH = r"""#!/bin/sh
# casebook 설치 — 한 줄. 서버가 이 스크립트를 만들어 보냈고 URL 은 이미 박혀 있다.
#   curl -fsSL {url}/install.sh | sh
# 다시 실행하면 최신으로 갱신한다(설정·기록은 그대로).
set -e
URL='{url}'
DIR="$HOME/.casebook/client"
# 판은 모드마다 다르다 — 로컬 묶음에는 서버 코어와 화면이 더 들어가므로 세는 파일이 다르다.
VERSION_THIN='{version_thin}'
VERSION_LOCAL='{version_local}'

say() {{ printf '  %s\n' "$1"; }}

# 0) 언어 — 무엇보다 먼저 정한다. 아래 안내와 **데이터 고지**가 전부 이 값으로 갈리므로,
#    영어권 사용자가 첫 줄부터 한국어를 만나면 거기서 끝난다. 기본값은 시스템 언어다.
L=""
for a in "$@"; do
  [ "$a" = "--ko" ] && L=ko
  [ "$a" = "--en" ] && L=en
done
# POSIX 는 LC_ALL 이 LANG 을 이긴다. 신호가 아예 없으면(둘 다 비어 있음) 영어로 **단정하지 않는다** —
# 그러면 한국어 사용자가 Enter 만 쳐도 영어가 박히고, 비대화식에서는 묻지도 않는다(실측: 이 맥이 그렇다).
# 그 경우 lang 파일을 쓰지 않고 화면이 브라우저 언어로 정하게 둔다.
SIG="${{LC_ALL:-$LANG}}"
if [ -z "$SIG" ]; then SYS=""; else case "$SIG" in ko*) SYS=ko ;; *) SYS=en ;; esac; fi
# 터미널이 실제로 **열리는지**로 잰다. -r/-w 는 파일이 있기만 해도 참이라, CI·도커처럼
# /dev/tty 가 있지만 "Device not configured" 로 열리지 않는 곳에서 set -e 로 죽었다(실측).
TTY=no
{{ : > /dev/tty; }} 2>/dev/null && TTY=yes
if [ -z "$L" ] && [ "$TTY" = yes ]; then
  if [ -n "$SYS" ]; then HINT="(Enter = $SYS)"; else HINT="(Enter = ask the screen later)"; fi
  printf 'Language / 언어   [1] English   [2] 한국어   %s > ' "$HINT" > /dev/tty
  read -r pick < /dev/tty || pick=""
  case "$pick" in 1) L=en ;; 2) L=ko ;; *) L=$SYS ;; esac
fi
[ -n "$L" ] || L=$SYS

if [ "${{L:-en}}" = ko ]; then
  M_HEAD="casebook 설치"
  M_NOPY="python3 가 없다 — 설치한 뒤 다시 실행한다."
  M_OLDPY="python3.11 이상이 필요하다 (지금: %s)."
  M_PICK="고른다 [1/2] > "
  M_NOTTY="고를 수 없는 곳에서 실행됐다 — --local 또는 --server 로 명시한다."
  M_VER="판"; M_WHERE="기록 위치"; M_HERE="이 맥에만"; M_SRV="서버"
  M_NOFETCH="파일을 받지 못했다 — 서버가 잠깐 내려갔거나 주소가 틀렸다."
  M_NOCHANGE="아무것도 바꾸지 않았다. 잠시 뒤 다시 실행한다."
  M_NOTAR="받은 파일을 풀지 못했다. 아무것도 바꾸지 않았다."
  M_MISSING="받은 묶음에 %s 가 없다. 아무것도 바꾸지 않았다."
  M_GOTFILES="파일 받음"; M_VENV="파이썬 환경 준비"
  M_SRVURL="서버 주소 기록"; M_LOCALONLY="기록은 ~/.casebook/casebook.db 에만 쌓인다"
  M_UPDURL="갱신 주소만 따로 둔다 (~/.casebook/update-url) — casebook-update 를 부를 때만 쓴다"
  M_CLAUDE="Claude Code 등록"; M_HOOKS="Claude Code 훅 설치"; M_NOCLAUDE="Claude Code 없음 — 건너뜀"
  M_CODEX="Codex 등록"; M_CODEXWAIT="Codex 시작 대기 30초"; M_NOCODEX="Codex 없음 — 건너뜀"
  M_DONE_C="됐다. 아무 저장소에서 claude 를 열면 첫 화면에 casebook 이 뜬다."
  M_DONE_CX="codex 에서는 훅이 없으니 시작할 때 한 번: \"casebook 열린 스레드 보여줘\""
  M_DONE_X="됐다. codex 에는 세션 훅이 없으니 시작할 때 한 번: \"casebook 열린 스레드 보여줘\""
  M_DONE_N="됐다. 다만 등록할 에이전트를 못 찾았다 — claude 나 codex 를 설치한 뒤 이 줄을 다시 실행한다."
  M_LOGIN="이제 로그인한다 — 아래 주소를 브라우저에서 열면 된다"
  M_LOGGEDIN="이미 로그인돼 있다"
  M_LOGINFAIL="로그인을 끝내지 못했다. 기록은 로그인해야 쌓인다 — 다시 하려면:"
  M_LOGINLATER="터미널이 없어 로그인을 건너뛰었다. 기록은 로그인해야 쌓인다 — 이 한 줄을 돌린다:"
  M_UI="기록을 눈으로 보려면:"; M_UIT="(브라우저가 열린다)"
  M_UNI="그만 쓰려면 (무엇을 지울지 먼저 보여 준다):"
  M_BF="깔기 전에 한 작업도 가져오려면:"
  M_BF_L="  작업공간 목록을 보여 주고 무엇을 가져올지 고르게 한다 — 전부 이 맥 안에서 끝난다."
  M_BF_S="  작업공간 목록을 보여 주고 무엇을 올릴지 고르게 한다 — 고른 것만 서버로 간다."
  M_BF_2="  가져오는 것은 당신이 친 말의 원문뿐이다(에이전트의 말·도구 결과는 안 간다)."
  M_GIT="터미널 커밋도 기록에 붙이려면 저장소마다 한 번:"
  M_UPD="새 판이 나왔는지 보려면:"
  M_UPD_1="  로컬 모드는 화면·코드가 받은 시점에 얼어 있다. 이 줄을 부를 때만 서버에 닿는다."
  M_UPD_2="  기록은 그대로 남는다."
else
  M_HEAD="Installing Worktrail"
  M_NOPY="python3 not found — install it and run this again."
  M_OLDPY="python3.11 or newer is required (now: %s)."
  M_PICK="Choose [1/2] > "
  M_NOTTY="Ran somewhere with no terminal — pass --local or --server explicitly."
  M_VER="version"; M_WHERE="Records live"; M_HERE="on this machine only"; M_SRV="on the server"
  M_NOFETCH="Could not download the files — the server may be down, or the address is wrong."
  M_NOCHANGE="Nothing was changed. Try again in a moment."
  M_NOTAR="Could not unpack what was downloaded. Nothing was changed."
  M_MISSING="The bundle is missing %s. Nothing was changed."
  M_GOTFILES="Files downloaded"; M_VENV="Python environment ready"
  M_SRVURL="Server address saved"; M_LOCALONLY="Records go only to ~/.casebook/casebook.db"
  M_UPDURL="Update address kept separately (~/.casebook/update-url) — used only when you run casebook-update"
  M_CLAUDE="Registered with Claude Code"; M_HOOKS="Claude Code hooks installed"; M_NOCLAUDE="Claude Code not found — skipped"
  M_CODEX="Registered with Codex"; M_CODEXWAIT="Codex startup timeout set to 30s"; M_NOCODEX="Codex not found — skipped"
  M_DONE_C="Done. Open claude in any repository and Worktrail shows up on the first screen."
  M_DONE_CX="Codex has no session hook, so say this once when you start: \"show my open Worktrail threads\""
  M_DONE_X="Done. Codex has no session hook, so say this once when you start: \"show my open Worktrail threads\""
  M_DONE_N="Done — but no agent was found to register with. Install claude or codex, then run this line again."
  M_LOGIN="Now signing you in — open the address below in your browser"
  M_LOGGEDIN="Already signed in"
  M_LOGINFAIL="Sign-in did not finish. Records need it — to try again:"
  M_LOGINLATER="No terminal here, so sign-in was skipped. Records need it — run this line:"
  M_UI="To see your records:"; M_UIT="(opens a browser)"
  M_UNI="To remove it again (it shows what would go first):"
  M_BF="To pull in work you did before installing:"
  M_BF_L="  It lists your workspaces and lets you choose — everything stays on this machine."
  M_BF_S="  It lists your workspaces and lets you choose — only what you pick goes to the server."
  M_BF_2="  Only your own words are pulled in (never the agent's replies or tool output)."
  M_GIT="To attach terminal commits too, once per repository:"
  M_UPD="To check for a new version:"
  M_UPD_1="  Local mode is frozen at the version you downloaded. It reaches the server only when you run this."
  M_UPD_2="  Your records are left untouched."
fi

echo "$M_HEAD"

command -v python3 >/dev/null || {{ echo "$M_NOPY" >&2; exit 1; }}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || {{
  printf "$M_OLDPY\n" "$(python3 -V 2>&1)" >&2; exit 1; }}

# 1) 기록을 어디 둘지 — 사람이 정한다. 되돌릴 수 있지만 그때 기록은 따라오지 않는다.
MODE=""
for a in "$@"; do
  [ "$a" = "--local" ] && MODE=local
  [ "$a" = "--server" ] && MODE=server
done
if [ -z "$MODE" ]; then
  if [ "$L" = ko ]; then
    cat <<'ASK_KO'

기록을 어디에 둘지 정한다. 나중에 바꿀 수 있지만 그때까지 쌓인 기록은 따라오지 않는다.

  1) 서버에  (지금은 이쪽을 권한다)
     · 컴퓨터를 여러 대 쓰거나 바꿔도 기록이 한곳에 모인다
     · Claude.ai · ChatGPT 에 주소만 넣으면 폰에서도 "뭐 하다 말았지"를 볼 수 있다
     · 백업이 자동이다 — 맥이 고장 나도 기록은 남는다
     · 시험 기간이라 고치는 일이 잦다. 서버 쪽은 고치면 바로 닿지만, 아래 로컬은
       당신이 casebook-update 를 부를 때까지 받은 그대로 얼어 있다

     기록을 맡기는 것이므로 이렇게 다룬다:
     · 보관 — 계정이 살아 있는 동안 둔다. 시험 기간이라 기한을 못 박지 않는다
     · 삭제 — 말하면 그 계정의 기록을 전부 지운다. 7일 안에 처리한다
     · 열람 — 운영자는 기술적으로 DB 를 열 수 있다. 그게 구조다
     · 종료 — 시험을 끝낼 때 알리고 30일 뒤 전부 지운다. 그 전에 받아 갈 수 있다
     · 그리고 작업하다 나온 에러 메시지나 명령 출력을 에이전트가 원문 그대로 기록에 넣는데,
       거기 API 키 같은 게 섞여 있으면 그것도 같이 올라간다
       (그때그때 "그건 기록하지 마" 라고 하면 안 넣는다)

  2) 이 맥에만
     기록이 이 컴퓨터를 떠나지 않는다. 설치할 때만 이 주소를 쓰고, 그 뒤로는 아무것도 보내지 않는다.
     · 백업은 직접 해야 한다 — 파일 하나다 (~/.casebook/casebook.db)
     · 맥을 바꾸거나 두 대로 일하면 기록이 따로 논다
     · Claude.ai · ChatGPT 앱에서는 못 본다 (그쪽은 인터넷으로 닿을 주소가 필요하다)

ASK_KO
  else
    cat <<'ASK_EN'

Choose where your records are kept. You can change this later, but records already
written do not move with you.

  1) On the server   (recommended for now)
     · Records stay in one place across machines
     · Put the address into Claude.ai or ChatGPT and you can check "where was I?"
       from your phone
     · Backups are automatic — the records survive if your machine dies
     · This is a test period and fixes land often. The server side gets them right
       away; the local option below stays frozen as you received it until you run
       casebook-update yourself

     You are handing your records over, so here is how they are handled:
     · Kept — for as long as your account exists. No fixed term during the test
     · Deleted — say the word and everything for your account is erased, within 7 days
     · Read — the operator can technically open the database. That is the shape of it
     · Ending — when the test ends you will be told, and everything is erased 30 days
       later. You can take yours before that
     · And when your agent records an error message or command output, it stores it
       verbatim — if an API key happens to be in there, it goes up too
       (say "do not record that" at the time and it will not)

  2) On this machine only
     Your records never leave this computer. This address is used to install, and
     nothing is sent after that.
     · You back it up yourself — it is a single file (~/.casebook/casebook.db)
     · If you switch machines or work on two, the records do not follow
     · You cannot see them in the Claude.ai or ChatGPT apps (those need an address
       reachable over the internet)

ASK_EN
  fi
  # curl … | sh 로 오면 stdin 은 파이프다 — 터미널은 /dev/tty 로 따로 연다.
  # stdin 이 터미널인지로 재면 문서에 적힌 그 명령이 늘 거부된다(2026-09-08 실측).
  if [ "$TTY" = yes ]; then
    printf '%s' "$M_PICK" > /dev/tty
    read -r pick < /dev/tty || pick=""
    # 확장 112호 (D15636) — 1 이 서버다. 안내문에서 서버를 위에 놓고 권했으므로 번호도 그쪽이다.
    # Enter 만 쳐도 권하는 쪽으로 간다 — 묻기는 그대로 두되 권장만 옮긴 것이 이 결정의 전부다.
    case "$pick" in 2) MODE=local ;; *) MODE=server ;; esac
  else
    echo "$M_NOTTY" >&2
    exit 1
  fi
fi
VERSION=$([ "$MODE" = local ] && echo "$VERSION_LOCAL" || echo "$VERSION_THIN")
say "$M_VER $VERSION"
say "$M_WHERE: $([ "$MODE" = local ] && echo "$M_HERE" || echo "$M_SRV")"

# 2) 클라이언트 파일
rm -rf "$DIR.new"
mkdir -p "$DIR.new"
[ "$MODE" = local ] && FETCH="$URL/client.tar.gz?mode=local" || FETCH="$URL/client.tar.gz"
# 파이프로 바로 풀면 curl 이 죽어도 tar 가 빈 입력으로 성공해 설치가 그대로 이어진다 —
# 그러면 파일 하나 없이 에이전트 등록과 설정 변경만 일어난다(2026-09-08 실제로 났다).
# 받아서 확인한 뒤에 푼다. 여기서 실패하면 아무것도 바꾸지 않고 멈춘다.
if ! curl -fsSL "$FETCH" -o "$DIR.new/client.tgz"; then
  echo "$M_NOFETCH" >&2
  echo "$M_NOCHANGE" >&2
  rm -rf "$DIR.new"; exit 1
fi
if ! tar xzf "$DIR.new/client.tgz" -C "$DIR.new" --strip-components=1; then
  echo "$M_NOTAR" >&2
  rm -rf "$DIR.new"; exit 1
fi
rm -f "$DIR.new/client.tgz"
printf '%s\n' "$VERSION" > "$DIR.new/VERSION"
for f in bin/casebook-proxy bin/casebook-ui bin/casebook-uninstall tools/hooks/install_claude_hooks.sh; do
  [ -e "$DIR.new/$f" ] || {{
    printf "$M_MISSING\n" "$f" >&2
    rm -rf "$DIR.new"; exit 1; }}
done
say "$M_GOTFILES"

# 3) 파이썬 환경 — 그 모드가 실제로 쓰는 것까지 있어야 재사용한다. 서버 묶음 venv 에는
# fastapi 가 없어서, 서버→로컬 재설치가 조용히 성공한 뒤 창이 ModuleNotFoundError 로 죽었다.
if [ "$MODE" = local ]; then NEED='import mcp, fastapi, uvicorn'; else NEED='import mcp'; fi
if [ -x "$DIR/.venv/bin/python" ] && "$DIR/.venv/bin/python" -c "$NEED" 2>/dev/null; then
  cp -R "$DIR/.venv" "$DIR.new/.venv"
else
  python3 -m venv "$DIR.new/.venv"
  [ "$MODE" = local ] && DEPS="mcp fastapi uvicorn" || DEPS="mcp"
  "$DIR.new/.venv/bin/pip" install -q --disable-pip-version-check $DEPS
fi
say "$M_VENV"

rm -rf "$DIR.old"
[ -d "$DIR" ] && mv "$DIR" "$DIR.old" || true
mv "$DIR.new" "$DIR"
rm -rf "$DIR.old"

# 4) 고른 모드와 언어를 적어 둔다 — 짐작하지 않기 위해서다. 주소 파일 유무로 모드를 재면
#    서버를 쓰던 사람의 주소가 사라졌을 때 조용히 로컬이 되어 빈 DB 에 기록이 쌓인다.
mkdir -p "$HOME/.casebook"
printf '%s\n' "$MODE" > "$HOME/.casebook/mode"
chmod 600 "$HOME/.casebook/mode"
# 고르지 않았고 로케일 신호도 없으면 적지 않는다 — 짐작을 파일에 굳히지 않는다.
if [ -n "$L" ]; then
  printf '%s\n' "$L" > "$HOME/.casebook/lang"
  chmod 600 "$HOME/.casebook/lang"
fi

# 서버 주소 — 서버 모드일 때만 쓴다. 로컬 모드면 이 파일이 없고, 그래서 아무것도 나가지 않는다.
if [ "$MODE" = server ]; then
  printf '%s\n' "$URL" > "$HOME/.casebook/remote-url"
  chmod 600 "$HOME/.casebook/remote-url"
  say "$M_SRVURL"
else
  rm -f "$HOME/.casebook/remote-url"
  say "$M_LOCALONLY"
fi
printf '%s\n' "$URL" > "$HOME/.casebook/update-url"
chmod 600 "$HOME/.casebook/update-url"
[ "$MODE" = local ] && say "$M_UPDURL"

# 5) 에이전트에 등록 — 있는 것만. 확인 문구는 실제로 등록된 것으로 말한다(Codex 만 깔린 사람에게
#    "claude 를 열어라"고 하지 않기 위해).
HAVE_CLAUDE=no
HAVE_CODEX=no
if command -v claude >/dev/null 2>&1; then
  claude mcp remove casebook -s user >/dev/null 2>&1 || true
  claude mcp add casebook -s user -- "$DIR/bin/casebook-proxy" >/dev/null && say "$M_CLAUDE"
  sh "$DIR/tools/hooks/install_claude_hooks.sh" >/dev/null && say "$M_HOOKS"
  HAVE_CLAUDE=yes
else
  say "$M_NOCLAUDE"
fi
if command -v codex >/dev/null 2>&1; then
  codex mcp remove casebook >/dev/null 2>&1 || true
  codex mcp add casebook -- "$DIR/bin/casebook-proxy" >/dev/null && say "$M_CODEX"
  # 붙는 데 시간이 걸린다(파이썬 가상환경 + 서버 접속). Codex 기본 대기가 짧으면 첫 연결이 끊긴다.
  python3 - "$HOME/.codex/config.toml" <<'TOMLPY' >/dev/null 2>&1 && say "$M_CODEXWAIT"
import pathlib, shutil, sys, time
p = pathlib.Path(sys.argv[1]).expanduser()
if not p.is_file():
    raise SystemExit(1)
lines = p.read_text().splitlines()
head = "[mcp_servers.casebook]"
if head not in lines:
    raise SystemExit(1)
i = lines.index(head)
j = i + 1
while j < len(lines) and not lines[j].startswith("["):
    if lines[j].strip().startswith("startup_timeout_sec"):
        raise SystemExit(1)
    j += 1
shutil.copy2(p, p.parent / ("config.toml.bak-" + time.strftime("%Y%m%d-%H%M%S")))
lines.insert(i + 1, "startup_timeout_sec = 30")
p.write_text("\n".join(lines) + "\n")
TOMLPY
  HAVE_CODEX=yes
else
  say "$M_NOCODEX"
fi

# 6) 로그인 — 서버 모드는 이것까지 해야 쓸 수 있다 (확장 104호).
#    전에는 설치가 "됐다"로 끝나고 로그인을 한 마디도 하지 않았다. 그런데 운영 서버는 구형 경로를
#    닫아 두었으므로(확장 92호) 로그인 없이는 기록이 한 줄도 남지 않는다 — 받은 사람은 claude 를
#    열어 401 만 보고, 무엇을 해야 하는지 모른 채 거기서 멈춘다. 설치와 분리해 둘 이유가 없다.
#    로컬 모드는 서버에 닿지 않으므로 건너뛴다. 로그인이 실패해도 설치는 실패로 만들지 않는다 —
#    깔린 것은 깔린 것이고, 다시 하는 한 줄을 알려 주면 된다.
LOGIN_TODO=no
if [ "$MODE" = server ]; then
  if "$DIR/bin/casebook-login" --status >/dev/null 2>&1; then
    say "$M_LOGGEDIN"
  elif [ "$TTY" = yes ]; then
    echo
    say "$M_LOGIN"
    "$DIR/bin/casebook-login" || LOGIN_TODO=fail
  else
    LOGIN_TODO=later
  fi
fi

echo
if [ "$HAVE_CLAUDE" = yes ]; then
  echo "$M_DONE_C"
  [ "$HAVE_CODEX" = yes ] && echo "$M_DONE_CX"
elif [ "$HAVE_CODEX" = yes ]; then
  echo "$M_DONE_X"
else
  echo "$M_DONE_N"
fi
echo "$M_UI  $DIR/bin/casebook-ui   $M_UIT"
echo
echo "$M_BF  $DIR/bin/casebook-backfill"
if [ "$MODE" = local ]; then
  echo "$M_BF_L"
else
  echo "$M_BF_S"
fi
echo "$M_BF_2"
echo
echo "$M_GIT sh $DIR/tools/hooks/install_git_hook.sh"
echo
echo "$M_UNI  $DIR/bin/casebook-uninstall"
echo
echo "$M_UPD  $DIR/bin/casebook-update"
if [ "$MODE" = local ]; then
  echo "$M_UPD_1"
  echo "$M_UPD_2"
fi

# 로그인을 못 끝냈으면 그 말이 **마지막 줄**이어야 한다 (확장 104호). 위쪽 안내 사이에 끼우면
# 아래 여섯 줄에 묻힌다 — 사람은 마지막에 읽은 것을 한다. 로그인 없이는 기록이 한 줄도 안 쌓인다.
if [ "$LOGIN_TODO" != no ]; then
  echo
  if [ "$LOGIN_TODO" = fail ]; then say "$M_LOGINFAIL"; else say "$M_LOGINLATER"; fi
  say "$DIR/bin/casebook-login"
fi
"""


def install_script(base_url: str, root: pathlib.Path | None = None) -> str:
    """base_url = https://<host>/mcp/<token> — 요청이 온 그 URL 을 그대로 박는다."""
    return INSTALL_SH.format(url=base_url.rstrip("/"),
                         version_thin=version(root, "thin"),
                         version_local=version(root, "local"))


TRACKER = "web/worktrail/index.html"


def tracker_html(root: pathlib.Path | None = None) -> bytes:
    """로컬 창(casebook-ui)이 받아 갈 화면. 서버가 진실이라 UI 변경이 재설치 없이 따라온다."""
    p = (root or source_root()) / TRACKER
    if not p.is_file():
        raise FileNotFoundError(f"tracker page missing: {p}")
    return p.read_bytes()
