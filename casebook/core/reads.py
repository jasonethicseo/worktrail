"""읽기 기록 — 확장 11호 (2026-09-03). "기록이 실제로 다시 읽히는가"를 재는 유일한 장치.

원장은 조사 기록이라 여기 섞지 않는다. 12테이블 밖의 파생 테이블 `read_log` 하나:
  kind      search | inspect | resume | handoff | handoff_refused(engineer_asked 없이 부름) | evidence_refused(요약문)
  case_id   inspect·resume·handoff 는 대상 케이스, search 는 NULL(케이스 횡단)
  detail    search 의 query, resume 의 mode, inspect 의 evidence_id
  hits      search 의 결과 수 (0 이면 헛검색 — 이것도 신호다)
report() 가 GPT 와 합의한 지표를 낸다: 만든 케이스 · 다시 열린 케이스 · 횡단 검색 · 적중 검색 ·
inspect 된 evidence · 한 번도 안 읽힌 케이스.
"""
from __future__ import annotations

from typing import Any

_DDL = """
CREATE TABLE IF NOT EXISTS read_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  case_id INTEGER,
  detail TEXT,
  hits INTEGER
);
CREATE INDEX IF NOT EXISTS ix_read_log_user ON read_log(user_id, kind);
CREATE TABLE IF NOT EXISTS handoff_review (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  case_id INTEGER,
  constraints_retained INTEGER,
  decisions_retained INTEGER,
  duplicate_investigation INTEGER,
  wrong_next_step INTEGER,
  re_explained INTEGER,
  retrieval_used INTEGER,
  retrieval_changed_action INTEGER,
  note TEXT
);
"""

# 확장 19호 — 측정 v2. KPI 는 "만든 케이스 수"가 아니라 handoff 기회를 분모로 한 continuity 다.
#   read_log kind='session_start' (detail = startup|resume|clear|compact|fork, case_id = 그때 worktree 에 바인딩된 스레드)
#   handoff_review: 사람이 handoff 하나를 판정한 것 — 에이전트가 자기 점수를 매기지 않는다(authority 는 언제나 사람).
REVIEW_FIELDS = ("constraints_retained", "decisions_retained", "duplicate_investigation", "wrong_next_step",
                 "re_explained", "retrieval_used", "retrieval_changed_action")


def ensure(db) -> None:
    with db._lock:
        db.conn.executescript(_DDL)


def log(db, user_id: int, kind: str, case_id: int | None = None, detail: str | None = None,
        hits: int | None = None) -> None:
    ensure(db)
    with db._lock:
        db.conn.execute(
            "INSERT INTO read_log (created_at, user_id, kind, case_id, detail, hits) VALUES (?, ?, ?, ?, ?, ?)",
            (db.now_ms(), user_id, kind, case_id, detail, hits),
        )


def review_handoff(db, user_id: int, case_id: int | None, **fields: Any) -> dict[str, Any]:
    """사람의 판정 하나. bool 은 0/1, re_explained 는 횟수. 빠진 칸은 NULL(모름)."""
    ensure(db)
    unknown = set(fields) - set(REVIEW_FIELDS) - {"note"}
    if unknown:
        raise ValueError(f"unknown review fields: {sorted(unknown)}")
    vals = {k: (None if fields.get(k) is None else (int(fields[k]) if k == "re_explained" else int(bool(fields[k]))))
            for k in REVIEW_FIELDS}
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO handoff_review (created_at, user_id, case_id, " + ", ".join(REVIEW_FIELDS) + ", note) VALUES (?, ?, ?, "
            + ", ".join("?" for _ in REVIEW_FIELDS) + ", ?)",
            (db.now_ms(), user_id, case_id, *[vals[k] for k in REVIEW_FIELDS], fields.get("note")))
        db.conn.commit()
        return {"review_id": cur.lastrowid, "case_id": case_id, **vals}


def report(db, user_id: int, since_ms: int | None = None) -> dict[str, Any]:
    ensure(db)
    since = since_ms or 0
    q = lambda sql, *a: db.conn.execute(sql, a).fetchone()[0]  # noqa: E731
    with db._lock:
        session_starts = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='session_start' AND created_at>=?", user_id, since)
        compact_restarts = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='session_start' AND detail='compact' AND created_at>=?", user_id, since)
        # handoff 기회 = 압축이 아닌 세션 시작에 worktree 가 스레드에 바인딩돼 있던 횟수(전달 신뢰성의 분모)
        handoff_opportunities = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='session_start' AND detail!='compact' "
                                  "AND case_id IS NOT NULL AND created_at>=?", user_id, since)
        reviews = q("SELECT COUNT(*) FROM handoff_review WHERE user_id=? AND created_at>=?", user_id, since)
        review_sums = db.conn.execute(
            "SELECT " + ", ".join(f"SUM({k})" for k in REVIEW_FIELDS) + ", " + ", ".join(f"COUNT({k})" for k in REVIEW_FIELDS)
            + " FROM handoff_review WHERE user_id=? AND created_at>=?", (user_id, since)).fetchone()
        cases_created = q("SELECT COUNT(*) FROM 'case' WHERE user_id=? AND created_at>=?", user_id, since)
        cases_resumed = q("SELECT COUNT(DISTINCT case_id) FROM read_log WHERE user_id=? AND kind='resume' AND created_at>=?", user_id, since)
        resumes = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='resume' AND created_at>=?", user_id, since)
        searches = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='search' AND created_at>=?", user_id, since)
        searches_hit = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='search' AND hits>0 AND created_at>=?", user_id, since)
        inspected = q("SELECT COUNT(DISTINCT detail) FROM read_log WHERE user_id=? AND kind='inspect' AND created_at>=?", user_id, since)
        handoffs = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='handoff' AND created_at>=?", user_id, since)
        handoffs_refused = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='handoff_refused' AND created_at>=?", user_id, since)
        evidence_refused = q("SELECT COUNT(*) FROM read_log WHERE user_id=? AND kind='evidence_refused' AND created_at>=?", user_id, since)
        never_read = q(
            "SELECT COUNT(*) FROM 'case' c WHERE c.user_id=? AND c.created_at>=? AND NOT EXISTS "
            "(SELECT 1 FROM read_log r WHERE r.user_id=c.user_id AND r.case_id=c.id)", user_id, since)
    n = len(REVIEW_FIELDS)
    review = {}
    for i, k in enumerate(REVIEW_FIELDS):
        total, answered = review_sums[i] or 0, review_sums[n + i] or 0
        review[k] = {"sum": total, "of": answered}
    return {
        # continuity — 이것이 KPI 다 (분모: handoff 기회)
        "session_starts": session_starts, "compact_restarts": compact_restarts,
        "handoff_opportunities": handoff_opportunities,
        "cases_resumed": cases_resumed, "resumes": resumes,
        "handoff_reviews": reviews, "review": review,
        # retrieval — 별도 가설. 0 이어도 continuity 의 실패가 아니다
        "cross_case_searches": searches, "searches_with_hit": searches_hit,
        "evidence_inspected": inspected, "handoffs": handoffs, "handoffs_refused": handoffs_refused,
        "evidence_refused": evidence_refused,
        # KPI 아님 — 참고
        "cases_created": cases_created, "cases_never_read": never_read,
    }
