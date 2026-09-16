"""확장 52호 — 기록의 첫 줄은 제목이다(D14428). 확장 77호(C15187)로 단위가 표시폭 120칸이 됐다. 보장할 것:
(1) 120칸 초과 · ' — ' · 빈 제목은 거절하고, 어떻게 쓰라는지 말한다(영어로) (2) 제목 + 빈 줄 + 본문은 통과하고 원문 그대로 저장된다
(3) 선언·결정·제약·결론·결과·주제 결론이 모두 같은 규칙이다 (4) 증거(content)와 관찰은 예외 — 길어도 받는다."""
from __future__ import annotations

import pytest

from casebook.core import headline
from casebook.core.errors import InputError
from tests.drive import USER
from tests.test_threads import _git_repo

LONG = "사용자가 남은 관찰을 계속할지 접을지 정한다 — 접수 9/18·제출 9/20 이 열흘 앞이고 결함이 열려 있으므로 접는 쪽이면 이 스레드를 닫는다."
GOLD = "관찰을 계속할지 접을지 정한다\n\n접수 9/18·제출 9/20 이 열흘 앞이고 결함 494 가 열려 있다. 접으면 닫는다."


def test_check():
    assert headline.check("짧은 제목", "x") == "짧은 제목"
    assert headline.check(GOLD, "x") == GOLD and headline.title_of(GOLD) == "관찰을 계속할지 접을지 정한다"
    assert headline.body_of(GOLD).startswith("접수 9/18")
    with pytest.raises(InputError, match="columns wide.*blank line"):
        headline.check(LONG, "next")
    with pytest.raises(InputError, match="joined with ' — '"):
        headline.check("짧지만 — 이어 붙였다", "next")
    with pytest.raises(InputError, match="is empty"):
        headline.check("   ", "next")


