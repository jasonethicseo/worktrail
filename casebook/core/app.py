"""Casebook 코어 — 조사 제품. HTTP 를 모른다(규칙 1).

확장 39호 (2026-09-08) 로 기록은 `core/worktrail.py` 로 갈라 나갔다. 여기 남은 것은 **조사**다:
자료를 받아(intake) 첫 답변을 만들고(submit_turn · recover), 웹을 찾고(web_lookup), 레코드를
조립한다(generate_record). 전부 LLM 과 검색이 필요하다.

Casebook 은 Worktrail 을 상속한다 — 조사도 스레드 위에서 일어나므로 기록 기능을 그대로 쓴다.
반대 방향은 없다: Worktrail 은 이 파일을 임포트하지 않고, 모델 키 없이 돈다.

스펙 원본: ~/Desktop/casebook-copilot/backend/xano_export/
"""
from __future__ import annotations

from typing import Any, Protocol

from . import threads, workers
from .errors import InputError, NotFoundError
from .worktrail import Worktrail, _new_key


class LLM(Protocol):
    def answer(self, system: str, prompt: str, effort: str = "none",
               web: bool = False) -> dict[str, Any]: ...
    def record_brief(self, system: str, prompt: str) -> dict[str, Any]: ...
    def draft_query(self, system: str, prompt: str) -> dict[str, Any]: ...
    def assemble_record(self, system: str, prompt: str) -> dict[str, Any]: ...


class Search(Protocol):
    def lookup(self, query: str, engine: str) -> dict[str, Any]: ...


