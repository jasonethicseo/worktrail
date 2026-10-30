"""사용자 발화 원문 — 확장 157호 (2026-09-25, 스레드 595).

기록은 대부분 에이전트의 말이라 사람이 읽고 맥락을 되살리기 어렵다. 그래서 사람이 친 말을 턴마다 원문 그대로
남기고(D17975 · D17987), 에이전트 기록이 그 말에서 나왔으면 그 원문을 source_ref 로 가리키게 한다(D17989).
원문은 기록 안에 복제하지 않는다 — 기록 payload 에는 번호(source_refs)만 들어가고, 읽을 때 여기서 붙인다.

  utterance   사람이 친 말 한 건. 열쇠는 호스트가 준 프롬프트 식별자다(Claude Code prompt_id, Codex turn_id — D17988).
              서버가 번호를 발급하지 않으므로 훅이 서버를 기다리지 않고 같은 턴에 에이전트에게 ref 를 알려 줄 수 있다.

두 출처(origin)를 가른다 — 어느 도구든 같은 규칙으로 돌게 하되, 믿을 수 있는 정도는 숨기지 않는다:
  hook          코딩 에이전트의 UserPromptSubmit 훅이 잡은 것. 모델을 거치지 않았으므로 바이트 그대로다.
  model_pasted  훅이 없는 채팅 클라이언트(ChatGPT·Claude.ai)에서 모델이 옮겨 붙인 것. 서버는 원문과 같은지 확인할 수
                없다 — 이 스레드에서 ChatGPT 가 요약을 user_paste 로 넣은 전례가 있다(증거 2656–2658). 3인칭 요약 꼴은
                거절하고, 나머지는 출처를 달아 받는다.

지키는 것:
- append-only. 같은 (user, prompt_id) 가 다시 오면 무시한다 — 업로더는 실패하면 통째로 다시 보낸다(D18009).
- source_ref 가 원문보다 먼저 와도 받는다(D17990) — 훅 경로만. 업로더가 뒤에서 올리기 때문이다. 읽을 때 아직 없으면
  pending 으로 보인다. 훅이 없는 경로는 붙여 넣기(add_pasted)가 동기라, 없는 ref 는 지어낸 것으로 보고 거절한다.
- 사람 발화만(D18012). 에이전트 말·도구 결과는 여기 오지 않는다. 검색(search.py 계약)에도 섞지 않는다 — 원문을
  어디서 어떻게 꺼내 보일지는 아직 열린 질문이다(스레드 595 open).
"""
from __future__ import annotations

import re
from typing import Any

from .errors import InputError

_DDL = """
CREATE TABLE IF NOT EXISTS utterance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,     -- 서버가 받은 때
  user_id INTEGER NOT NULL,
  prompt_id TEXT NOT NULL,         -- 호스트가 준 식별자 = source_ref
  session_id TEXT,
  client TEXT,                     -- claude-code …
  worktree TEXT,                   -- 말한 곳(맥의 경로). 스레드는 여기서 도출하지 않는다 — 기록의 ref 가 잇는다
  said_at INTEGER,                 -- 맥에서 받은 때(ms)
  origin TEXT NOT NULL DEFAULT 'hook',   -- hook | model_pasted
  content TEXT NOT NULL,           -- 원문 그대로
  original_bytes INTEGER           -- 상한을 넘어 잘렸을 때만 원래 크기. 평소에는 NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_utterance_prompt ON utterance(user_id, prompt_id);
"""

REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
# 한 건의 상한. 원격 문의 요청 상한이 1 MiB 라(request_limits) 그 안에 한 건이 들어가야 한다.
# 넘는 붙여넣기는 앞부분만 남기고 원래 크기를 적는다 — 조용히 버리지 않는다.
CONTENT_CAP = 600_000
BATCH_CAP = 100
ORIGINS = ("hook", "model_pasted")
# 붙여 넣기가 원문이 아니라 해석이라는 꼴. 첫머리만 본다 — 사람이 스스로를 3인칭으로 쓰는 일은 드물다.
_PARAPHRASE = re.compile(
    r"^\s*(?:(?:사용자|유저|엔지니어|개발자)\s*(?:는|은|가|이|께서|의|:)"
    r"|(?:the\s+)?(?:user|engineer)\s*(?:'s\b|:|said\b|says\b|wants\b|asked\b|decided\b|thinks\b|confirmed\b|is\b|was\b)"
    r"|(?:요약|정리|summary)\s*[:：]?)", re.I)


def ensure(db) -> None:
    with db._lock:
        db.conn.executescript(_DDL)


