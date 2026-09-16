"""확장 62호 — 스레드 크기(D14754, #511): 결정 → 구현이 이어지면 같은 스레드다. 규칙은 문구로만 둔다 —
사용자 2026-09-12: 쪼개기는 턴 수가 아니라 뜻의 문제이고, 확인해 보니 대체로 정당했으니(7개 중 2개만 잘못) 힌트·측정은 두지 않는다(A).
보장할 것: 안내문·도구 설명에서 "20-minute fix" 가 사라지고 재선언 길이 적혀 있다."""
from __future__ import annotations

from casebook.adapters.mcp_server import INSTRUCTIONS


def test_안내문이_쪼개기를_부추기지_않는다():
    assert "20-minute" not in INSTRUCTIONS
    assert "re-declare focus" in INSTRUCTIONS and "only when the question changes" in INSTRUCTIONS
