"""클라이언트 모드 — 기록을 어디에 두는지 한 곳에서 정한다 (온보딩·인증 인계서 3절).

    mode()      "local" | "server"        사람이 고른 것
    resolve()   (mode, url)               server 인데 붙을 것이 없으면 ModeError

왜 따로 적어 두나: 전에는 `~/.casebook/remote-url` 이 있느냐로 모드를 짐작했다. 그러면 서버를 쓰던 사람의
주소 파일이 사라졌을 때(지워졌든, 덮어썼든, 홈이 바뀌었든) 조용히 로컬 모드가 되어 빈 DB 에 기록이 쌓이고
본인은 서버에 쌓이는 줄 안다 — 기록 제품에서 가장 나쁜 실패다. 모드는 설치할 때 사람이 고른 것이므로
고른 대로 `~/.casebook/mode` 에 적고, server 모드에서 접속에 필요한 것이 없으면 폴백하지 않고 멈춘다.

옛 설치본에는 mode 파일이 없다. 그때만 remote-url 이 있으면 server 로 본다 — 설치나 갱신을 다시 하면
파일이 생기고, 그 뒤로는 짐작하지 않는다.
"""
from __future__ import annotations

import os
import pathlib

LOCAL = "local"
SERVER = "server"
MODES = (LOCAL, SERVER)

MODE_FILE = "~/.casebook/mode"
REMOTE_URL_FILE = "~/.casebook/remote-url"

# 로컬 모드에서 기록이 있는 곳. 문(mcp_server)과 훅이 **같은 값**을 봐야 한다 — 전에는 훅이
# 자기 설치 폴더 밑의 casebook.db 와 빈 이메일로 떨어져, 로컬 모드에서 훅 다섯이 조용히 죽었다.
DEFAULT_DB = "~/.casebook/casebook.db"
DEFAULT_EMAIL = "mcp@local"

# 화면 언어 (확장 79호). 설치할 때 고른 값이고, 창이 화면에 넘겨 기본값으로 쓴다.
# 브라우저에 저장된 선택이 이것보다 우선한다 — 화면은 보는 사람이 고르는 것이기 때문이다.
LANG_FILE = "~/.casebook/lang"


class ModeError(RuntimeError):
    """서버 모드인데 붙을 수 없다. 로컬로 내려가지 않고 이것을 올린다."""


def _read_file(path: str) -> str:
    p = pathlib.Path(path).expanduser()
    try:
        return p.read_text().strip() if p.is_file() else ""
    except OSError:      # 권한·깨진 링크 — 없는 것과 같이 다룬다
        return ""


def remote_url() -> str | None:
    """서버 주소. 환경변수 → ~/.casebook/remote-url(600) 순. 지금은 URL 자체가 비밀이다(경로 토큰)."""
    return (os.environ.get("CASEBOOK_REMOTE_URL") or "").strip() or _read_file(REMOTE_URL_FILE) or None


def mode() -> str:
    """환경변수 → mode 파일 → (옛 설치본) remote-url 유무. 모르는 값은 local 로 보지 않고 ModeError."""
    raw = ((os.environ.get("CASEBOOK_MODE") or "").strip() or _read_file(MODE_FILE)).lower()
    if raw:
        if raw not in MODES:
            raise ModeError(say(
                f"모드가 '{raw}' 로 적혀 있다 — local 또는 server 여야 한다 ({MODE_FILE})",
                f"mode is written as '{raw}' — it has to be local or server ({MODE_FILE})"))
        return raw
    return SERVER if remote_url() else LOCAL      # 옛 설치본 — 갱신하면 파일이 생긴다


def local_db() -> str:
    """로컬 모드의 sqlite 경로. CASEBOOK_DB 가 이긴다."""
    return str(pathlib.Path((os.environ.get("CASEBOOK_DB") or "").strip() or DEFAULT_DB).expanduser())


