"""확장 52호 — 기록의 첫 줄은 제목이다 (D14428, 2026-09-11). 커밋 메시지의 제목/본문 규칙을 빌린다.

    제목: 첫 줄 · 표시폭 120칸 이내 · " — " 로 절을 잇지 않는다.
    본문: 빈 줄 뒤에 길이 자유.

왜: 실측(605개)에서 첫 문장 중앙값이 65자, 54% 가 60자 초과, 노트의 43% 가 " — " 로 두 절을 잇는다.
도구가 "한 문장"만 요구하고 상한이 없어 에이전트가 한 문장에 전부 우겨 넣는다 — 그래서 화면이 raw 하다.
검사 대상: focus·open·next 선언, 결정·제약 문장, 노트의 결론, 닫을 때의 결과, 주제 결론.
확장 58호(D14625): 결정·제약의 이유(reason)도 — 카드의 "왜" 줄이 이유의 첫 줄이라서. 비어 있으면 그대로.
증거(add_evidence)와 관찰(observed)은 원문 그대로라 검사하지 않는다.

확장 77호 (C15187, 2026-09-13): 단위가 글자에서 **표시폭**으로 바뀌었다. 60"자"는 언어마다 다른 규칙이다 —
같은 뜻을 영어로 쓰면 글자를 두 배 넘게 쓰므로, 상한 없이 쓴 영어 focus 444줄의 중앙값이 128자였고
60자 통과율이 0.6% 였다. 영어권 사용자는 첫 declare 에서 막힌다. 표시폭 120칸은 len<=60 의 상위집합이라
한국어에 새로 거절되는 문장이 0건이면서(오늘과 완전히 동일) 영어가 18~20단어 들어간다.
언어를 지정하지 않는다 — "한국어로 쓰라" 는 요구는 뺐다.
"""
from __future__ import annotations

import re
import unicodedata

from casebook.core.errors import InputError

WIDTH_LIMIT = 120
JOINER = " — "

LIMIT = WIDTH_LIMIT      # 이름 유지 (옛 호출부·문서)

# 확장 123호 (D15872, 2026-09-16) — 첫 줄은 사람이 화면에서 읽는 줄이다. 기록 번호는 거기 두지 않는다.
# 실측: #517 에서 에이전트가 쓴 노트 제목 넷 중 셋이 "(증거 #2234)" 꼬리표를 달고 있었고, next 는
# "사용자가 …한다" 는 3인칭 일지였다. 사람은 번호를 못 따라가고, 번호는 본문(빈 줄 뒤)이나 evidence_ids
# 로 가면 링크를 잃지 않는다. 검사는 기계적인 것만 — "읽기 쉬운가" 는 검사할 수 없고, 검사하려 들면
# 에이전트는 더 모호한 제목으로 빠져나간다(15턴 상한이 그랬다, #522).
TAG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("#N", re.compile(r"#\d+")),                                              # #517 · 증거 #2234
    ("D/C/R N", re.compile(r"\b[DCR]\d+\b")),                                  # D15635 · C14993 · R12
    ("확장 N호", re.compile(r"확장\s*\d+(?:\s*[·~,\-–]\s*\d+)*\s*호")),          # 확장 122호 · 확장 87·89호
    ("turn N", re.compile(r"(?:\bturn|턴)\s+\d+\b", re.IGNORECASE)),          # turn 80 · 턴 80
    # 7ce092d · 3e968bbcead2 · 40자 전체. 숫자만인 것(시각·수량)과 그 밖의 길이(EC2 id 17자)는 해시가 아니다.
    ("commit hash", re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)(?:[0-9a-f]{7,12}|[0-9a-f]{40})\b")),
)
# owner 가 user 인 next 의 머리말 — 사람에 대해 쓴 줄. 기계적으로 잡히는 것만.
SUBJECT_PREFIXES = ("사용자가", "사용자는", "사용자께서", "the user", "the engineer")


