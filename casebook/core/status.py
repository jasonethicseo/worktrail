"""확장 31호 — 현황(status): 주제로 묶은 스레드 목록, 단계·차례·결과 포함. 저장하지 않고 읽을 때 조립한다.

Tracker 의 첫 화면이 저장소별(all_threads)에서 주제별로 바뀐다 — 사용자 2026-09-07: 저장소 축(Tracker)과 주제 축(Review)을
서로 빌려 준다. 주제가 필수(확장 30호)가 된 뒤라 가능해졌다. 어느 주제에도 없는 옛 스레드는 unassigned 로 그대로 보인다.
"""
from __future__ import annotations

import time
from typing import Any

from . import prior, phase, review, threads

RECENT_MS = 7 * 24 * 3600 * 1000
# 확장 32호 — 현황에 사실을 넣는다(사용자 2026-09-07: "회고가 현황보다 작업 추적에 더 좋아 보인다" — 현황은 약속(next)만 보였고
# 회고는 원장의 사실을 보였다). 행마다 마지막으로 한 일, 맨 위에 최근 일어난 일(결정·닫힘·열림·제약·배제·질문).
LATEST_KINDS = ("opened", "closed", "decision", "constraint", "ruled_out", "asked")
LATEST_LIMIT = 24
# 확장 64호 — "지금" 스트림(#512): 종류를 가리지 않고 최근 사건 40개. 화면 오른쪽 띠에 흐르고 5초마다 새로 받는다.
# latest 와 다른 점 — 노트·증거·커밋·선언까지 전부(살아 있는 느낌은 잔 사건에서 온다), 노트는 갈래(note_kind)를 싣는다.
STREAM_LIMIT = 40
STREAM_SKIP = ("evidence", "record")   # 증거는 스레드의 증거 칸에, record 는 옛 조사 산출물 — 나머지는 전부 흐른다
_TOPIC_ORDER = {phase.TOPIC_ACTIVE: 0, phase.TOPIC_QUIET: 1, phase.TOPIC_DONE: 2}
_THREAD_ORDER = {phase.MINE: 0, phase.UNSET: 1, phase.AGENT: 2, phase.WATCH: 3, phase.CLOSED: 4}   # 내 손이 걸린 것 먼저

# 확장 39호 (2026-09-08) — 기록 도구와 조사 도구를 가른다. casebook 은 두 가지를 한 테이블에 담고 있다:
# 에이전트와 한 작업(스레드, MCP 문으로 들어온다)과 casebook 이 스스로 답한 조사(HTTP intake 로 들어온다).
# 사용자 2026-09-08: "저 질문 자체를 내가 한 적이 없는데 왜 생긴 거냐" — 랩 러너의 고정 문구가 현황에 섞여 있었다.
# origin 은 threads 가 여는 순간 저장한다(도출하면 랩 판정을 기록할 때 뒤집힌다 — threads.origins 주석).
AGENT_ORIGIN, INTAKE_ORIGIN = threads.AGENT_ORIGIN, threads.INTAKE_ORIGIN
origins = threads.origins


def _sort_threads(ths: list[dict[str, Any]]) -> None:
    ths.sort(key=lambda t: (_THREAD_ORDER.get(t["phase"], 9), -(t["updated_at"] or 0), -t["case_id"]))


