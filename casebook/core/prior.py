"""설치 전 기록 — 확장 42호 (2026-09-08). 깔기 전에 한 작업을 저장소 이력으로만 들인다.

왜: 설치 첫날 화면이 비어 있는 것이 casebook 의 약점이었다. 세션 로그는 이미 디스크에 있으니 끌어온다.
어디까지: **스레드도 주제도 결정도 만들지 않는다**(C13615·C13608). 여기 들어오는 것은 사람이 실제로 친 말의
원문과, 그것이 언제 어느 세션에 있었는지뿐이다. focus·목표·결정을 추론해 채우면 뒤집힌 안이 유효한 결정으로
되살아난다 — 제로샷 실험에서 실제로 관측한 실패다(case 482).

에이전트의 말은 들이지 않는다. 검색이 과거의 결론을 돌려주지 않는다는 계약(search.py 1번)과 같은 이유다.
원문 식별자(source_path·external_id·ref)는 지금 보존한다 — 나중에 "결정에서 그 순간의 원문으로" 내려갈 재료다(C13616).

서버는 사용자 디스크를 볼 수 없다. 읽고 고르는 일은 클라이언트(adapters/backfill.py)가 하고 여기는 받아 적기만 한다.
"""
from __future__ import annotations

import re
from typing import Any

from .errors import InputError, NotFoundError

SOURCES = ("claude-code", "codex")
LEAD_MIN = 15        # 목록 단서로 쓸 말의 최소 길이 — "run" 같은 한 마디로는 세션을 고를 수 없다