class Casebook(Worktrail):
    """조사 제품. 기록(Worktrail) 위에 답변 생성과 레코드를 얹는다."""

    def __init__(self, db: Any, llm: LLM, search: Search, default_effort: str = "none",
                 invite_codes: frozenset[str] = frozenset(), default_web: bool = False) -> None:
        super().__init__(db, invite_codes=invite_codes)
        self.llm, self.search = llm, search
        # 확장 3호 — 답변 호출의 내장 web_search. 기본 off(.xs 그대로). 켜도 원장 시퀀스는 같다.
        self.default_web = bool(default_web)
        if default_effort not in workers.EFFORT_OPTIONS:
            raise ValueError(
                f"CASEBOOK_EFFORT/default_effort 는 {workers.EFFORT_OPTIONS} 중 하나여야 한다: {default_effort!r}"
            )
        self.default_effort = default_effort
        self._workers.update({          # 조사 워커 셋. external_turn_worker 는 Worktrail 것을 그대로 쓴다.
            "turn_worker": lambda **kw: workers.turn_worker(self.db, self.llm, **kw),
            "web_lookup_worker": lambda **kw: workers.web_lookup_worker(self.db, self.search, **kw),
            "record_worker": lambda **kw: workers.record_worker(self.db, self.llm, **kw),
        })

    # ── 내부 공용 ───────────────────────────────────────────────────
    def _resolve_effort(self, effort: str | None) -> str:
        # 우선순위: 요청값 > 서버 기본값(main.py 의 CASEBOOK_EFFORT) > "none"(.xs 고정값)
        if effort is None:
            return self.default_effort
        if effort not in workers.EFFORT_OPTIONS:
            raise InputError(f"effort must be one of {', '.join(workers.EFFORT_OPTIONS)}")
        return effort

    def _resolve_web(self, web: bool | None) -> bool:
        # 우선순위: 요청값 > 서버 기본값(main.py 의 CASEBOOK_WEB_SEARCH) > False(.xs 고정값)
        if web is None:
            return self.default_web
        if not isinstance(web, bool):
            raise InputError("web must be true or false")
        return web

    # ── turn ────────────────────────────────────────────────────────
    def submit_turn(self, user_id: int, case_id: int, content: str, action_key: str,
                    effort: str | None = None, web: bool | None = None) -> dict:
        self._assert_case_owner(user_id, case_id)
        effort = self._resolve_effort(effort)
        web = self._resolve_web(web)
        if not (content or "").strip():
            raise InputError("Content cannot be empty.")
        existing = self.db.query("turn", where={"case_id": case_id, "action_key": action_key})
        if existing:
            return {"turn_id": existing[0]["id"], "status": existing[0]["status"]}
        # 확장 6호 — 답변이 아직 없는 턴 위에 다음 턴을 얹지 않는다(2026-09-03 라이브 Xano 에도 같은 가드 적용). 얹으면 두 번째 답변이 첫 번째
        # 답변을 못 본 채 만들어지고 브리프는 늦게 끝난 쪽이 남는다. 레코드 생성의 같은 가드와 짝.
        # 기다리거나, 90초 넘게 멈춘 턴이면 사용자가 Re-run(recovery)으로 정리한다(불변식 8).
        if self.db.query("turn", where={"case_id": case_id, "status": "pending"}):
            raise InputError("Previous turn is still processing. Wait for it, or re-run it if it is stuck.")

        sequence = self.db.count("turn", where={"case_id": case_id}) + 1
        with self.db.transaction():
            msg = self.db.add("message", {"case_id": case_id, "role": "user", "content": content})
            ev = self.db.add("evidence", {"case_id": case_id, "kind": "user_paste", "content": content})
            turn = self.db.add("turn", {
                "case_id": case_id, "sequence": sequence, "turn_kind": "input",
                "status": "pending", "answer_status": "not_attempted",
                "action_key": action_key,
                "user_message_id": msg["id"], "evidence_id": ev["id"],
            })
            self.db.attach_turn("message", msg["id"], turn["id"])
            self.db.attach_turn("evidence", ev["id"], turn["id"])
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": turn["id"],
                "event_type": "user_message", "payload": {"message_id": msg["id"]},
            })
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": turn["id"],
                "event_type": "canonical_evidence_created", "payload": {"evidence_id": ev["id"]},
            })
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": turn["id"],
                "event_type": "turn_status_changed", "payload": {"from": None, "to": "pending"},
            })
        self.enqueue("turn_worker", turn_id=turn["id"], effort=effort, web=web)
        return {"turn_id": turn["id"], "status": "pending"}

    def get_turn(self, user_id: int, case_id: int, turn_id: int) -> dict:
        case = self._assert_case_owner(user_id, case_id)
        turn = self.db.get("turn", turn_id)
        if turn is None or turn["case_id"] != case_id:
            raise NotFoundError("turn not found")
        answer_text = None
        if turn["assistant_message_id"] is not None:
            msg = self.db.get("message", turn["assistant_message_id"])
            answer_text = msg["content"] if msg else None
        return {
            "status": turn["status"],
            "answer_status": turn["answer_status"],
            "error": turn["error"],
            "assistant_message": answer_text,
            "brief": case["brief_cache"],
            "brief_stale": case["brief_cache_turn_id"] != turn_id,
            # 도메인 편의 키 — HTTP 응답에는 없다
            "evidence": self.db.get("evidence", turn["evidence_id"]),
            "finished_at": turn["finished_at"],
        }

    def recover(self, user_id: int, case_id: int, source_turn_id: int,
                action_key: str | None = None, effort: str | None = None,
                web: bool | None = None) -> dict:
        self._assert_case_owner(user_id, case_id)
        effort = self._resolve_effort(effort)
        web = self._resolve_web(web)
        action_key = action_key or _new_key()
        existing = self.db.query("turn", where={"case_id": case_id, "action_key": action_key})
        if existing:
            return {"turn_id": existing[0]["id"], "status": existing[0]["status"]}

        source = self.db.get("turn", source_turn_id)
        if source is None or source["case_id"] != case_id:
            raise NotFoundError("turn not found")
        if not (source["status"] == "pending" or source["answer_status"] == "failed"):
            raise InputError("turn is not recoverable")

        sequence = self.db.count("turn", where={"case_id": case_id}) + 1
        with self.db.transaction():
            if source["status"] == "pending":
                abandoned = self.db.edit_where(
                    "turn", source["id"], {"status": "pending"},
                    {
                        "status": "abandoned",
                        "error": {"type": "ProcessingAbandoned"},
                        "finished_at": self.db.now_ms(),
                    },
                )
                if abandoned:
                    self.db.add("ledger", {
                        "case_id": case_id, "turn_id": source["id"],
                        "event_type": "turn_status_changed", "payload": {"to": "abandoned"},
                    })
            turn = self.db.add("turn", {
                "case_id": case_id, "sequence": sequence, "turn_kind": "recovery",
                "status": "pending", "answer_status": "not_attempted",
                "action_key": action_key,
                "source_turn_id": source["id"],
                "source_evidence_id": source["evidence_id"],
                "evidence_id": source["evidence_id"],
                "user_message_id": source["user_message_id"],
            })
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": turn["id"],
                "event_type": "recovery_requested",
                "payload": {
                    "source_turn_id": source["id"],
                    "source_evidence_id": source["evidence_id"],
                    "recovery_turn_id": turn["id"],
                    "action_key": action_key,
                },
            })
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": turn["id"],
                "event_type": "turn_status_changed", "payload": {"to": "pending"},
            })
        self.enqueue("turn_worker", turn_id=turn["id"], effort=effort, web=web)
        return {"turn_id": turn["id"], "status": "pending"}

    # ── evidence / web ──────────────────────────────────────────────
    def draft_query(self, user_id: int, case_id: int, candidate: str) -> dict:
        self._assert_case_owner(user_id, case_id)
        if not (candidate or "").strip():
            raise InputError("candidate is empty")
        d = workers.draft_query_fn(self.db, self.llm, case_id, candidate)
        return {"query": d["query"]}

    def web_lookup(self, user_id: int, case_id: int, query: str, engine: str,
                   action_key: str | None = None) -> dict:
        self._assert_case_owner(user_id, case_id)
        action_key = action_key or _new_key()
        existing = self.db.query("turn", where={"case_id": case_id, "action_key": action_key})
        if existing:
            return {"turn_id": existing[0]["id"], "status": existing[0]["status"]}
        if not (query or "").strip():
            raise InputError("Query cannot be empty")
        if engine not in ("google", "google_news"):
            raise InputError("Engine must be 'google' or 'google_news'")

        sequence = self.db.count("turn", where={"case_id": case_id}) + 1
        with self.db.transaction():
            turn = self.db.add("turn", {
                "case_id": case_id, "sequence": sequence, "turn_kind": "web_lookup",
                "status": "pending", "answer_status": "not_attempted",
                "action_key": action_key,
            })
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": turn["id"],
                "event_type": "web_lookup_requested",
                "payload": {"query": query, "engine": engine},
            })
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": turn["id"],
                "event_type": "turn_status_changed", "payload": {"to": "pending"},
            })
        self.enqueue("web_lookup_worker", turn_id=turn["id"], query=query, engine=engine)
        return {"turn_id": turn["id"], "status": "pending"}

    # ── record / ledger ─────────────────────────────────────────────
    def list_records(self, user_id: int, case_id: int) -> list[dict]:
        self._assert_case_owner(user_id, case_id)
        records = self.db.query("record", where={"case_id": case_id})
        return [
            {k: r[k] for k in (
                "id", "version", "status", "content", "turn_id", "error",
                "created_at", "finished_at",
            )}
            for r in records
        ]

    def generate_record(self, user_id: int, case_id: int) -> dict:
        self._assert_case_owner(user_id, case_id)
        pending = self.db.query("record", where={"case_id": case_id, "status": "pending"})
        if pending:
            return {"record_id": pending[0]["id"], "status": "pending"}

        latest_list = self.db.query("turn", where={"case_id": case_id}, order="sequence", desc=True)
        if not latest_list:
            raise InputError("No turns found for this case.")
        latest = latest_list[0]
        if latest["status"] == "pending":
            raise InputError("Latest turn is still processing.")

        version = self.db.count("record", where={"case_id": case_id}) + 1
        with self.db.transaction():
            rec = self.db.add("record", {
                "case_id": case_id, "turn_id": latest["id"],
                "version": version, "status": "pending",
            })
            self.db.add("ledger", {
                "case_id": case_id, "turn_id": latest["id"],
                "event_type": "record_requested",
                "payload": {"record_id": rec["id"], "version": version},
            })
        self.enqueue("record_worker", record_id=rec["id"])
        return {"record_id": rec["id"], "version": version, "status": "pending"}

    def intake(self, user_id: int, focus: str, worktree: str, items: list[dict], ask: str | None,
               action_key: str, title: str | None = None, authority: str = "agent",
               via: str = "intake", wait_s: float = 5.0, topic: str | None = None, next_: str | None = None) -> dict:
        """스레드를 열고 items 를 각각 external 턴(원문 byte-exact evidence, 답변 호출 없음)으로 넣은 뒤,
        ask 가 있으면 그것을 입력 턴으로 제출한다 — 모델은 items 를 이전 사용자 메시지로 본다.
        external 턴은 워커가 마무리하므로 pending 가드(확장 6호)에 걸리지 않게 여기서 잠깐 드레인한다.
        casebook 은 items 의 뜻을 모른다(OTel 도 알림도 모른다) — 항목은 수집기가 자기 설명적으로 만든다."""
        import time
        if not isinstance(items, list) or not items:
            raise InputError("items must be a non-empty list of {name, content}")
        total = 0
        for it in items:
            if not isinstance(it, dict) or not (it.get("content") or "").strip():
                raise InputError("each item needs a non-empty content")
            n = len(it["content"].encode("utf-8")); total += n
            if n > self.INTAKE_ITEM_CAP:
                raise InputError(f"item '{it.get('name', '?')}' is {n} bytes — cap is {self.INTAKE_ITEM_CAP}; trim at the collector, do not drop it here")
        if total > self.INTAKE_TOTAL_CAP:
            raise InputError(f"bundle is {total} bytes — cap is {self.INTAKE_TOTAL_CAP}")
        # 확장 30호 (2026-09-07): intake 로 연 스레드도 여는 순간 주제에 놓인다 — 랩 스레드가 매번 미분류로 열려
        # 손으로 배정하던 길을 막는다. MCP open_thread 와 같은 규칙("미분류" 거절).
        topic = (topic or "").strip()
        if not topic or topic == "미분류":
            names = [t["name"] for t in self.list_topics(user_id)]
            listed = "; ".join(names) if names else "(none yet)"
            raise InputError("topic is required — the topic this intake thread belongs to (an existing name, or a new one); "
                             f"\"미분류\" is not accepted. Existing topics: {listed}")
        opened = self.open_thread(user_id, focus, worktree, title, authority)
        case_id = opened["case_id"]
        threads.set_origin(self.db, case_id, threads.INTAKE_ORIGIN)   # 확장 39호 — 조사로 열린 스레드
        t = self.topic_by_name(user_id, topic, "", None, [case_id])
        if (ask or "").strip():
            # 확장 37호 — 묶음에 질문이 실리면 첫 답변이 나오고, 그 다음 손은 늘 사람이다(랩 회차 #455·#460·#463 이
            # 전부 "사용자 판정 → 닫는다" 로 끝났다). 자동 생성 스레드가 차례 없이 열려 미정으로 쌓이던 구멍(#472).
            self.declare(user_id, case_id, "next", next_ or "첫 답변을 확인한다.", authority="agent", owner="user")
        turn_ids, evidence_ids = [], []
        for i, it in enumerate(items):
            r = self.external_turn(user_id, case_id, it["content"], action_key=f"{action_key}:item:{i}", note=None,
                                   source={"via": via, "item": it.get("name") or f"item {i + 1}", "bundle": action_key})
            turn_ids.append(r["turn_id"]); evidence_ids.append(r["evidence_id"])
        out = {"case_id": case_id, "repo": opened["repo"], "worktree": opened["worktree"],
               "topic": {"topic_id": t["topic_id"], "name": t["name"]},
               "evidence_ids": evidence_ids, "turn_id": None, "status": "bundle_only"}
        if not (ask or "").strip():
            return out
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            self.run_one()
            if all(self.db.get("turn", t)["status"] != "pending" for t in turn_ids):
                break
            time.sleep(0.05)
        try:
            r = self.submit_turn(user_id, case_id, ask, action_key=f"{action_key}:ask")
            out.update({"turn_id": r["turn_id"], "status": r["status"]})
        except InputError as exc:   # 묶음 턴이 아직 pending — 자동 시작은 미뤄지고 묶음은 남는다
            out["status"] = f"ask_deferred: {exc.message}"
        return out

