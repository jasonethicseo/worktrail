"""가상 데모 기록의 날짜를 짓는 날에 맞춰 민다 (tools/demo_shift.py).

왜 필요한가: 시드의 날짜가 2026-09-12 에서 멈춰 있어 심사 기간(09-21 ~ 10-05)에 열면 현황의 열린 스레드가
몇 주 전 일로 보인다(사용자 2026-09-18). 시계만 밀면 글 안의 날짜가 어긋나고, 실제 기록까지 밀면 거짓이 된다.
"""
from __future__ import annotations

import datetime as dt
import sqlite3

from tools import demo_shift as ds
from tools.demo_seed_data import THREADS, TOPICS


def test_7의_배수로만_밀고_미래로_가지_않는다():
    last = ds.last_stamp(THREADS)
    for now in (last, last + dt.timedelta(days=6, hours=23), last + dt.timedelta(days=7),
                dt.datetime(2026, 9, 21, 9, 0), dt.datetime(2026, 10, 5, 23, 0)):
        n = ds.days_to_shift(THREADS, now)
        assert n % 7 == 0, "요일이 바뀐다"
        assert last + dt.timedelta(days=n) <= now, "가장 늦은 기록이 미래에 찍힌다"
        assert now - (last + dt.timedelta(days=n)) < dt.timedelta(days=7), "일주일 넘게 낡았다"
    assert ds.days_to_shift(THREADS, last - dt.timedelta(days=30)) == 0, "뒤로는 밀지 않는다"


def test_글_안의_날짜가_꼴을_지키며_같이_밀린다():
    got = ds.shift_text("후보 비교(8/18) · 2026-09-01 09:12:03 · webhook-2026-09-01.log · 9월 26일부터 · "
                        "date(2026, 9, 26) · September 26 · 2026-08 부터", 21)
    assert got == ("후보 비교(9/8) · 2026-09-22 09:12:03 · webhook-2026-09-22.log · 10월 17일부터 · "
                   "date(2026, 10, 17) · October 17 · 2026-08 부터")


def test_날짜가_아닌_것은_건드리지_않는다():
    same = "5회/24시간 · 84 / 200 · 3/200 · v1/2 · 13/40 · 2만~3만 · PAY-33120 · 0.412 · 3.4%"
    assert ds.shift_text(same, 21) == same


def test_시드의_날짜_표기는_전부_밀개가_아는_꼴이다():
    """밀개가 모르는 꼴('8일에')이 시드에 들어오면 시계와 글이 어긋난다. 일(日)만 적은 날짜를 막는다."""
    import re

    def strings(node):
        if isinstance(node, str):
            yield node
        elif isinstance(node, dict):
            for v in node.values():
                yield from strings(v)
        elif isinstance(node, (list, tuple)):
            for v in node:
                yield from strings(v)
    bare = re.compile(r"(?<![월\d] )(?<!\d)\d{1,2}일(에|부터|까지)")
    for s in strings((TOPICS, THREADS)):
        assert not bare.search(s), f"달 없이 날만 적었다: {bare.search(s).group(0)!r}"


def test_가상_기록은_밀리고_실제_기록은_제_날짜에_남는다(tmp_path, monkeypatch):
    """증상을 본다: 심사 마지막 날(10-05)에 지으면 가상 팀의 마지막 기록은 일주일 안, 실제 기록은 9월 13~15일 그대로."""
    from casebook.core.db import SqliteDB
    from tools.demo_seed import seed

    # seed() 는 SqliteDB.now_ms 를 가상 시계로 갈아 끼운다(클래스 전역). 되돌리지 않으면 같은 프로세스의 뒤 시험들이
    # 멈춘 시계로 돈다 — 처음 붙였을 때 실제로 다섯이 그렇게 깨졌다.
    monkeypatch.setattr(SqliteDB, "now_ms", SqliteDB.__dict__["now_ms"])

    db = tmp_path / "demo.db"
    now = dt.datetime(2026, 10, 5, 12, 0)
    counts = seed(str(db), tmp_path / "work", "ko", real=True, now=now)
    assert counts["moved"] == 21

    con = sqlite3.connect(db)
    cases = dict(con.execute("select title, created_at from 'case'").fetchall())
    con.close()

    def day(ms: int) -> dt.date:
        return dt.datetime.fromtimestamp(ms / 1000).date()

    fake = day(cases["무료배송 기준을 올린 뒤 장바구니 이탈이 느는지 지켜본다"])
    real = day(cases["기록 도구 호출이 5분마다 매달리던 것을 고친다"])
    assert fake == dt.date(2026, 10, 3), fake          # 09-12 + 21일
    assert (now.date() - fake).days < 7
    assert real == dt.date(2026, 9, 14), real          # 사실이라 그대로


def test_시드의_시각은_짓는_기계의_시간대와_무관하게_한국_시간이다(monkeypatch):
    """서버 컨테이너는 UTC 다. time.mktime 으로 읽으면 "9/14 16:50" 이 화면(KST)에서 01:50 으로 보였다(실측 2026-09-18)."""
    import os
    import time

    from tools.demo_seed import Clock

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        assert Clock.set("2026-09-14 16:50") == 1789372200000      # 2026-09-14T16:50+09:00
    finally:
        os.environ.pop("TZ", None)
        time.tzset()


def test_밀_날수는_기계가_아니라_한국_시간으로_잰다(monkeypatch):
    """서버 컨테이너는 UTC 다. 거기서 naive now() 로 재면 한국보다 9시간 뒤처져 밀기가 하루 늦는다.
    실측 2026-09-19 16:05(KST) 재빌드에서 한국 기준 7일인데 0 이 나와 데모 날짜가 그대로 있었다."""
    import os
    import time

    from tools.demo_seed import now_seoul

    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        at = dt.datetime(2026, 9, 19, 7, 5, tzinfo=dt.timezone.utc)      # 한국은 이미 9/19 16:05
        assert now_seoul(at) == dt.datetime(2026, 9, 19, 16, 5)
        assert ds.days_to_shift(THREADS, now_seoul(at)) == 7
        assert ds.days_to_shift(THREADS, at.replace(tzinfo=None)) == 0   # 옛 길 — 증상 그대로
    finally:
        os.environ.pop("TZ", None)
        time.tzset()
