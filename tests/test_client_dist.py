"""클라이언트 배포 (확장 34호) — 토큰 뒤의 install.sh · client.tar.gz.

(1) 묶음에 든 파일만으로 프록시와 훅이 import 된다 — 서버 코어를 끌고 오지 않는다.
(2) install.sh 에는 요청이 온 그 URL 이 박힌다 — 사용자가 주소를 손으로 적지 않는다.
(3) 토큰이 없거나 틀리면 401 — 배포도 문지기 뒤에 있다(저장소는 private).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import io
import pathlib

import pytest

from casebook.adapters import client_dist
from tests.conftest import FakeLLM, FakeSearch, _build_app

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def door():
    """토큰이 확인된 뒤의 문 — TestClient 와 그 사용자의 /mcp/<token> 접두."""
    pytest.importorskip("mcp")
    from starlette.testclient import TestClient

    from casebook.adapters.mcp_server import build_http_app, mint_token
    cb = _build_app(FakeLLM(), FakeSearch())
    with TestClient(build_http_app(cb), raise_server_exceptions=False) as c:
        c.token_path = "/mcp/" + mint_token(cb, "tester@example.test")
        yield c


def test_묶음은_클라이언트_파일과_실행기만_담는다():
    with tarfile.open(fileobj=io.BytesIO(client_dist.build_tarball(ROOT))) as tar:
        names = sorted(n[len("casebook-client/"):] for n in tar.getnames())
    assert names == sorted([*client_dist.CLIENT_FILES, *client_dist.LAUNCHERS])
    # 서버 코어는 가지 않는다 — 원격 모드에서 쓰이지 않고 보낼 이유도 없다
    assert not [n for n in names if n.endswith(("app.py", "db.py", "prompts.py", "hookctx.py"))]


def test_묶음만_풀어도_프록시와_훅이_선다(tmp_path):
    """CLIENT_FILES 가 낡으면 여기서 깨진다 — 클라이언트가 서버 코어를 몰래 import 하지 않는지의 오라클."""
    with tarfile.open(fileobj=io.BytesIO(client_dist.build_tarball(ROOT))) as tar:
        tar.extractall(tmp_path, filter="data")
    d = tmp_path / "casebook-client"
    for mod in ("casebook.adapters.mcp_proxy", "casebook.core.threads"):
        r = subprocess.run([sys.executable, "-c", f"import {mod}"], cwd=d, capture_output=True, text=True)
        assert r.returncode == 0, f"{mod}: {r.stderr}"
    # 훅 스크립트도 제 힘으로 뜬다(원격 URL 이 없으면 skipped 를 낸다 — 세션을 막지 않는다)
    r = subprocess.run([sys.executable, str(d / "tools/hooks/casebook_hook.py"), "session-start"],
                       input="{}", cwd=d, capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and r.stdout.strip().startswith("{")


def test_install_sh_에_그_URL_이_박힌다():
    s = client_dist.install_script("https://example.test/mcp/TOKEN123", ROOT)
    assert "URL='https://example.test/mcp/TOKEN123'" in s
    assert client_dist.version(ROOT) in s
    assert "remote-url" in s and "claude mcp add" in s


def test_실행기는_설치_위치를_스스로_찾는다():
    """파이썬을 부르는 실행기는 자기 위치에서 설치 경로를 찾는다 — 사람이 PATH 를 만지지 않게.

    둘만 예외다. casebook-update 는 클라이언트 묶음 자체를 통째로 바꾸고, casebook-uninstall 은
    그것을 걷어낸다 — 둘 다 묶음 안이 아니라 ~/.casebook 을 본다."""
    for name, script in client_dist.LAUNCHERS.items():
        if name == "bin/casebook-update":
            assert "$HOME/.casebook/update-url" in script
            continue
        if name == "bin/casebook-uninstall":
            assert 'H="$HOME/.casebook"' in script
            continue
        assert "$(cd \"$(dirname \"$0\")/..\" && pwd)" in script


@pytest.mark.parametrize("path", ["/mcp/badtoken/install.sh", "/mcp/badtoken/client.tar.gz"])
def test_토큰이_틀리면_배포도_401(door, path):
    assert door.get(path).status_code == 401


def test_토큰이_맞으면_install_sh_와_묶음을_준다(door):
    door_url = door.token_path
    r = door.get(f"{door_url}/install.sh")
    assert r.status_code == 200 and f"URL='http://testserver{door_url}'" in r.text

    r = door.get(f"{door_url}/client.tar.gz")
    assert r.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
        assert "casebook-client/casebook/adapters/mcp_proxy.py" in tar.getnames()


def test_프록시_뒤에서는_전달된_스킴을_쓴다(door):
    """Caddy 뒤에서 http:// 가 박히면 클라이언트가 평문으로 붙는다 — 전달 헤더가 이긴다."""
    r = door.get(f"{door.token_path}/install.sh",
                 headers={"x-forwarded-proto": "https", "x-forwarded-host": "casebook-api.example"})
    assert f"URL='https://casebook-api.example{door.token_path}'" in r.text


