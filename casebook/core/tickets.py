"""티켓 계층 — `api/tickets` 11 엔드포인트 + `fn:ticket_transition_guard` 의 도메인 짝.

코어(case)를 읽지 않는다 — case_id 는 자기 증명 int 값이다(신뢰 경계 1).
CAS 의 실체는 ticket_event (ticket_id, base_version) unique.
멱등의 실체는 행 자체의 (…, action_key) unique — 재시도는 같은 행으로 수렴하고,
행-이벤트 사이 크래시 창은 재시도가 이벤트를 보수한다(2026-08-29/31 보강).
Xano 에서 unique 인덱스가 하던 일을 여기서는 sqlite unique 인덱스 + IntegrityError
catch 가 한다 — `.xs` 의 try/catch 구조를 그대로 따른다.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .db import IntegrityError
from .errors import ApiError, NotFoundError

ORG_ID = 1


class Tickets:
    def __init__(self, db: Any) -> None:
        self.db = db

    # ── 조회 ─────────────────────────────────────────────────────────
    def list_members(self) -> dict:
        # id·name 만 투영 — 이메일·비밀번호 해시를 응답에 싣지 않는다
        users = self.db.query("user")
        return {"members": [{"id": u["id"], "name": u["name"]} for u in users]}

    def list_tickets(self, status: str) -> dict:
        where: dict[str, Any] = {"org_id": ORG_ID}
        if status != "all":
            where["status"] = status
        rows = self.db.query("ticket", where=where, order="created_at", desc=True)
        return {"tickets": rows}

    def get_ticket(self, ticket_id: int) -> dict:
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")
        cases = self.db.query("ticket_case", where={"ticket_id": ticket_id}, order="created_at")
        snap_count = self.db.count("ticket_snapshot", where={"ticket_id": ticket_id})
        latest_snapshot = None
        if snap_count > 0:
            latest_list = self.db.query(
                "ticket_snapshot", where={"ticket_id": ticket_id, "version": snap_count}
            )
            latest_snapshot = latest_list[0] if latest_list else None
        events = self.db.query("ticket_event", where={"ticket_id": ticket_id}, order="created_at")
        comments_raw = self.db.query(
            "ticket_comment", where={"ticket_id": ticket_id}, order="created_at"
        )
        redacted_ids = {
            e["payload"].get("comment_id")
            for e in events if e["event_type"] == "redacted"
        }
        comments = [
            {
                "id": c["id"],
                "author_id": c["author_id"],
                "content": "[redacted]" if c["id"] in redacted_ids else c["content"],
                "snapshot_version": c["snapshot_version"],
                "anchor": c["anchor"],
                "redacted": c["id"] in redacted_ids,
                "created_at": c["created_at"],
            }
            for c in comments_raw
        ]
        return {
            "ticket": ticket,
            "cases": cases,
            "latest_snapshot": latest_snapshot,
            "snapshot_count": snap_count,
            "events": events,
            "comments": comments,
        }

    # ── 생성 ─────────────────────────────────────────────────────────
    def create_ticket(self, user_id: int, title: str, description: str,
                      priority: str, action_key: str) -> dict:
        if not (title or "").strip():
            raise ApiError("title is empty")
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        prio = priority if priority in ("low", "high") else "normal"

        def _insert() -> dict:
            number = self.db.count("ticket", where={"org_id": ORG_ID}) + 1
            t = self.db.add("ticket", {
                "org_id": ORG_ID, "number": number, "title": title,
                "description": description, "status": "open", "priority": prio,
                "version": 1, "action_key": action_key,
                "reporter_id": user_id, "assignee_id": None, "resolved_at": None,
            })
            self.db.add("ticket_event", {
                "ticket_id": t["id"], "actor_id": user_id, "event_type": "created",
                "payload": {"number": t["number"]},
                "action_key": action_key, "base_version": None,
            })
            return t

        try:
            created = _insert()
        except IntegrityError:
            # 멱등 재시도면 기존 행 반환, 발번 경합이면 1회 재시도
            mine = self.db.query("ticket", where={"reporter_id": user_id, "action_key": action_key})
            created = mine[-1] if mine else _insert()
        return created

    # ── 연결·인수·전이 ───────────────────────────────────────────────
    def link_case(self, user_id: int, ticket_id: int, case_id: int, action_key: str) -> dict:
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")
        if ticket["reporter_id"] != user_id:
            raise ApiError("only reporter can link the primary case")
        if ticket["status"] not in ("open", "investigating"):
            raise ApiError("case can be linked only while open or investigating")

        primaries = self.db.query("ticket_case", where={"ticket_id": ticket_id, "kind": "primary"})
        link = next((p for p in primaries if p["case_id"] == case_id), None)
        if primaries and link is None:
            raise ApiError("primary case already linked")

        inserted = False
        if link is None:
            try:
                link = self.db.add("ticket_case", {
                    "ticket_id": ticket_id, "kind": "primary", "case_id": case_id,
                    "source_snapshot_version": None, "created_by": user_id,
                })
                inserted = True
            except IntegrityError:
                dup = self.db.query("ticket_case", where={"ticket_id": ticket_id, "case_id": case_id})
                link = dup[-1] if dup else None
        if link is None:
            raise ApiError("link failed — safe to retry with the same action_key")

        if inserted:
            try:
                self.db.add("ticket_event", {
                    "ticket_id": ticket_id, "actor_id": user_id, "event_type": "case_linked",
                    "payload": {"case_id": case_id, "kind": "primary", "attested_by": user_id},
                    "action_key": action_key, "base_version": None,
                })
            except IntegrityError:
                pass  # action_key 재시도 — 링크는 unique 로 멱등, 이벤트만 스킵

        # open → investigating 자동 전이 (전이표의 첫 행) — 새 삽입에서만
        if inserted and ticket["status"] == "open":
            try:
                self.db.add("ticket_event", {
                    "ticket_id": ticket_id, "actor_id": user_id, "event_type": "case_linked",
                    "payload": {"auto_transition": "investigating"},
                    "action_key": None, "base_version": ticket["version"],
                })
                self.db.edit("ticket", ticket_id, {
                    "status": "investigating", "version": ticket["version"] + 1,
                })
            except IntegrityError:
                pass  # base_version 경합 — 다른 전이가 먼저였다. 링크는 그대로 둔다

        return {"ticket": self.db.get("ticket", ticket_id), "link": link}

    def claim(self, user_id: int, ticket_id: int, expected_version: int, action_key: str) -> dict:
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")
        if ticket["status"] != "needs_help":
            raise ApiError("ticket is not in the help queue")
        if ticket["assignee_id"] is not None:
            raise ApiError("already claimed")
        if ticket["version"] != expected_version:
            raise ApiError("version conflict — reload the ticket")

        try:
            self.db.add("ticket_event", {
                "ticket_id": ticket_id, "actor_id": user_id, "event_type": "taken_over",
                "payload": {"assignee_id": user_id},
                "action_key": action_key, "base_version": expected_version,
            })
        except IntegrityError:
            mine = self.db.query("ticket_event", where={"ticket_id": ticket_id, "action_key": action_key})
            if not any(m["actor_id"] == user_id for m in mine):
                raise ApiError("someone else claimed first — reload") from None

        return self.db.edit("ticket", ticket_id, {
            "assignee_id": user_id, "status": "investigating",
            "version": expected_version + 1,
        })

    def _transition_guard(self, ticket_id: int, to: str, expected_version: int,
                          actor_id: int, snapshot_version: int) -> dict:
        # fn:ticket_transition_guard — 전이표는 서버에만 존재한다
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")
        if ticket["version"] != expected_version:
            raise ApiError("version conflict — reload the ticket")
        frm = ticket["status"]
        # 바통 보유자(2026-09-02, .xs 와 동일): assignee 있으면 assignee, 없으면 reporter.
        holder = ticket["assignee_id"] if ticket["assignee_id"] is not None else ticket["reporter_id"]
        # 최신 패킷의 발행자 — 핸드오프(hand_back/release)에 상태 문서가 따라가는지 검사
        snap_count = self.db.count("ticket_snapshot", where={"ticket_id": ticket_id})
        latest_by = 0
        if snap_count:
            latest = self.db.query("ticket_snapshot", where={"ticket_id": ticket_id, "version": snap_count})
            if latest:
                latest_by = latest[-1]["published_by"]

        if frm == "investigating" and to == "needs_help":
            if actor_id != holder:
                raise ApiError("only the current holder can request help — the assignee while assigned, otherwise the reporter")
            if snap_count == 0:
                raise ApiError("publish a handover snapshot before requesting help")
            if ticket["assignee_id"] is not None and latest_by != actor_id:
                raise ApiError("publish your handover packet before releasing the ticket — the queue must carry your latest state")
        elif frm == "investigating" and to == "hand_back":
            if ticket["assignee_id"] is None:
                raise ApiError("nothing to hand back — no assignee holds this ticket")
            if actor_id != ticket["assignee_id"]:
                raise ApiError("only the current assignee can hand the ticket back")
            if latest_by != actor_id:
                raise ApiError("publish your handover packet before handing the ticket back — the reporter must receive your latest state")
        elif frm == "investigating" and to == "resolved":
            if actor_id != holder:
                raise ApiError("only the current holder can resolve — the assignee while assigned, otherwise the reporter")
            final = self.db.query(
                "ticket_snapshot", where={"ticket_id": ticket_id, "version": snapshot_version}
            )
            if not final:
                raise ApiError("resolution requires an existing final snapshot version")
        elif frm == "resolved" and to == "closed":
            if actor_id != ticket["reporter_id"]:
                raise ApiError("only reporter can close")
        elif frm == "resolved" and to == "investigating":
            if actor_id not in (ticket["reporter_id"], ticket["assignee_id"]):
                raise ApiError("only reporter or last assignee can reopen")
        else:
            # needs_help → investigating 은 여기 없다 — claim 엔드포인트 전용 경로다
            raise ApiError("illegal transition")
        return ticket

    def transition(self, user_id: int, ticket_id: int, to: str, expected_version: int,
                   snapshot_version: int, action_key: str) -> dict:
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        ticket = self._transition_guard(ticket_id, to, expected_version, user_id, snapshot_version)

        # 바통 모델: assignee 가 놓는 두 전이 — release(needs_help, assignee 있음) · hand_back
        if to == "needs_help" and ticket["assignee_id"] is not None:
            etype = "released"
        elif to == "hand_back":
            etype = "handed_back"
        else:
            etype = {
                "needs_help": "help_requested",
                "resolved": "resolved",
                "closed": "closed",
            }.get(to) or ("reopened" if to == "investigating" and ticket["status"] == "resolved" else "")
        if not etype:
            raise ApiError("illegal transition")

        try:
            self.db.add("ticket_event", {
                "ticket_id": ticket_id, "actor_id": user_id, "event_type": etype,
                "payload": {"to": to, "snapshot_version": snapshot_version, "assignee_was": ticket["assignee_id"]},
                "action_key": action_key, "base_version": expected_version,
            })
        except IntegrityError:
            # 내 재시도인지(멱등 재적용) 남의 선점인지(409 의미) 구분
            mine = self.db.query("ticket_event", where={"ticket_id": ticket_id, "action_key": action_key})
            if not any(m["actor_id"] == user_id for m in mine):
                raise ApiError("version conflict — someone else transitioned first, reload") from None

        # 행 갱신 — 재시도에서도 같은 값으로 다시 적용된다(idempotent set)
        data: dict[str, Any] = {"status": to, "version": expected_version + 1}
        if to == "resolved":
            data["resolved_at"] = self.db.now_ms()
        elif to == "hand_back":
            data["status"] = "investigating"          # 의사 전이 — 상태는 그대로, assignee 만 비운다
            data["assignee_id"] = None
        elif to == "needs_help" and ticket["assignee_id"] is not None:
            data["assignee_id"] = None                # release — 큐로 돌아가 누구든 claim
        self.db.edit("ticket", ticket_id, data)
        return self.db.get("ticket", ticket_id)

    # ── 스냅샷·포크·코멘트·redaction ─────────────────────────────────
    def snapshot(self, user_id: int, ticket_id: int, source_case_id: int,
                 source_turn_count_before: int, source_turn_count_after: int,
                 brief: Any, evidence_items: Any, record_content: Any,
                 action_key: str) -> dict:
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")
        # 바통 모델(2026-09-02): 발행은 보유자만 — assignee 있으면 assignee, 없으면 reporter
        holder = ticket["assignee_id"] if ticket["assignee_id"] is not None else ticket["reporter_id"]
        if user_id != holder:
            raise ApiError("only the current holder can publish — the assignee while assigned, otherwise the reporter")
        if source_turn_count_before != source_turn_count_after:
            raise ApiError("case changed while assembling the packet — fetch and publish again")
        if not isinstance(evidence_items, list):
            raise ApiError("evidence_items must be an array")

        # 항목별 서버 해시 — fork 시드 검증의 기준은 서버 계산값만이다
        bundle_items = []
        for idx, item in enumerate(evidence_items, start=1):
            content = item.get("content") or ""
            bundle_items.append({
                "idx": idx,
                "id": item.get("id"),
                "kind": item.get("kind"),
                "content": item.get("content"),
                "source": item.get("source"),
                "char_len": len(content),
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            })

        def _insert() -> dict:
            version = self.db.count("ticket_snapshot", where={"ticket_id": ticket_id}) + 1
            snap = self.db.add("ticket_snapshot", {
                "ticket_id": ticket_id, "action_key": action_key, "version": version,
                "source_case_id": source_case_id,
                "source_turn_count": source_turn_count_after,
                "brief": brief, "evidence_bundle": {"items": bundle_items},
                "record_content": record_content, "published_by": user_id,
            })
            self.db.add("ticket_event", {
                "ticket_id": ticket_id, "actor_id": user_id,
                "event_type": "snapshot_published",
                "payload": {"version": snap["version"], "source_case_id": source_case_id},
                "action_key": action_key, "base_version": None,
            })
            return snap

        try:
            created = _insert()
        except IntegrityError:
            # (ticket_id, action_key) unique — 내 재시도. 키가 행을 정확히 지목하며,
            # 크래시 창(행은 있고 이벤트가 없음)이면 이벤트를 보수한다.
            mine = self.db.query("ticket_snapshot", where={"ticket_id": ticket_id, "action_key": action_key})
            if mine:
                created = mine[-1]
                evs = self.db.count("ticket_event", where={"ticket_id": ticket_id, "action_key": action_key})
                if evs == 0:
                    self.db.add("ticket_event", {
                        "ticket_id": ticket_id, "actor_id": user_id,
                        "event_type": "snapshot_published",
                        "payload": {"version": created["version"], "source_case_id": source_case_id},
                        "action_key": action_key, "base_version": None,
                    })
            else:
                created = _insert()  # (ticket_id, version) 발번 경합 — 1회 재시도
        return created

    def fork(self, user_id: int, ticket_id: int, case_id: int,
             snapshot_version: int, action_key: str) -> dict:
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        if not case_id or case_id <= 0:
            raise ApiError("case_id is required")
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")
        if ticket["assignee_id"] != user_id:
            raise ApiError("only the current assignee can register a takeover fork")
        if ticket["status"] != "investigating":
            raise ApiError("fork is registered while investigating (after claim)")
        src = self.db.query("ticket_snapshot", where={"ticket_id": ticket_id, "version": snapshot_version})
        if not src:
            raise ApiError("snapshot_version does not exist on this ticket")

        dup = self.db.query("ticket_case", where={"ticket_id": ticket_id, "case_id": case_id})
        if dup:
            return {"link": dup[-1], "ticket": ticket}

        link = None
        fork_ok = False
        try:
            link = self.db.add("ticket_case", {
                "ticket_id": ticket_id, "kind": "fork", "case_id": case_id,
                "source_snapshot_version": snapshot_version, "created_by": user_id,
            })
            fork_ok = True
        except IntegrityError:
            dup2 = self.db.query("ticket_case", where={"ticket_id": ticket_id, "case_id": case_id})
            link = dup2[-1] if dup2 else None
        if fork_ok:
            try:
                self.db.add("ticket_event", {
                    "ticket_id": ticket_id, "actor_id": user_id, "event_type": "forked",
                    "payload": {
                        "case_id": case_id, "snapshot_version": snapshot_version,
                        "attested_by": user_id,
                    },
                    "action_key": action_key, "base_version": None,
                })
            except IntegrityError:
                pass  # action_key 재시도 — 링크는 unique 로 멱등, 이벤트만 스킵
        return {"link": link, "ticket": ticket}

    def comment(self, user_id: int, ticket_id: int, content: str,
                snapshot_version: int, anchor: Any, action_key: str) -> dict:
        if not (content or "").strip():
            raise ApiError("comment is empty")
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")

        snap_ver = None
        if snapshot_version and snapshot_version > 0:
            target = self.db.query(
                "ticket_snapshot", where={"ticket_id": ticket_id, "version": snapshot_version}
            )
            if not target:
                raise ApiError("anchor points to a snapshot version that does not exist")
            snap_ver = snapshot_version

        try:
            # 대상 행 먼저 — 행 자체의 (ticket_id, action_key) unique 가 원자적 방벽이다
            created = self.db.add("ticket_comment", {
                "ticket_id": ticket_id, "action_key": action_key, "author_id": user_id,
                "content": content, "snapshot_version": snap_ver, "anchor": anchor,
            })
            self.db.add("ticket_event", {
                "ticket_id": ticket_id, "actor_id": user_id, "event_type": "commented",
                "payload": {}, "action_key": action_key, "base_version": None,
            })
        except IntegrityError:
            mine = self.db.query("ticket_comment", where={"ticket_id": ticket_id, "action_key": action_key})
            created = mine[-1] if mine else None
            if created is not None:
                evs = self.db.count("ticket_event", where={"ticket_id": ticket_id, "action_key": action_key})
                if evs == 0:
                    self.db.add("ticket_event", {
                        "ticket_id": ticket_id, "actor_id": user_id, "event_type": "commented",
                        "payload": {}, "action_key": action_key, "base_version": None,
                    })
        if created is None:
            raise ApiError("comment failed — safe to retry with the same action_key")
        return created

    def redact(self, user_id: int, ticket_id: int, comment_id: int, action_key: str) -> dict:
        if not (action_key or "").strip():
            raise ApiError("action_key is required")
        if not comment_id or comment_id <= 0:
            raise ApiError("comment_id is required")
        ticket = self.db.get("ticket", ticket_id)
        if ticket is None:
            raise ApiError("ticket not found")
        comment = self.db.get("ticket_comment", comment_id)
        if comment is None or comment["ticket_id"] != ticket_id:
            raise ApiError("comment not found on this ticket")
        if user_id not in (comment["author_id"], ticket["reporter_id"]):
            raise ApiError("only the comment author or ticket reporter can redact")
        try:
            self.db.add("ticket_event", {
                "ticket_id": ticket_id, "actor_id": user_id, "event_type": "redacted",
                "payload": {"comment_id": comment_id},
                "action_key": action_key, "base_version": None,
            })
        except IntegrityError:
            pass  # action_key 재시도 — 이미 redact 됨. 멱등 성공으로 취급
        return {"redacted": True, "comment_id": comment_id}
