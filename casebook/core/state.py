"""durable state — 확장 10호 (2026-09-03). "같은 케이스의 과거가 지금의 나를 돕는가."

설계 원칙: 경계는 "누가 만들었는가"가 아니라 **"무엇이 그것을 뒤집을 수 있는가"**다.
  - epistemic: evidence(기계가 관측, 불변) · hypothesis(세계가 반박 가능 — durable state 에 넣지 않는다)
  - normative: goal · constraint · decision — 사람/작업자만 바꾼다. 승인은 요구하지 않고 provenance 만 남긴다
    (decision 은 진실이 아니라 "현재 작업이 따르기로 한 선택"이다). 되돌림은 supersedes 로.
  - verified negative: ruled-out = hypothesis + 그것을 죽인 evidence + **scope**(범위 밖에서는 다시 가설).

저장은 UPDATE 가 아니라 원장 이벤트(append-only, 불변식 13)이고, resume() 이 현재 상태를 projection 한다.
  decision_recorded    {statement, reason, evidence_ids, authority, supersedes, client?}
  constraint_recorded  {statement, reason, authority, supersedes}
  hypothesis_ruled_out {hypothesis, scope, evidence_ids}
resume 은 durable state 만 준다 — 브리프·레코드·note(호스트 해석)는 주지 않는다. Goal 은 첫 입력 원문이다.
"""
from __future__ import annotations

import re
from typing import Any

from .errors import InputError, NotFoundError
from casebook.core.headline import check as check_headline, check_addressed

AUTHORITIES = ("agent", "user")
STATE_EVENTS = ("decision_recorded", "constraint_recorded", "hypothesis_ruled_out")
# 확장 16호 — scope. thread: 이 스레드가 닫히면 같이 archive. repo: 저장소의 모든 스레드에 유효(durable state 로 승격).
# 승격은 닫힘의 부산물이 아니라 선언의 결과다. repo 항목의 supersede 는 같은 저장소의 다른 스레드에서도 된다.
SCOPES = ("thread", "repo")

# 확장 15호 — 선언(declaration): focus · open · next. mutable 컬럼이 아니라 append-only 이벤트이고
# 현재값은 projection 이다. 한 종류에 현재값은 하나 — 새 선언이 직전 현재값을 자동으로 supersede 한다.
DECLARATIONS = {"focus": "focus_declared", "open": "open_declared", "next": "next_declared"}
# 확장 31호 — next 의 차례(owner): 누가 다음 손을 대는가. "user" = 사람 차례, "watch" = 할 일 없이 지켜보는 중,
# 그 밖(agent · codex · claude · gpt …)은 그 에이전트의 차례. 확장 37호: next 에 필수이고, 단계(core/phase.py)가 곧 이것이다.
OWNER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,23}$")
OWNER_USER, OWNER_WATCH = "user", "watch"


def normalize_owner(owner: str | None) -> str | None:
    o = (owner or "").strip().lower()
    if not o:
        return None
    if not OWNER_RE.match(o):
        raise InputError('owner must be a short handle — "user" (the engineer\'s turn), "watch" (nothing to do, just watching) '
                         'or an agent name like "codex" / "claude" / "gpt"')
    return o


def owned_evidence(db, user_id: int, evidence_ids: list[int] | None) -> list[int]:
    """이 사람의 증거인지 — 남의 것·없는 것은 NotFoundError. 결정·배제·노트(확장 123호)가 같은 검사를 쓴다."""
    return _owned_evidence(db, user_id, evidence_ids)


def _owned_evidence(db, user_id: int, evidence_ids: list[int] | None) -> list[int]:
    ids = [int(i) for i in (evidence_ids or [])]
    for eid in ids:
        ev = db.get("evidence", eid)
        case = db.get("case", ev["case_id"]) if ev else None
        if ev is None or case is None or case["user_id"] != user_id:
            raise NotFoundError(f"evidence not found: {eid}")
    return ids


def _active_of(db, case_id: int, event_type: str) -> list[dict[str, Any]]:
    events = db.query("ledger", where={"case_id": case_id, "event_type": event_type}, order="id")
    superseded = {e["payload"].get("supersedes") for e in events if e["payload"].get("supersedes")}
    return [e for e in events if e["id"] not in superseded]