def normalize_refs(refs: Any) -> list[str]:
    """source_refs 입력 → 중복 없는 문자열 목록. 형식이 틀리면 거절한다(존재는 묻지 않는다, D17990)."""
    if refs is None:
        return []
    if isinstance(refs, str):
        refs = [refs]
    out: list[str] = []
    for r in refs:
        s = str(r).strip()
        if not REF_RE.match(s):
            raise InputError(f"source_refs: {s[:40]!r} is not a prompt id — pass the id the hook showed as this "
                             "prompt's source_ref, exactly")
        if s not in out:
            out.append(s)
    return out


def clip(content: str) -> tuple[str, int | None]:
    raw = content.encode("utf-8")
    if len(raw) <= CONTENT_CAP:
        return content, None
    return raw[:CONTENT_CAP].decode("utf-8", errors="ignore"), len(raw)


def record(db, user_id: int, items: list[dict[str, Any]]) -> dict[str, int]:
    """업로더가 보낸 발화 묶음을 넣는다. 이미 있는 prompt_id 는 건너뛴다(재전송)."""
    if not isinstance(items, list):
        raise InputError("items must be a list")
    if len(items) > BATCH_CAP:
        raise InputError(f"at most {BATCH_CAP} utterances per call")
    ensure(db)
    rows = []
    for it in items:
        if not isinstance(it, dict):
            raise InputError("each utterance is an object")
        pid = normalize_refs([it.get("prompt_id")])[0]
        content = it.get("content")
        if not isinstance(content, str) or not content.strip():
            raise InputError(f"utterance {pid}: content is empty")
        content, original = clip(content)
        said = it.get("said_at")
        rows.append((db.now_ms(), user_id, pid, _short(it.get("session_id")), _short(it.get("client")),
                     _short(it.get("worktree"), 1024), int(said) if isinstance(said, (int, float)) else None,
                     "hook", content, original))
    stored = _insert(db, rows)
    return {"stored": stored, "known": len(rows) - stored}


def _insert(db, rows: list[tuple]) -> int:
    stored = 0
    with db.transaction():
        for row in rows:
            cur = db.conn.execute(
                "INSERT OR IGNORE INTO utterance (created_at, user_id, prompt_id, session_id, client, worktree, "
                "said_at, origin, content, original_bytes) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            stored += cur.rowcount
    return stored


def looks_paraphrased(text: str) -> bool:
    return bool(_PARAPHRASE.match(text or ""))


def add_pasted(db, user_id: int, text: str, client: str | None = None) -> str:
    """훅이 없는 채팅 클라이언트에서 모델이 사람 메시지를 붙여 넣는다. 새 ref 를 돌려준다. 요약 꼴은 거절한다."""
    import uuid
    if not isinstance(text, str) or not text.strip():
        raise InputError("add_utterance: text is empty — paste the engineer's message exactly as they wrote it")
    if looks_paraphrased(text):
        raise InputError(
            "add_utterance: this reads like your account of what the engineer said, not their words. Paste their "
            "message exactly as they typed it — first person, every character, no summary. Nothing was recorded.")
    ensure(db)
    pid = uuid.uuid4().hex
    content, original = clip(text)
    _insert(db, [(db.now_ms(), user_id, pid, None, _short(client), None, db.now_ms(), "model_pasted", content, original)])
    return pid


def known(db, user_id: int, refs: list[str]) -> set[str]:
    if not refs:
        return set()
    ensure(db)
    marks = ",".join("?" for _ in refs)
    with db._lock:
        return {r[0] for r in db.conn.execute(
            f"SELECT prompt_id FROM utterance WHERE user_id=? AND prompt_id IN ({marks})", (user_id, *refs))}


def _short(v: Any, n: int = 128) -> str | None:
    return str(v)[:n] if isinstance(v, str) and v else None


def lookup(db, user_id: int, refs: list[str]) -> list[dict[str, Any]]:
    """기록 옆에 붙일 원문. 아직 안 올라온 것은 pending 으로 — 없다고 꾸미지도, 지어내지도 않는다."""
    if not refs:
        return []
    ensure(db)
    marks = ",".join("?" for _ in refs)
    with db._lock:
        found = {r["prompt_id"]: r for r in db.conn.execute(
            f"SELECT prompt_id, said_at, origin, content, original_bytes FROM utterance "
            f"WHERE user_id=? AND prompt_id IN ({marks})",
            (user_id, *refs)).fetchall()}
    out = []
    for ref in refs:
        r = found.get(ref)
        if r is None:
            out.append({"source_ref": ref, "pending": True})
            continue
        item = {"source_ref": ref, "origin": r["origin"], "said_at": r["said_at"], "content": r["content"]}
        if r["original_bytes"]:
            item["clipped_from_bytes"] = r["original_bytes"]
        out.append(item)
    return out