def build(db, user_id: int, vitals: dict[int, dict[str, Any]], now: int | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000) if now is None else now
    by_case: dict[int, dict[str, Any]] = {}
    for rep in threads.all_threads(db, user_id, vitals):
        for t in rep["threads"]:
            row = dict(t); row["repo"] = rep["identity"]; row["repo_hint"] = rep["hint"]
            by_case[t["case_id"]] = row
    org = origins(db, user_id, list(by_case))
    for cid, row in by_case.items():
        row["origin"] = org.get(cid, AGENT_ORIGIN)
    events = review.events_for_cases(db, user_id, list(by_case)) if by_case else {}
    latest: list[dict[str, Any]] = []
    stream: list[dict[str, Any]] = []
    for cid, row in by_case.items():
        for e in events.get(cid, []):
            if e["kind"] in STREAM_SKIP:
                continue
            stream.append({"kind": e["kind"], "text": e["text"], "at": e["at"], "case_id": cid, "title": row["title"],
                           "authority": e.get("authority"), "client": e.get("client"), "note_kind": e.get("note_kind"),
                           "id": e.get("id")})
        evs = [e for e in events.get(cid, []) if e["kind"] not in review.RECENT_SKIP]
        last = evs[-1] if evs else None
        row["last_event"] = ({"kind": last["kind"], "text": last["text"], "at": last["at"], "authority": last.get("authority"),
                              "result": last.get("result"), "note_kind": last.get("note_kind"),   # 65호 — 현황 행의 색
                              **({"later": True} if last.get("later") else {})} if last else None)
        for e in evs:
            if e["kind"] in LATEST_KINDS:
                latest.append({"kind": e["kind"], "text": e["text"], "at": e["at"], "authority": e.get("authority"),
                               "case_id": cid, "title": row["title"], "result": e.get("result"),
                               **({"later": True} if e.get("later") else {})})
    latest.sort(key=lambda e: -e["at"])
    latest = latest[:LATEST_LIMIT]
    stream.sort(key=lambda e: -e["at"])
    stream = stream[:STREAM_LIMIT]
    topics_out: list[dict[str, Any]] = []
    assigned: set[int] = set()
    for tp in review.list_topics(db, user_id):
        ths = [by_case[c] for c in tp["case_ids"] if c in by_case]
        assigned.update(t["case_id"] for t in ths)
        _sort_threads(ths)
        open_ = [t for t in ths if t["state"] == "open"]
        closed = [t for t in ths if t["state"] != "open"]
        last_closed = max((t["closed_at"] or 0) for t in closed) if closed else None
        # 결론이 없으면 마지막으로 닫힌 스레드의 결과가 그 자리를 채운다(시안 규칙)
        last_result = next((t["result"] for t in sorted(closed, key=lambda t: -(t["closed_at"] or 0)) if t.get("result")), None)
        topics_out.append({
            "topic_id": tp["id"], "name": tp["name"], "summary": tp.get("summary") or "",
            "conclusion": tp.get("conclusion") or None, "concluded_at": tp.get("concluded_at"),
            "repos": sorted({t["repo"] for t in ths}),
            "phase": phase.topic_phase([t["phase"] for t in open_]),
            "threads": ths, "open_count": len(open_), "closed_count": len(closed),
            "updated_at": max([(t["updated_at"] or 0) for t in ths] + [tp["created_at"]]),
            "last_closed_at": last_closed or None, "last_result": last_result,
        })
    topics_out.sort(key=lambda t: (_TOPIC_ORDER.get(t["phase"], 9), -(t["updated_at"] or 0)))
    unassigned = [t for c, t in by_case.items() if c not in assigned]
    _sort_threads(unassigned)
    everything = list(by_case.values())
    open_all = [t for t in everything if t["state"] == "open"]
    counts = {
        "open": len(open_all),
        "mine": sum(1 for t in open_all if t["phase"] == phase.MINE),
        "agent": sum(1 for t in open_all if t["phase"] == phase.AGENT),
        "watching": sum(1 for t in open_all if t["phase"] == phase.WATCH),
        "unset": sum(1 for t in open_all if t["phase"] == phase.UNSET),
        "closed_recent": sum(1 for t in everything if t["state"] != "open" and (t["closed_at"] or 0) > now - RECENT_MS),
        "topics": len(topics_out), "repos": len({t["repo"] for t in everything}),
        "intake": sum(1 for t in open_all if t["origin"] == INTAKE_ORIGIN),
    }
    # 확장 42호 — 설치 전 기록은 목록에 섞지 않는다(D13627). 있다는 사실 한 줄만 싣는다.
    pre = prior.summary_all(db, user_id)
    return {"generated_at": now, "topics": topics_out, "unassigned": unassigned, "counts": counts,
            "latest": latest, "stream": stream, "prior": (pre if pre.get("sessions") else None)}
