"""케이스 목록 vitals — 확장 12호 (2026-09-03). 목록이 "훑는 층"이 되기 위한 행별 집계.

HARVEST_V2 B1~B4. 읽기 전용 — 원장·12테이블을 바꾸지 않고, 쿼리 한 번의 조인/집계다.
정의(프론트·MCP 가 같은 뜻으로 읽는다):
  turn_count        가장 큰 turn.sequence — 브리프 latestTurn · 레코드 asOfTurn 과 같은 셈법
                    (abandoned 도 번호는 차지한다). 턴이 없으면 0.
  updated_at        max(case.created_at, 턴의 finished_at|created_at, 레코드의 finished_at|created_at).
                    "마지막 활동" — 목록 정렬 키.
  record_count      status='created' 인 레코드 판수. 실패 판은 세지 않는다.
  last_turn_status  abandoned 를 뺀 마지막 턴(sequence 최대)의 상태:
                    pending | failed | finalized | None(턴 없음).
                    failed = answer_status='failed' 또는 evidence 없이 끝난 web_lookup
                    (프론트가 "Web lookup returned no evidence" 로 보이는 것과 같은 판정).
"""
from __future__ import annotations

from typing import Any

EMPTY: dict[str, Any] = {"turn_count": 0, "updated_at": None, "record_count": 0, "last_turn_status": None}

_SQL = """
SELECT c.id AS case_id,
       COALESCE(t.turn_count, 0)                                            AS turn_count,
       MAX(c.created_at, COALESCE(t.last_at, 0), COALESCE(r.last_at, 0))    AS updated_at,
       COALESCE(r.record_count, 0)                                          AS record_count,
       lt.status AS lt_status, lt.answer_status AS lt_answer,
       lt.turn_kind AS lt_kind, lt.evidence_id AS lt_evidence_id
FROM "case" c
LEFT JOIN (SELECT case_id, MAX(sequence) AS turn_count,
                  MAX(COALESCE(finished_at, created_at)) AS last_at
           FROM turn GROUP BY case_id) t ON t.case_id = c.id
LEFT JOIN (SELECT case_id, SUM(status = 'created') AS record_count,
                  MAX(COALESCE(finished_at, created_at)) AS last_at
           FROM record GROUP BY case_id) r ON r.case_id = c.id
LEFT JOIN turn lt ON lt.id = (SELECT x.id FROM turn x
                              WHERE x.case_id = c.id AND x.status != 'abandoned'
                              ORDER BY x.sequence DESC, x.id DESC LIMIT 1)
WHERE c.user_id = ?
"""


def _last_turn_status(row: Any) -> str | None:
    if row["lt_status"] is None:
        return None
    if row["lt_status"] == "pending":
        return "pending"
    if row["lt_answer"] == "failed":
        return "failed"
    if row["lt_kind"] == "web_lookup" and row["lt_evidence_id"] is None:
        return "failed"
    return "finalized"


def case_vitals(db, user_id: int) -> dict[int, dict[str, Any]]:
    """case_id → {turn_count, updated_at, record_count, last_turn_status}. 사용자의 모든 케이스."""
    with db._lock:
        rows = db.conn.execute(_SQL, (user_id,)).fetchall()
    return {
        row["case_id"]: {
            "turn_count": int(row["turn_count"]),
            "updated_at": int(row["updated_at"]),
            "record_count": int(row["record_count"]),
            "last_turn_status": _last_turn_status(row),
        }
        for row in rows
    }
