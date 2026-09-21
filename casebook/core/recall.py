"""필요할 때 당겨 오는 과거 맥락 — 확장 153호 (2026-09-21, D17255).

resume 은 "지금" 만 준다. 어떻게 여기까지 왔는지는 밀어 넣지 않고, 물을 때만 세 층으로 당겨 온다:
  find(query)      스레드를 가로질러 기록을 찾는다 — 한 줄씩, 번호(ref)와 함께
  trail(case_id)   스레드 하나를 시간순으로 되짚는다 — 한 줄씩
  inspect(ref)     번호 하나를 전문으로 — 결정·제약·배제·선언·노트·증거

노트(note_turn 의 결론)는 그때 기록한 에이전트의 해석이지 증거가 아니다. 숨기지 않되 매번
interpretation 으로 표시하고, 기대는 증거 번호를 같이 준다(D17255). 사건은 새로 저장하지 않는다 —
회고(review.events_for_cases)와 같은 도출을 쓴다. 모델은 부르지 않는다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import review, search, threads
from .errors import InputError, NotFoundError

LINE = 110
TRAIL_LIMIT = 60
FIND_LIMIT = 20
_PREFIX = {"decision_recorded": "D", "constraint_recorded": "C", "hypothesis_ruled_out": "R", "term_defined": "T",
           "focus_declared": "F", "open_declared": "O", "next_declared": "N", "case_status_changed": "S"}
_KIND_PREFIX = {"decision": "D", "constraint": "C", "ruled_out": "R", "term": "T", "focus": "F", "open": "O",
                "next": "N", "closed": "S"}
_REF = re.compile(r"^\s*(?:([A-Za-z])\s*(\d+)|#?(\d+)\s*[:/]\s*(?:t|turn\s*)?(\d+))\s*$", re.I)
INTERPRETATION = ("notes are the recording agent's interpretation at the time, not evidence — check them "
                  "against the evidence they cite (inspect E<id>)")


def _line(text: str | None, n: int = LINE) -> str:
    first = (text or "").strip().split("\n", 1)[0].strip()
    return first if len(first) <= n else first[: n - 1] + "…"


def _excerpt(text: str | None, n: int) -> str:
    # FTS 발췌는 여러 줄이다 — 첫 줄만 자르면 맞은 낱말([…])이 빠진다. 한 줄로 펴고, 길면 맞은 곳 둘레를 남긴다.
    flat = " ".join((text or "").split())
    if len(flat) <= n:
        return flat
    at = max(0, flat.find("[") - n // 3)
    cut = flat[at: at + n - 2]
    return ("…" if at else "") + cut + "…"


def _ref(e: dict[str, Any]) -> str | None:
    if e["kind"] == "note":
        return f"{e['case_id']}:{e['turn']}"
    if e["kind"] == "evidence":
        return f"E{e['evidence_id']}" if e.get("evidence_id") else None
    if e["kind"] == "commit":
        return (e.get("head") or "")[:12] or None
    p = _KIND_PREFIX.get(e["kind"])
    return f"{p}{e['id']}" if p and e.get("id") else None


def _row(e: dict[str, Any], with_case: bool = False) -> dict[str, Any]:
    """사건 하나 → 한 줄. 판단을 보태지 않는다: 대체됐으면 무엇으로인지, 노트면 해석이라는 것만 붙인다."""
    r: dict[str, Any] = {"at": e["at"], "kind": e["kind"], "ref": _ref(e), "line": _line(e.get("text"))}
    if with_case:
        r["case_id"] = e["case_id"]
    if e["kind"] == "note":
        r["interpretation"] = True
        if e.get("note_kind"):
            r["note_kind"] = e["note_kind"]
        if e.get("evidence_ids"):
            r["evidence_ids"] = e["evidence_ids"]
        elif e.get("evidence_id"):
            r["evidence_ids"] = [e["evidence_id"]]
    if e.get("owner"):
        r["owner"] = e["owner"]
    if e.get("authority") and e["kind"] in ("decision", "constraint", "focus"):
        r["authority"] = e["authority"]
    if e.get("scope") == "repo":
        r["scope"] = "repo"
    if e.get("superseded_by"):
        r["superseded_by"] = f"{_KIND_PREFIX.get(e['kind'], 'D')}{e['superseded_by']}"
    if e["kind"] == "ruled_out" and e.get("scope"):
        r["within"] = _line(e["scope"], 80)
    return r


def _owned_case(db, user_id: int, case_id: int) -> dict[str, Any]:
    case = db.get("case", case_id)
    if case is None or case["user_id"] != user_id:
        raise NotFoundError("case not found")
    return case


def trail(db, user_id: int, case_id: int, limit: int = TRAIL_LIMIT, before: int | None = None) -> dict[str, Any]:
    """스레드 하나를 되짚는다. 최근 limit 줄, 더 앞은 before=<가장 이른 줄의 at> 로."""
    case = _owned_case(db, user_id, case_id)
    threads.ensure_change(db)
    limit = max(1, min(int(limit), 200))
    evs = [e for e in review.events_for_cases(db, user_id, [case_id]).get(case_id, []) if e["kind"] != "record"]
    if before is not None:
        evs = [e for e in evs if e["at"] < int(before)]
    shown = evs[-limit:]
    return {"case_id": case_id, "title": _line(case["title"], 120), "status": case["status"],
            "events": [_row(e) for e in shown], "earlier": len(evs) - len(shown),
            "note": INTERPRETATION + ". Full text of any line: inspect(ref)."}


def _haystack(e: dict[str, Any]) -> str:
    parts = [e.get("text") or "", e.get("reason") or "", e.get("scope") or "", e.get("focus") or "",
             e.get("observed") or ""]
    return "\n".join(parts).casefold()


def find(db, user_id: int, query: str, limit: int = FIND_LIMIT, include_closed: bool = True) -> dict[str, Any]:
    """스레드를 가로질러 결정·제약·배제·선언·노트·닫은 결과를 찾는다(낱말 모두 포함, 최근 것 먼저).
    남은 자리에는 증거 원문 검색(search_evidence)의 적중을 붙인다."""
    toks = [t.casefold() for t in (query or "").split() if t.strip()]
    if not toks:
        raise InputError("query is empty")
    limit = max(1, min(int(limit), 50))
    threads.ensure_change(db)
    where = "user_id=? AND status != 'archived'" + ("" if include_closed else " AND status='open'")
    with db._lock:
        cases = {r["id"]: r["title"] for r in db.conn.execute(f'SELECT id, title FROM "case" WHERE {where}', (user_id,))}
    hits = []
    for cid, evs in review.events_for_cases(db, user_id, list(cases)).items():
        for e in evs:
            if e["kind"] in ("record", "commit", "evidence", "asked"):
                continue
            hay = _haystack(e)
            if all(t in hay for t in toks):
                hits.append(e)
    hits.sort(key=lambda e: (e["superseded_by"] is None if "superseded_by" in e else True, e["at"]), reverse=True)
    rows = []
    for e in hits[:limit]:
        r = _row(e, with_case=True)
        r["thread"] = _line(cases.get(e["case_id"]), 70)
        rows.append(r)
    room = limit - len(rows)
    evidence = []
    if room > 0:
        try:
            for x in search.search_evidence(db, user_id, query, min(room, 5)):
                evidence.append({"ref": f"E{x['evidence_id']}", "case_id": x["case_id"], "at": x["created_at"],
                                 "kind": "evidence", "excerpt": _excerpt(x["excerpt"], 160)})
        except InputError:
            pass
    return {"query": query, "records": rows, "total": len(hits), "evidence": evidence,
            "note": ("records are newest first, superseded ones after the ones in force; " + INTERPRETATION +
                     ". Walk a thread with trail(case_id); full text with inspect(ref).")}


def parse_ref(ref: str | int) -> tuple[str, int, int | None]:
    """'D17255' · 'C11100' · 'E2513' · '564:3' → (종류 글자, 번호, 턴). 숫자만 주면 증거 번호다(옛 inspect)."""
    if isinstance(ref, int) or str(ref).strip().isdigit():
        return "E", int(ref), None
    m = _REF.match(str(ref))
    if not m:
        raise InputError("ref must look like D17255, C11100, R42, N17252, E2513, or 564:3 (thread:turn)")
    if m.group(1):
        return m.group(1).upper(), int(m.group(2)), None
    return "#", int(m.group(3)), int(m.group(4))


def _superseders(db, user_id: int, entry_id: int) -> dict[str, Any] | None:
    with db._lock:
        r = db.conn.execute(
            'SELECT l.id, l.case_id, l.event_type, l.payload FROM ledger l JOIN "case" c ON c.id = l.case_id '
            "WHERE c.user_id=? AND json_extract(l.payload, '$.supersedes') = ? ORDER BY l.id LIMIT 1",
            (user_id, entry_id)).fetchone()
    if not r:
        return None
    p = review._load_json(r["payload"])
    return {"ref": f"{_PREFIX.get(r['event_type'], 'L')}{r['id']}", "case_id": r["case_id"],
            "title": _line(p.get("statement") or p.get("term"))}


def _ledger(db, user_id: int, entry_id: int) -> dict[str, Any]:
    with db._lock:
        r = db.conn.execute('SELECT l.id, l.case_id, l.event_type, l.payload, l.created_at, c.title AS case_title '
                            'FROM ledger l JOIN "case" c ON c.id = l.case_id WHERE l.id=? AND c.user_id=?',
                            (entry_id, user_id)).fetchone()
    if not r or r["event_type"] not in _PREFIX:
        raise NotFoundError("record not found")
    p = review._load_json(r["payload"])
    kind = {"D": "decision", "C": "constraint", "R": "ruled_out", "T": "term", "F": "focus", "O": "open",
            "N": "next", "S": "status"}[_PREFIX[r["event_type"]]]
    out: dict[str, Any] = {"ref": f"{_PREFIX[r['event_type']]}{r['id']}", "kind": kind, "case_id": r["case_id"],
                           "case_title": r["case_title"], "recorded_at": r["created_at"]}
    keep = ("statement", "reason", "authority", "scope", "owner", "evidence_ids", "hypothesis", "term", "meaning",
            "from", "to", "result")
    out.update({k: p[k] for k in keep if p.get(k) not in (None, "")})
    if p.get("supersedes"):
        prev = _ledger_head(db, int(p["supersedes"]))
        out["supersedes"] = prev
    later = _superseders(db, user_id, r["id"])
    out["status"] = "superseded" if later else "in force"
    if later:
        out["superseded_by"] = later
    return out


def _ledger_head(db, entry_id: int) -> dict[str, Any]:
    with db._lock:
        r = db.conn.execute("SELECT id, event_type, payload FROM ledger WHERE id=?", (entry_id,)).fetchone()
    if not r:
        return {"ref": f"L{entry_id}"}
    p = review._load_json(r["payload"])
    return {"ref": f"{_PREFIX.get(r['event_type'], 'L')}{r['id']}", "title": _line(p.get("statement") or p.get("term"))}


def _turn(db, user_id: int, case_id: int, seq: int) -> dict[str, Any]:
    case = _owned_case(db, user_id, case_id)
    with db._lock:
        t = db.conn.execute(
            "SELECT t.id, t.sequence, t.created_at, t.evidence_id, am.content AS conclusion, ev.content AS observed, "
            "json_extract(lk.payload, '$.kind') AS note_kind, json_extract(lk.payload, '$.refs') AS refs "
            "FROM turn t LEFT JOIN message am ON am.id = t.assistant_message_id "
            "LEFT JOIN ledger lk ON lk.turn_id = t.id AND lk.event_type = 'answer_created' "
            "LEFT JOIN evidence ev ON ev.id = t.evidence_id WHERE t.case_id=? AND t.sequence=?",
            (case_id, seq)).fetchone()
    if not t:
        raise NotFoundError("turn not found")
    out: dict[str, Any] = {"ref": f"{case_id}:{seq}", "kind": "note", "case_id": case_id, "case_title": case["title"],
                           "turn": seq, "at": t["created_at"]}
    if t["note_kind"]:
        out["note_kind"] = t["note_kind"]
    if t["evidence_id"]:
        out["observed"] = {"evidence_id": t["evidence_id"], "content": t["observed"]}   # 원문 그대로
    if t["conclusion"]:
        out["conclusion"] = t["conclusion"]
        out["interpretation"] = True
        out["note"] = INTERPRETATION + "."
    if t["refs"]:
        out["evidence_ids"] = json.loads(t["refs"])
    return out


def inspect(db, user_id: int, ref: str | int) -> dict[str, Any]:
    kind, n, seq = parse_ref(ref)
    if kind == "E":
        out = search.inspect_evidence(db, user_id, n)
        out["ref"] = f"E{n}"
        return out
    if kind == "#":
        return _turn(db, user_id, n, seq)
    return _ledger(db, user_id, n)