def local_email() -> str:
    """로컬 모드의 기록 주인. CASEBOOK_MCP_EMAIL 이 이긴다."""
    return ((os.environ.get("CASEBOOK_MCP_EMAIL") or "").strip() or DEFAULT_EMAIL).lower()


def resolve() -> tuple[str, str | None]:
    """(모드, 서버 주소). server 인데 주소가 없으면 멈춘다 — 빈 로컬 DB 로 갈아타지 않는다."""
    m = mode()
    if m == LOCAL:
        return LOCAL, None
    url = remote_url()
    if not url:
        raise ModeError(say(
            "서버 모드인데 서버 주소가 없다 — 기록이 어디로도 가지 않는다.\n"
            f"  주소를 잃었으면 받은 설치 한 줄을 다시 실행한다. 이 맥에만 두려면 {MODE_FILE} 를 local 로 바꾼다.\n"
            "  (로컬 DB 로 자동 전환하지 않는다 — 서버에 쌓이는 줄 알고 빈 DB 에 쌓이는 일을 막는다)",
            "server mode, but there is no server address — nothing would be recorded anywhere.\n"
            f"  If you lost the address, run the install line you were given again. To keep records on this\n"
            f"  machine only, change {MODE_FILE} to local.\n"
            "  (It does not fall back to a local DB — that would pile records into an empty file\n"
            "   while you believe they are going to the server.)"))
    return SERVER, url


def lang() -> str:
    """설치할 때 고른 화면 언어. 못 읽으면 빈 문자열 — 화면이 브라우저 언어로 간다."""
    v = ((os.environ.get("CASEBOOK_LANG") or "").strip() or _read_file(LANG_FILE)).lower()
    return v if v in ("ko", "en") else ""


_SYSTEM_LANG: list[str] = []


def system_lang() -> str:
    """시스템 언어 — "ko" 또는 "en" (#541, D16531). 로케일 환경변수가 먼저고, 없으면 macOS 의 시스템 언어 설정을 읽는다.

    macOS 는 LANG 이 비어 있는 일이 흔하다(이 제품을 만든 맥이 그렇다: LANG="", AppleLanguages ("ko-KR")).
    그래서 환경변수만 보면 한국어 맥도 영어로 떨어진다. C·POSIX 는 고른 언어가 아니므로 신호로 보지 않는다.
    한 프로세스 안에서는 한 번만 읽는다."""
    if _SYSTEM_LANG:
        return _SYSTEM_LANG[0]
    found = ""
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = (os.environ.get(key) or "").strip().lower()
        if v and v not in ("c", "posix") and not v.startswith(("c.", "posix.")):
            found = v
            break
    if not found:
        import shutil
        import subprocess
        defaults = shutil.which("defaults")
        if defaults:
            try:
                out = subprocess.run([defaults, "read", "-g", "AppleLanguages"],
                                     capture_output=True, text=True, timeout=2).stdout
                found = out.replace("(", " ").replace(")", " ").replace('"', " ").replace(",", " ").split()[0].lower()
            except (OSError, subprocess.SubprocessError, IndexError):
                found = ""
    _SYSTEM_LANG.append("ko" if found.startswith("ko") else "en")
    return _SYSTEM_LANG[0]


def say(ko: str, en: str) -> str:
    """맥에서 도는 것이 사람에게 하는 말 (확장 85호). 설치할 때 고른 언어로 고른다.

    고른 것이 없으면 시스템 언어를 따른다(#541, D16531) — 한국어가 아니면 영어다. 예전에는 한국어로
    떨어졌는데, 플러그인은 설치 때 언어를 묻지 않아 마켓에서 온 사람 대부분이 lang 파일이 없다.
    서버가 내는 거절문은 이 길로 오지 않는다 — 서버는 부른 쪽의 ~/.casebook/lang 을 볼 수 없어 영어 한 벌이다."""
    return ko if (lang() or system_lang()) == "ko" else en