def _active_repo_scope(db, case_ids: set[int], event_type: str) -> list[dict[str, Any]]:
    """저장소의 모든 스레드에서 scope=repo 인 활성 이벤트. supersede 는 스레드를 넘어 추적한다."""
    events: list[dict[str, Any]] = []
    for cid in sorted(case_ids):
        events += db.query("ledger", where={"case_id": cid, "event_type": event_type}, order="id")
    superseded = {e["payload"].get("supersedes") for e in events if e["payload"].get("supersedes")}
    return sorted((e for e in events if e["id"] not in superseded and e["payload"].get("scope") == "repo"),
                  key=lambda e: e["id"])


def superseded_of(db, case_id: int, event_type: str, kind: str) -> list[dict[str, Any]]:
    """확장 56호 — 이 스레드에서 대체된 결정·제약. 갈음한 것이 다른 스레드(저장소 규칙)에 있어도 찾는다.
    resume 에는 넣지 않는다(에이전트 문맥이 아니라 사람 화면의 것) — 찾기에서 누른 대체된 기록이 갈 자리다."""
    mine = db.query("ledger", where={"case_id": case_id, "event_type": event_type}, order="id")
    if not mine:
        return []
    ids = {e["id"] for e in mine}
    by: dict[int, int] = {}
    for e in db.query("ledger", where={"event_type": event_type}, order="id"):
        sup = e["payload"].get("supersedes")
        if sup in ids:
            by[sup] = e["id"]
    out = []
    for e in mine:
        if e["id"] in by:
            item = _item(e, kind); item["superseded_by"] = by[e["id"]]; item["supersedes"] = e["payload"].get("supersedes")
            out.append(item)
    return out


def _check_supersedes(db, case: dict, supersedes: int | None, event_type: str, siblings: set[int] | None, what: str):
    if supersedes is None:
        return None
    prev = db.get("ledger", int(supersedes))
    same_case = prev is not None and prev["case_id"] == case["id"]
    same_repo = (prev is not None and siblings is not None and prev["case_id"] in siblings
                 and prev["payload"].get("scope") == "repo")
    if prev is None or prev["event_type"] != event_type or not (same_case or same_repo):
        raise NotFoundError(f"{what} to supersede not found in this thread (or, for repo scope, in this repository)")
    return int(supersedes)


def record_decision(db, user_id: int, case: dict, statement: str, reason: str | None,
                    evidence_ids: list[int] | None, authority: str, supersedes: int | None,
                    scope: str = "thread", siblings: set[int] | None = None,
                    client: str | None = None) -> dict:
    if not (statement or "").strip():
        raise InputError("statement is empty")
    if authority not in AUTHORITIES:
        raise InputError(f"authority must be one of {AUTHORITIES}")
    if scope not in SCOPES:
        raise InputError(f"scope must be one of {SCOPES}")
    ids = _owned_evidence(db, user_id, evidence_ids)
    sup = _check_supersedes(db, case, supersedes, "decision_recorded", siblings, "decision")
    e = db.add("ledger", {
        "case_id": case["id"], "turn_id": None, "event_type": "decision_recorded",
        "payload": {"statement": statement, "reason": reason or "", "evidence_ids": ids,
                    "authority": authority, "supersedes": sup, "scope": scope,
                    # 확장 40호 — 어느 클라이언트가 남겼는가. 못 받으면 넣지 않는다(미상이라고 꾸미지 않는다).
                    **({"client": client} if client else {})},
    })
    return {"decision_id": e["id"], "supersedes": sup, "scope": scope}


def record_constraint(db, user_id: int, case: dict, statement: str, reason: str | None,
                      authority: str, supersedes: int | None,
                      scope: str = "thread", siblings: set[int] | None = None) -> dict:
    if not (statement or "").strip():
        raise InputError("statement is empty")
    if authority not in AUTHORITIES:
        raise InputError(f"authority must be one of {AUTHORITIES}")
    if scope not in SCOPES:
        raise InputError(f"scope must be one of {SCOPES}")
    sup = _check_supersedes(db, case, supersedes, "constraint_recorded", siblings, "constraint")
    e = db.add("ledger", {
        "case_id": case["id"], "turn_id": None, "event_type": "constraint_recorded",
        "payload": {"statement": statement, "reason": reason or "", "authority": authority,
                    "supersedes": sup, "scope": scope},
    })
    return {"constraint_id": e["id"], "supersedes": sup, "scope": scope}


