"""SQLite 저장 계층 — Xano 12테이블의 로컬 짝. 필드명·타입은 2026-08-31 스키마 덤프와 동일.

Xano 가 플랫폼으로 보장하던 것을 여기서 코드로 강제한다(인계 §5):
- 불변식 13: message·evidence·ledger 는 append-only. 일반 edit 을 아예 노출하지 않고,
  `.xs` 가 실제로 하는 유일한 갱신(생성 직후 turn_id 부착)만 `attach_turn` 으로 좁게 연다.
- 불변식 14: 터미널 상태 write-once — `edit_where` 의 조건부 UPDATE 가 멱등 가드다.
- 티켓 CAS·멱등: unique 인덱스가 방벽. IntegrityError 를 호출부(.xs 의 catch 짝)가 받는다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any

APPEND_ONLY = frozenset({"message", "evidence", "ledger", "ticket_event"})

# 테이블별 json 컬럼 — 저장은 TEXT, 경계에서 encode/decode.
JSON_COLS: dict[str, frozenset[str]] = {
    "case": frozenset({"brief_cache"}),
    "evidence": frozenset({"source"}),
    "ledger": frozenset({"payload"}),
    "record": frozenset({"error"}),
    "turn": frozenset({"error"}),
    "ticket_event": frozenset({"payload"}),
    "ticket_comment": frozenset({"anchor"}),
    "ticket_snapshot": frozenset({"brief", "evidence_bundle"}),
}

DDL = """
CREATE TABLE IF NOT EXISTS user (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  name TEXT NOT NULL,
  email TEXT,
  password TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_email ON user(email);

-- 서버 이용 허용 (인계서 4절). user 에 칸을 더하지 않고 따로 둔다 — 마이그레이션 장치가 없어
-- ALTER 를 돌릴 자리가 없고, identity·동의·세션이 앞으로 더 붙을 자리이기도 하다.
CREATE TABLE IF NOT EXISTS user_access (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  state TEXT NOT NULL,              -- allowed | blocked
  scope TEXT NOT NULL,              -- worktrail | operator
  consent_version TEXT,             -- 동의한 안내문 판. NULL 이면 미동의
  consent_at INTEGER,
  updated_at INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_user_access_user ON user_access(user_id);

CREATE TABLE IF NOT EXISTS "case" (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  title TEXT,
  status TEXT,
  schema_version INTEGER,
  brief_cache TEXT,
  user_id INTEGER,
  brief_cache_turn_id INTEGER
);

CREATE TABLE IF NOT EXISTS message (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  role TEXT,
  content TEXT,
  case_id INTEGER,
  turn_id INTEGER
);

CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  kind TEXT,
  content TEXT,
  source TEXT,
  case_id INTEGER,
  turn_id INTEGER
);

CREATE TABLE IF NOT EXISTS turn (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  sequence INTEGER,
  turn_kind TEXT,
  status TEXT,
  answer_status TEXT,
  action_key TEXT,
  error TEXT,
  finished_at INTEGER,
  case_id INTEGER,
  user_message_id INTEGER,
  assistant_message_id INTEGER,
  evidence_id INTEGER,
  source_turn_id INTEGER,
  source_evidence_id INTEGER
);

-- MCP 요청이 응답을 받지 못해 재전송돼도 한 턴만 만든다. 새 표라 기존 원장은 건드리지 않는다.
CREATE TABLE IF NOT EXISTS mcp_request (
  user_id INTEGER NOT NULL,
  request_id TEXT NOT NULL,
  case_id INTEGER NOT NULL,
  turn_id INTEGER,
  created_at INTEGER NOT NULL,
  fingerprint TEXT,        -- 요청 내용의 해시. 열쇠가 같아도 이것이 다르면 재전송이 아니라 새 기록이다
  PRIMARY KEY (user_id, case_id, request_id)
);

CREATE TABLE IF NOT EXISTS ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  event_type TEXT,
  payload TEXT,
  case_id INTEGER,
  turn_id INTEGER
);

CREATE TABLE IF NOT EXISTS record (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  version INTEGER,
  status TEXT,
  content TEXT,
  error TEXT,
  finished_at INTEGER,
  case_id INTEGER,
  turn_id INTEGER
);

