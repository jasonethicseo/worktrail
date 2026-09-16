"""확장 37호 (2026-09-07) — 단계는 차례(owner) 그 자체다. 추정하지 않는다.

31호는 단계를 활동 시각·worktree 바인딩으로 도출했다. 실사용 데이터(열린 스레드 10개, 2026-09-07)가 그 규칙이
한 일을 보여 줬다: owner 가 있는 7개는 전부 정확했고, "진행" 으로 뜬 2개는 owner 가 빠진 것뿐이었다. 즉 시간 문턱과
바인딩 조건은 owner 누락을 덮어 주는 역할만 했고, 그렇게 가려진 스레드(#446 — 문장은 "사용자가 판정한다")가 정작
사람이 봐야 할 것이었다. 사용자: "codex가 진짜 돌아가고 있는것처럼 느껴지는데".

그래서 축을 하나로 둔다 — **다음 손이 누구 것인가.** 선언에 있으면 그대로 쓰고, 없으면 없다고 말한다(미정).
mine(내 차례) · agent(에이전트에게) · watch(관찰 중) · unset(미정) · closed(종료).
"""
from __future__ import annotations

MINE, AGENT, WATCH, UNSET, CLOSED = "mine", "agent", "watch", "unset", "closed"
THREAD_PHASES = (MINE, AGENT, WATCH, UNSET, CLOSED)
TOPIC_ACTIVE, TOPIC_QUIET, TOPIC_DONE = "active", "quiet", "done"


def thread_phase(state: str, owner: str | None) -> str:
    """선언된 차례를 그대로 옮긴다. 시간도 바인딩도 보지 않는다."""
    if state != "open":
        return CLOSED
    if owner == "user":
        return MINE
    if owner == "watch":
        return WATCH
    return AGENT if owner else UNSET


def topic_phase(open_phases: list[str]) -> str:
    """열린 스레드가 없으면 완료. 하나라도 손이 걸려 있으면(내 차례·에이전트·미정) 진행, 전부 관찰이면 휴면."""
    if not open_phases:
        return TOPIC_DONE
    return TOPIC_ACTIVE if any(p in (MINE, AGENT, UNSET) for p in open_phases) else TOPIC_QUIET