def record_term(db, user_id: int, case: dict, term: str, meaning: str, authority: str, supersedes: int | None,
                scope: str = "thread", siblings: set[int] | None = None) -> dict:
    """확장 55호 — 지은 이름에 뜻 한 줄(#502). 결정·제약과 같은 급: 범위가 있고, 고치지 않고 대체한다.
    강제하지 않는다 — 사람이 물었을 때·정리할 때, 또는 에이전트가 기록에 새 이름을 쓸 때."""
    term = re.sub(r"\s+", " ", (term or "").strip())
    if not term or len(term) > 40:
        raise InputError("term: the name as written, 1–40 chars, one line")
    meaning = check_headline(meaning, "meaning")
    if authority not in AUTHORITIES:
        raise InputError(f"authority must be one of {AUTHORITIES}")
    if scope not in SCOPES:
        raise InputError(f"scope must be one of {SCOPES}")
    sup = _check_supersedes(db, case, supersedes, "term_defined", siblings, "term")
    e = db.add("ledger", {
        "case_id": case["id"], "turn_id": None, "event_type": "term_defined",
        "payload": {"term": term, "meaning": meaning, "authority": authority, "supersedes": sup, "scope": scope},
    })
    return {"term_id": e["id"], "term": term, "supersedes": sup, "scope": scope}


def _term_item(e: dict) -> dict[str, Any]:
    p = e["payload"]
    return {"term_id": e["id"], "term": p["term"], "meaning": p["meaning"], "authority": p.get("authority"),
            "scope": p.get("scope", "thread"), "supersedes": p.get("supersedes"), "recorded_at": e["created_at"], "case_id": e["case_id"]}


def rule_out(db, user_id: int, case: dict, hypothesis: str, scope: str, evidence_ids: list[int] | None) -> dict:
    # 배제는 재조사를 막는 강한 정보다 — 증거와 범위 없이는 기록하지 않는다.
    if not (hypothesis or "").strip():
        raise InputError("hypothesis is empty")
    if not (scope or "").strip():
        raise InputError("scope is required: where does this ruling-out hold (repro path, commit, environment)?")
    ids = _owned_evidence(db, user_id, evidence_ids)
    if not ids:
        raise InputError("evidence_ids is required: a ruling-out must point at the evidence that killed the hypothesis")
    e = db.add("ledger", {
        "case_id": case["id"], "turn_id": None, "event_type": "hypothesis_ruled_out",
        "payload": {"hypothesis": hypothesis, "scope": scope, "evidence_ids": ids},
    })
    return {"ruled_out_id": e["id"]}


def _item(e: dict, kind: str) -> dict[str, Any]:
    p = e["payload"]
    out = {f"{kind}_id": e["id"], "statement": p["statement"], "reason": p.get("reason", ""),
           "authority": p["authority"], "scope": p.get("scope", "thread"), "case_id": e["case_id"],
           "recorded_at": e["created_at"]}
    if kind == "decision":
        out["evidence_ids"] = p.get("evidence_ids", [])
    return out


