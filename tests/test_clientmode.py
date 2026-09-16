"""모드는 짐작하지 않는다 — 온보딩·인증 인계서 3절.

지키는 계약 둘. (1) 기록을 어디에 두는지는 사람이 고른 것(~/.casebook/mode)이고 주소 파일 유무로 재지 않는다.
(2) server 모드에서 붙을 것이 없으면 멈춘다 — 빈 로컬 DB 로 갈아타면 서버에 쌓이는 줄 알고 이 맥에 쌓인다.
옛 설치본에는 mode 파일이 없으므로 그때만 remote-url 로 본다(갱신하면 파일이 생긴다).
"""
from __future__ import annotations

import pathlib
import re

import pytest

from casebook.core import clientmode
from casebook.core.clientmode import LOCAL, SERVER, ModeError

URL = "https://x.test/mcp/tok"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CASEBOOK_MODE", raising=False)
    monkeypatch.delenv("CASEBOOK_REMOTE_URL", raising=False)
    d = tmp_path / ".casebook"
    d.mkdir()
    return d


def test_고른_모드를_적어_두면_그대로_읽는다(home):
    (home / "mode").write_text("local\n")
    assert clientmode.mode() == LOCAL
    (home / "mode").write_text("server\n")
    (home / "remote-url").write_text(URL + "\n")
    assert clientmode.mode() == SERVER


def test_로컬이라고_적혀_있으면_주소가_있어도_로컬이다(home):
    """주소 파일이 남아 있다고 서버로 끌려가지 않는다 — 고른 것이 이긴다."""
    (home / "mode").write_text("local\n")
    (home / "remote-url").write_text(URL + "\n")
    assert clientmode.resolve() == (LOCAL, None)


def test_서버인데_주소가_없으면_로컬로_내려가지_않고_멈춘다(home):
    """이 스레드가 막으려는 실패다: 서버에 쌓이는 줄 알고 빈 로컬 DB 에 쌓이는 것."""
    (home / "mode").write_text("server\n")
    with pytest.raises(ModeError) as exc:
        clientmode.resolve()
    assert "서버 주소가 없다" in str(exc.value)


def test_옛_설치본은_주소_유무로_본다(home):
    """mode 파일이 없는 설치본 — 갱신 전까지는 종전대로 동작해야 한다."""
    assert clientmode.mode() == LOCAL
    (home / "remote-url").write_text(URL + "\n")
    assert clientmode.mode() == SERVER
    assert clientmode.resolve() == (SERVER, URL)


def test_환경변수가_파일보다_먼저다(home, monkeypatch):
    (home / "mode").write_text("server\n")
    monkeypatch.setenv("CASEBOOK_MODE", "local")
    assert clientmode.mode() == LOCAL


def test_모르는_값은_로컬로_보지_않고_거절한다(home):
    """오타를 로컬로 읽으면 서버 사용자가 조용히 로컬이 된다 — 그래서 멈춘다."""
    (home / "mode").write_text("serverr\n")
    with pytest.raises(ModeError):
        clientmode.mode()


def test_프록시가_서버_모드에서_주소가_없으면_죽는다(home, capsys):
    """호스트에는 '서버 없음' 으로 보인다 — 로컬 문을 열어 주면 안 된다."""
    from casebook.adapters import mcp_proxy
    (home / "mode").write_text("server\n")
    with pytest.raises(SystemExit) as exc:
        mcp_proxy.main()
    assert exc.value.code == 1
    assert "서버 주소가 없다" in capsys.readouterr().err


def test_설치_스크립트가_고른_모드를_적는다():
    from casebook.adapters import client_dist
    s = client_dist.INSTALL_SH
    assert 'printf \'%s\\n\' "$MODE" > "$HOME/.casebook/mode"' in s
    assert 'chmod 600 "$HOME/.casebook/mode"' in s


def test_갱신기는_주소가_아니라_모드를_읽는다():
    from casebook.adapters import client_dist
    s = client_dist.LAUNCHERS["bin/casebook-update"]
    assert '[ -r "$HOME/.casebook/mode" ]' in s
    assert s.index('.casebook/mode') < s.index('.casebook/remote-url')   # 모드가 먼저, 주소는 옛 설치본용


# ── 훅과 문이 같은 곳을 봐야 한다 ─────────────────────────────────────────────

