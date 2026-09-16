"""훅 문맥 — 확장 29호 (2026-09-06). Claude Code 훅(SessionStart · compact)이 주입하는 본문을 코어에서 만든다.

원래 tools/hooks/casebook_hook.py 에 있던 렌더링을 옮겼다(확장 14·15·18·19·26호의 문장 그대로). 이유: 얇은 클라이언트에서는
훅이 sqlite 를 직접 열지 못하고 원격 문의 `hook` 도구를 부르는데, 그 도구가 같은 본문을 서버에서 만들어야 한다.
로컬 훅은 이 모듈을 직접 부르고, 원격 훅은 도구를 거쳐 같은 함수에 닿는다 — 본문은 한 곳에서만 정의된다.
모델은 부르지 않는다. 읽기만 하고 read_log(session_start)만 남긴다.
"""
from __future__ import annotations

import time

from . import reads, state, threads, vitals


RULES = (
    "Rules: one thread per question or change, followed to the end — deciding and building are one thread (re-declare focus); "
    "open a new one only when the question changes (open_thread / switch_thread / close_thread). resume(case_id) before continuing. A turn = the machine told you something that changed what "
    "you would do next: add_evidence (verbatim, sparse; long output → file → add_evidence_file) + note_turn(kind=change|verified|finding|thought). "
    "Every statement's FIRST LINE is its title (≤120 columns, CJK counts as 2, no ' — ' joiner, no record numbers such as #517 · D15635 · turn 80 · commit hash); "
    "details and numbers go after a blank line or into evidence_ids — the server refuses otherwise. A next owned by the user is written TO the engineer ('Decide X'), never about them. "
    "Nothing is saved for you at compaction — record as you go."
)
CONTEXT_CAP = 10_000


def _next_age(declaration: dict | None, now: int) -> str:
    if declaration is None:
        return "not declared"
    seconds = max(0, (now - declaration["declared_at"]) // 1000)
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def _session_context(lines: list[str], elsewhere: list[str]) -> str:
    """현재 문맥과 규칙을 먼저 확보하고 다른 저장소는 완전한 한 줄씩 넣는다."""
    budget = CONTEXT_CAP - len(RULES) - 1
    body = "\n".join(lines)
    if len(body) > budget:
        body = body[:budget - 1] + "…"
    if elsewhere:
        heading = "\nOpen threads in other repositories (repo · thread · focus · next age):"
        omitted = "\n  (more open threads omitted)"
        if len(body) + len(heading) + len(omitted) <= budget:
            body += heading
            for i, line in enumerate(elsewhere):
                reserve = len(omitted) if i < len(elsewhere) - 1 else 0
                if len(body) + 1 + len(line) + reserve > budget:
                    body += omitted
                    break
                body += "\n" + line
    return body + "\n" + RULES


def _fmt(ms: int | None) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ms / 1000)) if ms else "—"


def _cases(db, uid: int, limit: int = 5) -> list[dict]:
    v = vitals.case_vitals(db, uid)
    rows = []
    for c in db.query("case", where={"user_id": uid, "status": "open"}):
        row = {"id": c["id"], "title": c["title"] or "", "focus": ((c["brief_cache"] or {}).get("focus") or "")}
        row.update(v.get(c["id"], vitals.EMPTY)); row["updated_at"] = row["updated_at"] or c["created_at"]
        rows.append(row)
    rows.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
    return rows[:limit]


def _last_resume(db, uid: int) -> tuple[int | None, int | None]:
    with db._lock:
        row = db.conn.execute(
            "SELECT case_id, created_at FROM read_log WHERE user_id=? AND kind='resume' ORDER BY id DESC LIMIT 1",
            (uid,)).fetchone()
    return (row["case_id"], row["created_at"]) if row else (None, None)


def _case_line(r: dict) -> str:
    title = r["title"] if len(r["title"]) <= 70 else r["title"][:67] + "…"
    focus = _clip(r["focus"], 110)
    status = r["last_turn_status"] or "no turns"
    line = f"  #{r['id']} · {title} · {r['turn_count']} turns · last {status} · {_fmt(r['updated_at'])}"
    return line + (f"\n      focus: {focus}" if focus else "")