def resume(db, case: dict, mode: str = "continuity", siblings: set[int] | None = None,
           anchor: dict[str, Any] | None = None, repo: str | None = None) -> dict[str, Any]:
    """확장 16호 — 7칸: focus · anchor · constraints · decisions · open · next · evidence_refs.
    thread scope 는 이 스레드의 것, repo scope 는 저장소 전체(`repo_state`)에 따로 둔다 — 둘이 충돌해도 우선순위를
    암묵적으로 정하지 않고 둘 다 보인다(에이전트가 추측하게 만들면 원점이다). 과거 추론(브리프·note)은 여전히 없다."""
    if mode not in ("continuity", "fresh"):
        raise InputError("mode must be 'continuity' or 'fresh'")
    case_id = case["id"]
    turns = db.query("turn", where={"case_id": case_id}, order="sequence")
    first_user = None
    for t in turns:
        if t["user_message_id"] is not None:
            first_user = db.get("message", t["user_message_id"]); break
    # Goal 은 사람이 쓴 것만: 케이스 제목(MCP 에선 open_case 의 한 줄) + 첫 입력 원문의 머리.
    # 브리프 focus(모델 문장)는 쓰지 않는다. 첫 입력이 로그 붙여넣기면 제목이 곧 goal 이다.
    first_text = (first_user["content"] if first_user else "") or ""
    goal = {"title": case["title"], "first_input_head": first_text.strip()[:300]}

    evidences = db.query("evidence", where={"case_id": case_id}, order="id")
    seq_of = {t["id"]: t["sequence"] for t in turns}
    index = [{"evidence_id": e["id"], "turn": seq_of.get(e["turn_id"]), "kind": e["kind"],
              "created_at": e["created_at"],
              "head": (e["content"] or "").strip().splitlines()[0][:120] if (e["content"] or "").strip() else ""}
             for e in evidences]
    total = len(index)
    index = index[-50:]  # 색인은 최근 50건까지 — 나머지는 inspect/search 로

    thread_c = [e for e in _active_of(db, case_id, "constraint_recorded") if e["payload"].get("scope", "thread") == "thread"]
    constraints = [_item(e, "constraint") for e in thread_c]
    out: dict[str, Any] = {
        "case_id": case_id, "title": case["title"], "status": case["status"], "mode": mode,
        "goal": goal,
        "focus": current_declaration(db, case_id, "focus"),
        "anchor": anchor,
        "constraints": constraints,
        "open": current_declaration(db, case_id, "open"),
        "next": current_declaration(db, case_id, "next"),
        # 확장 55호 — 용어는 어휘라 fresh 에서도 준다: 다음 에이전트가 같은 이름을 쓰게
        "terms": [_term_item(e) for e in _active_of(db, case_id, "term_defined") if e["payload"].get("scope", "thread") == "thread"],
        "evidence_count": total, "evidence_index": index,
        "last_activity": {"turn": turns[-1]["sequence"], "at": turns[-1]["created_at"]} if turns else None,
        "note": ("durable state only: what the machine observed, what the work committed to, and what evidence "
                 "closed. No hypotheses, brief, or record are included — ask the engineer if you need them."),
    }
    refs: set[int] = set()
    if mode == "continuity":
        thread_d = [e for e in _active_of(db, case_id, "decision_recorded") if e["payload"].get("scope", "thread") == "thread"]
        out["decisions"] = [_item(e, "decision") for e in thread_d]
        out["ruled_out"] = [{"ruled_out_id": e["id"], "hypothesis": e["payload"]["hypothesis"], "scope": e["payload"]["scope"],
                             "evidence_ids": e["payload"]["evidence_ids"], "recorded_at": e["created_at"]}
                            for e in db.query("ledger", where={"case_id": case_id, "event_type": "hypothesis_ruled_out"}, order="id")]
        for d in out["decisions"]:
            refs.update(d["evidence_ids"])
        for r in out["ruled_out"]:
            refs.update(r["evidence_ids"])
    if siblings is not None:
        rs: dict[str, Any] = {"repo": repo, "threads": sorted(siblings),
                              "constraints": [_item(e, "constraint") for e in _active_repo_scope(db, siblings, "constraint_recorded")],
                              "terms": [_term_item(e) for e in _active_repo_scope(db, siblings, "term_defined")]}
        if mode == "continuity":
            rs["decisions"] = [_item(e, "decision") for e in _active_repo_scope(db, siblings, "decision_recorded")]
            for d in rs["decisions"]:
                refs.update(d["evidence_ids"])
        out["repo_state"] = rs
    out["evidence_refs"] = sorted(refs)   # open state·결정·배제가 실제로 가리키는 evidence 만
    return out