def tags_in(first: str) -> list[str]:
    """첫 줄에 든 기록 번호들 — 나온 순서대로, 겹치지 않게."""
    found: list[str] = []
    for _label, rx in TAG_PATTERNS:
        for m in rx.finditer(first):
            if m.group(0) not in found:
                found.append(m.group(0))
    return found


def width(text: str) -> int:
    """터미널 표시폭. 동아시아 넓은 글자(한글·한자·가나·전각)는 2칸, 나머지는 1칸.

    east_asian_width 의 W(Wide)·F(Fullwidth)만 2로 센다. A(Ambiguous)는 1로 둔다 — 폰트에 따라
    갈리는 부류라 2로 세면 키릴·그리스 문자가 근거 없이 절반만 들어간다. 결합 문자(Mn·Me)는 0칸이다."""
    # 정규화를 먼저 맞춘다 — NFD 한글은 자모가 낱낱이라 같은 글이 두 배로 세어졌다
    # (NFC "한"*31 = 62칸인데 NFD 는 124칸이었다). 화면에 보이는 것은 같은 글자다.
    text = unicodedata.normalize("NFC", text)
    n = 0
    for ch in text:
        if unicodedata.combining(ch):
            continue
        if ch == "\t":
            n += 8 - (n % 8)          # 탭은 다음 탭 스톱까지 — 1칸으로 세면 한 글자가 8배로 어긋난다
            continue
        if unicodedata.category(ch) in ("Cc", "Cf"):
            continue                   # 제어·서식 문자는 화면에서 0칸이다(ESC·CR·ZWJ 등)
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def title_of(text: str | None) -> str:
    return (text or "").strip().split("\n", 1)[0].strip()


def body_of(text: str | None) -> str:
    parts = (text or "").strip().split("\n", 1)
    return parts[1].strip() if len(parts) > 1 else ""


def check(text: str | None, what: str) -> str:
    """통과하면 앞뒤 공백만 벗긴 원문을 돌려준다. 넘치면 어떻게 쓰라는지까지 말하고 거절한다.

    거절문은 영어다. 이것을 읽는 것은 에이전트이고, 같은 부류의 거절문 43개 중 38개가 이미 영어다.
    예시도 스스로 규칙을 지킨다 — 예전 거절문은 자기가 금지한 ' — ' 로 절을 잇고 있었다."""
    s = (text or "").strip()
    first = title_of(s)
    if not first:
        raise InputError(f"{what} is empty")
    w = width(first)
    problems, why = [], []
    if w > WIDTH_LIMIT:
        problems.append(f"{w} columns wide"); why.append("width")
    if JOINER in first:
        problems.append("joined with ' — '"); why.append("joiner")
    tags = tags_in(first)                     # 확장 123호 — 번호 꼬리표는 본문이나 evidence_ids 로
    if tags:
        problems.append("record numbers in the title: " + ", ".join(tags)); why.append("tags")
    if problems:
        _record_refusal(what, first, w, why)
        hint = (" A person reads that line on the screen: record numbers (#517, D15635, 확장 122호, "
                "turn 80, commit hashes) go in the body after a blank line, or into evidence_ids."
                if tags else "")
        raise InputError(
            f"{what}: the first line is a title. At most {WIDTH_LIMIT} columns "
            f"(CJK characters count as 2), do not join clauses with ' — ', and no record numbers. "
            f"Now: {', '.join(problems)}.{hint} Write one title line, then a blank line, "
            f"then the rest as the body. Example:\n"
            f"The API door still accepted the old token\n\n"
            f"Same token: MCP door 401, /app/status 200. Evidence #2234, commit 7ce092d.")
    return s


