"""인계문 — 확장 158호 (2026-09-26, 스레드 596). 사람에게 내는 한 장.

전에는 resume 의 칸을 한 목록에 쏟았다: 선언 · 결정 · 저장소 공통 항목 · 증거 첫 줄. 원문 보존 스레드(595)로
뽑아 읽어 보니 어디까지 했는지(커밋·노트)가 없었고, 이 스레드와 무관한 저장소 항목이 절반이었고, 결정에 사람
원문이 붙지 않았고, 여러 줄 본문이 목록을 깨뜨렸다. 그래서 읽는 순서대로 다시 짠다(D18078):

  지금           focus · next(차례) · open — 무엇을 위한 스레드이고 다음은 누구 차례인가
  한 일          커밋과 바꿈·확인 노트의 제목, 시간순. 생각·발견 노트는 해석이라 싣지 않는다
  결정 · 제약    ADR 형식 — 상태 · 결정한 사람 · 맥락(이유와 사람 원문) · 결정 · 근거
  배제           가설 · 범위 · 근거
  저장소 규칙    제목 한 줄씩. 전문은 inspect(ref)
  인용된 증거    결정·배제가 기대는 증거만

지키는 것: 모델을 부르지 않는다. 기록에 없는 것은 쓰지 않는다 — ADR 의 Consequences 칸은 두지 않는다(에이전트가
결과를 지어 쓰면 이 스레드가 막으려던 "에이전트 말로 채운 기록"이 된다). 사람이 쓴 문장은 손대지 않고, 둘레의
라벨만 스레드가 쓰인 언어로 붙인다(D15186).
"""
from __future__ import annotations

import time
from typing import Any

from . import threads, utterances
from .headline import body_of, title_of

DONE_LIMIT = 20
SOURCE_CLIP = 400
EVIDENCE_HEAD = 100
DONE_KINDS = ("change", "verified")

LABELS = {
    "ko": {"intro": "_Worktrail — 선언된 상태와 근거로 구성한 인계입니다._",
           "now": "지금", "owner": "차례", "done": "한 일", "done_more": "앞선 {n}건은 trail({cid}) 로 본다.",
           "done_none": "아직 커밋도 바꿈·확인 노트도 없다.", "commit": "커밋",
           "change": "바꿈", "verified": "확인",
           "decisions": "결정", "constraints": "제약", "ruled_out": "배제", "repo": "저장소 공통 규칙",
           "repo_tail": "전문은 inspect(ref) 로 본다.", "cited": "인용된 증거",
           "status": "상태", "in_force": "유효", "replaces": "{ref} 을 대체함", "by": "정한 사람",
           "context": "맥락", "decision": "결정", "grounds": "근거", "evidence": "증거", "scope": "범위",
           "source": "원문", "pending": "원문이 아직 서버에 도착하지 않았다", "clipped": "전문은 inspect({ref})",
           "hook": "훅이 잡음", "model_pasted": "모델이 옮겨 붙임"},
    "en": {"intro": "_Worktrail — a handoff built from the declared state and what grounded it._",
           "now": "Now", "owner": "turn", "done": "Done so far", "done_more": "{n} earlier items: see trail({cid}).",
           "done_none": "No commits or change/verified notes yet.", "commit": "commit",
           "change": "change", "verified": "verified",
           "decisions": "Decisions", "constraints": "Constraints", "ruled_out": "Ruled out",
           "repo": "Repository rules", "repo_tail": "Full text: inspect(ref).", "cited": "Cited evidence",
           "status": "status", "in_force": "in force", "replaces": "replaces {ref}", "by": "set by",
           "context": "context", "decision": "decision", "grounds": "grounds", "evidence": "evidence",
           "scope": "scope", "source": "engineer's words", "pending": "not on the server yet",
           "clipped": "full text: inspect({ref})", "hook": "captured by hook", "model_pasted": "pasted by the model"},
}


def _when(ms: int | None) -> str:
    return time.strftime("%m-%d %H:%M", time.localtime(ms / 1000)) if ms else "—"


def _split(text: str | None) -> tuple[str, str]:
    """첫 줄(제목)과 나머지(본문). 기록 규칙상 제목 뒤에는 빈 줄이 온다."""
    return title_of(text), body_of(text)


def _indent(text: str, pad: str = "  ") -> str:
    return "\n".join(pad + line if line.strip() else "" for line in text.splitlines())


def _quote(text: str, pad: str) -> str:
    return "\n".join(f"{pad}> {line}" if line.strip() else f"{pad}>" for line in text.splitlines())


def _done(db, case_id: int) -> list[tuple[int, str]]:
    threads.ensure_change(db)
    rows: list[tuple[int, str]] = []
    with db._lock:
        for r in db.conn.execute(
                "SELECT t.sequence, t.created_at, am.content, json_extract(lk.payload, '$.kind') AS kind FROM turn t "
                "JOIN ledger lk ON lk.turn_id = t.id AND lk.event_type = 'answer_created' "
                "JOIN message am ON am.id = t.assistant_message_id WHERE t.case_id = ? ORDER BY t.sequence",
                (case_id,)).fetchall():
            if r["kind"] in DONE_KINDS:
                rows.append((r["created_at"], f"{r['kind']}\x00turn {r['sequence']}\x00{title_of(r['content']) or ''}"))
        for r in db.conn.execute("SELECT head, message, created_at FROM change WHERE kind='commit' AND case_id=? "
                                 "ORDER BY id", (case_id,)).fetchall():
            rows.append((r["created_at"], f"commit\x00{(r['head'] or '')[:7]}\x00{title_of(r['message']) or ''}"))
    # 같은 밀리초면 커밋을 앞에 둔다 — 커밋은 훅이 받아 적기 전에 이미 일어났다.
    return sorted(rows, key=lambda x: (x[0], not x[1].startswith("commit\x00")))