def declare(db, case: dict, kind: str, statement: str, authority: str = "agent", owner: str | None = None) -> dict:
    """kind ∈ focus|open|next. 직전 현재값이 있으면 그것을 supersede 한다(사슬이 provenance).
    owner(확장 31호)는 next 에만 — 누구 차례인지. 없으면 단계는 활동 시각만으로 도출된다."""
    if kind not in DECLARATIONS:
        raise InputError(f"kind must be one of {tuple(DECLARATIONS)}")
    statement = check_headline(statement, kind)          # 확장 52호 — 첫 줄은 제목(60자 · ' — ' 없이)
    if authority not in AUTHORITIES:
        raise InputError(f"authority must be one of {AUTHORITIES}")
    owner = normalize_owner(owner)
    if kind == "next":
        statement = check_addressed(statement, owner, "next")   # 확장 123호 — 사람의 차례면 사람에게 쓴다
    if kind == "next" and not owner:
        # 확장 37호 — 미분류 차단(30호)과 같은 계열: 규칙을 에이전트 기억이 아니라 도구가 지킨다. #446 은 owner=codex 로
        # 선언해 뒀는데 Codex 가 같은 스레드에 다시 선언하면서 owner 없이 덮어 화면이 "진행" 으로 틀렸다.
        raise InputError('next needs an owner — who takes the next action: "user" (the engineer must act), '
                         '"watch" (nothing to do, only watching), or an agent name such as "codex" / "claude"')
    if owner and kind != "next":
        raise InputError("owner belongs to next only — who takes the next action")
    active = _active_of(db, case["id"], DECLARATIONS[kind])
    prev = active[-1]["id"] if active else None
    statement = statement.strip()
    # 확장 114호 — focus 를 글자 그대로 다시 말하면 그것은 "확인" 이다. 줄은 남긴다(15턴 상한이
    # 그 줄로 다시 세고, append-only 라 언제 확인했는지가 기록이다 — D13875). 다만 회고와
    # 타임라인은 이것을 사건으로 세지 않는다: 방향이 바뀐 적이 없는데 "초점" 줄이 늘어나면
    # 읽는 사람이 무엇이 바뀐 줄 안다. 전에는 빠져나갈 길이 재선언뿐이라 에이전트가 진척을
    # focus 본문에 적어 넣었고, 그러면 SO FAR 와 겹쳐 둘 다 못 믿게 된다.
    confirmed = False
    if kind == "focus" and active and active[-1]["payload"].get("statement", "").strip() == statement:
        confirmed = True
    # 확장 110호 (D15573) — next 를 턴마다 다시 말하게 했으므로 같은 글이 줄줄이 들어온다.
    # 글과 차례가 그대로면 새 줄을 쓰지 않는다: 원장이 거의 같은 next 로 시끄러워지면 회고와
    # 타임라인이 읽히지 않는다. 대가는 "마지막으로 다시 확인한 시각"을 안 남기는 것이다.
    if kind == "next" and active:
        last = active[-1]["payload"]
        if last.get("statement", "").strip() == statement and last.get("owner") == owner:
            return {"declaration_id": prev, "kind": kind, "supersedes": last.get("supersedes"),
                    "owner": owner, "unchanged": True}
    payload = {"statement": statement, "authority": authority, "supersedes": prev}
    if kind == "next":
        payload["owner"] = owner
    if confirmed:
        payload["confirmed"] = True
    e = db.add("ledger", {"case_id": case["id"], "turn_id": None, "event_type": DECLARATIONS[kind], "payload": payload})
    return {"declaration_id": e["id"], "kind": kind, "supersedes": prev, "owner": owner,
            "unchanged": False, "confirmed": confirmed}


def current(db, case_id: int, kind: str) -> str | None:
    active = _active_of(db, case_id, DECLARATIONS[kind])
    return active[-1]["payload"]["statement"] if active else None


def current_declaration(db, case_id: int, kind: str) -> dict | None:
    active = _active_of(db, case_id, DECLARATIONS[kind])
    if not active:
        return None
    e = active[-1]
    out = {"id": e["id"], "statement": e["payload"]["statement"], "authority": e["payload"]["authority"],
           "declared_at": e["created_at"], "supersedes": e["payload"].get("supersedes")}
    if kind == "next":
        out["owner"] = e["payload"].get("owner")
    return out


def turns_since_focus(db, case_id: int) -> dict:
    """확장 46호 — 마지막 focus 선언 뒤에 쌓인 턴 수. 스레드를 열 때 넣은 focus 가 첫 선언이고(D13874),
    focus 를 다시 선언하면 그 시각부터 다시 센다. focus 가 한 번도 없는 케이스(open_case)는 전부 센다."""
    focus = current_declaration(db, case_id, "focus")
    # 시각이 아니라 원장 순서로 센다 — 같은 밀리초에 선언과 턴이 나란히 생기면 시각으로는 앞뒤를 못 가른다.
    # 턴마다 user_message 이벤트가 하나다(external_turn · 조사 턴 모두).
    after = focus["id"] if focus else 0
    events = db.query("ledger", where={"case_id": case_id, "event_type": "user_message"}, order="id")
    return {"turns": sum(1 for e in events if e["id"] > after), "focus": focus}