SCHEMA = """
CREATE TABLE IF NOT EXISTS prior_session (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  repo_id INTEGER NOT NULL,
  source TEXT NOT NULL,
  external_id TEXT NOT NULL,
  source_path TEXT,
  cwd TEXT,
  branch TEXT,
  started_at INTEGER,
  ended_at INTEGER,
  instructions INTEGER NOT NULL DEFAULT 0,
  imported_at INTEGER NOT NULL,
  UNIQUE (user_id, repo_id, source, external_id)
);
CREATE INDEX IF NOT EXISTS ix_prior_session_repo ON prior_session(user_id, repo_id, started_at);
CREATE TABLE IF NOT EXISTS prior_instruction (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  seq INTEGER NOT NULL,
  at INTEGER,
  ref TEXT,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_prior_instruction_session ON prior_instruction(session_id, seq);
CREATE VIRTUAL TABLE IF NOT EXISTS prior_fts USING fts5(text, content='prior_instruction', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS prior_fts_ai AFTER INSERT ON prior_instruction BEGIN
  INSERT INTO prior_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS prior_fts_ad AFTER DELETE ON prior_instruction BEGIN
  INSERT INTO prior_fts(prior_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


def ensure(db) -> None:
    with db._lock:
        db.conn.executescript(SCHEMA)


def _now() -> int:
    import time
    return int(time.time() * 1000)


def cutoff_for(db, user_id: int, repo_id: int) -> int | None:
    """이 저장소에서 Worktrail 이 처음 기록한 때. 그 뒤는 native 기록이 있으므로 들이지 않는다 —
    안 그러면 같은 내용이 증거와 설치 전 기록으로 두 번 보인다(이 저장소 실측: 순증 0건)."""
    with db._lock:
        row = db.conn.execute(
            'SELECT MIN(t.created_at) AS at FROM thread t JOIN "case" c ON c.id = t.case_id'
            " WHERE t.repo_id=? AND c.user_id=?", (repo_id, user_id)).fetchone()
    return row["at"] if row and row["at"] else None


def _validate(sessions: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """쓰기 전에 전부 본다 — 절반 쓰고 실패하면 지운 기록이 돌아오지 않는다."""
    if not isinstance(sessions, list):
        raise InputError("sessions must be a list")
    out = []
    for s in sessions:
        if not isinstance(s, dict):
            raise InputError("each session must be an object")
        source = (s.get("source") or "").strip()
        external = (s.get("external_id") or "").strip()
        if source not in SOURCES:
            raise InputError(f"unknown source {source!r} — one of {', '.join(SOURCES)}")
        if not external:
            raise InputError("external_id is required — 원문을 다시 찾을 수 있어야 한다")
        items = []
        for i in (s.get("instructions") or []):
            if not isinstance(i, dict):
                raise InputError("each instruction must be an object")
            text = i.get("text")
            if not isinstance(text, str):
                raise InputError("instruction text must be a string")
            if not text.strip():
                continue
            ref = i.get("ref")
            at = i.get("at")
            if ref is not None and not isinstance(ref, (str, int)):
                raise InputError("instruction ref must be a string, a number, or absent")
            if at is not None and not isinstance(at, (int, float)):
                raise InputError("instruction at must be a number or absent")
            items.append({"text": text, "ref": None if ref is None else str(ref),
                          "at": None if at is None else int(at)})
        out.append((s, items))
    return out


def import_sessions(db, user_id: int, repo_id: int, sessions: list[dict[str, Any]],
                    reset: bool = False, cutoff: int | None = None) -> dict[str, Any]:
    """클라이언트가 고른 세션을 그대로 적는다. 요청 하나가 통째로 되거나 통째로 안 된다.

    reset=True 면 그 저장소의 설치 전 기록을 먼저 비운다(클라이언트가 첫 조각에만 단다).

    session = {source, external_id, source_path?, cwd?, branch?, started_at?, ended_at?, append?,
               instructions: [{at?, ref?, text}]}
    text 는 사람이 친 말 원문이다 — 여기서 다듬지 않는다.
    append=True 는 큰 세션의 이어지는 조각이다 — 지우지 않고 뒤에 붙인다. 같은 ref 가 이미 있으면
    건너뛰므로 조각을 다시 보내도 늘지 않는다. 다시 돌리면 첫 조각이 지우고 다시 쓰므로 결과는 같다.
    """
    ensure(db)
    checked = _validate(sessions)
    if cutoff:
        # native 기록이 시작된 뒤의 말은 이미 casebook 안에 있다 — 들이지 않는다.
        checked = [(s, [i for i in items if i["at"] is None or i["at"] < cutoff]) for s, items in checked]
    added = updated = skipped = stored = 0
    now = _now()
    with db.transaction():
        if reset:
            db.conn.execute(
                "DELETE FROM prior_instruction WHERE session_id IN"
                " (SELECT id FROM prior_session WHERE user_id=? AND repo_id=?)", (user_id, repo_id))
            db.conn.execute("DELETE FROM prior_session WHERE user_id=? AND repo_id=?", (user_id, repo_id))
        for s, items in checked:
            if not items:
                skipped += 1                      # 사람이 친 말이 없는 세션은 들이지 않는다
                continue
            source, external = s["source"].strip(), s["external_id"].strip()
            row = db.conn.execute(
                "SELECT id FROM prior_session WHERE user_id=? AND repo_id=? AND source=? AND external_id=?",
                (user_id, repo_id, source, external)).fetchone()
            fields = (s.get("source_path"), s.get("cwd"), s.get("branch"),
                      s.get("started_at"), s.get("ended_at"))
            append = bool(s.get("append")) and row is not None
            if row is None:
                cur = db.conn.execute(
                    "INSERT INTO prior_session (user_id, repo_id, source, external_id, source_path, cwd, branch,"
                    " started_at, ended_at, instructions, imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (user_id, repo_id, source, external, *fields, 0, now))
                sid = cur.lastrowid
                added += 1
            else:
                sid = row["id"]
                if not append:
                    db.conn.execute("DELETE FROM prior_instruction WHERE session_id=?", (sid,))
                    db.conn.execute(
                        "UPDATE prior_session SET source_path=?, cwd=?, branch=?, started_at=?, ended_at=?"
                        " WHERE id=?", (*fields, sid))
                updated += 1
            have = db.conn.execute("SELECT COALESCE(MAX(seq),0) m FROM prior_instruction"
                                   " WHERE session_id=?", (sid,)).fetchone()["m"]
            seen = {r["ref"] for r in db.conn.execute(
                "SELECT ref FROM prior_instruction WHERE session_id=? AND ref IS NOT NULL", (sid,))}
            seq = have
            for i in items:
                if i["ref"] is not None and i["ref"] in seen:
                    continue                      # 같은 조각을 다시 받아도 늘지 않는다
                seq += 1
                stored += 1
                if i["ref"] is not None:
                    seen.add(i["ref"])
                db.conn.execute("INSERT INTO prior_instruction (session_id, seq, at, ref, text)"
                                " VALUES (?,?,?,?,?)", (sid, seq, i["at"], i["ref"], i["text"]))
            db.conn.execute("UPDATE prior_session SET instructions=(SELECT COUNT(*) FROM prior_instruction"
                            " WHERE session_id=?), imported_at=? WHERE id=?", (sid, now, sid))
    return {"added": added, "updated": updated, "skipped": skipped, "sessions": added + updated,
            "cutoff": cutoff, "stored_instructions": stored}


def summary(db, user_id: int, repo_id: int) -> dict[str, Any]:
    """저장소 한 줄 요약 — 화면과 세션 시작 블록이 쓴다."""
    ensure(db)
    with db._lock:
        r = db.conn.execute(
            "SELECT COUNT(*) AS sessions, COALESCE(SUM(instructions),0) AS instructions,"
            " MIN(started_at) AS first_at, MAX(COALESCE(ended_at, started_at)) AS last_at"
            " FROM prior_session WHERE user_id=? AND repo_id=?", (user_id, repo_id)).fetchone()
        by = db.conn.execute(
            "SELECT source, COUNT(*) AS n FROM prior_session WHERE user_id=? AND repo_id=? GROUP BY source",
            (user_id, repo_id)).fetchall()
    out = dict(r)
    out["by_source"] = {x["source"]: x["n"] for x in by}
    return out


def summary_all(db, user_id: int) -> dict[str, Any]:
    """현황 한 줄 — 저장소를 가리지 않고 이 사용자의 설치 전 기록 전부."""
    ensure(db)
    with db._lock:
        r = db.conn.execute(
            "SELECT COUNT(*) AS sessions, COALESCE(SUM(instructions),0) AS instructions,"
            " COUNT(DISTINCT repo_id) AS repos, MIN(started_at) AS first_at,"
            " MAX(COALESCE(ended_at, started_at)) AS last_at"
            " FROM prior_session WHERE user_id=?", (user_id,)).fetchone()
    return dict(r)


def list_sessions(db, user_id: int, repo_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """설치 전 세션 목록 — 최신 먼저. 첫 지시 한 줄만 곁들인다(요약이 아니라 원문의 앞부분)."""
    ensure(db)
    limit = max(1, min(int(limit), 100))
    with db._lock:
        rows = db.conn.execute(
            "SELECT * FROM prior_session WHERE user_id=? AND repo_id=?"
            " ORDER BY COALESCE(started_at, imported_at) DESC LIMIT ?", (user_id, repo_id, limit)).fetchall()
        out = []
        for r in rows:
            first = db.conn.execute(
                "SELECT text FROM prior_instruction WHERE session_id=? ORDER BY seq LIMIT 1", (r["id"],)).fetchone()
            d = dict(r)
            d["first_instruction"] = (first["text"][:200] if first else None)
            out.append(d)
    return out


_TOKEN = re.compile(r"[\w.\-:/#]+", re.UNICODE)


def _fts_query(query: str) -> str:
    """토큰마다 접두 일치로 잇는다. 한국어는 조사가 붙어 한 토큰이 되므로(unicode61 기준 "토큰은"이 통째로 하나)
    접두가 아니면 "토큰"으로 "토큰은"을 못 찾는다 — 2026-09-08 실측."""
    toks = [t.replace('"', '""') for t in _TOKEN.findall(query or "")]
    if not toks:
        raise InputError("query is empty")
    return " ".join(f'"{t}"*' for t in toks)


def search(db, user_id: int, query: str, limit: int = 10, repo_id: int | None = None) -> list[dict[str, Any]]:
    """설치 전 기록에서 사람이 친 말을 찾는다. 돌려주는 것은 원문과 출처뿐 — 결론은 없다."""
    ensure(db)
    limit = max(1, min(int(limit), 50))
    clause = " AND s.repo_id = ?" if repo_id else ""
    args: list[Any] = [_fts_query(query), user_id]
    if repo_id:
        args.append(repo_id)
    args.append(limit)
    with db._lock:
        rows = db.conn.execute(
            "SELECT i.id, i.at, i.ref, i.text, s.source, s.external_id, s.source_path, s.repo_id"
            " FROM prior_fts JOIN prior_instruction i ON i.id = prior_fts.rowid"
            " JOIN prior_session s ON s.id = i.session_id"
            f" WHERE prior_fts MATCH ? AND s.user_id = ?{clause}"
            " ORDER BY bm25(prior_fts), i.id DESC LIMIT ?", args).fetchall()
    return [dict(r) for r in rows]


# ── 과거 기록 화면 (확장 43호) ──────────────────────────────────────────────
# 현황·회고와 섞지 않는다(D13627). 여기서 보이는 것은 저장된 원자료뿐이고,
# 원문 파일로 내려가지 않는다(C13616 — 그 화면은 이번 범위 밖).
def archive(db, user_id: int) -> dict[str, Any]:
    """작업공간별 묶음과 총계. 무엇이 들어 있는지 알아야 검색을 믿을 수 있다."""
    ensure(db)
    with db._lock:
        rows = db.conn.execute(
            "SELECT s.repo_id, r.identity AS repo, COUNT(*) AS sessions,"
            " COALESCE(SUM(s.instructions),0) AS instructions,"
            " MIN(s.started_at) AS first_at, MAX(COALESCE(s.ended_at, s.started_at)) AS last_at,"
            " MIN(s.cwd) AS cwd"
            " FROM prior_session s LEFT JOIN repo r ON r.id = s.repo_id"
            " WHERE s.user_id=? GROUP BY s.repo_id ORDER BY instructions DESC", (user_id,)).fetchall()
    ws = [dict(r) for r in rows]
    return {"workspaces": ws,
            "totals": {"workspaces": len(ws),
                       "sessions": sum(w["sessions"] for w in ws),
                       "instructions": sum(w["instructions"] for w in ws)}}


def sessions_of(db, user_id: int, repo_id: int, limit: int = 200) -> list[dict[str, Any]]:
    """한 작업공간의 세션 목록 — 최신 먼저. 첫 말 한 줄을 곁들인다(요약이 아니라 원문의 앞부분)."""
    ensure(db)
    limit = max(1, min(int(limit), 500))
    with db._lock:
        rows = db.conn.execute(
            "SELECT id, source, external_id, cwd, branch, started_at, ended_at, instructions"
            " FROM prior_session WHERE user_id=? AND repo_id=?"
            " ORDER BY COALESCE(started_at, imported_at) DESC LIMIT ?", (user_id, repo_id, limit)).fetchall()
        out = []
        for r in rows:
            # 목록에서 세션을 고르는 단서다. 첫 말이 "run" 같은 한 마디면 쓸모가 없으므로
            # **처음 만나는 충분히 긴 말**을 쓰고, 그런 것이 없으면 가장 긴 말을 쓴다. 요약하지는 않는다.
            texts = [x["text"] for x in db.conn.execute(
                "SELECT text FROM prior_instruction WHERE session_id=? ORDER BY seq", (r["id"],))]
            lead = next((x for x in texts if len(x.strip()) >= LEAD_MIN), None)
            if lead is None and texts:
                lead = max(texts, key=lambda x: len(x.strip()))
            d = dict(r)
            d["first_instruction"] = (lead[:180] if lead else None)
            out.append(d)
    return out


def session_detail(db, user_id: int, session_id: int) -> dict[str, Any]:
    """그 세션에서 사람이 친 말 전부, 순서대로. 다듬지 않는다."""
    ensure(db)
    with db._lock:
        row = db.conn.execute("SELECT * FROM prior_session WHERE id=? AND user_id=?",
                              (session_id, user_id)).fetchone()
        if row is None:
            raise NotFoundError("prior session not found")
        items = db.conn.execute("SELECT seq, at, ref, text FROM prior_instruction"
                                " WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
    out = dict(row)                      # instructions 는 개수 그대로 두고, 말은 items 로 준다
    out["items"] = [dict(i) for i in items]
    return out
