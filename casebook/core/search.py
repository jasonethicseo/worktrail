"""읽기 층 1 — 케이스북 검색 (확장 9호, 2026-09-03). "다른 케이스의 과거가 지금의 나를 돕는가."

계약 (서버 instructions·테스트에 같은 문장):
  1. search 는 evidence 만 돌려준다 — 과거의 결론(답변·브리프·레코드)은 기본 반환하지 않는다.
  2. inspect 는 저장된 원문과 출처를 그대로 돌려준다 — 재요약하지 않는다.
  3. 검색 결과가 현재 문제와 같다고 추론하지 않는다 — similarity is retrieval, not conclusion.

구현: sqlite FTS5 가상 테이블 `evidence_fts` (12테이블 밖의 파생 색인). evidence 는 append-only 라
INSERT 트리거 하나로 동기화된다. 소유자 범위: case.user_id 로 조인 — 남의 evidence 는 절대 나오지 않는다.
"""
from __future__ import annotations

import re
from typing import Any

from .errors import InputError, NotFoundError

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(content, content='evidence', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS evidence_fts_ai AFTER INSERT ON evidence BEGIN
  INSERT INTO evidence_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def ensure_index(db) -> None:
    """색인이 없으면 만들고 기존 evidence 로 채운다. 있으면 no-op."""
    with db._lock:
        exists = db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='evidence_fts'"
        ).fetchone()
        db.conn.executescript(_FTS_DDL)
        if not exists:
            db.conn.execute("INSERT INTO evidence_fts(evidence_fts) VALUES ('rebuild')")


_TOKEN = re.compile(r"[\w.\-:/#]+", re.UNICODE)


def _fts_query(query: str) -> str:
    # 사용자 문자열을 FTS5 문법에 노출하지 않는다 — 토큰마다 큰따옴표로 감싸 AND 로 잇는다.
    toks = [t.replace('"', '""') for t in _TOKEN.findall(query)]
    if not toks:
        raise InputError("query is empty")
    return " ".join(f'"{t}"' for t in toks)


def search_evidence(db, user_id: int, query: str, limit: int = 10,
                    include_archived: bool = False) -> list[dict[str, Any]]:
    ensure_index(db)
    limit = max(1, min(int(limit), 50))
    # archived 케이스(스모크·폐기)는 기본 제외 — 색인에는 남아 있고 플래그로 포함한다
    archived_clause = "" if include_archived else " AND c.status != 'archived'"
    sql = """
    SELECT e.id AS evidence_id, e.case_id, c.title AS case_title, e.kind, e.created_at, e.source,
           t.sequence AS turn,
           snippet(evidence_fts, 0, '[', ']', ' … ', 24) AS excerpt
    FROM evidence_fts
    JOIN evidence e ON e.id = evidence_fts.rowid
    JOIN "case" c ON c.id = e.case_id
    LEFT JOIN turn t ON t.id = e.turn_id
    WHERE evidence_fts MATCH ? AND c.user_id = ?{archived}
    ORDER BY bm25(evidence_fts), e.id DESC
    LIMIT ?
    """
    with db._lock:
        rows = db.conn.execute(sql.replace("{archived}", archived_clause), (_fts_query(query), user_id, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["source"] = db._decode("evidence", r)["source"] if r["source"] else None
        out.append(d)
    return out


def inspect_evidence(db, user_id: int, evidence_id: int) -> dict[str, Any]:
    ev = db.get("evidence", evidence_id)
    case = db.get("case", ev["case_id"]) if ev else None
    if ev is None or case is None or case["user_id"] != user_id:
        raise NotFoundError("evidence not found")
    turn = db.get("turn", ev["turn_id"]) if ev["turn_id"] else None
    return {
        "evidence_id": ev["id"], "case_id": case["id"], "case_title": case["title"],
        "turn": turn["sequence"] if turn else None, "kind": ev["kind"],
        "created_at": ev["created_at"], "source": ev["source"],
        "content": ev["content"],  # byte-exact — 재요약·정리 없음
    }