# ── 로컬 창 (확장 38호) ──────────────────────────────────────────────────────
def test_창은_토큰을_브라우저에_주지_않는다():
    """주입한 config 에 진짜 토큰이 없고, 요청은 이 프로세스(/api)로만 간다."""
    from casebook.adapters import ui_server

    html = b'<head><script src="../app/config.js"></script><script src="../app/config.local.js"></script></head>'
    out = ui_server.inject(html).decode()
    assert "config.js" not in out and "config.local.js" not in out
    assert 'APP_BASE:"/api/app"' in out and 'AUTH_BASE:"/api/auth"' in out
    assert "casebook-api" not in out                      # 진짜 서버 주소도 페이지에 안 나간다
    assert "via-local-proxy" in out                       # 자리표시자일 뿐


def test_창은_remote_url_에서_서버와_토큰을_가른다():
    from casebook.adapters import ui_server

    assert ui_server.split_remote("https://h.example/mcp/TOK") == ("https://h.example", "TOK")
    assert ui_server.split_remote("https://h.example/mcp/TOK/") == ("https://h.example", "TOK")
    for bad in ("https://h.example", "https://h.example/mcp/", "https://h.example/mcp/a/b"):
        with pytest.raises(ValueError):
            ui_server.split_remote(bad)


def test_화면이_바뀌면_주입이_조용히_실패하지_않는다():
    from casebook.adapters import ui_server

    with pytest.raises(ValueError):
        ui_server.inject(b"<head></head>")               # config 태그가 없으면 알린다


def test_문이_화면을_내준다(door):
    r = door.get(f"{door.token_path}/ui")
    assert r.status_code == 200 and "CASEBOOK_CONFIG" in r.text
    assert door.get("/mcp/badtoken/ui").status_code == 401


def test_저장소_안에서는_화면을_로컬에서_읽는다(tmp_path):
    """UI 를 고치는 사람은 배포 없이 새로고침만 하면 된다. 설치본에는 web/ 가 없어 영향이 없다."""
    from casebook.adapters import ui_server

    assert ui_server.local_page() is not None                  # 이 저장소에서 돌 때
    assert ui_server.local_page().name == "index.html"

    f = tmp_path / "mine.html"; f.write_text("<b>mine</b>")
    os.environ["CASEBOOK_UI_SOURCE"] = str(f)
    try:
        assert ui_server.fetch_page("http://unused.invalid") == b"<b>mine</b>"
    finally:
        del os.environ["CASEBOOK_UI_SOURCE"]


def test_설치본에는_화면_파일이_없다():
    """클라이언트 묶음에 web/ 를 넣지 않는다 — 대상자는 늘 서버 화면을 받는다."""
    assert not [f for f in client_dist.CLIENT_FILES if f.startswith("web/")]


# ── 기록 위치 고르기 (로컬/서버) ────────────────────────────────────────────
def test_로컬_묶음은_기록까지_설_수_있어야_한다():
    """로컬 모드는 이 맥에서 문과 화면이 다 서야 한다 — 코어와 화면 파일이 들어간다."""
    files = client_dist.local_files(ROOT)
    assert any(f.endswith("core/worktrail.py") for f in files)
    assert any(f.endswith("adapters/http_api.py") for f in files)
    assert "web/worktrail/index.html" in files
    assert len(files) > len(client_dist.CLIENT_FILES)
    with tarfile.open(fileobj=io.BytesIO(client_dist.build_tarball(ROOT, mode="local"))) as tar:
        names = {n[len("casebook-client/"):] for n in tar.getnames()}
    assert "web/worktrail/index.html" in names and "bin/casebook-ui" in names


def test_얇은_묶음에는_서버_코어가_없다():
    files = client_dist.CLIENT_FILES
    assert not [f for f in files if f.endswith(("worktrail.py", "http_api.py", "app.py", "db.py"))]


