"""호스트 안내문 상한 (2026-09-03). Claude Code 는 MCP 서버 instructions 를 2,048자(코드포인트)에서 자른다 —
case 442 Evidence #1170: 원문 2,774자 중 정확히 2,048자까지 전달, 뒤 726자(읽기 층 계약) 탈락.
한 번 측정한 값이지만 정확히 2^11 에서 잘렸다. "guarantees belong in code": 상한은 테스트가 지킨다."""
from casebook.adapters.mcp_server import INSTRUCTIONS

CLAUDE_CODE_INSTRUCTIONS_CAP = 2048


def test_안내문이_claude_code_상한_안이다():
    assert len(INSTRUCTIONS) <= CLAUDE_CODE_INSTRUCTIONS_CAP, len(INSTRUCTIONS)


def test_안내문에_핵심_규칙이_남아_있다():
    # 잘라내며 잃으면 안 되는 문장들 — 캡처 규칙 · 해석/증거 분리 · handoff 조건 · durable state 경계 · 읽기 층 한 줄
    for must in ("there is no turn", "not evidence", "handoff only when the engineer asks",
                 "what could overturn it", "scope where that holds", "retrieval, not conclusion"):
        assert must in INSTRUCTIONS, must