def _thread_line(t: dict) -> str:
    title = t["title"] if len(t["title"]) <= 70 else t["title"][:67] + "…"
    focus = "" if (t["focus"] or "") == t["title"] else (t["focus"] or "")
    focus = _clip(focus, 110)
    line = f"  #{t['case_id']} · {title} · {t['turn_count']} turns · last {t['last_turn_status'] or 'no turns'} · {_fmt(t['updated_at'])}"
    return line + (f"\n      focus: {focus}" if focus else "")


def _clip(x: str | None, n: int = 160) -> str:
    x = (x or "").strip().split("\n", 1)[0].strip()        # 확장 52호 — 첫 줄이 제목이다, 본문은 resume 에서
    return x if len(x) <= n else x[:n - 1] + "…"


def _thread_block(app, uid: int, case_id: int, wt: str) -> list[str]:
    """확장 18호 — 현재 스레드의 작은 resume: focus · open · next · anchor 한 줄. 전체 7칸은 resume(case_id)."""
    r = app.resume(uid, case_id, "continuity", wt)
    lines = [f"Current thread (bound to this worktree): #{case_id} · {_clip(r['title'], 90)}"]
    if r["focus"] and r["focus"]["statement"] != r["title"]:
        lines.append(f"  focus: {_clip(r['focus']['statement'])}")
    lines.append(f"  open:  {_clip(r['open']['statement']) if r['open'] else '(not declared)'}")
    lines.append(f"  next:  {_clip(r['next']['statement']) if r['next'] else '(not declared)'}")
    if r.get("terms"):   # 확장 55호 — 이 스레드의 말: 다음 에이전트가 같은 이름을 쓰게
        lines.append("  terms: " + " · ".join(f"{t['term']} = {_clip(t['meaning'], 60)}" for t in r["terms"][:6]))
    a = r.get("anchor") or {}
    if a.get("head"):
        cp = a.get("last_checkpoint")
        cps = (f" · last checkpoint {_fmt(cp['at'])} @ {cp['head'][:12]} dirty {cp['dirty']}"
               f" · changed since: {'yes' if a.get('changed_since_checkpoint') else 'no'}") if cp else " · no checkpoint yet"
        lines.append(f"  anchor: {a['branch']} @ {a['head']} · dirty {a['dirty']}{cps}")
        if a.get("commits"):
            c = a["commits"][0]
            lines.append(f"  last commit on thread: {c['head'][:12]} {_clip(c['message'], 80)}")
    n_c, n_d = len(r["constraints"]), len(r.get("decisions", []))
    lines.append(f"  thread-scope constraints {n_c} · decisions {n_d} → resume({case_id}) for all seven fields before continuing.")
    return lines, r


def session_start(app, uid: int, wt: str) -> dict:
    """확장 15·18호 — 이 worktree 의 current thread(작은 resume) + 저장소의 repo-scope durable state + 열린 스레드.
    closed 스레드는 넣지 않는다(search 가 닿는다). 스레드가 없는 저장소면 옛 방식(열린 케이스)."""
    db = app.db
    reads.ensure(db)
    v = vitals.case_vitals(db, uid)
    lt = threads.list_threads(db, uid, wt, v)
    lines = []
    if lt["threads"]:
        opened = [t for t in lt["threads"] if t["state"] == "open"]
        cur = next((t for t in opened if t["case_id"] == lt["current"]), None)
        lines.append(f"casebook (MCP) — repository {lt['repo']} · worktree {lt['worktree']}")
        repo_state = None
        if cur:
            block, r = _thread_block(app, uid, cur["case_id"], wt)
            lines += block
            repo_state = r.get("repo_state")
        else:
            lines.append("No thread is bound to this worktree — open_thread(focus) for new work, or switch_thread(case_id).")
            if opened:
                repo_state = app.resume(uid, opened[0]["case_id"], "continuity").get("repo_state")
        rc = (repo_state or {}).get("constraints", []); rd = (repo_state or {}).get("decisions", []); rt = (repo_state or {}).get("terms", [])
        if rc or rd or rt:
            lines.append("Repository durable state (repo scope — holds for every thread here):")
            lines += [f"  C{x['constraint_id']} {_clip(x['statement'])} ({x['authority']})" for x in rc[:8]]
            lines += [f"  D{x['decision_id']} {_clip(x['statement'])} ({x['authority']})" for x in rd[:8]]
            lines += [f"  T{x['term_id']} {x['term']} = {_clip(x['meaning'], 100)}" for x in rt[:8]]   # 확장 55호
        others = [t for t in opened if not cur or t["case_id"] != cur["case_id"]]
        if others:
            lines.append("Other open threads here (most recent activity first):")
            lines += [_thread_line(t) for t in others[:5]]
    else:
        rows = _cases(db, uid)
        lines.append("casebook (MCP) — no threads in this repository yet; this engineer's open cases, most recent activity first:")
        lines += [_case_line(r) for r in rows] or ["  (none open)"]
        active, at = _last_resume(db, uid)
        if active:
            lines.append(f"Last resumed: #{active} at {_fmt(at)}.")
    elsewhere = []
    now = db.now_ms()
    for repo in threads.all_threads(db, uid, v):
        if repo["identity"] == lt["repo"]:
            continue
        for t in repo["threads"]:
            if t["state"] == "open":
                age = _next_age(state.current_declaration(db, t["case_id"], "next"), now)
                elsewhere.append(f"  {_clip(repo['identity'], 160)} · #{t['case_id']} · "
                                 f"{_clip(t['focus'] or t['title'], 110)} · next: {age}")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                    "additionalContext": _session_context(lines, elsewhere)}}