CREATE TABLE IF NOT EXISTS ticket (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  org_id INTEGER,
  number INTEGER,
  title TEXT,
  description TEXT,
  status TEXT,
  priority TEXT,
  version INTEGER,
  action_key TEXT,
  resolved_at INTEGER,
  reporter_id INTEGER,
  assignee_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_org_number ON ticket(org_id, number);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_reporter_key ON ticket(reporter_id, action_key);

CREATE TABLE IF NOT EXISTS ticket_case (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  kind TEXT,
  case_id INTEGER,
  source_snapshot_version INTEGER,
  ticket_id INTEGER,
  created_by INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_case ON ticket_case(ticket_id, case_id);

CREATE TABLE IF NOT EXISTS ticket_comment (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  content TEXT,
  snapshot_version INTEGER,
  anchor TEXT,
  ticket_id INTEGER,
  author_id INTEGER,
  action_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_comment_key
  ON ticket_comment(ticket_id, action_key) WHERE action_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS ticket_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  event_type TEXT,
  payload TEXT,
  action_key TEXT,
  base_version INTEGER,
  ticket_id INTEGER,
  actor_id INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_event_cas
  ON ticket_event(ticket_id, base_version) WHERE base_version IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_event_key
  ON ticket_event(ticket_id, action_key) WHERE action_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS ticket_snapshot (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  version INTEGER,
  source_case_id INTEGER,
  source_turn_count INTEGER,
  brief TEXT,
  evidence_bundle TEXT,
  record_content TEXT,
  ticket_id INTEGER,
  published_by INTEGER,
  action_key TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_snapshot_ver ON ticket_snapshot(ticket_id, version);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ticket_snapshot_key
  ON ticket_snapshot(ticket_id, action_key) WHERE action_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS _meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
"""

IntegrityError = sqlite3.IntegrityError


def _quote(table: str) -> str:
    return f'"{table}"'


class SqliteDB:
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.isolation_level = None  # 트랜잭션은 transaction() 으로만
        self._lock = threading.RLock()
        with self._lock:
            if path != ":memory:":
                # 서빙용: WAL 은 백업 리더(litestream·sqlite3 .backup)와 쓰기가 공존하게 하고,
                # busy_timeout 은 그 리더가 잠깐 잡은 락을 기다리게 한다. 의미론 변화 없음.
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=NORMAL")
                self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(DDL)
            if "fingerprint" not in {r[1] for r in self.conn.execute("PRAGMA table_info(mcp_request)")}:
                try:                         # fingerprint 없이 만들어진 mcp_request 표 (35c11b1 로 연 DB)
                    self.conn.execute("ALTER TABLE mcp_request ADD COLUMN fingerprint TEXT")
                except sqlite3.OperationalError as exc:   # 다른 프로세스가 먼저 더했다
                    if "duplicate column" not in str(exc):
                        raise

    # ── 시간 ─────────────────────────────────────────────────────────
    @staticmethod
    def now_ms() -> int:
        return int(time.time() * 1000)

    # ── 직렬화 경계 ──────────────────────────────────────────────────
    def _encode(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        cols = JSON_COLS.get(table, frozenset())
        return {
            k: (json.dumps(v, ensure_ascii=False) if k in cols and v is not None else v)
            for k, v in data.items()
        }

    def _decode(self, table: str, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        cols = JSON_COLS.get(table, frozenset())
        out = dict(row)
        for k in cols:
            if out.get(k) is not None:
                out[k] = json.loads(out[k])
        return out

    # ── 쓰기 ─────────────────────────────────────────────────────────
    def add(self, table: str, data: dict[str, Any]) -> dict[str, Any]:
        payload = dict(data)
        payload.setdefault("created_at", self.now_ms())
        enc = self._encode(table, payload)
        keys = list(enc)
        sql = (
            f"INSERT INTO {_quote(table)} ({', '.join(keys)}) "
            f"VALUES ({', '.join('?' for _ in keys)})"
        )
        with self._lock:
            cur = self.conn.execute(sql, [enc[k] for k in keys])
            return self.get(table, cur.lastrowid)  # type: ignore[arg-type]

    def edit(self, table: str, row_id: int, data: dict[str, Any]) -> dict[str, Any]:
        if table in APPEND_ONLY:
            raise RuntimeError(f"append-only 테이블은 갱신할 수 없다: {table} (불변식 13)")
        self._update(table, row_id, {}, data)
        return self.get(table, row_id)  # type: ignore[return-value]

    def edit_where(self, table: str, row_id: int, expected: dict[str, Any], data: dict[str, Any]) -> bool:
        """조건부 UPDATE. expected 가 지금 값과 다르면 아무것도 바꾸지 않고 False.

        터미널 write-once(불변식 14)와 SQS at-least-once 중복 전달의 방어선이다(인계 §5).
        """
        if table in APPEND_ONLY:
            raise RuntimeError(f"append-only 테이블은 갱신할 수 없다: {table} (불변식 13)")
        return self._update(table, row_id, expected, data)

    def attach_turn(self, table: str, row_id: int, turn_id: int) -> None:
        """message·evidence 생성 직후의 turn_id 부착 — `.xs` post_case_turn 5d·5e 의 짝.

        append-only 예외는 이 한 가지뿐이며, 아직 비어 있을 때만 쓸 수 있다.
        """
        if table not in {"message", "evidence"}:
            raise RuntimeError(f"attach_turn 은 message·evidence 전용이다: {table}")
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE {_quote(table)} SET turn_id = ? WHERE id = ? AND turn_id IS NULL",
                (turn_id, row_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"{table}.turn_id 는 write-once 다 (불변식 13)")

    def _update(self, table: str, row_id: int, expected: dict[str, Any], data: dict[str, Any]) -> bool:
        enc = self._encode(table, data)
        sets = ", ".join(f"{k} = ?" for k in enc)
        args: list[Any] = list(enc.values())
        where = "id = ?"
        args.append(row_id)
        for k, v in expected.items():
            if v is None:
                where += f" AND {k} IS NULL"
            else:
                where += f" AND {k} = ?"
                args.append(v)
        with self._lock:
            cur = self.conn.execute(f"UPDATE {_quote(table)} SET {sets} WHERE {where}", args)
            return cur.rowcount > 0

    # ── 읽기 ─────────────────────────────────────────────────────────
    def get(self, table: str, row_id: int | None) -> dict[str, Any] | None:
        if row_id is None:
            return None
        with self._lock:
            row = self.conn.execute(
                f"SELECT * FROM {_quote(table)} WHERE id = ?", (row_id,)
            ).fetchone()
        return self._decode(table, row)

    def get_by(self, table: str, field: str, value: Any) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                f"SELECT * FROM {_quote(table)} WHERE {field} = ?", (value,)
            ).fetchone()
        return self._decode(table, row)

    def query(
        self,
        table: str,
        where: dict[str, Any] | None = None,
        not_equal: dict[str, Any] | None = None,
        order: str = "id",
        desc: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        for k, v in (where or {}).items():
            if v is None:
                clauses.append(f"{k} IS NULL")
            else:
                clauses.append(f"{k} = ?")
                args.append(v)
        for k, v in (not_equal or {}).items():
            clauses.append(f"{k} != ?")
            args.append(v)
        sql = f"SELECT * FROM {_quote(table)}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        # created_at 정렬은 같은 ms 동률을 id 로 깨서 삽입 순서를 보존한다 —
        # 원장 시퀀스가 오라클이므로 동률의 비결정성은 그대로 버그다.
        sql += f" ORDER BY {order} {'DESC' if desc else 'ASC'}, id {'DESC' if desc else 'ASC'}"
        with self._lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [self._decode(table, r) for r in rows]  # type: ignore[misc]

    def count(self, table: str, where: dict[str, Any] | None = None) -> int:
        clauses: list[str] = []
        args: list[Any] = []
        for k, v in (where or {}).items():
            if v is None:
                clauses.append(f"{k} IS NULL")
            else:
                clauses.append(f"{k} = ?")
                args.append(v)
        sql = f"SELECT COUNT(*) FROM {_quote(table)}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._lock:
            return self.conn.execute(sql, args).fetchone()[0]

    # ── 트랜잭션 ─────────────────────────────────────────────────────
    @contextmanager
    def transaction(self):
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    # ── 메타 ─────────────────────────────────────────────────────────
    def meta(self, key: str) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT value FROM _meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO _meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
