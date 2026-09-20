"""가상 데모 기록의 날짜를 짓는 날에 맞춰 민다.

왜: 시드의 날짜는 2026-08-17 ~ 09-12 로 박혀 있다. 심사 기간(09-21 ~ 10-05)에 열면 현황의 열린 스레드가
"3주 전" 에 멈춘 일로 보인다. 지어낸 기록이니 날짜를 통째로 밀어도 거짓이 생기지 않는다 — 실제 기록
(demo_seed_data_real.py)은 밀지 않는다. 그쪽 날짜는 사실이다.

어떻게: 7의 배수만큼만 민다. 그래서 요일이 그대로 남고("매주 월요일"), 가장 늦은 기록은 짓는 시각으로부터
0~6일 전에 온다(미래로는 안 간다). 시각 열쇠(t·start·at)뿐 아니라 **글 안의 날짜**도 같이 민다 —
로그의 2026-09-01, 요약의 (9/1), 본문의 9월 26일, 코드의 date(2026, 9, 26). 시계만 밀면 "엿새 전" 에
쓴 노트가 3주 전 날짜를 말한다.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

YEAR = 2026          # 시드가 쓰인 해. 연도 없는 표기(9/1 · 9월 1일 · September 1)를 이 해로 읽는다.
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

_ISO = re.compile(r"(?<!\d)(20\d\d)-(\d\d)-(\d\d)(?!\d)")
_ISO_MONTH = re.compile(r"(?<![\d-])(20\d\d)-(\d\d)(?![\d-])")
_CALL = re.compile(r"\bdate\((20\d\d), (\d{1,2}), (\d{1,2})\)")
_KO = re.compile(r"(?<!\d)(\d{1,2})월 (\d{1,2})일")
_SLASH = re.compile(r"(?<![\w/.])(\d{1,2})/(\d{1,2})(?![\w/.])")   # 앞뒤에 글자가 붙은 v1/2 같은 것은 날짜가 아니다
_EN = re.compile(r"\b(" + "|".join(MONTHS) + r") (\d{1,2})\b")


def _date(y: int, m: int, d: int) -> dt.date | None:
    try:
        return dt.date(y, m, d)
    except ValueError:
        return None          # 날짜가 아닌 숫자 짝(13/40 같은)은 건드리지 않는다


def shift_text(text: str, days: int) -> str:
    """글 안의 날짜 표기를 days 만큼 민다. 표기 꼴은 그대로 둔다."""
    if not days:
        return text
    delta = dt.timedelta(days=days)

    def sub(rx: re.Pattern, parse, fmt) -> None:
        nonlocal text

        def one(m: re.Match) -> str:
            d = parse(m)
            return fmt(d + delta, m) if d else m.group(0)
        text = rx.sub(one, text)

    sub(_ISO, lambda m: _date(int(m[1]), int(m[2]), int(m[3])), lambda d, m: d.isoformat())
    sub(_CALL, lambda m: _date(int(m[1]), int(m[2]), int(m[3])), lambda d, m: f"date({d.year}, {d.month}, {d.day})")
    sub(_ISO_MONTH, lambda m: _date(int(m[1]), int(m[2]), 1), lambda d, m: f"{d.year}-{d.month:02d}")
    sub(_KO, lambda m: _date(YEAR, int(m[1]), int(m[2])), lambda d, m: f"{d.month}월 {d.day}일")
    sub(_SLASH, lambda m: _date(YEAR, int(m[1]), int(m[2])), lambda d, m: f"{d.month}/{d.day}")
    sub(_EN, lambda m: _date(YEAR, MONTHS.index(m[1]) + 1, int(m[2])), lambda d, m: f"{MONTHS[d.month - 1]} {d.day}")
    return text


def shift_all(node: Any, days: int) -> Any:
    """시드 구조(dict·list·str)를 통째로 민 사본. 시각 열쇠도 글이라 같은 길로 밀린다."""
    if isinstance(node, str):
        return shift_text(node, days)
    if isinstance(node, dict):
        return {k: shift_all(v, days) for k, v in node.items()}
    if isinstance(node, (list, tuple)):
        return type(node)(shift_all(v, days) for v in node)
    return node


def last_stamp(threads: dict) -> dt.datetime:
    """시드에서 가장 늦은 기록 시각."""
    stamps = [th["start"] for th in threads.values()]
    stamps += [st["t"] for th in threads.values() for st in th["steps"]]
    return max(dt.datetime.strptime(s, "%Y-%m-%d %H:%M") for s in stamps)


def days_to_shift(threads: dict, now: dt.datetime) -> int:
    """밀 날수 — 7의 배수, 가장 늦은 기록이 now 를 넘지 않는 가장 큰 값. 이미 최근이면 0."""
    gap = (now - last_stamp(threads)).days        # 꽉 찬 24시간 단위로 내림 → 미래로 안 간다
    return max(0, gap // 7 * 7)