def test_설치는_기록_위치를_묻고_고르지_않으면_멈춘다():
    s = client_dist.install_script("https://h.test/mcp/TOK")
    assert "기록을 어디에 둘지 정한다" in s and "이 맥에만" in s
    # 서버 쪽도 장점부터 읽히게 — 위험만 적으면 고를 수가 없다
    assert "한곳에 모인다" in s and "백업이 자동" in s and "폰에서도" in s
    # "증거"라는 우리 말 대신 처음 듣는 사람이 아는 말로
    assert "에러 메시지나 명령 출력" in s and "API 키" in s and "그건 기록하지 마" in s
    assert "--local" in s and "--server" in s
    assert "고를 수 없는 곳에서 실행됐다" in s            # 터미널이 없으면 멈춘다
    # curl … | sh 는 stdin 이 파이프다 — 터미널은 /dev/tty 로 열어야 한다
    assert '{ : > /dev/tty; } 2>/dev/null' in s and "read -r pick < /dev/tty" in s
    assert "[ -t 0 ]" not in s
    assert 'rm -f "$HOME/.casebook/remote-url"' in s      # 로컬이면 서버 주소를 지운다
    assert "mode=local" in s                              # 로컬이면 전체 묶음을 받는다


def test_문은_로컬_묶음도_내준다(door):
    r = door.get(f"{door.token_path}/client.tar.gz?mode=local")
    assert r.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(r.content)) as tar:
        assert "casebook-client/casebook/core/worktrail.py" in tar.getnames()


def test_로컬_모드_안내가_서버를_말하지_않는다():
    """로컬을 고른 사람에게 "서버로 간다"고 하면 거짓말이다."""
    s = client_dist.install_script("https://h.test/mcp/TOK")
    assert 'if [ "$MODE" = local ]; then' in s
    assert "전부 이 맥 안에서 끝난다" in s
    assert "기록은 ~/.casebook/casebook.db 에만 쌓인다" in s


def test_받기_실패하면_아무것도_바꾸지_않는다():
    """curl | tar 는 curl 이 죽어도 tar 가 성공한다 — 2026-09-08 그 탓에 파일 없이 등록만 일어났다."""
    s = client_dist.install_script("https://h.test/mcp/TOK")
    assert "curl -fsSL \"$FETCH\" | tar" not in s          # 파이프로 바로 풀지 않는다
    assert 'if ! curl -fsSL "$FETCH" -o "$DIR.new/client.tgz"; then' in s
    assert "아무것도 바꾸지 않았다" in s
    # 받은 것이 온전한지 보고 나서야 다음으로 간다
    assert ("bin/casebook-proxy bin/casebook-ui bin/casebook-uninstall "
            "tools/hooks/install_claude_hooks.sh") in s
    # 실패 지점이 설정 변경보다 앞이어야 한다
    assert s.index("파일을 받지 못했다") < s.index('rm -f "$HOME/.casebook/remote-url"')
    assert s.index("파일을 받지 못했다") < s.index("claude mcp add")


def test_화면은_마크다운을_서식으로_보이되_증거_원문은_건드리지_않는다():
    """확장 48호 — 에이전트는 문장에 **굵게**·`코드` 를 섞어 쓴다(피실험자 1호 캡처, #494).

    화면은 그것을 기호가 아니라 서식으로 보여야 한다. 다만 경계가 하나 있다: 증거와
    설치 전 기록은 바이트 그대로여야 하므로 거기에는 md() 를 쓰지 않는다.
    """
    from casebook.adapters import ui_server

    page = ui_server.local_page().read_text()

    # md() 는 반드시 esc() 를 먼저 거친다 — 태그가 들어오면 안 된다
    assert "const md = (s) => esc(s)" in page

    # 사람이 읽는 문장에는 쓴다
    for spot in ('<span class="hd">${md(head)}${by}${rep}</span>',
                 '<div class="t">${md(t.title)}</div>',
                 '<div class="full">${md(text)}'):
        assert spot in page, f"화면 문장에 md 가 빠졌다: {spot}"

    # 증거 원문에는 쓰지 않는다 — 바이트 그대로 보여야 한다
    assert "${esc(e.content)}" in page, "증거 원문이 esc 가 아니다"
    assert "${md(e.content)}" not in page, "증거 원문에 서식을 입히면 안 된다"


def _serve(body: bytes):
    """install.sh 를 한 번 내주는 임시 서버. (base_url, 멈추는 함수)"""
    import http.server, threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                       # noqa: N802
            self.send_response(200); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *a):                              # noqa: A003
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_port}", srv.shutdown


def _fake_home(tmp_path, url, version, lang="ko"):
    home = tmp_path / "home"
    (home / ".casebook" / "client" / "bin").mkdir(parents=True)
    if lang:
        (home / ".casebook" / "lang").write_text(lang + "\n")   # 갱신기는 설치 때 고른 언어로 말한다
    (home / ".casebook" / "update-url").write_text(url + "\n")
    (home / ".casebook" / "client" / "VERSION").write_text(version + "\n")
    from casebook.adapters import client_dist
    up = home / ".casebook" / "client" / "bin" / "casebook-update"
    up.write_text(client_dist.LAUNCHERS["bin/casebook-update"])
    up.chmod(0o755)
    return home, up