def check_addressed(text: str | None, owner: str | None, what: str = "next") -> str:
    """owner 가 user 인 next 는 사람에게 쓰는 줄이지 사람에 대해 쓰는 줄이 아니다 (확장 123호, D15872).

    "사용자가 소개글에 로컬 모드를 넣는다" 는 일지이고 "소개글에 로컬 모드를 넣는다" 는 안내다 — 같은
    정보인데 읽는 사람이 다르다. 머리말이 기계적으로 잡히는 것만 거절한다. check() 뒤에 부른다."""
    s = (text or "").strip()
    if (owner or "").strip().lower() != "user":
        return s
    first = title_of(s)
    low = first.lower()
    if any(low.startswith(p) for p in SUBJECT_PREFIXES):
        _record_refusal(what, first, width(first), ["subject"])
        raise InputError(
            f"{what} (owner: user): the engineer reads this line, so write it to them, not about them. "
            f"Start with the action, not with '사용자가' / 'The user'. Example:\n"
            f"소개글에 로컬 모드 한 단락을 넣고 레딧에 올린다\n\n"
            f"README 가 아직 한국어라 영어권 사람은 설치 전에 막힐 수 있다. 그것을 먼저 할지 정한다.")
    return s


# ── 거절 계측 (확장 77호) ─────────────────────────────────────────────────────
# 상한을 무엇으로 둘지는 지금 추측 위에 있다: 영어 예산 추정치가 "60자에 맞춰 쓴 한국어" 를 번역해
# 얻은 것이라 순환 논증이다. 거절이 일어날 때 길이를 남겨 두면 다음 라운드에서 이 숫자가 논쟁이
# 아니라 관측으로 정해진다. 프로세스 안에만 쌓고 DB 를 건드리지 않는다 — 인증 전 경로에서도 불릴 수
# 있어(open_thread 는 _guard 밖이다) 요청마다 쓰면 그것이 곧 무제한 쓰기 표면이 된다(확장 75호).
REFUSALS: list[dict] = []
_REFUSAL_CAP = 500


def script_of(text: str) -> str:
    """무슨 글자로 쓰였나 — hangul · han · kana · latin.

    기록 층의 유일한 문자종 판정기다. 조사 쪽에는 workers._pick_lang 이 따로 있는데 그것은
    모델에게 줄 프롬프트의 언어 **이름**("한국어"·"영어(English)")을 고르는 것이라 하는 일이 다르다.
    세 번째를 만들지 않는다 — 같은 글을 두 판정기가 다르게 보면 화면과 기록이 갈린다."""
    # 보이지 않는 글자는 단서가 아니다 — U+3164 HANGUL FILLER 한 글자로 판정이 뒤집혔다.
    # 자모 채움(U+115F·U+1160·U+3164·U+FFA0)은 화면에 공백으로만 보인다.
    fillers = {"\u115f", "\u1160", "\u3164", "\uffa0"}
    for ch in text:
        if ch in fillers:
            continue
        name = unicodedata.name(ch, "")
        if "HANGUL" in name:
            return "hangul"
        if "CJK" in name:
            return "han"
        if "HIRAGANA" in name or "KATAKANA" in name:
            return "kana"
    return "latin"


def _record_refusal(what: str, first: str, w: int, why: list[str] | None = None) -> None:
    """why (확장 123호): width · joiner · tags · subject — 거부가 잦으면 어느 규칙이 잦은지 봐야 풀 수 있다."""
    if len(REFUSALS) >= _REFUSAL_CAP:
        del REFUSALS[0]
    REFUSALS.append({"what": what, "chars": len(first), "width": w, "script": script_of(first), "why": list(why or [])})


def lang_of(text: str | None) -> str:
    """이 글이 한국어로 쓰였나 — "ko" 또는 "en". 기록 둘레에 붙는 라벨이 이 값으로 갈린다.

    묻지 않고 쓴 글에서 읽는다(D15186). 저장하지 않는 것이 중요하다 — 지금 focus 에서 그때그때
    도출하므로 focus 를 다시 선언하면 라벨도 따라오고, 틀렸을 때 고칠 자리가 따로 필요 없다.
    한자·가나는 한국어 쪽으로 둔다: CJK 는 라벨의 폭·어순이 한국어와 같은 편이고, 일본어 라벨을
    따로 갖추지 않은 지금 영어보다 덜 어긋난다."""
    return "en" if script_of(text or "") == "latin" else "ko"
