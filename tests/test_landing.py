"""소개(랜딩) 페이지 — 처음 온 사람이 읽는 한 장. 보장할 것:
(1) 한국어 데모 폴더에 intro/ 로 같이 나가고, 영어판에는 나가지 않는다
(2) 데모 띠의 소개 링크는 한국어 스냅샷에서만 뜬다(랜딩이 한국어다)
(3) 페이지가 말하는 것이 실제 제품에 있는 것이다 — 인용한 기록은 데모 시드에, 설치 명령은 플러그인 이름에 있다
(4) 데모·현황·회고로 가는 길이 끊기지 않는다
"""
from __future__ import annotations

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("demo_snapshot", ROOT / "tools/demo_snapshot.py")
ds = importlib.util.module_from_spec(spec); spec.loader.exec_module(ds)   # type: ignore[union-attr]
PAGE = (ROOT / "web/worktrail/index.html").read_text(encoding="utf-8")
LANDING = (ROOT / "web/landing/index.html").read_text(encoding="utf-8")
SEED = (ROOT / "tools/demo_seed_data.py").read_text(encoding="utf-8")
MARKET = (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
README = (ROOT / "README.ko.md").read_text(encoding="utf-8")


def _demo(tmp_path, lang: str) -> pathlib.Path:
    """작은 데모 폴더 하나 — 스냅샷 도구가 실제로 쓰는 길 그대로(코어 직접 호출)."""
    from casebook.adapters.mcp_server import Tools
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    from tests.test_threads import _git_repo
    db = tmp_path / f"{lang}.db"
    wt = Worktrail(SqliteDB(str(db)))
    wt.signup("사람", "d@x.test", "pw")
    t = Tools(wt, 1)
    cid = t.open_thread("소개 페이지가 데모와 같이 나가는지 본다", _git_repo(tmp_path / f"r-{lang}"), topic="검증")["case_id"]
    t.note_turn(cid, "$ ls", "폴더가 섰다", kind="finding", next="소개가 들어갔는지 본다", owner="user")
    out = tmp_path / f"site-{lang}"
    ds.build(ds.db_getter(str(db), "d@x.test"), PAGE, out, [], lang=lang)
    return out


def test_소개는_한국어_데모에만_들어간다(tmp_path):
    ko = _demo(tmp_path, "ko")
    intro = ko / "intro/index.html"
    assert intro.is_file()                                    # (1)
    assert intro.read_text(encoding="utf-8") == LANDING       # 저장소의 그 파일 그대로
    assert not (_demo(tmp_path, "en") / "intro").exists()


def test_데모_띠의_소개_링크는_한국어_스냅샷에서만(tmp_path):
    assert '<a class="intro" id="demoIntro" href="intro/" hidden></a>' in PAGE
    assert 'intro.hidden = !(SNAP && CFG.LANG === "ko" && LANG === "ko")' in PAGE      # (2)


def test_인용한_기록은_데모에_있는_것이다():
    # (3) 랜딩이 예로 든 문장은 전부 데모 시드가 실제로 쌓는 기록이다 — 지어낸 예시를 따로 들지 않는다.
    for 인용 in ("재고가 음수로 내려간 주문 3건의 경로를 찾는다",
                "읽고 쓰는 사이가 잠겨 있지 않다, 전형적인 경쟁 상태다",
                "외부 결제 호출을 사용자 요청 안에서 기다리지 않는다",
                "막는 방법 셋 중 무엇을 쓸지 안 정했다",
                "세일 트래픽을 재고 방식 셋과 맞춰 보고 하나를 고른다",
                "결제 대행사는 페이레일로 간다",
                "페이레일로 옮겼고 실결제 전환만 남았다",
                "대행사를 부르는 코드는 어댑터 뒤에만 둔다"):
        assert 인용 in LANDING and 인용 in SEED, 인용


def test_설치_명령은_실제_플러그인_이름이다():
    assert '"name": "worktrail"' in MARKET                                             # 마켓 이름 = 플러그인 이름
    for 줄 in ("/plugin marketplace add jasonethicseo/worktrail", "/plugin install worktrail@worktrail"):
        assert 줄 in LANDING and 줄 in README, 줄                                       # (3) 안내문과 같은 줄


def test_Codex_길은_실제로_되는_것만_적는다():
    """플러그인은 Claude Code 전용이고, Codex 는 설치 한 줄(신청)이나 소스에서 직접 등록한다.
    소스 카드가 환경변수 없이 붙는다고 적었으니 로컬 기본값이 실제로 있어야 한다."""
    from casebook.core.clientmode import DEFAULT_DB, LOCAL, MODE_FILE
    assert "플러그인이 없으므로 소스에서 깔고 직접 등록합니다" in LANDING     # 플러그인은 Claude Code 것이다
    assert "codex mcp add casebook" in LANDING and "casebook.adapters.mcp_proxy" in LANDING
    assert f"echo {LOCAL} &gt; {MODE_FILE}" in LANDING                  # 모드 파일과 그 값
    assert f"<code>{DEFAULT_DB}</code>" in LANDING                     # 환경변수를 안 줄 때 기록이 가는 자리
    # 설치 한 줄이 두 에이전트에 등록한다는 말은 설치기가 실제로 하는 일이다
    installer = (ROOT / "casebook/adapters/client_dist.py").read_text(encoding="utf-8")
    assert "codex mcp add casebook" in installer and "claude mcp add casebook" in installer
    # Codex 에는 세션 훅이 없다 — 훅 설정에는 Claude Code 의 훅만 있다
    assert "codex" not in (ROOT / "hooks/hooks.json").read_text(encoding="utf-8").lower()
    assert "세션 훅이 없습니다" in LANDING


def test_나가는_길이_다_있다():
    for 길 in ('href="../"',                       # 데모 첫 화면
               'href="../#/threads"',              # 현황
               'href="../#/review/1"',             # 회고(주제)
               'href="../#/threads/6"',            # 예로 든 스레드
               'href="/join?from=landing"',        # 서버에 저장 — 신청
               'href="/go/plugin-ko"',             # 로컬에 저장 — 플러그인 (우리 서버를 거쳐 나간다)
               "https://github.com/jasonethicseo/worktrail"):
        assert 길 in LANDING, 길                                                        # (4)


def test_한_장이_혼자_선다():
    # 데모 폴더에 통째로 복사되는 한 장이다 — 저장소 안의 다른 파일을 부르지 않는다(글꼴만 밖에서 받는다).
    assert "<script src=" not in LANDING and 'rel="stylesheet"' in LANDING
    assert "../app/config.js" not in LANDING
    assert LANDING.count("<script>") == 1
