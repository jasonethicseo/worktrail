"""원장 이벤트 명세 — Xano `.xs` 전수에서 실측으로 뽑았다(2026-08-26).

이 목록은 상태 기계의 명세다. 이식 중에 여기에 없는 이벤트를 만들거나
있는 이벤트를 빠뜨리면 오라클(골든·리플레이)이 잡는다.
"""

# 어떤 컴포넌트가 어떤 이벤트를 쓰는가 — .xs 실측. 이식의 배치도다.
EVENT_WRITERS: dict[str, tuple[str, ...]] = {
    # 확장 4호(2026-09-03): MCP 문의 external 턴 — 답변 호출 없이 같은 이벤트 어휘를 쓴다.
    "user_message":                ("api:post_case_turn", "mcp:external_turn"),
    "canonical_evidence_created":  ("api:post_case_turn", "fn:web_lookup_worker", "mcp:external_turn"),
    "turn_status_changed":         ("api:post_case_turn", "api:post_case_web_lookup",
                                    "api:post_case_recovery", "fn:turn_worker", "fn:web_lookup_worker",
                                    "mcp:external_turn", "fn:external_turn_worker"),
    "answer_created":              ("fn:turn_worker", "fn:external_turn_worker"),
    "answer_leak_suspected":       ("fn:turn_worker",),
    "investigation_brief_updated": ("fn:turn_worker", "fn:external_turn_worker"),
    "brief_rejected":              ("fn:turn_worker", "fn:external_turn_worker"),
    "model_call":                  ("fn:turn_worker", "fn:draft_query", "fn:external_turn_worker"),
    "web_lookup_requested":        ("api:post_case_web_lookup",),
    "web_lookup_completed":        ("fn:web_lookup_worker",),
    "web_lookup_failed":           ("fn:web_lookup_worker",),
    "record_requested":            ("api:post_case_record",),
    "record_created":              ("fn:record_worker",),
    "record_failed":               ("fn:record_worker",),
    "recovery_requested":          ("api:post_case_recovery",),
    "case_status_changed":         ("api:post_case_status",),
    # 확장 10호(2026-09-03): durable state — 원장 이벤트로만 쌓고 resume 이 projection 한다.
    "decision_recorded":           ("mcp:decide",),
    "constraint_recorded":         ("mcp:constrain",),
    "term_defined":                ("mcp:define",),          # 확장 55호 — 지은 이름에 뜻 한 줄
    "hypothesis_ruled_out":        ("mcp:rule_out",),
}

EVENT_TYPES: frozenset[str] = frozenset(EVENT_WRITERS)

# 원장에 한 번도 발화하지 않은 이벤트. 정의는 있으나 관측 0건 —
# 원장 7,858행 기준(2026-08-26). 이식 후에도 0이면 정상이다.
NEVER_OBSERVED: frozenset[str] = frozenset({"answer_leak_suspected"})

# 기록자 호출이 낼 수 있는 brief 거부 사유. 답변 호출은 tools 가 없으므로
# `brief_absent` 는 현행 코드가 낼 수 없다(2026-08-25 죽은 블록 제거).
BRIEF_REJECT_REASONS: frozenset[str] = frozenset({
    "brief_decode_failed", "brief_bad_keys", "brief_bad_focus", "brief_bad_evidence",
    "brief_bad_considering", "brief_bad_recent_updates", "brief_bad_next_up",
})
