"""사용 문서 — 소개 옆 문서(web/landing/docs/index.html, 쪽마다 <article data-page> 하나인 한 파일). 보장할 것:
(1) 한국어 데모의 intro/docs/ 로 같이 나가고, 영어판에는 나가지 않는다
(2) 도구 목록이 서버에 등록된 도구와 같다 — 하나 더하거나 빼면 여기서 깨진다
(3) 설치 명령은 소개의 설치 절과 같은 줄이다
(4) 소개에서 문서로, 문서에서 소개·데모로 가는 길이 있다
(5) 문서 안 링크(#/쪽/절)가 모두 있는 쪽·절을 가리킨다
(6) 명령 쪽에 적은 명령은 실제로 설치되는 것이다
"""
from __future__ import annotations

import pathlib
import re

from tests.test_landing import LANDING, _demo

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = (ROOT / "web/landing/docs/index.html").read_text(encoding="utf-8")
SERVER = (ROOT / "casebook/adapters/mcp_server.py").read_text(encoding="utf-8")


def test_문서는_한국어_데모의_소개_아래로_나간다(tmp_path):
    ko = _demo(tmp_path, "ko") / "intro/docs/index.html"
    assert ko.read_text(encoding="utf-8") == DOCS                               # (1) 저장소의 그 파일 그대로
    assert not (_demo(tmp_path, "en") / "intro").exists()


def test_도구_목록은_서버에_등록된_그대로다():
    registered = set(re.findall(r'@server\.tool\(name="([a-z_]+)"', SERVER))
    listed = re.findall(r"<code data-tool>([a-z_]+)</code>", DOCS)
    assert len(listed) == len(set(listed))                                     # 한 번씩만
    assert set(listed) == registered                                           # (2)


def test_설치_명령은_소개와_같은_줄이다():
    for 줄 in ("/plugin marketplace add jasonethicseo/worktrail\n/plugin install worktrail@worktrail",
               'python3 -m venv .venv &amp;&amp; .venv/bin/pip install -e ".[mcp]"',
               "mkdir -p ~/.casebook &amp;&amp; echo local &gt; ~/.casebook/mode",
               "codex mcp add casebook -- $PWD/.venv/bin/python -m casebook.adapters.mcp_proxy",
               "curl -fsSL &lt;받은 URL&gt;/install.sh | sh -s -- --server"):
        assert 줄 in DOCS and 줄 in LANDING, 줄                                   # (3)
    assert (ROOT / "tools/hooks/install_git_hook.sh").is_file()
    assert (ROOT / "bin/casebook-ui").is_file()


def test_문서로_가는_길과_나가는_길():
    assert LANDING.count('href="docs/"') >= 2                                  # (4) 소개의 위 띠와 설치 절
    for 길 in ('href="../"', 'href="../../"', 'href="/join?from=docs"', "https://github.com/jasonethicseo/worktrail"):
        assert 길 in DOCS, 길


def test_문서_안_링크는_있는_쪽과_절로_간다():
    pages = {}
    for m in re.finditer(r'<article data-page="([\w-]+)"(.*?)</article>', DOCS, re.S):
        pages[m.group(1)] = set(re.findall(r'<h[23] id="([\w-]+)"', m.group(2)))
    assert len(pages) >= 10
    for page, sec in re.findall(r'href="#/([\w-]+)(?:/([\w-]+))?"', DOCS):
        assert page in pages, page                                              # (5)
        assert not sec or sec in pages[page], f"{page}/{sec}"


def test_명령은_실제로_설치되는_것이다():
    dist = (ROOT / "casebook/adapters/client_dist.py").read_text(encoding="utf-8")
    cmds = re.findall(r"<code data-cmd>([\w-]+)</code>", DOCS)
    assert "casebook-ui" in cmds
    for c in cmds:
        assert f'"bin/{c}"' in dist, c                                           # (6)
    for c in ("casebook-ui", "casebook-login", "casebook-backfill"):
        assert (ROOT / "bin" / c).is_file(), c                                  # 플러그인도 싣는 셋


def test_한_장이_혼자_선다():
    assert "<script src=" not in DOCS and DOCS.count("<script>") == 2           # 색을 먼저 정하는 것 · 본 스크립트
    assert "/Users/" not in DOCS
