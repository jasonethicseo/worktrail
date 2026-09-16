"""기록의 언어는 쓴 글에서 읽는다 (확장 81호, D15186).

지키는 계약 셋.
(1) 묻지 않는다 — focus 첫 줄의 문자종으로 판정한다. 설치 플래그도 계정 칸도 웹 토글도 없다.
(2) 저장하지 않는다 — 그때그때 focus 에서 도출하므로 focus 를 다시 선언하면 라벨이 따라오고,
    틀렸을 때 고칠 자리가 따로 필요 없다(사용자가 정정 경로를 이번 범위에서 뺐다).
(3) 기록 **본문**은 손대지 않는다. 바뀌는 것은 Worktrail 이 둘레에 붙이는 라벨뿐이다.
"""
from __future__ import annotations

import pytest

from casebook.core import headline
from casebook.core.db import SqliteDB
from casebook.core.worktrail import Worktrail

mcp_server = pytest.importorskip("casebook.adapters.mcp_server")


def _tools(tmp_path):
    db = SqliteDB(str(tmp_path / "h.db"))
    cb = Worktrail(db=db)
    cb.signup("t", "t@x.test", "pw")
    uid = db.get_by("user", "email", "t@x.test")["id"]
    return mcp_server.Tools(cb, uid, remote=True)


@pytest.mark.parametrize("text,want", [
    ("Split the read path from the write path", "en"),
    ("Pick one of three payment providers", "en"),
    ("결제 대행사 후보 셋 중 하나를 고른다", "ko"),
    ("재고가 음수로 내려간 주문 3건의 경로를 찾는다", "ko"),
    ("決済代行を切り替える", "ko"),          # CJK 는 라벨 폭·어순이 한국어 쪽이다
    ("Payrail 로 옮긴다", "ko"),            # 영어 낱말이 섞여도 한글이 있으면 한국어
    ("", "en"),                             # 빈 글은 영어로 — 기본값이 있어야 한다
    (None, "en"),
])
def test_글에서_언어를_읽는다(text, want):
    assert headline.lang_of(text) == want


def test_판정기가_하나다():
    """기록 층의 문자종 판정기는 headline.script_of 하나다. 조사 쪽 workers._pick_lang 은
    모델 프롬프트의 언어 이름을 고르는 것이라 하는 일이 다르다 — 세 번째를 만들면 같은 글을
    두 판정기가 다르게 봐서 화면과 기록이 갈린다."""
    assert headline.script_of("한글") == "hangul"
    assert headline.script_of("漢字") == "han"
    assert headline.script_of("カナ") == "kana"
    assert headline.script_of("plain") == "latin"


def test_인계_라벨이_스레드_언어로_붙는다(tmp_path):
    T = _tools(tmp_path)
    en = T.open_thread(focus="Split the read path from the write path", topic="demo")["case_id"]
    T.decide(case_id=en, statement="We're going with Payrail", reason="Only two could refund partially",
             authority="user")
    T.add_evidence(case_id=en, text="$ curl sandbox\n200 OK")
    T.declare(case_id=en, kind="next", statement="Check the cancel limit", owner="user")
    out = T.handoff(case_id=en, engineer_asked=True)
    assert "- decision (user):" in out and "reason:" in out
    assert "- evidence #" in out and "(turn: user)" in out
    assert "결정" not in out and "이유" not in out and "증거" not in out and "차례" not in out

    ko = T.open_thread(focus="결제 대행사 후보 셋 중 하나를 고른다", topic="demo")["case_id"]
    T.decide(case_id=ko, statement="결제 대행사는 페이레일로 간다", reason="부분취소를 여는 곳이 둘뿐이었다",
             authority="user")
    T.add_evidence(case_id=ko, text="$ curl sandbox\n200 OK")
    T.declare(case_id=ko, kind="next", statement="취소 한도를 확인한다", owner="user")
    out = T.handoff(case_id=ko, engineer_asked=True)
    assert "- 결정 (user):" in out and "이유:" in out
    assert "- 증거 #" in out and "(차례: user)" in out
    assert "decision (user)" not in out and "evidence #" not in out


