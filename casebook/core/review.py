"""확장 24호 — Review(회고): 주제 → 스레드 → 시간순 사건.

Tracker 가 "지금 어디(durable state)"라면 Review 는 "시작부터 무엇을 언제 어떻게 했고 결과는 어땠나" — 사람이 흩어진
기억을 복원하는 화면이다. 사건(event)은 새로 저장하지 않는다: 케이스·원장·턴·change·git 에서 매번 도출한다.
편집물(스냅샷)이 아니라 뷰이므로 기록이 자라면 회고도 같이 자란다 — GPT 시안(정적 17사건)이 "복원이 안 됐다"고
판정된 뒤의 선택(2026-09-05).

topic 은 12 테이블 밖의 부속(repo/thread 와 같은 층). 스레드는 여러 주제에 속할 수 있고(다대다), 어느 주제에도 안
속한 스레드는 "unassigned" 로 그대로 보인다 — 분류가 빠졌다고 사건이 사라지면 안 된다. topic.repos 에 적은 저장소의
git 커밋 중 어느 스레드에도 안 붙은 것(스레드 층 이전의 커밋)은 그 주제의 사건으로 들어간다 — 시작일부터 복원하려면
스레드가 없던 시절도 보여야 한다.

사건 종류(kind): opened · closed · focus · open · next · decision · constraint · ruled_out · note(note_turn 의 결론) ·
asked(사람이 입력 턴으로 물은 것) · evidence(결론 없는 외부 턴 — 화면에서 접힌다) · commit(change 또는 git) · record.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any

from .errors import InputError

_DDL = """
CREATE TABLE IF NOT EXISTS topic (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  summary TEXT,
  repos TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_topic_user ON topic(user_id, id);
CREATE TABLE IF NOT EXISTS topic_case (
  topic_id INTEGER NOT NULL,
  case_id INTEGER NOT NULL,
  added_at INTEGER NOT NULL,
  PRIMARY KEY (topic_id, case_id)
);
"""

KINDS = ("opened", "closed", "focus", "open", "next", "decision", "constraint", "ruled_out",
         "note", "asked", "evidence", "commit", "record")
_DECL = {"focus_declared": "focus", "open_declared": "open", "next_declared": "next"}
HEAD_CHARS = 160
GIT_LOG_LIMIT = 1000


def _now() -> int:
    return int(time.time() * 1000)


def ensure(db) -> None:
    with db._lock:
        db.conn.executescript(_DDL)
        # 확장 31호 — 주제의 결론 한 줄(마무리될 때 사람이 붙인다). 기존 DB 에는 열이 없으니 여기서 더한다.
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(topic)").fetchall()}
        if "conclusion" not in cols:
            db.conn.execute("ALTER TABLE topic ADD COLUMN conclusion TEXT")
            db.conn.execute("ALTER TABLE topic ADD COLUMN concluded_at INTEGER")
            db.conn.commit()


def _row(r) -> dict[str, Any]:
    d = dict(r)
    d["repos"] = json.loads(d["repos"]) if d.get("repos") else []
    return d


# ---------------------------------------------------------------- topics

def create_topic(db, user_id: int, name: str, summary: str = "", repos: list[str] | None = None) -> dict[str, Any]:
    ensure(db)
    if not (name or "").strip():
        raise InputError("topic name is empty")
    with db._lock:
        cur = db.conn.execute("INSERT INTO topic (user_id, name, summary, repos, created_at) VALUES (?,?,?,?,?)",
                              (user_id, name.strip(), (summary or "").strip(), json.dumps(list(repos or [])), _now()))
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM topic WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row(row)


def conclude(db, topic: dict[str, Any], conclusion: str | None) -> dict[str, Any]:
    """주제의 결론 한 줄을 붙인다(빈 문자열이면 지운다). 주제는 사람이 닫지 않는다 — 단계는 도출되고, 결론만 사람의 말이다."""
    ensure(db)
    c = (conclusion or "").strip() or None
    with db._lock:
        db.conn.execute("UPDATE topic SET conclusion=?, concluded_at=? WHERE id=?", (c, _now() if c else None, topic["id"]))
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM topic WHERE id=?", (topic["id"],)).fetchone()
    return _row(row)


def get_topic(db, user_id: int, topic_id: int) -> dict[str, Any] | None:
    ensure(db)
    with db._lock:
        row = db.conn.execute("SELECT * FROM topic WHERE id=? AND user_id=?", (topic_id, user_id)).fetchone()
    return _row(row) if row else None


def find_topic(db, user_id: int, name: str) -> dict[str, Any] | None:
    ensure(db)
    with db._lock:
        row = db.conn.execute("SELECT * FROM topic WHERE user_id=? AND name=? ORDER BY id LIMIT 1",
                              (user_id, (name or "").strip())).fetchone()
    return _row(row) if row else None


def assign(db, topic: dict[str, Any], case_ids: list[int]) -> dict[str, Any]:
    """스레드를 주제에 넣는다(멱등). 소유권 검사는 호출자(app)가 한다."""
    ensure(db)
    now = _now()
    with db._lock:
        for cid in case_ids:
            db.conn.execute("INSERT OR IGNORE INTO topic_case (topic_id, case_id, added_at) VALUES (?,?,?)",
                            (topic["id"], int(cid), now))
        db.conn.commit()
        rows = db.conn.execute("SELECT case_id FROM topic_case WHERE topic_id=? ORDER BY case_id", (topic["id"],)).fetchall()
    return {"topic_id": topic["id"], "name": topic["name"], "case_ids": [r["case_id"] for r in rows]}


def unassign(db, topic: dict[str, Any], case_ids: list[int]) -> dict[str, Any]:
    ensure(db)
    with db._lock:
        for cid in case_ids:
            db.conn.execute("DELETE FROM topic_case WHERE topic_id=? AND case_id=?", (topic["id"], int(cid)))
        db.conn.commit()
        rows = db.conn.execute("SELECT case_id FROM topic_case WHERE topic_id=? ORDER BY case_id", (topic["id"],)).fetchall()
    return {"topic_id": topic["id"], "name": topic["name"], "case_ids": [r["case_id"] for r in rows]}


def merge_topics(db, src: dict[str, Any], dst: dict[str, Any]) -> dict[str, Any]:
    """확장 47호 (2026-09-09) — 주제 합치기: src 의 스레드와 저장소를 dst 로 옮기고 src 를 지운다(#485).
    이름이 글자 그대로 같을 때만 같은 주제라(find_topic), 짧게 적은 이름이 조용히 새 주제가 됐다 — 분류는 판단이라
    사람이 나중에 고치는 것이 정상이고, 그 고치기가 싸야 한다(D13955). 스레드의 added_at 은 그대로 가져간다(언제
    분류됐는지는 사실이다). 설명·결론은 dst 것을 지키고, dst 에 없을 때만 src 것을 가져온다. 원장에는 아무것도 남지
    않는다 — 주제는 12 테이블 밖의 부속이다."""
    ensure(db)
    if src["id"] == dst["id"]:
        raise InputError("a topic cannot be merged into itself")
    with db._lock:
        rows = db.conn.execute("SELECT case_id, added_at FROM topic_case WHERE topic_id=? ORDER BY case_id", (src["id"],)).fetchall()
        for r in rows:
            db.conn.execute("INSERT OR IGNORE INTO topic_case (topic_id, case_id, added_at) VALUES (?,?,?)",
                            (dst["id"], r["case_id"], r["added_at"]))
        db.conn.execute("DELETE FROM topic_case WHERE topic_id=?", (src["id"],))
        repos = list(dict.fromkeys([*dst.get("repos", []), *src.get("repos", [])]))
        summary = (dst.get("summary") or "").strip() or (src.get("summary") or "").strip()
        keep = bool((dst.get("conclusion") or "").strip())
        conclusion = dst.get("conclusion") if keep else (src.get("conclusion") or None)
        concluded_at = dst.get("concluded_at") if keep else (src.get("concluded_at") if conclusion else None)
        db.conn.execute("UPDATE topic SET repos=?, summary=?, conclusion=?, concluded_at=? WHERE id=?",
                        (json.dumps(repos), summary, conclusion, concluded_at, dst["id"]))
        db.conn.execute("DELETE FROM topic WHERE id=?", (src["id"],))
        db.conn.commit()
        cases = db.conn.execute("SELECT case_id FROM topic_case WHERE topic_id=? ORDER BY case_id", (dst["id"],)).fetchall()
    return {"topic_id": dst["id"], "name": dst["name"], "case_ids": [c["case_id"] for c in cases], "repos": repos,
            "merged": {"topic_id": src["id"], "name": src["name"], "moved": len(rows)}}


def list_topics(db, user_id: int) -> list[dict[str, Any]]:
    ensure(db)
    with db._lock:
        rows = db.conn.execute("SELECT * FROM topic WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
        members = db.conn.execute(
            "SELECT tc.topic_id, tc.case_id FROM topic_case tc JOIN topic t ON t.id = tc.topic_id WHERE t.user_id=? "
            "ORDER BY tc.case_id", (user_id,)).fetchall()
    by: dict[int, list[int]] = {}
    for m in members:
        by.setdefault(m["topic_id"], []).append(m["case_id"])
    out = []
    for r in rows:
        t = _row(r); t["case_ids"] = by.get(t["id"], []); out.append(t)
    return out


# ---------------------------------------------------------------- events

def _ev(at: int, kind: str, case_id: int | None, text: str, **extra: Any) -> dict[str, Any]:
    e = {"at": at, "kind": kind, "case_id": case_id, "text": text or ""}
    e.update(extra)
    return e


def _error_head(raw: Any) -> str:
    """turn.error(JSON 또는 문자열)에서 사람이 읽을 한 줄 — 실패한 질문 줄의 펼침에 보인다."""
    if not raw:
        return ""
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return _head(str(raw), 200)
    if isinstance(d, dict):
        parts = [str(d[k]) for k in ("stage", "type", "reason") if d.get(k)]
        return _head(" · ".join(parts) or json.dumps(d, ensure_ascii=False), 200)
    return _head(str(d), 200)


def _head(s: str | None, n: int = HEAD_CHARS) -> str:
    s = (s or "").strip()
    first = s.splitlines()[0] if s else ""
    return first[:n] + ("…" if len(first) > n else "")


def _load_json(v: Any) -> dict[str, Any]:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return {}
    return v or {}


def _client_of(raw: Any) -> str | None:
    """evidence.source 의 client 한 칸(확장 40호). 원장에 없으면 None — 화면도 아무것도 말하지 않는다."""
    return _load_json(raw).get("client") or None


def events_for_cases(db, user_id: int, case_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """케이스별 사건 목록(시간순). 저장하지 않고 도출한다."""
    if not case_ids:
        return {}
    ids = [int(c) for c in case_ids]
    q = ",".join("?" * len(ids))
    out: dict[int, list[dict[str, Any]]] = {c: [] for c in ids}
    with db._lock:
        cases = db.conn.execute(f'SELECT id, title, status, created_at FROM "case" WHERE user_id=? AND id IN ({q})',
                                (user_id, *ids)).fetchall()
        ledger = db.conn.execute(
            f"SELECT id, case_id, event_type, payload, created_at FROM ledger WHERE case_id IN ({q}) AND event_type IN "
            "('focus_declared','open_declared','next_declared','decision_recorded','constraint_recorded',"
            "'hypothesis_ruled_out','case_status_changed','record_created','term_defined') ORDER BY id", ids).fetchall()
        turns = db.conn.execute(
            f"SELECT t.id, t.case_id, t.sequence, t.turn_kind, t.created_at, t.evidence_id, "
            "t.status, t.answer_status, t.source_turn_id, t.error, ev.source AS ev_source, "
            "um.content AS asked, am.content AS note, ev.content AS ev_content, "
            "json_extract(lk.payload, '$.kind') AS note_kind, "   # 확장 60호 — 노트의 갈래(D14720). 옛 노트는 NULL.
            "json_extract(lk.payload, '$.refs') AS note_refs "    # 확장 123호 — 결론이 기대는 증거 번호(D15872)
            "FROM turn t LEFT JOIN message um ON um.id = t.user_message_id "
            "LEFT JOIN message am ON am.id = t.assistant_message_id "
            "LEFT JOIN ledger lk ON lk.turn_id = t.id AND lk.event_type = 'answer_created' "
            "LEFT JOIN evidence ev ON ev.id = t.evidence_id "
            f"WHERE t.case_id IN ({q}) ORDER BY t.case_id, t.sequence", ids).fetchall()
        # 확장 41호 — 대체된 결정 지도. 새 결정이 남긴 supersedes 포인터를 거꾸로 읽을 뿐이고, 무엇이 무엇을
        # 대체했는지 판단하지 않는다(C13533). 이 사용자의 결정 전체를 본다 — repo 범위 결정은 다른 스레드에서
        # 대체될 수 있고, 스레드 화면은 그 한 스레드만 넘겨 부르기 때문이다(recent_events).
        replaced = db.conn.execute(
            "SELECT l.id AS new_id, l.case_id AS new_case, json_extract(l.payload, '$.supersedes') AS old_id, "
            "COALESCE(json_extract(l.payload, '$.statement'), json_extract(l.payload, '$.term')) AS new_stmt FROM ledger l "
            'JOIN "case" c ON c.id = l.case_id WHERE c.user_id = ? AND l.event_type IN (\'decision_recorded\', \'constraint_recorded\', \'term_defined\') '   # 확장 52호 — 제약도
            "AND json_extract(l.payload, '$.supersedes') IS NOT NULL", (user_id,)).fetchall()
        changes = db.conn.execute(
            f"SELECT id, case_id, head, message, stat, created_at FROM change WHERE kind='commit' AND case_id IN ({q}) ORDER BY id",
            ids).fetchall()
    owned = {c["id"] for c in cases}
    replaced_by = {int(r["old_id"]): r["new_id"] for r in replaced}
    # 확장 43호 — 번호만으로는 무엇으로 대체됐는지 그 자리에서 알 수 없다("번호는 사람이 기억하는 단위가
    # 아니다"). 대체한 결정의 첫 문장 머리와 그것이 사는 스레드를 옆 칸으로 준다 — superseded_by 는 번호
    # 그대로 두어 이미 쓰는 곳이 깨지지 않게.
    replaced_head = {int(r["old_id"]): _head(r["new_stmt"], 60) for r in replaced}
    replaced_case = {int(r["old_id"]): r["new_case"] for r in replaced}
    for c in cases:
        out[c["id"]].append(_ev(c["created_at"], "opened", c["id"], c["title"] or ""))
    first_focus_seen: set[int] = set()
    for e in ledger:
        cid = e["case_id"]
        if cid not in owned:
            continue
        p = _load_json(e["payload"]); et = e["event_type"]
        if et in _DECL:
            kind = _DECL[et]
            if kind == "focus" and cid not in first_focus_seen:
                first_focus_seen.add(cid)
                opened = out[cid][0]
                if opened["kind"] == "opened" and e["created_at"] - opened["at"] < 5000:
                    # 여는 순간의 focus 는 '열었다' 줄에 합친다. 줄에는 제목(짧은 이름)이 남고 focus 는 펼쳐야 보인다 —
                    # focus 는 문장이라 한 줄로 두면 길어진다(2026-09-05 #453 줄, 사용자 지적).
                    opened["focus"] = p.get("statement") or ""
                    opened["authority"] = p.get("authority")
                    if not opened["text"]:
                        opened["text"] = opened["focus"]
                    continue
            # 확장 114호 — 같은 글로 다시 말한 focus 는 확인이지 변경이 아니다. 원장에는 남지만
            # 회고·타임라인에서는 세지 않는다 — 안 그러면 바뀐 적 없는 초점이 줄로 쌓인다.
            if p.get("confirmed"):
                continue
            out[cid].append(_ev(e["created_at"], kind, cid, p.get("statement", ""), id=e["id"],
                                authority=p.get("authority"), supersedes=p.get("supersedes"),
                                **({"owner": p["owner"]} if p.get("owner") else {})))   # 확장 153호 — 되짚기에 차례를
        elif et == "decision_recorded":
            # 확장 41호 — 대체 관계를 양쪽에서 보여 준다. supersedes 는 "내가 무엇을 대체했나",
            # superseded_by 는 그 포인터를 거꾸로 읽은 것("나를 무엇이 대체했나")이다. 판단하지 않는다.
            out[cid].append(_ev(e["created_at"], "decision", cid, p.get("statement", ""), id=e["id"], reason=p.get("reason", ""),
                                authority=p.get("authority"), scope=p.get("scope", "thread"),
                                evidence_ids=p.get("evidence_ids", []), client=p.get("client"),
                                supersedes=p.get("supersedes"), superseded_by=replaced_by.get(e["id"]),
                                superseded_by_head=replaced_head.get(e["id"]),
                                superseded_by_case=replaced_case.get(e["id"])))
        elif et == "constraint_recorded":
            # 확장 52호 — 대체된 제약도 결정처럼 "대체됨"으로 읽힌다(사용자 2026-09-11: 같은 말이 셋 겹쳐 보였다)
            out[cid].append(_ev(e["created_at"], "constraint", cid, p.get("statement", ""), id=e["id"], reason=p.get("reason", ""),
                                authority=p.get("authority"), scope=p.get("scope", "thread"),
                                supersedes=p.get("supersedes"), superseded_by=replaced_by.get(e["id"]),
                                superseded_by_head=replaced_head.get(e["id"]), superseded_by_case=replaced_case.get(e["id"])))
        elif et == "term_defined":
            # 확장 55호 — 용어: "이름 = 뜻" 한 줄. 대체된 것은 결정·제약처럼 표시된다.
            out[cid].append(_ev(e["created_at"], "term", cid, f'{p.get("term", "")} = {_head(p.get("meaning", ""), 120)}', id=e["id"],
                                term=p.get("term"), meaning=p.get("meaning", ""), authority=p.get("authority"), scope=p.get("scope", "thread"),
                                supersedes=p.get("supersedes"), superseded_by=replaced_by.get(e["id"]),
                                superseded_by_head=replaced_head.get(e["id"]), superseded_by_case=replaced_case.get(e["id"])))
        elif et == "hypothesis_ruled_out":
            out[cid].append(_ev(e["created_at"], "ruled_out", cid, p.get("hypothesis", ""), id=e["id"], scope=p.get("scope"),
                                evidence_ids=p.get("evidence_ids", [])))
        elif et == "case_status_changed":
            to = p.get("to")
            if to in ("resolved", "archived"):
                # 확장 31호 — 닫을 때 남긴 결과 한 줄이 있으면 그것이 사건의 문장이다
                # 확장 48호 — 닫은 뒤에 붙인 결과(from == to)는 later 로 표시: 닫은 사람과 채운 사람이 따로 보인다
                out[cid].append(_ev(e["created_at"], "closed", cid, p.get("result") or to, id=e["id"], result=p.get("result"),
                                    **({"later": True} if p.get("from") == to else {})))
            elif to == "open" and p.get("from") in ("resolved", "archived"):
                out[cid].append(_ev(e["created_at"], "opened", cid, "reopened", id=e["id"]))
        elif et == "record_created":
            out[cid].append(_ev(e["created_at"], "record", cid, f"record v{p.get('version')}", id=e["id"],
                                record_id=p.get("record_id"), version=p.get("version")))
    seq_of = {t["id"]: t["sequence"] for t in turns}
    for t in turns:
        cid = t["case_id"]
        if cid not in owned:
            continue
        if t["turn_kind"] == "external":
            # 확장 40호 — 어느 클라이언트가 남겼는지. 없으면 키 자체를 넣지 않는다.
            who = _client_of(t["ev_source"])
            if (t["note"] or "").strip():
                out[cid].append(_ev(t["created_at"], "note", cid, t["note"], turn=t["sequence"], evidence_id=t["evidence_id"],
                                    observed=_head(t["ev_content"], 600), client=who,   # 확장 71호 — 읽기 모드: 서랍이 관찰을 보여 주므로 600자(#516)
                                    **({"note_kind": t["note_kind"]} if t["note_kind"] else {}),
                                    **({"evidence_ids": json.loads(t["note_refs"])} if t["note_refs"] else {})))
            else:
                out[cid].append(_ev(t["created_at"], "evidence", cid, _head(t["ev_content"], 120), turn=t["sequence"],
                                    evidence_id=t["evidence_id"], client=who))
        elif t["asked"]:
            # 답변 실패 턴과 그것을 이어받은 recovery 턴은 같은 사용자 메시지를 가리켜 같은 문장이 두 줄로 보인다(2026-09-06
            # case 450, 사용자 "기록을 두번한건가"). 숨기지 않고 상태를 붙인다 — 실패 이력도 실험 데이터다.
            out[cid].append(_ev(t["created_at"], "asked", cid, t["asked"][:600], turn=t["sequence"], turn_kind=t["turn_kind"],
                                status=t["status"], answer_status=t["answer_status"],
                                source_turn=seq_of.get(t["source_turn_id"]) if t["source_turn_id"] else None,
                                error=_error_head(t["error"])))
    for ch in changes:
        if ch["case_id"] in owned:
            out[ch["case_id"]].append(_ev(ch["created_at"], "commit", ch["case_id"], ch["message"] or "", head=ch["head"],
                                          stat=ch["stat"], via="thread"))
    for cid in out:
        out[cid].sort(key=lambda e: (e["at"], e.get("id") or 0))
    return out


def _repo_paths(db, identity: str) -> list[str]:
    """저장소를 디스크에서 찾을 후보 경로: repo.hint(local: 은 .git 디렉터리, remote 는 URL 이라 못 쓴다) →
    그 저장소 스레드에 바인딩된 worktree → change 에 남은 worktree. 있는 것 중 첫 번째를 쓴다."""
    with db._lock:
        repo = db.conn.execute("SELECT id, hint FROM repo WHERE identity=?", (identity,)).fetchone()
        if repo is None:
            return []
        bound = db.conn.execute(
            "SELECT DISTINCT b.worktree FROM worktree_binding b JOIN thread t ON t.case_id = b.case_id WHERE t.repo_id=?",
            (repo["id"],)).fetchall()
        changed = db.conn.execute(
            "SELECT DISTINCT c.worktree FROM change c JOIN thread t ON t.case_id = c.case_id WHERE t.repo_id=? AND c.worktree IS NOT NULL "
            "ORDER BY c.id DESC", (repo["id"],)).fetchall()
    out: list[str] = []
    hint = repo["hint"] or ""
    if hint.startswith("/"):
        out.append(os.path.dirname(hint) if hint.endswith(".git") else hint)
    out.extend(r["worktree"] for r in bound)
    out.extend(r["worktree"] for r in changed)
    seen: set[str] = set()
    return [p for p in out if p and not (p in seen or seen.add(p)) and os.path.isdir(p)]


def git_commits(path: str | None, exclude_heads: set[str], limit: int = GIT_LOG_LIMIT) -> list[dict[str, Any]]:
    """저장소의 커밋 중 어느 스레드에도 안 붙은 것(change 에 없는 것). 저장소가 디스크에 없으면 빈 목록 — 기록이 아니라 뷰다."""
    if not path or not os.path.isdir(path):
        return []
    try:
        p = subprocess.run(["git", "-C", path, "log", "--no-merges", f"-n{limit}", "--format=%H%x1f%ct%x1f%s"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    out = []
    for line in p.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) != 3 or parts[0] in exclude_heads:
            continue
        out.append(_ev(int(parts[1]) * 1000, "commit", None, parts[2], head=parts[0], via="git"))
    out.sort(key=lambda e: e["at"])
    return out


RECENT_SKIP = ("evidence", "focus", "open", "next", "record")   # 마지막 발자국에는 결론·커밋·질문·결정만 — 선언·증거는 다른 칸에 있다


def recent_events(db, user_id: int, case_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """스레드의 마지막 행동 몇 개(최신 먼저). Tracker 스레드 화면의 '마지막으로 한 일' — Parnin & DeLine(CHI 2010):
    중단 뒤 복귀에는 최근 행동의 시간순 목록이 돕는다. resume(에이전트용)에는 넣지 않는다 — 과거 추론을 주지 않는 원칙."""
    ev = [e for e in events_for_cases(db, user_id, [case_id]).get(case_id, []) if e["kind"] not in RECENT_SKIP]
    return list(reversed(ev[-limit:]))


# ---------------------------------------------------------------- review

def review(db, user_id: int, vitals: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """주제별 회고 한 장. topics(각각 case_ids · git 커밋) · cases(케이스별 사건) · unassigned(어느 주제에도 없는 스레드)."""
    ensure(db)
    from . import phase, state, threads
    threads.ensure(db); threads.ensure_change(db)
    topics = list_topics(db, user_id)
    vitals = vitals or {}
    assigned_any: set[int] = {c for t in topics for c in t["case_ids"]}
    # 회고의 케이스 = 스레드(저장소에 속한 것) ∪ 어떤 주제에든 넣은 것. 스레드 층 이전의 장애 케이스(Casebook 본연의
    # 사건들)는 주제에 넣지 않는 한 여기 안 나온다 — 작업 회고에 장애 기록이 섞이면 읽기 부채다.
    with db._lock:
        cases = db.conn.execute(
            'SELECT c.id, c.title, c.status, c.created_at, r.identity FROM "case" c '
            "LEFT JOIN thread t ON t.case_id = c.id LEFT JOIN repo r ON r.id = t.repo_id "
            "WHERE c.user_id=? AND c.status != 'archived' ORDER BY c.id", (user_id,)).fetchall()
        recorded = db.conn.execute("SELECT head FROM change WHERE kind='commit' AND user_id=?", (user_id,)).fetchall()
        bound = {b["case_id"] for b in db.conn.execute("SELECT case_id FROM worktree_binding WHERE user_id=?", (user_id,)).fetchall()}
    cases = [c for c in cases if c["identity"] is not None or c["id"] in assigned_any]
    now = _now()
    recorded_heads = {r["head"] for r in recorded}
    case_ids = [c["id"] for c in cases]
    events = events_for_cases(db, user_id, case_ids)
    case_out: dict[int, dict[str, Any]] = {}
    for c in cases:
        st = "open" if c["status"] == "open" else "closed"
        nd = state.current_declaration(db, c["id"], "next")
        v = vitals.get(c["id"], {})
        evs = events.get(c["id"], [])
        last_at = max([e["at"] for e in evs] + [v.get("updated_at") or 0]) or None
        case_out[c["id"]] = {
            "case_id": c["id"], "title": c["title"], "state": st,
            "repo": c["identity"], "created_at": c["created_at"],
            "focus": state.current(db, c["id"], "focus"), "next": (nd or {}).get("statement"),
            "owner": (nd or {}).get("owner"),
            "open": state.current(db, c["id"], "open"),
            # 확장 31호 — 회고의 "지금" 줄도 현황과 같은 단계를 쓴다
            "phase": phase.thread_phase(st, (nd or {}).get("owner")),
            "result": next((e.get("result") for e in reversed(evs) if e["kind"] == "closed" and e.get("result")), None),
            "events": evs,
        }
    assigned: set[int] = set()
    for t in topics:
        t["case_ids"] = [c for c in t["case_ids"] if c in case_out]
        assigned.update(t["case_ids"])
        t["phase"] = phase.topic_phase([case_out[c]["phase"] for c in t["case_ids"] if case_out[c]["state"] == "open"])
        t["git"] = []
        for ident in t["repos"]:
            paths = _repo_paths(db, ident)
            t["git"].extend(git_commits(paths[0] if paths else None, recorded_heads))
    return {
        "generated_at": _now(),
        "topics": topics,
        "cases": case_out,
        "unassigned": [c for c in case_ids if c not in assigned],
        "note": "events are derived on read from the case, ledger, turns, change table and git — nothing here is stored twice.",
    }