def test_갱신_같은_판이면_아무것도_바꾸지_않는다(tmp_path):
    """로컬 모드는 화면이 얼어 있으므로 사람이 이걸 부른다. 같은 판이면 받지 않고 끝나야 한다."""
    url, stop = _serve("#!/bin/sh\nVERSION_THIN='thin-abc'\nVERSION_LOCAL='abc123'\n"
                       "echo '설치가 돌면 안 된다' >&2\nexit 9\n".encode())
    try:
        home, up = _fake_home(tmp_path, url, "abc123")
        r = subprocess.run(["sh", str(up)], capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)})
        assert r.returncode == 0, r.stderr
        assert "이미 최신" in r.stdout and "abc123" in r.stdout
        assert "설치가 돌면 안 된다" not in r.stderr        # 설치 스크립트를 실행하지 않았다
    finally:
        stop()


def test_갱신_새_판이면_모드를_지켜_설치를_돌린다(tmp_path):
    """되받은 스크립트를 그대로 돌리되, 지금 모드(로컬/서버)를 인자로 넘겨 되묻지 않는다."""
    url, stop = _serve("#!/bin/sh\nVERSION_LOCAL='new999'\nVERSION_THIN='srv777'\n"
                       "echo \"MODE=$1\"\n".encode())
    try:
        home, up = _fake_home(tmp_path, url, "old111")
        r = subprocess.run(["sh", str(up)], capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)})
        assert r.returncode == 0, r.stderr
        assert "새 판 new999" in r.stdout and "old111" in r.stdout
        assert "MODE=--local" in r.stdout                       # remote-url 이 없으면 로컬 모드

        (home / ".casebook" / "remote-url").write_text(url + "\n")
        r = subprocess.run(["sh", str(up)], capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)})
        assert "MODE=--server" in r.stdout
        assert "새 판 srv777" in r.stdout          # 서버 모드는 서버 판을 본다 — 판은 모드마다 다르다
    finally:
        stop()


def test_갱신_주소가_없거나_서버가_이상하면_멈춘다(tmp_path):
    """설치를 반쯤 돌려 설정만 망가뜨리는 길을 막는다 (#483 에서 실제로 겪은 것)."""
    from casebook.adapters import client_dist

    home = tmp_path / "h2"; (home / ".casebook" / "client" / "bin").mkdir(parents=True)
    (home / ".casebook" / "lang").write_text("ko\n")        # 갱신기는 설치 언어로 말한다
    up = home / ".casebook" / "client" / "bin" / "casebook-update"
    up.write_text(client_dist.LAUNCHERS["bin/casebook-update"]); up.chmod(0o755)
    r = subprocess.run(["sh", str(up)], capture_output=True, text=True,
                       env={**os.environ, "HOME": str(home)})
    assert r.returncode != 0 and "갱신 주소가 없다" in r.stderr

    url, stop = _serve(b"<html>404</html>")                     # 설치 스크립트가 아닌 것
    try:
        (home / ".casebook" / "update-url").write_text(url + "\n")
        r = subprocess.run(["sh", str(up)], capture_output=True, text=True,
                           env={**os.environ, "HOME": str(home)})
        assert r.returncode != 0 and "설치 스크립트가 아니다" in r.stderr
    finally:
        stop()


def test_설치_묶음에_갱신_실행기와_판이_들어간다():
    from casebook.adapters import client_dist

    for mode in ("thin", "local"):
        with tarfile.open(fileobj=io.BytesIO(client_dist.build_tarball(mode=mode))) as tar:
            assert "casebook-client/bin/casebook-update" in tar.getnames(), mode

    sh = client_dist.install_script("https://example.invalid/mcp/TOK")
    assert '"$DIR.new/VERSION"' in sh                            # 받은 판을 남긴다
    assert '"$HOME/.casebook/update-url"' in sh                  # 갱신 주소는 두 모드 다
    assert "bin/casebook-update" in sh                           # 마지막 안내에 나온다


# ── 확장 78호 — 설치가 언어를 먼저 묻는다 (D15188) ────────────────────────────

