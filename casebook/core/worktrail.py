"""Worktrail — 코드 에이전트와 한 작업의 기록. 확장 39호 (2026-09-08) 에 casebook 에서 갈라 나왔다.

두 제품이 한 원장을 나눠 본다:
  **Worktrail**  기록·인계. 스레드 · 주제 · 선언(focus·open·next+차례) · 결정 · 제약 · 배제 · 증거 · git 연결.
                 모델을 부르지 않는다 — `Worktrail(db)` 하나로 선다. 이것이 이 분리의 시험이다.
  **Casebook**   조사·티켓. 자료를 받아(intake) 첫 답변을 만들고 레코드를 조립한다. LLM 과 검색이 필요하다.
                 Casebook 은 Worktrail 을 상속한다 — 조사도 스레드 위에서 일어나므로 기록 기능을 그대로 쓴다.

원장(case · turn · message · evidence · ledger)은 **가르지 않는다**. 기록을 쪼개는 일이 되기 때문이다.
가른 것은 코드와 의존이다: Worktrail 은 조사 코드를 임포트하지 않고, 모델 키 없이 돈다.
"""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Any

from . import auth, prior, review, search, state, status, threads, vitals, recording
from casebook.core.headline import check as check_headline, title_of as headline_title_of
from .errors import AccessDeniedError, InputError, NotFoundError


def _new_key() -> str:
    return uuid.uuid4().hex