def _supersedes(db, entry_id: int) -> str | None:
    row = db.get("ledger", entry_id)
    sup = (row or {}).get("payload", {}).get("supersedes") if row else None
    if not sup:
        return None
    prev = db.get("ledger", int(sup))
    prefix = {"decision_recorded": "D", "constraint_recorded": "C"}.get((prev or {}).get("event_type"), "#")
    head = title_of((prev or {}).get("payload", {}).get("statement")) if prev else None
    return f"{prefix}{sup}" + (f" ({head})" if head else "")


def _sources(db, user_id: int, refs: list[str], ref: str, L: dict) -> list[str]:
    out = []
    for s in utterances.lookup(db, user_id, refs):
        if s.get("pending"):
            out.append(f"  - {L['source']}: _{L['pending']}_ ({s['source_ref']})")
            continue
        text = s["content"].strip()
        clipped = len(text) > SOURCE_CLIP
        text = text[:SOURCE_CLIP].rstrip() + ("…" if clipped else "")
        origin = L.get(s.get("origin") or "hook", s.get("origin") or "")
        out.append(f"  - {L['source']} ({origin} · {_when(s.get('said_at'))}):")
        out.append(_quote(text, "    "))
        if clipped:
            out.append(f"    _{L['clipped'].format(ref=ref)}_")
    return out


def _record(db, user_id: int, item: dict, kind: str, L: dict, cited: list[int]) -> list[str]:
    prefix = "D" if kind == "decision" else "C"
    ref = f"{prefix}{item[kind + '_id']}"
    head, body = _split(item["statement"])
    lines = [f"### {ref} · {head}"]
    sup = _supersedes(db, item[kind + "_id"])
    lines.append(f"- {L['status']}: {L['in_force']}" + (f" · {L['replaces'].format(ref=sup)}" if sup else ""))
    lines.append(f"- {L['by']}: {item.get('authority', 'agent')}")
    reason = (item.get("reason") or "").strip()
    refs = item.get("source_refs") or []
    if reason or refs:
        lines.append(f"- {L['context']}:" + (f" {title_of(reason)}" if reason else ""))
        rest = _split(reason)[1] if reason else ""
        if rest:
            lines.append(_indent(rest, "  "))
        lines += _sources(db, user_id, refs, ref, L)
    if body:
        lines.append(f"- {L['decision']}:")
        lines.append(_indent(body, "  "))
    ev = item.get("evidence_ids") or []
    if ev:
        cited += [i for i in ev if i not in cited]
        lines.append(f"- {L['grounds']}: " + ", ".join(f"{L['evidence']} #{i}" for i in ev))
    return lines


def _declared(st: dict, L: dict) -> list[str]:
    out = []
    for kind in ("focus", "next", "open"):
        d = st.get(kind)
        if not d:
            continue
        head, body = _split(d["statement"])
        who = f" ({L['owner']}: {d['owner']})" if kind == "next" and d.get("owner") else ""
        out.append(f"**{kind}**{who} · {head}")
        if body:
            out.append("")
            out.append(body)
        out.append("")
    return out


def build(db, user_id: int, case_id: int, title: str, st: dict, lang: str, record_body: str | None = None) -> str:
    """인계문 한 장. record_body 가 있으면(조사 도구의 레코드) 본문으로 두고 '지금'만 덧붙인다."""
    L = LABELS[lang]
    out = [f"# Handoff — case {case_id} · {title}", ""]
    if record_body is not None:
        out += [record_body, "", "---", f"## {L['now']}", ""] + _declared(st, L)
        return "\n".join(out).rstrip() + "\n"
    out += [L["intro"], "", f"## {L['now']}", ""] + _declared(st, L)

    done = _done(db, case_id)
    out += [f"## {L['done']}", ""]
    if not done:
        out.append(L["done_none"])
    else:
        if len(done) > DONE_LIMIT:
            out.append(f"_{L['done_more'].format(n=len(done) - DONE_LIMIT, cid=case_id)}_")
            done = done[-DONE_LIMIT:]
        for at, row in done:
            kind, where, text = row.split("\x00")
            out.append(f"- {_when(at)} · {L.get(kind, kind)} {where} · {text}")
    out.append("")

    cited: list[int] = []
    for kind, label in (("decision", "decisions"), ("constraint", "constraints")):
        items = st.get(kind + "s") or []
        if items:
            out += [f"## {L[label]}", ""]
            for item in items:
                out += _record(db, user_id, item, kind, L, cited) + [""]
    ruled = st.get("ruled_out") or []
    if ruled:
        out += [f"## {L['ruled_out']}", ""]
        for r in ruled:
            ev = r.get("evidence_ids") or []
            cited += [i for i in ev if i not in cited]
            out.append(f"- {r['hypothesis']} ({L['scope']}: {r['scope']}; {L['grounds']}: "
                       + ", ".join(f"#{i}" for i in ev) + ")")
        out.append("")

    repo = st.get("repo_state") or {}
    rules = [("D", x) for x in repo.get("decisions", [])] + [("C", x) for x in repo.get("constraints", [])]
    if rules:
        out += [f"## {L['repo']}", ""]
        for p, x in rules:
            out.append(f"- {p}{x.get(('decision' if p == 'D' else 'constraint') + '_id', '')} · {title_of(x['statement'])}")
        out += ["", f"_{L['repo_tail']}_", ""]

    if cited:
        out += [f"## {L['cited']}", ""]
        for i in cited:
            ev = db.get("evidence", i)
            head = title_of((ev or {}).get("content")) or ""
            out.append(f"- #{i} · {head[:EVIDENCE_HEAD]}{'…' if len(head) > EVIDENCE_HEAD else ''}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"