def test_설치_스크립트가_언어를_모드보다_먼저_정한다():
    """영어권 테스터의 첫 관문이다. 데이터 동의 고지가 이 안에 있어, 여기서 한국어를 만나면 끝난다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    assert s.index('SYS=en') < s.index('MODE=""')                  # 언어가 모드보다 먼저
    assert '[ "$a" = "--ko" ]' in s and '[ "$a" = "--en" ]' in s    # 비대화식으로도 고를 수 있다
    assert 'SIG="${LC_ALL:-$LANG}"' in s                              # 기본값은 시스템 언어(LC_ALL 우선)
    assert "ASK_KO" in s and "ASK_EN" in s                          # 고지가 두 벌이다
    assert 'printf \'%s\\n\' "$L" > "$HOME/.casebook/lang"' in s    # 고른 것을 적어 둔다


def test_터미널이_열리는지로_잰다():
    """-r/-w 는 파일이 있기만 해도 참이다. CI·도커의 /dev/tty 는 있지만 'Device not configured'
    로 열리지 않아, 그 상태에서 set -e 가 설치를 통째로 죽였다(실측)."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    assert '{ : > /dev/tty; } 2>/dev/null && TTY=yes' in s
    assert '[ -r /dev/tty ] && [ -w /dev/tty ]' not in s            # 옛 판정은 남아 있지 않다


def test_python3_가_없어도_안내가_나온다():
    """버전 문구를 변수 할당에서 $(python3 -V) 로 채우면, python3 가 없을 때 set -e 가
    그 줄에서 죽어 'python3 가 없다' 를 영영 못 본다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    assert 'M_OLDPY="python3.11 이상이 필요하다 (지금: %s)."' in s
    assert '$(python3 -V 2>&1)' not in s.split("command -v python3")[0]   # 할당부에는 없다


def test_두_언어가_같은_자리를_덮는다():
    """한쪽에만 있는 문구가 생기면 그 자리에서 언어가 갈린다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    ko = {ln.split("=", 1)[0].strip() for ln in s.splitlines()
          if ln.strip().startswith("M_") and "=" in ln}
    # M_* 는 두 분기에 같은 이름으로 있어야 한다 — 집합이 하나면 이름이 어긋나지 않았다는 뜻
    body = s.split('if [ "${L:-en}" = ko ]; then', 1)[1].split("\nfi\n", 1)[0]
    ko_side, en_side = body.split("\nelse\n", 1)
    names = lambda t: {ln.split("=", 1)[0].strip() for ln in t.splitlines()
                       if ln.strip().startswith("M_") and "=" in ln}
    assert names(ko_side) == names(en_side), names(ko_side) ^ names(en_side)
    assert len(names(ko_side)) >= 25
    assert ko                                                        # 이름을 실제로 찾았다


def test_갱신기가_설치_언어로_말한다(tmp_path):
    """영어 설치의 마지막 안내가 이 명령을 가리킨다 — 여기만 한국어면 영어 테스터가
    설치를 영어로 마치고 갱신에서 한국어를 만난다(검증이 잡은 결함)."""
    url, stop = _serve("#!/bin/sh\nVERSION_LOCAL='abc123'\nVERSION_THIN='t'\n".encode())
    try:
        for lang, want in (("ko", "이미 최신이다"), ("en", "Already up to date")):
            home, up = _fake_home(tmp_path / lang, url, "abc123", lang)
            r = subprocess.run(["sh", str(up)], capture_output=True, text=True,
                               env={**os.environ, "HOME": str(home)})
            assert r.returncode == 0 and want in r.stdout, (lang, r.stdout, r.stderr)
    finally:
        stop()


def test_갱신기가_언어를_재설치에_넘긴다():
    """안 넘기면 재설치가 언어를 다시 묻거나 로케일로 되돌린다 — 고른 것이 사라진다."""
    from casebook.adapters import client_dist
    u = client_dist.LAUNCHERS["bin/casebook-update"]
    assert 'sh "$TMP" "$M" "--$L"' in u
    assert 'cat "$HOME/.casebook/lang"' in u          # 적어 둔 것을 읽는다