class Worktrail:
    """기록 제품. db 하나로 선다 — llm 도 search 도 받지 않는다."""

    def __init__(self, db: Any, invite_codes: frozenset[str] = frozenset()) -> None:
        self.db = db
        # 확장 2호 — 초대 코드 가입. 비어 있으면 열린 가입(로컬 개발·테스트).
        self.invite_codes = frozenset(c.strip() for c in invite_codes if c.strip())
        self._queue: list[tuple[str, dict[str, Any]]] = []
        self._queue_lock = threading.Lock()   # 드레이너 여러 스레드가 같은 큐를 집는다(main.py)
        # 기록의 워커는 하나뿐이고 모델을 부르지 않는다(확장 39호 2단계). 조사 워커는 Casebook 이 더한다.
        self._workers: dict[str, Any] = {
            "external_turn_worker": lambda **kw: recording.external_turn_worker(self.db, **kw),
        }

    def _assert_case_owner(self, user_id: int, case_id: int) -> dict:
        # fn:assert_case_owner — 404 로 남의 case 존재 자체를 흘리지 않는다
        case = self.db.get("case", case_id)
        if case is None or case["user_id"] != user_id:
            raise NotFoundError("case not found")
        return case

    def _secret(self) -> str:
        secret = self.db.meta("auth_secret")
        if secret is None:
            secret = auth.new_secret()
            self.db.set_meta("auth_secret", secret)
        return secret

    # ── auth (Authentication 그룹 3개) ──────────────────────────────

    def signup(self, name: str | None, email: str, password: str,
               invite_code: str | None = None) -> dict:
        if self.invite_codes and (invite_code or "").strip() not in self.invite_codes:
            # 코드 유무를 구분해 알려주지 않는다 — 어느 쪽이든 같은 문
            raise AccessDeniedError("A valid invite code is required to sign up.")
        email = (email or "").strip().lower()
        if self.db.get_by("user", "email", email) is not None:
            raise AccessDeniedError("This account is already in use.")
        user = self.db.add("user", {
            "name": name, "email": email, "password": auth.hash_password(password or ""),
        })
        return {"authToken": auth.create_token(self._secret(), user["id"])}

    def login(self, email: str, password: str) -> dict:
        user = self.db.get_by("user", "email", (email or "").strip().lower())
        if user is None or not auth.check_password(password or "", user["password"]):
            raise AccessDeniedError("Invalid Credentials.")
        return {"authToken": auth.create_token(self._secret(), user["id"])}

    def me(self, token: str) -> dict:
        user_id = self.verify(token)
        user = self.db.get("user", user_id)
        if user is None:
            raise NotFoundError("user not found")
        return {k: user[k] for k in ("id", "created_at", "name", "email")}

    def verify(self, token: str) -> int:
        """Bearer 토큰 → user_id. HTTP 어댑터가 모든 auth=user 라우트에서 쓴다."""
        return auth.verify_token(self._secret(), token)

    # ── case ────────────────────────────────────────────────────────

    def list_cases(self, user_id: int) -> list[dict]:
        """확장 12호 — 행마다 vitals(turn_count · updated_at · record_count · last_turn_status)를
        싣고 updated_at 내림차순(마지막 활동 순)으로 준다. 이전엔 created_at 순이었다."""
        cases = self.db.query("case", where={"user_id": user_id}, order="created_at", desc=True)
        v = vitals.case_vitals(self.db, user_id)
        rows = [
            {
                "id": c["id"], "title": c["title"], "status": c["status"],
                "created_at": c["created_at"],
                "brief_focus": (c["brief_cache"] or {}).get("focus"),
                **v.get(c["id"], {**vitals.EMPTY, "updated_at": c["created_at"]}),
            }
            for c in cases
        ]
        rows.sort(key=lambda r: (r["updated_at"], r["id"]), reverse=True)
        return rows

    def create_case(self, user_id: int, title: str) -> dict:
        if not (title or "").strip():
            raise InputError("Title must not be empty.")
        case = self.db.add("case", {
            "user_id": user_id, "title": title, "status": "open", "schema_version": 1,
        })
        return {"case_id": case["id"]}

    def get_case(self, user_id: int, case_id: int) -> dict:
        case = self._assert_case_owner(user_id, case_id)
        messages = self.db.query("message", where={"case_id": case_id}, order="created_at")
        evidences = self.db.query("evidence", where={"case_id": case_id}, order="created_at")
        turns_raw = self.db.query("turn", where={"case_id": case_id}, order="sequence")
        turns = [
            {k: t[k] for k in (
                "id", "sequence", "turn_kind", "status", "answer_status",
                "user_message_id", "assistant_message_id", "evidence_id",
                "source_turn_id", "error", "created_at", "finished_at",
            )}
            for t in turns_raw
        ]
        return {
            "case": {k: case[k] for k in (
                "id", "title", "status", "created_at", "brief_cache", "brief_cache_turn_id",
            )},
            "conversation": messages,
            "evidence": evidences,
            "turns": turns,
            # 도메인 편의 키 — HTTP 응답에는 없다
            "brief": case["brief_cache"],
        }

    def set_status(self, user_id: int, case_id: int, status: str, result: str | None = None) -> dict:
        case = self._assert_case_owner(user_id, case_id)
        if status not in ("open", "resolved", "archived"):
            raise InputError("invalid status")
        result = (result or "").strip()
        if result:
            result = check_headline(result, "result")       # 확장 52호
        if case["status"] == status:
            if not result or status == "open":
                return {"case_id": case_id, "status": case["status"]}
            # 확장 48호 — 이미 닫힌 스레드에 결과를 나중에 붙인다. 사람이 화면에서 결과 없이 닫고,
            # 에이전트가 뒤에 "정리해줘"로 채우는 길. 원장은 append-only 라 편집이 아니라 새 이벤트다.
            with self.db.transaction():
                self.db.add("ledger", {"case_id": case_id, "turn_id": None, "event_type": "case_status_changed",
                                       "payload": {"from": status, "to": status, "result": result}})
            return {"case_id": case_id, "status": status, "result": result}
        payload: dict[str, Any] = {"from": case["status"], "to": status}
        if result:
            payload["result"] = result      # 확장 31호 — 닫을 때의 결과 한 줄, 원장에 같이 남는다
        with self.db.transaction():
            self.db.edit("case", case_id, {"status": status})
            self.db.add("ledger", {"case_id": case_id, "turn_id": None, "event_type": "case_status_changed", "payload": payload})
        return {"case_id": case_id, "status": status}

    def list_ledger(self, user_id: int, case_id: int) -> list[dict]:
        self._assert_case_owner(user_id, case_id)
        return self.db.query("ledger", where={"case_id": case_id}, order="created_at")

    # ── 읽기 층 (확장 9호) — 케이스북 검색. 소유자 범위, evidence 만, 재요약 없음 ────

    def external_turn(self, user_id: int, case_id: int, content: str, action_key: str,
                      note: str | None = None, source: dict | None = None, note_kind: str | None = None,
                      refs: list[int] | None = None, keep_message: bool = False) -> dict:
        """확장 4호 — MCP 문의 턴. content 는 byte-exact evidence, note 는 호스트의 결론(선택).
        refs (확장 123호) — 이 결론이 기대는 증거 번호들. 글 속 "(증거 #N)" 대신 여기로 온다.

        생성 이벤트는 canonical_evidence_created · turn_status_changed 다. #540 (D16413) — 예전에는 입력 턴을
        본떠 같은 글을 message(role=user)에도 쓰고 user_message 를 냈는데, 기록 경로가 모델을 부르지 않게 된
        뒤(확장 39호) 그 복사본을 읽는 곳이 없어 쓰지 않는다. keep_message 는 조사 intake 전용이다 — 그 묶음은
        다음 입력 턴의 모델 컨텍스트에 대화 줄로 들어가야 한다(workers._build_context).
        """
        self._assert_case_owner(user_id, case_id)
        if not (content or "").strip():
            raise InputError("Content cannot be empty.")
        if (note or "").strip():
            note = check_headline(note, "conclusion")      # 확장 52호 — 관찰(content)은 원문 그대로, 결론만 제목 규칙
        def result_of(turn: dict) -> dict:
            return {"turn_id": turn["id"], "status": turn["status"],
                    "evidence_id": turn["evidence_id"], "sequence": turn["sequence"]}

        # 같은 열쇠는 "같은 요청의 재전송"일 때만 같은 기록이다. 열쇠가 같아도 내용이 다르면 재전송이 아니라
        # 새 기록이므로 새 열쇠로 받는다 — 조용히 합치면 두 번째 기록이 "stored" 라는 답과 함께 사라진다.
        fingerprint = hashlib.sha256(json.dumps(
            [content, note or "", note_kind or "", sorted(int(i) for i in refs or [])],
            ensure_ascii=False).encode()).hexdigest()

        def same_request(key: str) -> bool | None:
            row = self.db.conn.execute(
                "SELECT fingerprint FROM mcp_request WHERE user_id=? AND case_id=? AND request_id=?",
                (user_id, case_id, key)).fetchone()
            if row is None:
                return None                                # 열쇠 표 이전의 턴 — 종전대로 같은 것으로 본다
            return row["fingerprint"] in (None, fingerprint)

        existing = self.db.query("turn", where={"case_id": case_id, "action_key": action_key})
        if existing:
            if same_request(action_key) is not False:
                return result_of(existing[0])
            action_key = uuid.uuid4().hex

        sequence = self.db.count("turn", where={"case_id": case_id}) + 1
        with self.db.transaction():
            claimed = self.db.conn.execute(
                "INSERT OR IGNORE INTO mcp_request(user_id, request_id, case_id, turn_id, created_at, fingerprint) "
                "VALUES (?,?,?,?,?,?)", (user_id, action_key, case_id, None, self.db.now_ms(), fingerprint))
            if claimed.rowcount == 0 and same_request(action_key) is False:
                action_key = uuid.uuid4().hex
                self.db.conn.execute(
                    "INSERT INTO mcp_request(user_id, request_id, case_id, turn_id, created_at, fingerprint) "
                    "VALUES (?,?,?,?,?,?)", (user_id, action_key, case_id, None, self.db.now_ms(), fingerprint))
            elif claimed.rowcount == 0:
                request = self.db.conn.execute(
                    "SELECT turn_id FROM mcp_request WHERE user_id=? AND case_id=? AND request_id=?",
                    (user_id, case_id, action_key)).fetchone()
                if request is None or request["turn_id"] is None:
                    raise InputError("request_id points to an incomplete record")
                turn = self.db.get("turn", request["turn_id"])
                if turn is None:
                    raise InputError("request_id points to a missing record")
                return result_of(turn)
            msg = self.db.add("message", {"case_id": case_id, "role": "user", "content": content}) if keep_message else None
            ev = self.db.add("evidence", {
                "case_id": case_id, "kind": "user_paste", "content": content,
                "source": source or {"via": "mcp"},
            })
            turn = self.db.add("turn", {
                "case_id": case_id, "sequence": sequence, "turn_kind": "external",
                "status": "pending", "answer_status": "not_attempted",
                "action_key": action_key,
                "user_message_id": msg["id"] if msg else None, "evidence_id": ev["id"],
            })
            self.db.attach_turn("evidence", ev["id"], turn["id"])
            if msg:
                self.db.attach_turn("message", msg["id"], turn["id"])
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
            self.db.conn.execute(
                "UPDATE mcp_request SET turn_id=? WHERE user_id=? AND case_id=? AND request_id=?",
                (turn["id"], user_id, case_id, action_key))
        self.enqueue("external_turn_worker", turn_id=turn["id"], note=note, note_kind=note_kind,
                     **({"refs": [int(i) for i in refs]} if refs else {}))
        return {"turn_id": turn["id"], "status": "pending", "evidence_id": ev["id"], "sequence": sequence}

    def search_evidence(self, user_id: int, query: str, limit: int = 10,
                        include_archived: bool = False) -> list[dict]:
        return search.search_evidence(self.db, user_id, query, limit, include_archived)

    def inspect_evidence(self, user_id: int, evidence_id: int) -> dict:
        return search.inspect_evidence(self.db, user_id, evidence_id)

    # ── durable state (확장 10호) — decide · constrain · rule_out · resume ──────────

    def decide(self, user_id: int, case_id: int, statement: str, reason: str | None = None,
               evidence_ids: list[int] | None = None, authority: str = "agent",
               supersedes: int | None = None, scope: str = "thread", client: str | None = None) -> dict:
        statement = check_headline(statement, "decision")            # 확장 52호
        if reason:
            reason = check_headline(reason, "decision reason")        # 확장 58호 — 카드의 "왜" 줄이 이유의 첫 줄이다(D14625)
        case = self._assert_case_owner(user_id, case_id)
        siblings = threads.repo_case_ids(self.db, user_id, case_id)
        if scope == "repo" and siblings is None:
            raise InputError("scope=repo needs a thread — this case belongs to no repository (open_thread / switch_thread first)")
        return state.record_decision(self.db, user_id, case, statement, reason, evidence_ids, authority, supersedes,
                                     scope, siblings, client=client)

    def constrain(self, user_id: int, case_id: int, statement: str, reason: str | None = None,
                  authority: str = "agent", supersedes: int | None = None, scope: str = "thread") -> dict:
        statement = check_headline(statement, "constraint")            # 확장 52호
        if reason:
            reason = check_headline(reason, "constraint reason")        # 확장 58호 (D14625)
        case = self._assert_case_owner(user_id, case_id)
        siblings = threads.repo_case_ids(self.db, user_id, case_id)
        if scope == "repo" and siblings is None:
            raise InputError("scope=repo needs a thread — this case belongs to no repository (open_thread / switch_thread first)")
        return state.record_constraint(self.db, user_id, case, statement, reason, authority, supersedes, scope, siblings)

    def define(self, user_id: int, case_id: int, term: str, meaning: str, authority: str = "agent",
               supersedes: int | None = None, scope: str = "thread") -> dict:
        case = self._assert_case_owner(user_id, case_id)
        siblings = threads.repo_case_ids(self.db, user_id, case_id)
        if scope == "repo" and siblings is None:
            raise InputError("scope=repo needs a thread — this case belongs to no repository (open_thread / switch_thread first)")
        return state.record_term(self.db, user_id, case, term, meaning, authority, supersedes, scope, siblings)

    def rule_out(self, user_id: int, case_id: int, hypothesis: str, scope: str,
                 evidence_ids: list[int] | None = None) -> dict:
        case = self._assert_case_owner(user_id, case_id)
        return state.rule_out(self.db, user_id, case, hypothesis, scope, evidence_ids)

    # ── 스레드 층 (확장 15호) — repo · thread · worktree 바인딩 · focus 선언 ─────────

    def declare(self, user_id: int, case_id: int, kind: str, statement: str, authority: str = "agent",
                owner: str | None = None) -> dict:
        case = self._assert_case_owner(user_id, case_id)
        return state.declare(self.db, case, kind, statement, authority, owner)

    # ── intake (확장 21호) — 받는 문: 묶음 evidence + 첫 질문으로 스레드를 자동 시작 ─────────
    INTAKE_ITEM_CAP = 100_000    # bytes, 항목당 — 조리법(case 446 D10599)의 상한. 수집기가 지키고 서버가 거부한다
    INTAKE_TOTAL_CAP = 400_000   # bytes, 묶음

    def open_thread(self, user_id: int, focus: str, worktree: str, title: str | None = None,
                    authority: str = "agent") -> dict:
        if not (focus or "").strip():
            raise InputError("focus is empty — say in one sentence what this thread is for")
        focus = check_headline(focus, "focus")          # 확장 52호 — 케이스를 만들기 전에 거절한다(고아 케이스 방지)
        # 제목을 따로 주지 않으면 focus 의 첫 줄(제목)이 스레드 제목이다 — 본문까지 제목이 되면 화면 머리가 문단이 된다
        r = self.create_case(user_id, (title or headline_title_of(focus)).strip()[:200])
        case = self.db.get("case", r["case_id"])
        return threads.open_thread(self.db, user_id, case, focus, worktree, authority)

    def switch_thread(self, user_id: int, case_id: int | None, worktree: str) -> dict:
        case = self._assert_case_owner(user_id, case_id) if case_id is not None else None
        # 확장 17호 — 떠나는 스레드에 마지막 git 관찰을 남긴다(pause/switch 의 checkpoint)
        prev = threads.current_thread(self.db, user_id, worktree)
        if prev and (case is None or prev["case_id"] != case["id"]):
            threads.record_checkpoint(self.db, user_id, prev["case_id"], worktree, "pause" if case is None else f"switch to {case['id']}")
        return threads.switch_thread(self.db, user_id, case, worktree)

    # ── git 자동 연결 (확장 17호) — 훅이 부른다. 바인딩된 스레드가 없으면 조용히 None ──

    def close_thread(self, user_id: int, case_id: int, result: str | None = None) -> dict:
        self.set_status(user_id, case_id, threads.CLOSED, result)      # 원장에 case_status_changed(+result) 가 남는다
        case = self._assert_case_owner(user_id, case_id)
        out = threads.close_thread(self.db, user_id, case)
        out["result"] = (result or "").strip() or None
        return out

    def current_thread(self, user_id: int, worktree: str) -> dict | None:
        return threads.current_thread(self.db, user_id, worktree)

    def list_threads(self, user_id: int, worktree: str) -> dict:
        return threads.list_threads(self.db, user_id, worktree, vitals.case_vitals(self.db, user_id))

    # ── Tracker 읽기 (확장 22호) — 웹용. worktree 를 모르니 바인딩된 worktree 가 있으면 그것으로 live git 을 읽는다 ──

    def record_commit(self, user_id: int, worktree: str) -> dict | None:
        cur = threads.current_thread(self.db, user_id, worktree)
        return threads.record_commit(self.db, user_id, cur["case_id"], worktree) if cur else None

    def record_checkpoint(self, user_id: int, worktree: str, why: str = "") -> dict | None:
        cur = threads.current_thread(self.db, user_id, worktree)
        return threads.record_checkpoint(self.db, user_id, cur["case_id"], worktree, why) if cur else None

    def list_all_threads(self, user_id: int) -> list[dict]:
        return threads.all_threads(self.db, user_id, vitals.case_vitals(self.db, user_id))

    def overview(self, user_id: int) -> list[dict]:
        """확장 26호 — 저장소를 고르기 전에 읽는 열린 작업. 기존 all_threads 투영, 모델 호출 없음."""
        fields = ("case_id", "title", "focus", "next", "updated_at", "bound_worktrees")
        repos = []
        for repo in self.list_all_threads(user_id):
            opened = [{key: t[key] for key in fields} for t in repo["threads"] if t["state"] == "open"]
            if opened:
                repos.append({"identity": repo["identity"], "hint": repo["hint"], "threads": opened,
                              "open_count": len(opened),
                              "updated_at": max(t["updated_at"] or 0 for t in opened)})
        repos.sort(key=lambda r: (-r["updated_at"], r["identity"]))
        return repos

    def thread_state(self, user_id: int, case_id: int) -> dict:
        self._assert_case_owner(user_id, case_id)
        wt = threads.bound_worktree_of(self.db, user_id, case_id)
        out = self.resume(user_id, case_id, "continuity", wt)
        if (out.get("anchor") or {}).get("mismatch"):
            # 원격 서버에는 클라이언트의 checkout이 없다. 화면은 기록된 git 관찰을 읽는다.
            # MCP resume의 명시적 worktree 불일치 검사는 그대로 둔다.
            out["anchor"] = threads.anchor_history(self.db, case_id)
        out["recent"] = review.recent_events(self.db, user_id, case_id)   # 사람 화면용 마지막 발자국 — MCP resume 에는 없다
        # 확장 60호 — 노트 전부(최신 먼저), 갈래(note_kind)와 함께: 스레드 화면의 노트 서랍이 읽는다. 사용자 2026-09-12
        # "기록이 어디 된 거임? 507 열어도 안 보이는데" — 노트는 화면 어디에도 없었다. MCP resume 에는 여전히 없다(과거 추론을 주지 않는다).
        out["notes"] = [e for e in reversed(review.events_for_cases(self.db, user_id, [case_id]).get(case_id, []))
                        if e["kind"] == "note"]
        # 확장 56호 — 대체된 결정·제약도 사람 화면에는 접혀서 보인다: 찾기에서 누른 "대체됨" 기록이 갈 자리
        out["superseded"] = {"decisions": state.superseded_of(self.db, case_id, "decision_recorded", "decision"),
                             "constraints": state.superseded_of(self.db, case_id, "constraint_recorded", "constraint")}
        # 확장 31호 — 두 축의 교차점: 단계·차례·결과와 이 스레드가 속한 주제
        row, repo_ident = None, None
        for r in self.list_all_threads(user_id):
            for t in r["threads"]:
                if t["case_id"] == case_id:
                    row, repo_ident = t, r["identity"]
        for k in ("phase", "owner", "result", "closed_at", "updated_at", "turn_count", "last_commit"):
            out[k] = (row or {}).get(k)
        # anchor 는 없을 수 있다(스레드 층 이전 케이스 — thread 행도 worktree 바인딩도 없다).
        # .get("anchor", {}) 는 키가 있고 값이 None 이면 기본값을 쓰지 않는다 — 확장 42호의 찾기가
        # 모든 케이스를 누를 수 있게 되면서 드러났다(#442 클릭 → HTTP 500).
        out["repo"] = repo_ident or (out.get("anchor") or {}).get("repo")
        out["topics"] = [{"topic_id": t["id"], "name": t["name"], "conclusion": t.get("conclusion") or None}
                         for t in review.list_topics(user_id=user_id, db=self.db) if case_id in t["case_ids"]]
        return out

    def prior_archive(self, user_id: int) -> dict:
        """확장 43호 — 과거 기록: 무엇이 들어 있는지 보는 화면. 현황·회고와 섞지 않는다."""
        return prior.archive(self.db, user_id)

    def prior_sessions(self, user_id: int, repo_id: int) -> list[dict]:
        return prior.sessions_of(self.db, user_id, repo_id)

    def prior_session(self, user_id: int, session_id: int) -> dict:
        return prior.session_detail(self.db, user_id, session_id)

    def status(self, user_id: int) -> dict:
        """확장 31호 — 현황: 주제로 묶은 스레드, 단계·차례·결과. 저장하지 않고 조립한다."""
        return status.build(self.db, user_id, vitals.case_vitals(self.db, user_id))

    # ---- 확장 24호 — Review(회고): 주제 → 스레드 → 시간순 사건 (도출, 저장 안 함)

    def create_topic(self, user_id: int, name: str, summary: str = "", repos: list[str] | None = None,
                     case_ids: list[int] | None = None, conclusion: str | None = None) -> dict:
        t = review.create_topic(self.db, user_id, name, summary, repos)
        if conclusion is not None and conclusion.strip():
            review.conclude(self.db, t, conclusion)
        if case_ids:
            return self.assign_topic(user_id, t["id"], case_ids)
        return {"topic_id": t["id"], "name": t["name"], "case_ids": []}

    def conclude_topic(self, user_id: int, topic_id: int, conclusion: str | None) -> dict:
        t = review.get_topic(self.db, user_id, topic_id)
        if t is None:
            raise NotFoundError("topic not found")
        if (conclusion or "").strip():
            conclusion = check_headline(conclusion, "topic conclusion")   # 확장 52호
        r = review.conclude(self.db, t, conclusion)
        return {"topic_id": r["id"], "name": r["name"], "conclusion": r.get("conclusion"), "concluded_at": r.get("concluded_at")}

    def assign_topic(self, user_id: int, topic_id: int, case_ids: list[int], remove: bool = False) -> dict:
        t = review.get_topic(self.db, user_id, topic_id)
        if t is None:
            raise NotFoundError("topic not found")
        for cid in case_ids:
            self._assert_case_owner(user_id, int(cid))       # 남의 케이스는 404
        return (review.unassign if remove else review.assign)(self.db, t, list(case_ids))

    def merge_topic(self, user_id: int, topic_id: int, into_id: int) -> dict:
        """확장 47호 — 주제 합치기(사람의 손). topic_id 의 스레드가 into_id 로 가고 topic_id 는 사라진다."""
        src = review.get_topic(self.db, user_id, topic_id)
        dst = review.get_topic(self.db, user_id, into_id)
        if src is None or dst is None:
            raise NotFoundError("topic not found")
        return review.merge_topics(self.db, src, dst)

    def move_topic(self, user_id: int, case_ids: list[int], from_id: int, to_id: int) -> dict:
        """확장 47호 — 스레드 옮기기: from 에서 빼고 to 에 넣는다. 스레드는 여러 주제에 속할 수 있으니 '옮기기'는 이 둘의 합이다."""
        src = review.get_topic(self.db, user_id, from_id)
        dst = review.get_topic(self.db, user_id, to_id)
        if src is None or dst is None:
            raise NotFoundError("topic not found")
        if not case_ids:
            raise InputError("case_ids is empty — which threads move?")
        for cid in case_ids:
            self._assert_case_owner(user_id, int(cid))
        ids = [int(c) for c in case_ids]
        gone = review.unassign(self.db, src, ids)
        out = review.assign(self.db, dst, ids)
        out["from"] = {"topic_id": src["id"], "name": src["name"], "case_ids": gone["case_ids"]}
        return out

    def topic_by_name(self, user_id: int, name: str, summary: str = "", repos: list[str] | None = None,
                      case_ids: list[int] | None = None, conclusion: str | None = None) -> dict:
        """이름으로 찾고 없으면 만든다 — MCP 도구가 쓰는 한 번의 호출. conclusion 이 오면 결론 한 줄을 붙인다(확장 31호)."""
        t = review.find_topic(self.db, user_id, name)
        if t is None:
            out = self.create_topic(user_id, name, summary, repos, case_ids, conclusion)
        else:
            if conclusion is not None and conclusion.strip():
                review.conclude(self.db, t, conclusion)
            out = self.assign_topic(user_id, t["id"], case_ids or [])
        if conclusion is not None and conclusion.strip():
            out["conclusion"] = conclusion.strip()
        return out

    def list_topics(self, user_id: int) -> list[dict]:
        return review.list_topics(self.db, user_id)

    def find_topic(self, user_id: int, name: str) -> dict | None:
        return review.find_topic(self.db, user_id, name)

    def review(self, user_id: int) -> dict:
        return review.review(self.db, user_id, vitals.case_vitals(self.db, user_id))

    def resume(self, user_id: int, case_id: int, mode: str = "continuity", worktree: str | None = None) -> dict:
        """확장 16호 — worktree 를 주면 Anchor(live git)를 싣는다. 단, 그 worktree 가 이 스레드의 저장소일 때만."""
        case = self._assert_case_owner(user_id, case_id)
        if worktree:
            threads.reconcile_repo(self.db, user_id, worktree)  # 자기 저장소의 identity 만 승격한다
        siblings = threads.repo_case_ids(self.db, user_id, case_id)
        repo = threads.repo_of_case(self.db, case_id)
        anc = None
        if worktree and repo is not None and not threads.is_virtual(worktree):
            a = threads.anchor(worktree)
            if a["repo"] == repo["identity"]:
                anc = threads.compare_anchor(a, threads.anchor_history(self.db, case_id))   # live 정본 + 마지막 관찰 비교
            else:
                anc = {"worktree": a["worktree"], "repo": a["repo"],
                       "mismatch": f"this worktree is {a['repo']}, the thread belongs to {repo['identity']}"}
        elif repo is not None:
            anc = threads.anchor_history(self.db, case_id)                                  # worktree 없이는 과거 관찰만
        return state.resume(self.db, case, mode, siblings, anc, repo["identity"] if repo else None)

    # ── 워커 (Xano background task 자리) ────────────────────────────

    def enqueue(self, worker: str, **kwargs: Any) -> None:
        """워커 잡 등록. 테스트가 at-least-once 중복 전달을 흉내낼 때도 쓴다."""
        if worker not in self._workers:
            raise KeyError(f"unknown worker: {worker}")
        with self._queue_lock:
            self._queue.append((worker, kwargs))

    def pending_jobs(self) -> int:
        with self._queue_lock:
            return len(self._queue)

    def run_one(self) -> bool:
        """큐에서 잡 하나를 집어 끝까지 돌린다. 비어 있으면 False.

        여러 드레이너 스레드가 동시에 불러도 한 잡은 한 스레드만 집는다(락).
        워커 안의 예외는 여기서 삼키지 않는다 — 호출자(드레이너)가 격리한다.
        """
        with self._queue_lock:
            if not self._queue:
                return False
            worker, kwargs = self._queue.pop(0)
        self._workers[worker](**kwargs)
        return True

    def run_workers(self) -> int:
        """대기 중인 워커를 전부 끝까지 돌린다. 테스트는 동기로 부른다.

        Xano 는 background task 였다. 여기서는 인메모리 큐가 그 자리이고, AWS 로 가면
        SQS 가 된다. 불변식 7(hidden retry 없음)·14(터미널 write-once)는 워커 진입 시
        조건부 클레임이 지킨다 — 중복 전달은 조용한 no-op 다.
        """
        n = 0
        while self.run_one():
            n += 1
        return n