def test_모든_쓰는_자리가_같은_규칙이다(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    n_cases = app.db.count("case")
    with pytest.raises(InputError, match="focus: the first line is a title"):
        app.open_thread(USER, LONG, wt)
    t = app.open_thread(USER, GOLD, wt)["case_id"]
    assert app.current_thread(USER, wt)["focus"] == GOLD                          # 원문 그대로 저장
    assert app.db.get("case", t)["title"] == "관찰을 계속할지 접을지 정한다"       # 제목 = focus 의 첫 줄
    assert app.db.count("case") == n_cases + 1                                    # 거절된 LONG 은 케이스를 만들지 않았다
    for kind in ("open", "next"):
        with pytest.raises(InputError, match=f"{kind}: the first line is a title"):
            app.declare(USER, t, kind, LONG, owner="user")
    app.declare(USER, t, "next", GOLD, owner="user")
    with pytest.raises(InputError, match="decision: the first line is a title"):
        app.decide(USER, t, LONG, "reason", [], authority="user")
    d = app.decide(USER, t, GOLD, "reason", [], authority="user"); assert d["decision_id"]
    with pytest.raises(InputError, match="decision reason: the first line is a title"):      # 확장 58호 — 이유도 첫 줄이 제목(D14625)
        app.decide(USER, t, GOLD, LONG, [], authority="user")
    assert app.decide(USER, t, GOLD, GOLD, [], authority="user")["decision_id"]
    assert app.decide(USER, t, GOLD, None, [], authority="user")["decision_id"]   # 이유 없음은 그대로 받는다
    with pytest.raises(InputError, match="constraint: the first line is a title"):
        app.constrain(USER, t, LONG, "reason", authority="user")
    with pytest.raises(InputError, match="constraint reason: the first line is a title"):
        app.constrain(USER, t, GOLD, LONG, authority="user")
    assert app.constrain(USER, t, GOLD, "", authority="user")["constraint_id"]
    with pytest.raises(InputError, match="conclusion.*the first line is a title"):
        app.external_turn(USER, t, "$ pytest\n1 passed", action_key="k1", note=LONG)
    app.external_turn(USER, t, LONG + "\n" + LONG, action_key="k2", note=GOLD)   # 증거는 길어도 받는다
    tp = app.create_topic(USER, "제목 규칙", "", case_ids=[t])["topic_id"]
    with pytest.raises(InputError, match="topic conclusion: the first line is a title"):
        app.conclude_topic(USER, tp, LONG)
    with pytest.raises(InputError, match="result: the first line is a title"):
        app.close_thread(USER, t, LONG)
    assert app.close_thread(USER, t, GOLD)["result"] == GOLD


# ── 확장 77호 — 단위가 표시폭이다 (C15187) ────────────────────────────────────

def test_한국어는_오늘과_똑같이_통과한다():
    """120칸은 옛 60자의 상위집합이다 — 한글은 2칸이므로 60자 = 120칸이 상한과 정확히 같다.
    기존 사용자에게 새로 거절되는 문장이 하나도 없어야 이 변경이 안전하다."""
    assert headline.width("가" * 60) == 120
    headline.check("가" * 60, "x")                       # 옛 상한 딱 맞는 줄 — 그대로 통과
    with pytest.raises(InputError):
        headline.check("가" * 61, "x")                   # 옛 상한을 넘던 줄 — 그대로 거절


def test_영어는_이제_한_줄이_들어간다():
    """영어권 사용자가 막히던 지점. 상한 없이 쓴 영어 focus 중앙값이 128자였다."""
    s = "Split the read path from the write path so the screen never waits on the recorder"
    assert headline.width(s) == len(s) == 81
    headline.check(s, "focus")                           # 옛 60자에서는 거절되던 길이
    with pytest.raises(InputError, match="121 columns wide"):
        headline.check("a" * 121, "focus")


def test_폭은_글자_종류로_센다():
    assert headline.width("abc") == 3
    assert headline.width("한글") == 4                    # 한글 W
    assert headline.width("漢字") == 4                    # 한자 W
    assert headline.width("カナ") == 4                    # 가타카나 W
    assert headline.width("ＡＢ") == 4                    # 전각 F
    assert headline.width("café") == 4                   # 라틴 악센트는 1칸
    assert headline.width("Ω") == 1                      # Ambiguous 는 1칸으로 둔다
    assert headline.width("한a글b") == 6                  # 섞여도 더한다


def test_거절을_계측한다():
    """상한 숫자는 아직 추측 위에 있다 — 거절을 재 두면 다음 라운드에서 관측으로 정해진다."""
    headline.REFUSALS.clear()
    with pytest.raises(InputError):
        headline.check("가" * 70, "focus")
    with pytest.raises(InputError):
        headline.check("z" * 200, "decision")
    assert [r["script"] for r in headline.REFUSALS] == ["hangul", "latin"]
    assert headline.REFUSALS[0] == {"what": "focus", "chars": 70, "width": 140, "script": "hangul", "why": ["width"]}
    assert headline.REFUSALS[1]["width"] == 200 and headline.REFUSALS[1]["chars"] == 200


def test_계측이_메모리를_먹지_않는다():
    """인증 전 경로에서도 불릴 수 있다 — 무한히 쌓이면 그 자체가 표면이 된다."""
    headline.REFUSALS.clear()
    for i in range(headline._REFUSAL_CAP + 50):
        with pytest.raises(InputError):
            headline.check("x" * (121 + i % 7), "focus")
    assert len(headline.REFUSALS) == headline._REFUSAL_CAP


def test_거절문이_스스로_규칙을_지킨다():
    """옛 거절문은 자기가 금지한 ' — ' 로 절을 잇고 한국어 예시를 들고 있었다."""
    try:
        headline.check("a" * 200, "focus")
    except InputError as exc:
        msg = str(exc)
    assert " — " not in msg.split("Example:")[0].replace("' — '", "")   # 인용 밖에서는 안 쓴다
    example = msg.split("Example:\n", 1)[1].split("\n", 1)[0]
    assert headline.width(example) <= headline.WIDTH_LIMIT              # 예시가 상한을 지킨다
    assert not any("가" <= c <= "힣" for c in msg)                       # 에이전트가 읽는 거절문은 영어다


# ── 확장 123호 — 첫 줄은 사람 말로 (D15872) ─────────────────────────────────────

def test_첫_줄의_번호_꼬리표를_거절한다():
    """2026-09-16 실측: 노트 제목 넷 중 셋이 "(증거 #2234)" 를 달고 있었다. 번호는 본문이나 evidence_ids 로."""
    for bad in ("API 문이 구형 토큰을 그대로 받는다 (증거 #2234)", "저장소는 private 으로 둔다 (D15635)",
                "테스터는 3~5명까지만 받는다, C14993 그대로", "확장 122호로 API 문을 닫았다",
                "확장 87·89호가 넣은 검사", "turn 80 의 판정이 틀렸다", "턴 80 의 판정이 틀렸다",
                "커밋 7ce092d 를 배포했다", "Deployed 3e968bbcead2 to the server", "#517 의 카드가 사람 말로 읽힌다"):
        with pytest.raises(InputError, match="record numbers in the title"):
            headline.check(bad, "conclusion")
    with pytest.raises(InputError, match="#2234, 7ce092d"):                     # 무엇이 걸렸는지 그대로 말한다
        headline.check("문을 닫았다 (증거 #2234, 커밋 7ce092d)", "next")
    assert headline.REFUSALS[-1]["why"] == ["tags"]


def test_번호가_아닌_숫자는_그대로_받는다():
    """번호 꼬리표만 잡는다 — 수량·상태 코드·시각·사용자가 지은 이름은 번호가 아니다."""
    for ok in ("피실험자 1호 실사용 관찰", "HTTP 401 이 난다", "테스터 3~5명을 받는다", "1789472463195 에 켰다",
               "deadbeef 는 해시가 아니다", "C# 코드를 고친다", "3D 모델이 뜬다", "15턴 상한이 우회를 낳았다",
               "API door still accepts the old token", "숨길 것: 인스턴스 i-0123456789abcdef0 --profile opsprofile"):
        assert headline.check(ok, "conclusion") == ok
    # 본문에는 얼마든지 — 첫 줄만 사람용이다
    assert headline.check("문을 닫았다\n\n증거 #2234, 커밋 7ce092d, D15635 를 따랐다.", "conclusion")


def test_사용자_차례의_next_는_사람에게_쓴다():
    """"사용자가 X 를 정한다" 는 일지이고 "X 를 정한다" 는 안내다. owner 가 user 일 때만."""
    for bad in ("사용자가 소개글에 로컬 모드를 넣는다", "사용자는 셋을 정한다", "The user decides which one", "The engineer picks the order"):
        with pytest.raises(InputError, match="write it to them, not about them"):
            headline.check_addressed(bad, "user")
    assert headline.REFUSALS[-1]["why"] == ["subject"]
    assert headline.check_addressed("소개글에 로컬 모드를 넣는다", "user")
    assert headline.check_addressed("사용자 화면을 고친다", "user")                 # "사용자 X" 는 행동일 수 있다 — 안 잡는다
    assert headline.check_addressed("사용자가 정하는 것을 기다린다", "claude")       # 에이전트 차례면 3인칭이 맞다
    assert headline.check_addressed("사용자가 정하는 것을 기다린다", "watch")


def test_선언과_노트가_같은_규칙을_쓴다(app, tmp_path):
    from casebook.adapters.mcp_server import Tools
    wt = _git_repo(tmp_path / "w2")
    t = app.open_thread(USER, "첫 줄 규칙\n\n본문", wt)["case_id"]
    with pytest.raises(InputError, match="write it to them"):
        app.declare(USER, t, "next", "사용자가 정한다", owner="user")
    app.declare(USER, t, "next", "사용자가 정하는 것을 기다린다", owner="claude")   # 차례가 아니면 지난다
    with pytest.raises(InputError, match="record numbers"):
        app.declare(USER, t, "open", "남은 것은 #518 의 (b) 뿐이다", owner=None)
    tools = Tools(app, USER)
    n = app.db.count("turn", where={"case_id": t})
    with pytest.raises(InputError, match="write it to them"):
        tools.note_turn(t, "관찰", "결론이다", kind="finding", next="사용자가 정한다", owner="user")
    with pytest.raises(InputError, match="record numbers"):
        tools.note_turn(t, "관찰", "결론이다 (증거 #1)", kind="finding", next="정한다", owner="user")
    assert app.db.count("turn", where={"case_id": t}) == n                       # 거절은 턴을 남기지 않는다