def test_기록_본문은_손대지_않는다(tmp_path):
    """라벨만 읽는 사람 쪽으로 맞춘다. 사람이 쓴 문장은 쓴 그대로 나간다."""
    T = _tools(tmp_path)
    cid = T.open_thread(focus="Split the read path from the write path", topic="demo")["case_id"]
    T.decide(case_id=cid, statement="결정문만 한국어로 적었다", reason="이유도 한국어다", authority="user")
    out = T.handoff(case_id=cid, engineer_asked=True)
    assert "- decision (user): 결정문만 한국어로 적었다" in out    # 라벨은 영어, 본문은 원문 그대로
    assert "  reason: 이유도 한국어다" in out


def test_focus_를_다시_선언하면_라벨이_따라온다(tmp_path):
    """저장하지 않기 때문에 생기는 성질이다 — 정정 경로를 따로 만들지 않아도 고칠 수 있다."""
    T = _tools(tmp_path)
    cid = T.open_thread(focus="결제 대행사 후보 셋 중 하나를 고른다", topic="demo")["case_id"]
    T.decide(case_id=cid, statement="x", reason="y", authority="user")
    assert "- 결정 (user):" in T.handoff(case_id=cid, engineer_asked=True)

    T.declare(case_id=cid, kind="focus", statement="Pick one of three payment providers")
    assert "- decision (user):" in T.handoff(case_id=cid, engineer_asked=True)


def test_라벨_표가_두_언어에서_같은_열쇠를_갖는다():
    ko, en = mcp_server.HANDOFF_LABELS["ko"], mcp_server.HANDOFF_LABELS["en"]
    assert set(ko) == set(en), set(ko) ^ set(en)
    assert not any("가" <= c <= "힣" for c in "".join(en.values()))   # 영어 표에 한글이 없다


def test_focus_가_없으면_케이스_제목으로_본다(tmp_path):
    """검증이 잡은 결함 — focus 선언 전 한국어 케이스의 인계 라벨이 영어로 뒤집혔다.
    바로 윗줄에서 한국어 제목을 이미 읽고 있으면서 판정에는 쓰지 않았다."""
    T = _tools(tmp_path)
    cid = T.open_case(title="결제 대행사 전환 건")["case_id"]
    T.decide(case_id=cid, statement="페이레일로 간다", reason="부분취소 때문", authority="user")
    out = T.handoff(case_id=cid, engineer_asked=True)
    assert "- 결정 (user): 페이레일로 간다" in out and "이유:" in out


def test_focus_본문의_인용문이_판정을_흔들지_않는다(tmp_path):
    """제목 줄만 본다 — 본문에는 로그·코드가 섞이고 그것이 한국어 사용자의 영어 인용일 수 있다."""
    T = _tools(tmp_path)
    cid = T.open_thread(focus="재고가 음수로 내려간 경로를 찾는다\n\n"
                              "SELECT * FROM stock WHERE qty < 0 returned three rows in production",
                        topic="demo")["case_id"]
    T.decide(case_id=cid, statement="조건부 UPDATE 로 바꾼다", reason="같은 부하에서 음수가 사라졌다",
             authority="user")
    assert "- 결정 (user):" in T.handoff(case_id=cid, engineer_asked=True)


def test_보이지_않는_글자가_판정을_뒤집지_않는다():
    """U+3164 HANGUL FILLER 는 화면에 공백으로만 보이는데 script_of 가 한글로 셌다.
    한국어 웹에서 복사한 영어 문장에 흔히 섞인다."""
    assert headline.lang_of("Split the read pathㅤfrom the write path") == "en"
    assert headline.lang_of("aᅟbᅠcﾠd") == "en"
    assert headline.lang_of("진짜 한글") == "ko"


def test_탭과_제어문자의_폭을_바로_센다():
    """탭을 1칸으로 세면 한 글자가 8배로 어긋나고, ESC·CR 은 화면에서 0칸인데 1칸씩 셌다."""
    assert headline.width("\t") == 8 and headline.width("ab\t") == 8      # 다음 탭 스톱까지
    assert headline.width("\x1b[2K") == 3                                  # ESC 는 0칸
    assert headline.width("가" * 60) == 120 and headline.width("가" * 61) == 122   # 한국어는 그대로
