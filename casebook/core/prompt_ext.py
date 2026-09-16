"""프롬프트 확장 — `.xs` 추출본(prompts.py, 바이트 동일 오라클)은 건드리지 않고 그 위에 덮어쓴다.

덮어쓰기는 OVERLAY 의 (원문, 대체) 쌍으로만 한다. 원문은 추출본에 정확히 한 번 있어야 하고
(assert), 대체를 되돌리면 추출본이 나와야 한다(tests/test_prompt_ext.py). `.xs` 를 재추출해
원문 줄이 바뀌면 여기가 먼저 깨진다 — 그때 덮어쓰기를 다시 검토한다.

확장 5호 (2026-09-03) — "What we got wrong, and when":
  각 정정 줄은 Timeline 처럼 "turn N:" 으로 시작하고 끝에 그 이해를 뒤집은 Evidence #n 을 단다.
  정정의 정의를 "이전 진술이 뒤집힌 것"으로 좁힌다(확인·범위 축소는 정정이 아니다).
  근거: Xano 시절 116개 레코드 중 이 절에 근거가 있던 건 26개(8/26 이후 <10%). case 345 v1 0 / v2 4.

확장 8호 (2026-09-03) — 밀도: facts 는 다른 절이 기대는 것만 한 줄씩, 같은 evidence 는 합침, Timeline 재서술 금지.
  "never drop items" 가 사실 절을 전사로 부풀렸다(442 v4: 11턴에 facts 26줄). 사용자 판정 "레코드가 많아".

확장 7호 (2026-09-03) — "Actions and outcomes" 절을 "Established facts" 로 흡수 (7절 → 6절):
  행한 조치와 그 결과(효과 없던 것 포함)도 evidence 가 붙은 사실이다. 절 하나 덜 읽고 정보는 안 빠진다.
  Timeline 이 턴별 행동을 어차피 남기므로 이중 기록도 준다. 프론트는 절 이름을 하드코딩하지 않는다.
"""
from __future__ import annotations

from . import prompts

OVERLAY: tuple[tuple[str, str], ...] = (
    # ── 5호 ──
    (
        "## What we got wrong, and when — every correction of an earlier understanding, "
        "with the turn where it flipped. If none, say so.",
        "## What we got wrong, and when — every correction of an earlier understanding. "
        "Each entry starts with 'turn N:' (the turn where it flipped) and ends, on the same line, "
        "with the Evidence #n that overturned it. A correction is an earlier statement that was later "
        "reversed; a confirmation, a narrowing of scope, or new information that did not contradict "
        "anything is not a correction. If none, say so.",
    ),
    # ── 7호 ──
    (
        "Output markdown with exactly these seven sections, in this order, as level-2 headings:",
        "Output markdown with exactly these six sections, in this order, as level-2 headings:",
    ),
    (
        "## Established facts — each fact followed by its Evidence #n reference. Only what the "
        "investigator reported or web evidence showed.\n"
        "## Ruled out — hypotheses set aside and the evidence that set them aside.\n"
        "## Actions and outcomes — what was done and what happened, including actions that had no effect.\n",
        "## Established facts — each fact followed by its Evidence #n reference. Only what the "
        "investigator reported or web evidence showed. Actions taken and their outcomes are facts too: "
        "include them here, including actions that had no effect, each with its Evidence #n. "
        "Hard limit: at most one line per Evidence #n — merge everything that evidence supports into "
        "that one line, and keep only what the other sections rely on. Do not restate the timeline here.\n"
        "## Ruled out — hypotheses set aside and the evidence that set them aside.\n",
    ),
    # ── 8호: 밀도 — "never drop items" 가 사실 절을 전사로 부풀렸다(442 v4: 11턴에 facts 26줄) ──
    (
        "Density rule: when the case is long, shorten sentences, never drop items.",
        "Density rule: the record is an index for someone who will look things up, not a transcript to "
        "read top to bottom. Keep 'Where this stands' and 'Open questions' complete; everywhere else "
        "prefer one short line per item, and never drop a fact that another section relies on.",
    ),
)


def apply(text: str, overlay: tuple[tuple[str, str], ...] = OVERLAY) -> str:
    for old, new in overlay:
        assert text.count(old) == 1, f"덮어쓸 원문이 추출본에 정확히 한 번 있어야 한다: {old[:60]!r}"
        text = text.replace(old, new)
    return text


RECORD_SYSTEM = apply(prompts.RECORD_SYSTEM)
