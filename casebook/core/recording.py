"""모델 없이 외부 관찰과 호스트의 결론을 원장에 기록한다."""
from __future__ import annotations


def external_turn_worker(db, turn_id: int, note: str | None = None, note_kind: str | None = None,
                         refs: list[int] | None = None) -> None:
    """확장 4호 — MCP 문의 턴. 추론은 호스트(Claude Code 등)가 이미 했으므로 답변 호출이 없다.

    note 가 있으면 호스트의 결론을 assistant 메시지로 저장한다
    (불변식 12 는 "답변 호출은 호스트에서 일어났다"로 읽는다). note 가 없으면 evidence 만 남긴다.

    확장 39호 (2026-09-08) — **이 길에서는 모델을 부르지 않는다.** 예전에는 note 마다 기록자를
    돌려 brief 를 갱신했는데, 재 보니 기록 화면(현황·회고·스레드·훅)에서 brief 를 쓰는 곳은
    hookctx 의 폴백 한 줄뿐이었다. 안 읽는 요약을 만드느라 기록이 매번 LLM 을 기다렸고, 그
    탓에 기록 도구가 모델 키 없이는 온전히 돌지 않았다. brief 는 조사 도구(turn_worker)의
    물건으로 남긴다 — 그쪽은 그대로다.
    """
    turn = db.get("turn", turn_id)
    if turn is None or turn["status"] != "pending":
        return  # 진입 클레임 (불변식 14)
    case_id = turn["case_id"]

    if not (note or "").strip():
        claimed = db.edit_where(
            "turn", turn_id, {"status": "pending"},
            {"status": "finalized", "answer_status": "not_attempted", "finished_at": db.now_ms()},
        )
        if claimed:
            db.add("ledger", {
                "case_id": case_id, "turn_id": turn_id,
                "event_type": "turn_status_changed",
                "payload": {"to": "finalized", "answer_status": "not_attempted"},
            })
        return

    amsg = db.add("message", {
        "case_id": case_id, "turn_id": turn_id, "role": "assistant", "content": note,
    })
    db.add("ledger", {
        "case_id": case_id, "turn_id": turn_id,
        "event_type": "answer_created",
        # 확장 60호 — 노트의 갈래(D14720): 바꿈·확인·발견·생각. 에이전트가 붙인 것만 남기고, 옛 노트는 칸이 없다.
        # 확장 123호 — refs: 결론이 기대는 증거 번호. 회고·타임라인이 링크로 그린다(D15872).
        "payload": {"message_id": amsg["id"], "source": "external", **({"kind": note_kind} if note_kind else {}),
                    **({"refs": [int(i) for i in refs]} if refs else {})},
    })
    claimed = db.edit_where(
        "turn", turn_id, {"status": "pending"},
        {
            "status": "finalized", "answer_status": "stored",
            "assistant_message_id": amsg["id"], "finished_at": db.now_ms(),
        },
    )
    if not claimed:
        return
    db.add("ledger", {
        "case_id": case_id, "turn_id": turn_id,
        "event_type": "turn_status_changed",
        "payload": {"to": "finalized", "answer_status": "stored"},
    })


