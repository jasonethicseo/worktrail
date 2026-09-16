"""리플레이 — 실사용 568턴의 이벤트 시퀀스를 재현한다.

Xano 워크스페이스 2026-08-25 덤프에서 뽑았다. 2026-08-22 불변식 12 개정 이후
세대만 쓴다(그 전 모양은 현행 코드가 낼 수 없다).
"""
from __future__ import annotations
from collections import Counter
import pytest
from tests.drive import drive

DRIVE_BY_KIND = {"input": dict(kind="input"), "web_lookup": dict(kind="web_lookup"),
                 "recovery": dict(kind="recovery")}


def test_코퍼스가_충분히_크다(replay_turns):
    assert len(replay_turns) >= 500, f"기대값 턴이 {len(replay_turns)}개뿐이다"


def test_기대_시퀀스가_현행_세대_모양뿐이다(replay_turns):
    sigs = Counter(tuple(t["expected_events"]) for t in replay_turns)
    assert len(sigs) <= 10, f"시퀀스 종류가 {len(sigs)}종 — 세대가 섞였을 수 있다"


@pytest.mark.parametrize("kind", sorted(DRIVE_BY_KIND))
def test_경로별_대표_시퀀스_재현(app, replay_turns, kind):
    """각 turn_kind 의 최빈 시퀀스를 재현한다. 전수는 test_전수_재현 에서."""
    same = [t for t in replay_turns if t["turn_kind"] == kind and "record_requested" not in t["expected_events"]]
    if not same:
        pytest.skip(f"{kind} 턴이 코퍼스에 없다")
    expected = Counter(tuple(t["expected_events"]) for t in same).most_common(1)[0][0]
    assert drive(app, **DRIVE_BY_KIND[kind]) == list(expected)


@pytest.mark.slow
def test_전수_재현(app, replay_turns):
    """568턴 전수. 느리므로 -m slow 로 따로 돌린다."""
    mismatch = []
    for t in replay_turns:
        cfg = DRIVE_BY_KIND.get(t["turn_kind"])
        if not cfg:
            continue
        got = drive(app, expected_events=t["expected_events"], **cfg)
        if got != t["expected_events"]:
            mismatch.append((t["turn_id"], t["expected_events"], got))
    assert not mismatch, f"{len(mismatch)}턴 불일치. 첫 3건: {mismatch[:3]}"