def test_로케일_신호가_없으면_언어를_굳히지_않는다():
    """이 맥이 그렇다 — $LANG·$LC_ALL 이 둘 다 비어 있다. 영어로 단정하면 한국어 사용자가
    Enter 만 쳐도 en 이 파일에 박히고 화면이 영어로 열린다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    assert 'SIG="${LC_ALL:-$LANG}"' in s                    # POSIX 우선순위
    assert 'if [ -z "$SIG" ]; then SYS=""' in s             # 신호 없음은 빈 값
    assert 'if [ -n "$L" ]; then' in s                      # 그때는 파일을 쓰지 않는다


# ── 확장 94호 — 런처가 부르는 모듈이 묶음에 실제로 들어 있나 ────────────────
def _launcher_modules():
    """bin/* 런처가 python -m 으로 부르는 모듈. casebook-update 는 셸 스크립트라 빼둔다."""
    import re
    from casebook.adapters import client_dist as cd
    out = {}
    for name, script in cd.LAUNCHERS.items():
        m = re.search(r'"\$DIR" (casebook\.[\w.]+)', script)   # 런처는 모듈을 마지막 인자로 준다
        if m:
            out[name] = m.group(1)
    return out


def test_런처가_부르는_모듈이_묶음에_있다():
    """확장 89호가 bin/casebook-login 을 더하면서 device_login.py 를 CLIENT_FILES 에 넣지 않았다.
    설치는 성공하고 런처도 생기는데 부르면 ModuleNotFoundError 다 — 실제로 그렇게 깔렸다(2026-09-14)."""
    from casebook.adapters import client_dist as cd
    mods = _launcher_modules()
    assert mods, "런처에서 모듈을 하나도 못 읽었다 — 이 시험이 헛돈다"
    for mode, files in (("server", set(cd.CLIENT_FILES)),
                        ("local", set(cd.local_files(cd.source_root())))):
        for launcher, mod in mods.items():
            path = mod.replace(".", "/") + ".py"
            assert path in files, f"{mode} 묶음에 {path} 가 없다 ({launcher} 가 부른다)"


def test_묶음만_풀어도_런처의_모듈이_임포트된다(tmp_path):
    """파일 목록에 있다고 끝이 아니다 — 그 모듈이 끌고 가는 것까지 묶음 안에 있어야 한다.
    묶음을 실제로 풀고 sys.path 를 거기로만 두고 임포트해 본다."""
    import subprocess, sys, tarfile, io as _io
    from casebook.adapters import client_dist as cd
    mods = _launcher_modules()
    for mode in ("thin", "local"):
        out = tmp_path / mode
        out.mkdir()
        with tarfile.open(fileobj=_io.BytesIO(cd.build_tarball(mode=mode)), mode="r:gz") as tar:
            tar.extractall(out)
        rootdir = out / "casebook-client"
        for launcher, mod in mods.items():
            r = subprocess.run([sys.executable, "-c", f"import {mod}"],
                               cwd=str(rootdir), capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(rootdir),
                                    "HOME": str(tmp_path)})
            assert r.returncode == 0, f"[{mode}] {launcher} → {mod}\n{r.stderr[-600:]}"


def test_런처가_인자를_모듈까지_넘긴다(tmp_path):
    """없으면 casebook-backfill --dry-run 의 --dry-run 이 사라져 진짜로 올린다(2026-09-14 실측).
    런처를 실제로 만들어 돌리고, 모듈이 받은 argv 를 그대로 본다."""
    import os, stat, subprocess, sys
    from casebook.adapters import client_dist as cd
    root = tmp_path / "client"
    (root / "bin").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    os.symlink(sys.executable, root / ".venv" / "bin" / "python")
    (root / "probe.py").write_text("import sys; print('ARGV', sys.argv[1:])\n", encoding="utf-8")
    sh = root / "bin" / "run"
    sh.write_text(cd._LAUNCHER.format(what="probe", module="probe"), encoding="utf-8")
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
    r = subprocess.run([str(sh), "--dry-run", "--only", "a b", "--status"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]
    assert r.stdout.strip() == "ARGV ['--dry-run', '--only', 'a b', '--status']", r.stdout


# ── 확장 104호 — 설치가 로그인까지 이어진다 ─────────────────────────────────
def test_서버_모드_설치는_로그인까지_이어진다():
    """운영 서버는 구형 경로를 닫아 두었으므로(확장 92호) 로그인 없이는 기록이 한 줄도 안 쌓인다.
    그런데 설치는 '됐다'로 끝나고 로그인을 한 마디도 하지 않았다 — 받은 사람은 claude 를 열어
    401 만 보고 무엇을 해야 하는지 모른 채 멈춘다. 설치와 분리해 둘 이유가 없다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    step = s.split("# 6) 로그인", 1)
    assert len(step) == 2, "설치 스크립트에 로그인 단계가 없다"
    step = step[1].split("\necho\nif [ \"$HAVE_CLAUDE\"", 1)[0]

    assert 'if [ "$MODE" = server ]' in step, "로컬 모드에서도 로그인하려 든다 — 서버에 닿지 않는 모드다"
    assert '"$DIR/bin/casebook-login" --status' in step, "이미 로그인한 사람에게 또 시키면 안 된다"
    assert '"$TTY" = yes' in step, "터미널이 없는 곳(CI·도커)에서 기기 흐름을 시작하면 멈춘 채로 끝난다"
    # 로그인이 실패해도 설치는 실패가 아니다 — 깔린 것은 깔린 것이다 (set -e 아래서 || 로 받는다)
    assert '"$DIR/bin/casebook-login" || LOGIN_TODO=fail' in step, "로그인 실패가 설치를 통째로 죽인다"

    # 순서: 로그인은 클라이언트가 다 깔리고 서버 주소가 적힌 뒤여야 한다
    assert s.index("$HOME/.casebook/remote-url") < s.index("# 6) 로그인")
    # 그리고 "됐다" 안내보다 앞이어야 한다 — 끝났다고 말한 뒤에 로그인시키지 않는다
    assert s.index("# 6) 로그인") < s.index("$M_DONE_C")

    # 못 끝냈을 때의 말은 **마지막 줄**이다 — 안내 여섯 줄 사이에 끼우면 묻힌다
    tail = s[s.index("$M_UPD  $DIR/bin/casebook-update"):]
    assert "$M_LOGINFAIL" in tail and "$M_LOGINLATER" in tail, "못 했을 때의 말이 마지막에 없다"
    assert tail.rstrip().endswith('say "$DIR/bin/casebook-login"\nfi'), tail[-200:]


def test_로그인_안내가_두_언어에_다_있다():
    """영어 테스터가 설치를 영어로 마치고 마지막 관문에서 한국어를 만나면 거기서 끝난다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    body = s.split('if [ "${L:-en}" = ko ]; then', 1)[1].split("\nfi\n", 1)[0]
    ko_side, en_side = body.split("\nelse\n", 1)
    for name in ("M_LOGIN", "M_LOGGEDIN", "M_LOGINFAIL", "M_LOGINLATER"):
        assert f"{name}=" in ko_side, f"한국어에 {name} 가 없다"
        assert f"{name}=" in en_side, f"영어에 {name} 가 없다"


# ── 확장 112호 — 서버를 권하고, 조건을 적어 보낸다 (D15636·D15637·D15638) ──
def test_안내문이_보관_삭제_열람_종료를_말한다():
    """서버를 권하려면 먼저 적어야 한다(D15637). 사용자 본인이 cmem 을 "딱히 아무 생각 없이" 썼다 —
    그것이 기본값의 힘이고 동시에 이 넷이 필요한 이유다. 특히 열람은 빼면 안 된다: 운영자가 DB 에
    닿을 수 있다는 것은 구조가 그렇다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    ko = s.split("ASK_KO", 1)[1].split("ASK_KO", 1)[0]
    en = s.split("ASK_EN", 1)[1].split("ASK_EN", 1)[0]
    for word in ("보관", "삭제", "열람", "종료", "7일", "30일"):
        assert word in ko, f"한국어 안내문에 {word} 가 없다"
    for word in ("Kept", "Deleted", "Read", "Ending", "7 days", "30 days"):
        assert word in en, f"영어 안내문에 {word} 가 없다"
    # 운영자가 볼 수 있다는 것은 두 언어 다 남아 있어야 한다
    assert "운영자는 기술적으로 DB 를 열 수 있다" in ko
    assert "operator can technically" in en


def test_서버가_권장이고_번호도_그쪽이다():
    """D15636 — 묻기는 그대로 두고 권장만 옮긴다. 번호를 안 바꾸면 1 을 눌러도 로컬이 된다."""
    from casebook.adapters import client_dist
    s = client_dist.install_script("https://x.test/mcp/tok")
    ko = s.split("ASK_KO", 1)[1].split("ASK_KO", 1)[0]
    en = s.split("ASK_EN", 1)[1].split("ASK_EN", 1)[0]
    assert ko.index("서버에") < ko.index("이 맥에만"), "서버가 위에 있지 않다"
    assert en.index("On the server") < en.index("On this machine only")
    assert "(지금은 이쪽을 권한다)" in ko and "(recommended for now)" in en
    assert "(권장)" not in ko and "(recommended)" not in en.replace("(recommended for now)", "")
    assert 'case "$pick" in 2) MODE=local ;; *) MODE=server ;; esac' in s, "1 을 눌러도 로컬이 된다"
    # 묻기 자체는 남아 있어야 한다 — 선택지를 없앤 것이 아니다
    assert "$M_PICK" in s and "--local" in s and "--server" in s


def test_묶음_둘_다_LICENSE_를_싣는다():
    """D15638 → D15971 — 받은 사람이 무엇을 해도 되는지 알 수 있어야 한다. 전에는 묶음에도 저장소에도
    없어 법적 기본값(모든 권리 유보)에 말없이 기대고 있었다. 확장 128호부터는 직접 쓴 문장이 아니라
    표준 FSL-1.1-ALv2 원문이다 — 지켜야 하는 것은 뜻이다: 표준 원문 그대로, 경쟁 서비스만 막고,
    2년 뒤 Apache 2.0 이 되며, 저작권자가 적혀 있다."""
    import io, pathlib, tarfile
    from casebook.adapters import client_dist
    root = pathlib.Path(__file__).resolve().parent.parent
    text = (root / "LICENSE").read_text(encoding="utf-8")
    assert "Functional Source License, Version 1.1, ALv2 Future License" in text
    assert "FSL-1.1-ALv2" in text
    assert "Copyright 2026" in text and "${" not in text          # 자리표시자를 채웠다
    assert "Competing Use" in text and "Permitted Purpose" in text  # 막는 것은 경쟁 사용 하나
    assert "Apache License, Version 2.0" in text                   # 2년 뒤 넓어지는 조항
    assert "LICENSE" in client_dist.CLIENT_FILES
    assert "LICENSE" in client_dist.local_files(root)
    for mode in ("thin", "local"):
        names = tarfile.open(fileobj=io.BytesIO(client_dist.build_tarball(root, mode))).getnames()
        assert any(n.endswith("/LICENSE") for n in names), f"{mode} 묶음에 LICENSE 가 없다"


# ── 삭제기 (확장 116호) ──────────────────────────────────────────────────────
# 깔았으면 걷어낼 수도 있어야 한다. 서버 쪽 forget 만으로는 이 맥에 실행기·훅·토큰이 남는다.
# 문자열로 확인하지 않고 진짜 HOME 을 만들어 돌린다 — rm 을 검사하는 유일한 방법이다.

def _uninstall_home(tmp_path) -> pathlib.Path:
    """설치가 남기는 것을 그대로 흉내 낸 HOME."""
    h = tmp_path / "home" / ".casebook"
    (h / "client" / "bin").mkdir(parents=True)
    for name in ("mode", "lang", "update-url", "remote-url", "oauth.json"):
        (h / name).write_text("x")
    (h / "casebook.db").write_text("RECORDS")           # 로컬 모드면 이것이 기록 전부다
    (h / "client" / "bin" / "casebook-uninstall").write_text(
        client_dist.LAUNCHERS["bin/casebook-uninstall"])
    return h


def _run(home: pathlib.Path, *args):
    script = home / "client" / "bin" / "casebook-uninstall"
    return subprocess.run(["sh", str(script), *args], capture_output=True, text=True,
                          env={"HOME": str(home.parent), "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"})


def test_묶음에_삭제기가_실행_권한으로_들어_있다():
    with tarfile.open(fileobj=io.BytesIO(client_dist.build_tarball(ROOT))) as tar:
        m = tar.getmember("casebook-client/bin/casebook-uninstall")
    assert m.mode & 0o111, "실행 권한이 없으면 사용자가 sh 를 앞에 붙여야 한다"


def test_삭제기는_먼저_보여_주기만_한다(tmp_path):
    h = _uninstall_home(tmp_path)
    r = _run(h)
    assert r.returncode == 0, r.stderr
    assert "Will remove" in r.stdout and str(h / "client") in r.stdout
    assert "--yes" in r.stdout
    # 한 줄도 지우지 않았다
    assert (h / "client").is_dir() and (h / "oauth.json").is_file() and (h / "casebook.db").is_file()


def test_yes_는_프로그램을_지우고_기록은_남긴다(tmp_path):
    h = _uninstall_home(tmp_path)
    r = _run(h, "--yes")
    assert r.returncode == 0, r.stderr
    assert not (h / "client").exists()
    for name in ("mode", "lang", "update-url", "remote-url", "oauth.json"):
        assert not (h / name).exists(), f"{name} 이 남았다"
    # 기록은 그대로다 — 프로그램을 지우는 것과 기록을 버리는 것은 다른 결심이다
    assert (h / "casebook.db").read_text() == "RECORDS"


def test_records_까지_줘야_기록이_지워진다(tmp_path):
    h = _uninstall_home(tmp_path)
    assert _run(h, "--yes", "--records").returncode == 0
    assert not (h / "casebook.db").exists()


def test_모르는_인자는_조용히_지나가지_않는다(tmp_path):
    h = _uninstall_home(tmp_path)
    r = _run(h, "--all")
    assert r.returncode != 0 and (h / "client").is_dir()


def test_깐_것이_없으면_그렇게_말한다(tmp_path):
    h = tmp_path / "home" / ".casebook"
    (h / "client" / "bin").mkdir(parents=True)
    (h / "client" / "bin" / "casebook-uninstall").write_text(
        client_dist.LAUNCHERS["bin/casebook-uninstall"])
    # client 디렉터리 자체는 있으므로 "지울 것" 에 잡힌다 — 그것마저 없는 HOME 을 따로 만든다
    other = tmp_path / "empty"
    (other / ".casebook").mkdir(parents=True)
    r = subprocess.run(["sh", str(h / "client" / "bin" / "casebook-uninstall")],
                       capture_output=True, text=True,
                       env={"HOME": str(other), "PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8"})
    assert r.returncode == 0 and "Nothing is installed." in r.stdout