def test_훅과_문이_같은_DB_와_계정을_본다(home, monkeypatch):
    """전에는 훅이 자기 설치 폴더의 casebook.db 와 빈 이메일로 떨어져, 로컬 모드에서
    훅 다섯이 'no user' 로 조용히 죽었다 — 커밋이 스레드에 안 붙는 것이 그 증상이었다."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_hook", "tools/hooks/casebook_hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    assert hook._config() == (clientmode.local_db(), clientmode.local_email())
    assert clientmode.local_email() == clientmode.DEFAULT_EMAIL
    assert clientmode.local_db().endswith("/.casebook/casebook.db")


def test_환경변수가_기본값을_이긴다(home, monkeypatch):
    monkeypatch.setenv("CASEBOOK_DB", "/tmp/x.db")
    monkeypatch.setenv("CASEBOOK_MCP_EMAIL", "Me@Example.Test")
    assert clientmode.local_db() == "/tmp/x.db"
    assert clientmode.local_email() == "me@example.test"


# ── 판은 모드마다 따로 센다 ──────────────────────────────────────────────────

def test_로컬_전용_파일을_고치면_로컬_판이_바뀐다(tmp_path):
    """전에는 판을 CLIENT_FILES 15개로만 세서, 기록 엔진·화면을 고쳐도 로컬 모드 설치본에
    '이미 최신이다' 가 나왔다 — 얇은 파일이 우연히 같이 바뀔 때만 편승해 내려갔다."""
    import shutil
    from casebook.adapters import client_dist
    root = tmp_path / "src"
    for item in ("casebook", "tools", "web", "main.py"):
        src = pathlib.Path(item)
        (shutil.copytree if src.is_dir() else shutil.copy2)(src, root / item)

    thin0, local0 = client_dist.version(root, "thin"), client_dist.version(root, "local")
    assert thin0 != local0                                  # 세는 파일이 다르다

    p = root / "casebook/core/worktrail.py"                 # 로컬 묶음에만 가는 파일
    p.write_text(p.read_text() + "\n# BUMP\n")
    assert client_dist.version(root, "local") != local0      # 로컬 판은 바뀌고
    assert client_dist.version(root, "thin") == thin0        # 서버 판은 그대로 (안 보내는 파일이다)

    q = root / "casebook/core/threads.py"                    # 둘 다에 가는 파일
    q.write_text(q.read_text() + "\n# BUMP\n")
    assert client_dist.version(root, "thin") != thin0


def test_설치_스크립트와_갱신기가_모드별_판을_쓴다():
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    assert "VERSION_THIN='" in s and "VERSION_LOCAL='" in s
    assert 'VERSION=$([ "$MODE" = local ] && echo "$VERSION_LOCAL" || echo "$VERSION_THIN")' in s
    up = client_dist.LAUNCHERS["bin/casebook-update"]
    assert "KEY=VERSION_LOCAL" in up and "KEY=VERSION_THIN" in up
    assert up.index(".casebook/mode") < up.index("KEY=VERSION_LOCAL")   # 모드를 먼저 정한다


def test_서버_venv_를_로컬_설치에_재사용하지_않는다():
    """서버 묶음 venv 에는 fastapi 가 없다 — 재사용하면 설치는 성공했다고 말하고 창이 죽는다."""
    from casebook.adapters import client_dist
    s = client_dist.INSTALL_SH
    assert "import mcp, fastapi, uvicorn" in s
    assert 'if [ -x "$DIR/.venv/bin/python" ] && "$DIR/.venv/bin/python" -c "$NEED"' in s


# ── 확장 79호 — 화면 언어 (D15186) ────────────────────────────────────────────

def test_창이_설치때_고른_언어를_화면에_넘긴다(home):
    """화면 언어의 기본값이다. 브라우저에 저장된 선택이 이것보다 우선한다."""
    from casebook.adapters import ui_server
    (home / "lang").write_text("en\n")
    assert clientmode.lang() == "en"
    out = ui_server.inject(b"<head></head>" + ui_server.CONFIG_TAG.encode(),
                           "/api", "K", clientmode.lang()).decode()
    assert 'LANG:"en"' in out


def test_모르는_언어는_빈_값이다(home):
    """빈 값이면 화면이 브라우저 언어로 간다 — 엉뚱한 값으로 화면을 고정하지 않는다."""
    (home / "lang").write_text("fr\n")
    assert clientmode.lang() == ""
    assert clientmode.lang() == "" or True
    (home / "lang").write_text("KO\n")
    assert clientmode.lang() == "ko"          # 대문자도 받는다


def test_화면이_두_언어를_갖췄다():
    """사전은 한국어 원문을 열쇠로 쓴다 — 빠뜨린 자리는 한국어로 나오지 화면이 비지 않는다."""
    html = pathlib.Path("web/worktrail/index.html").read_text()
    assert "const EN = {" in html and "const tr = (s) =>" in html
    # 브라우저가 기억한다. 열쇠는 LANG_KEY 하나로 모았다(확장 106호) — 데모는 판마다 따로 기억한다.
    assert "localStorage.getItem(LANG_KEY)" in html
    assert "const LANG_KEY = CFG.SNAPSHOT" in html
    assert 'LANGS.includes(CFG.LANG)' in html                 # 설치 때 고른 값이 기본
    assert 'navigator.language' in html                       # 그것도 없으면 브라우저 언어
    assert 'id="langToggle"' in html and "setLang(" in html   # 눌러서 바꾼다


def test_번역_함수가_스레드_변수와_안_부딪친다():
    """화면 코드가 t 를 스레드 변수로 쓴다 — 번역 함수를 t 로 두면 그 안에서 가려져
    't is not a function' 이 난다(실측). 이름을 tr 로 둔다."""
    html = pathlib.Path("web/worktrail/index.html").read_text()
    assert "const t = (s) => LANG" not in html
    assert "const tr = (s) => LANG" in html


def test_수분류사는_사전이_아니라_함수다():
    """영어는 어순이 바뀐다 — '3개 더 보기' → 'Show 3 more'. 사전 치환으로는 안 된다."""
    html = pathlib.Path("web/worktrail/index.html").read_text()
    assert 'const more = (n) => LANG === "en" ? `Show ${n} more`' in html
    assert "const fewer = (n) =>" in html


def test_영어에서만_칸을_넓힌다():
    """실측(11px mono): 유효 24.2 · 대체됨 36.3 대 in force 46.8 · superseded 74.1.
    44px 고정 칸을 superseded 가 30px 침범했다. .lrow 는 행마다 독립 그리드라 auto 로 바꾸면
    세로 정렬이 깨지므로, 언어를 아는 화면이 그 칸만 넓힌다 — 한국어는 한 픽셀도 안 바뀐다."""
    html = pathlib.Path("web/worktrail/index.html").read_text()
    # 칸 순서는 id · dt · tt · stt · who · c — 넓혀야 하는 것은 4번째(.stt)다.
    # 처음에는 5번째(.who)를 넓혔고, 이 테스트가 그 틀린 값을 그대로 고정해 잡지 못했다.
    assert 'html[lang="en"] .lrow{grid-template-columns:56px 40px minmax(0,1fr) 78px 44px 12px;}' in html
    assert '.lrow{display:grid;grid-template-columns:56px 40px minmax(0,1fr) 44px 44px 12px;' in html
    assert 'html[lang="en"] .lrow{grid-template-columns:56px minmax(0,1fr) 78px 12px;}' in html   # 좁은 폭도


def test_넓힌_칸이_실제로_상태_칸이다():
    """행 마크업의 칸 순서와 CSS 트랙 순서가 맞는지 본다. 처음에 어긋나 있었다 —
    .stt 를 넓히려고 했는데 실제로는 그 옆 .who 가 넓어졌고, 문자열만 보던 테스트는 통과했다."""
    html = pathlib.Path("web/worktrail/index.html").read_text()
    row = [ln for ln in html.splitlines() if 'class="lrow ${sup' in ln][0]
    order = re.findall(r'<span class="(id|dt|tt|stt|who|c)\b', row)
    assert order == ["id", "dt", "tt", "stt", "who", "c"], order
    en = re.search(r'html\[lang="en"\] \.lrow\{grid-template-columns:([^;]+);\}', html).group(1).split()
    assert en[order.index("stt")] == "78px"      # 상태 칸이 넓어진다
    assert en[order.index("who")] == "44px"      # 그 옆은 그대로


def test_번역_함수를_옛_이름으로_부르는_곳이_없다():
    """79호가 t→tr 로 바꾸며 두 자리를 빠뜨려 스레드 화면이 통째로 죽었다(ReferenceError).
    render 의 catch 가 오류를 삼켜 화면에 카드 한 장만 남았다 — 테스트가 없으면 또 난다."""
    html = pathlib.Path("web/worktrail/index.html").read_text()
    js = html[html.index("<script>"):]
    assert "${t(" not in js and "$" + "{t(k)}" not in js
    # tr 로 부르는 자리는 있어야 한다(사전이 연결돼 있다는 뜻)
    assert js.count("tr(") > 30


def test_토글이_목록_칸의_캐시를_무른다():
    """무르지 않으면 왼쪽 목록이 옛 언어로 남아 반쪽 화면이 된다."""
    html = pathlib.Path("web/worktrail/index.html").read_text()
    body = html[html.index("function setLang"):html.index("function setLang") + 500]
    assert "listDirty = true" in body