def post_compact(app, uid: int, wt: str) -> dict:
    """SessionStart(compact) — 열린 바인딩 우선, 없으면 아직 open 인 마지막 resume. open/next 를 같이 준다."""
    db = app.db
    reads.ensure(db)
    cur = threads.current_thread(db, uid, wt)
    active = cur["case_id"] if cur else _last_resume(db, uid)[0]
    case = db.get("case", active) if active else None
    v = vitals.case_vitals(db, uid)
    if case and case["status"] == "open" and active in v:
        block, _r = _thread_block(app, uid, active, wt)
        head = "casebook — context was just compacted. " + block[0].replace("Current thread (bound to this worktree)", "Active thread")
        last = v[active]
        body = block[1:] + [f"  last recorded activity: turn {last['turn_count']} at {_fmt(last['updated_at'])}."]
    else:
        head = "casebook — context was just compacted. No thread is bound here and no open case was resumed."
        body = []
    tail = ("Anything observed or concluded after that point now exists only in the summary above. Record it before "
            "doing anything else: add_evidence for machine output you still have verbatim, note_turn for what you "
            "concluded, decide/constrain for what the engineer decided, declare(next/open) for where you were. "
            "Then resume(case_id) to get durable state back.")
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join([head, *body, tail])}}




def log_session_start(db, uid: int, wt: str, reason: str) -> None:
    """확장 19호 — handoff 기회의 분모. 실패해도 주입은 계속된다."""
    try:
        reads.ensure(db)
        cur = threads.current_thread(db, uid, wt)
        reads.log(db, uid, "session_start", cur["case_id"] if cur else None, reason)
    except Exception:  # noqa: BLE001
        pass


def run(app, uid: int, event: str, wt: str, why: str = "", reason: str = "") -> dict:
    """훅 이벤트 하나 → 훅 스크립트가 그대로 print 할 JSON. event = session-start | post-compact | post-commit | checkpoint."""
    if event in ("session-start", "post-compact"):
        log_session_start(app.db, uid, wt, reason or ("compact" if event == "post-compact" else "startup"))
        return session_start(app, uid, wt) if event == "session-start" else post_compact(app, uid, wt)
    if event == "post-commit":
        r = app.record_commit(uid, wt)
        if not r:
            return {"suppressOutput": True}
        return {"suppressOutput": True, "systemMessage": f"casebook: commit {r['head'][:12]} → thread #{r['case_id']}"}
    if event == "checkpoint":
        r = app.record_checkpoint(uid, wt, why)
        if not r:
            return {"suppressOutput": True}
        return {"suppressOutput": True, "systemMessage": f"casebook: checkpoint ({why}) thread #{r['case_id']} @ {r['head'][:12]} dirty {r['dirty']}"}
    raise ValueError(f"unknown hook event {event!r}")
