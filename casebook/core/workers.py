"""워커 3종 + draft_query — Xano background task/function 의 로컬 짝.

이벤트를 쓰는 순서가 곧 오라클이다(골든·리플레이). `.xs` 의 스택 순서를 그대로 따른다.

`.xs` 에 없는 것 하나만 더한다: **진입 시 조건부 클레임**. Xano background task 는
정확히 1회 전달이었지만 로컬 큐·SQS 는 at-least-once 라 중복 전달을 워커가 직접
막아야 한다(인계 §5, 불변식 7·14). 이미 터미널인 턴을 다시 집으면 조용한 no-op 다.
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import prompt_ext, prompts

KO_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
JA_RE = re.compile(r"[ぁ-んァ-ヶー]")

# `.xs` 인라인 조각 — 이스케이프가 없어 추출기 대신 직접 옮겼다(turn_worker.xs 196·202·210행 등).
USER_PREFIX = "사용자: "
ASSISTANT_PREFIX = "나(어시스턴트): "
WEB_PREFIX = "웹 검색 결과: "
ANSWER_TASK_SUFFIX = "로 쓴다."          # turn_worker.xs 251행
RECORDER_TASK_SUFFIX = "로 쓴다."        # turn_worker.xs 511행
DRAFT_TASK_SUFFIX = "로 쓴다. 검색어만 출력한다."  # draft_query.xs 103행
WEB_CONTENT_PREFIX = "[웹 검색] "        # web_lookup_worker.xs 63행
# .xs 에 없는 조각 — 아직 답하지 않은 사용자 메시지가 여럿일 때(intake 묶음 → 질문). CTX_TURN_NOTICE 의 "아래 한 건이
# 유일한 입력" 은 이 경우 틀린 말이라, 묶음이 이전 메시지로 들어가면 모델이 "새 자료가 없다" 고 첫 문장을 썼다
# (case 462 — 랩 2·3회차 첫 답변에서 반복). 답을 받지 않은 것은 '지난 Turn의 기록' 이 아니라 이번 입력이다.
CTX_PRIOR_NOTICE = "위 내용은 모두 지난 Turn의 기록이다. "
CTX_UNANSWERED_NOTICE = ("아래 {n}건이 이번 Turn에 새로 들어온 입력이며 아직 답하지 않은 것이다.\n"
                         "답변의 '이번 입력으로 달라진 점'은 이것들을 가리킨다. 마지막 한 건이 사용자의 질문이다.\n")

BRIEF_FIELDS = ("focus", "evidence", "considering", "recent_updates", "next_up")

# 답변(troubleshoot) 호출의 reasoning effort 선택지 — investigation-copilot 원본과 같은 폭.
# Xano 시대 .xs 는 none 고정이었고, 그 값이 지금도 기본값이다. 기록자·draft(none)·
# record(medium)는 선택 대상이 아니다. effort 는 원장 시퀀스에 영향을 주지 않는다.
EFFORT_OPTIONS = ("none", "low", "medium")


def _pick_lang(is_ko: bool, is_ja: bool, prior_ko: bool = False, prior_ja: bool = False) -> str:
    if is_ko:
        return "한국어"
    if is_ja:
        return "일본어(日本語)"
    if prior_ko:
        return "한국어"
    if prior_ja:
        return "일본어(日本語)"
    return "영어(English)"


def _validate_brief(brief: Any) -> dict[str, Any]:
    # turn_worker.xs 538~559행 precondition 과 같은 검사·같은 순서.
    if not isinstance(brief, dict) or len(brief) != 5 or not all(k in brief for k in BRIEF_FIELDS):
        raise ValueError("brief_bad_keys")
    if not isinstance(brief["focus"], str) or not brief["focus"].strip():
        raise ValueError("brief_bad_focus")
    for field in ("evidence", "considering", "recent_updates", "next_up"):
        items = brief[field]
        ok = isinstance(items, list) and all(isinstance(i, str) and i.strip() for i in items)
        if not ok:
            raise ValueError(f"brief_bad_{field}")
    return brief


def _build_context(db, turn) -> tuple[str, str, str]:
    """턴의 모델 입력을 조립한다 — (context_core, lang, prompt_text). 조각은 .xs 에서 바이트 그대로.

    turn_worker 와 external_turn_worker(확장 4호)가 같은 조립을 쓴다. 기록자 입력은
    context_core('# 할 일' 이전까지)이고, 답변 호출만 prompt_text 를 쓴다.
    """
    case_id = turn["case_id"]
    messages = db.query(
        "message", where={"case_id": case_id}, not_equal={"id": turn["user_message_id"]}
    )
    web_evidences = db.query(
        "evidence", where={"case_id": case_id, "kind": "web_lookup"}, order="created_at"
    )
    # 마지막 어시스턴트 답변 뒤에 온 사용자 메시지(intake 묶음의 external 턴 등)는 아직 답하지 않은 것 —
    # '지난 Turn의 기록' 이 아니라 이번 입력으로 넣는다. 답변이 있는 보통 흐름에서는 빈 목록이라 조립이 바뀌지 않는다.
    cur_id = turn["user_message_id"] or 0
    last_answer_id = max((m["id"] for m in messages if m["role"] == "assistant" and m["id"] < cur_id), default=0)
    unanswered = [m for m in messages if m["role"] == "user" and last_answer_id < m["id"] < cur_id]
    unanswered_ids = {m["id"] for m in unanswered}
    timeline = sorted([m for m in messages if m["id"] not in unanswered_ids] + web_evidences, key=lambda r: r["created_at"])
    current_evidence = db.get("evidence", turn["evidence_id"]) or {"content": ""}

    # 3.7 언어는 스택이 정한다 — 이번 턴 원문, 없으면 이전 사용자 턴들 (모델 판정 금지)
    text = current_evidence["content"] or ""
    prior_user_text = ""
    for pm in messages:
        if pm["role"] == "user":
            prior_user_text = prior_user_text + "\n" + pm["content"]
    lang = _pick_lang(
        bool(KO_RE.search(text)), bool(JA_RE.search(text)),
        bool(KO_RE.search(prior_user_text)), bool(JA_RE.search(prior_user_text)),
    )

    # 4. 컨텍스트 조립 — 조각은 .xs 에서 바이트 그대로
    context_text = ""
    if timeline:
        context_text = prompts.CTX_HEADER
        for item in timeline:
            if "role" in item:
                prefix = USER_PREFIX if item["role"] == "user" else ASSISTANT_PREFIX
                entry = prefix + item["content"]
            elif item.get("kind") == "web_lookup":
                entry = WEB_PREFIX + item["content"]
            else:
                entry = ""
            context_text = context_text + entry + "\n\n"
        context_text = context_text.strip()
    context_text = context_text + prompts.CTX_CURRENT_INPUT_HEADER
    if unanswered:
        context_text = context_text + (CTX_PRIOR_NOTICE if timeline else "") + CTX_UNANSWERED_NOTICE.format(n=len(unanswered) + 1)
        for m in unanswered:
            context_text = context_text + m["content"] + "\n\n"
    elif timeline:
        context_text = context_text + prompts.CTX_TURN_NOTICE
    context_text = context_text + current_evidence["content"]

    context_core = context_text  # 기록자 입력은 '# 할 일' 이전까지만
    context_text = context_text + prompts.ANSWER_TASK_PREFIX + lang + ANSWER_TASK_SUFFIX
    prompt_text = context_text + prompts.SCOPE_ANCHOR
    return context_core, lang, prompt_text


def _run_recorder(db, llm, case_id: int, turn_id: int, context_core: str, answer_text: str, lang: str) -> None:
    """기록자 2차 호출 — 브리프는 여기서만 갱신된다 (불변식 12 개정). 이벤트 순서는 .xs 그대로."""
    try:
        t1 = db.now_ms()
        recorder_prompt = (
            context_core + prompts.RECORDER_ANSWER_HEADER + answer_text
            + prompts.RECORDER_TASK_PREFIX + lang + RECORDER_TASK_SUFFIX
        )
        out = llm.record_brief(prompts.RECORDER_SYSTEM, recorder_prompt)
        brief = _validate_brief((out or {}).get("brief"))
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "investigation_brief_updated",
            "payload": {"brief": brief, "source": "fallback"},
        })
        db.edit("case", case_id, {"brief_cache": brief, "brief_cache_turn_id": turn_id})
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "model_call",
            "payload": {
                "role": "extract_brief", "status": "completed",
                "latency_ms": db.now_ms() - t1,
            },
        })
    except Exception:  # noqa: BLE001 — .xs catch 는 사유를 fallback_failed 로 고정한다
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "brief_rejected", "payload": {"reason": "fallback_failed"},
        })


def turn_worker(db, llm, turn_id: int, effort: str = "none", web: bool = False) -> None:
    turn = db.get("turn", turn_id)
    if turn is None or turn["status"] != "pending":
        return  # 진입 클레임 — 중복 전달·이미 종결된 턴은 조용한 no-op (불변식 14)

    case_id = turn["case_id"]
    context_core, lang, prompt_text = _build_context(db, turn)

    t0 = db.now_ms()
    try:
        result = llm.answer(prompts.ANSWER_SYSTEM, prompt_text, effort=effort, web=web)
        answer_text = (result or {}).get("text")
        web_info = (result or {}).get("web") or {}
        if not isinstance(answer_text, str) or not answer_text.strip():
            raise RuntimeError("No valid message returned by model")
    except Exception as exc:  # noqa: BLE001 — .xs catch: 조용히 실패로 종결, 재던지지 않는다
        claimed = db.edit_where(
            "turn", turn_id, {"status": "pending"},
            {
                "status": "finalized",
                "answer_status": "failed",
                "error": {"stage": "troubleshoot", "reason": str(exc)},
                "finished_at": db.now_ms(),
            },
        )
        if not claimed:
            return
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "turn_status_changed",
            "payload": {"to": "finalized", "answer_status": "failed"},
        })
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "model_call",
            "payload": {
                "role": "troubleshoot", "effort": effort, "web": web, "status": "failed", "reason": str(exc),
                "latency_ms": db.now_ms() - t0, "output_chars": 0,
            },
        })
        return

    # 7.5 아직 pending 인가 — recovery 가 abandoned 로 바꿨으면 답변을 버린다
    turn_now = db.get("turn", turn_id)
    if turn_now["status"] != "pending":
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "model_call",
            "payload": {
                "role": "troubleshoot", "effort": effort, "web": web, "status": "discarded_not_pending",
                "latency_ms": db.now_ms() - t0, "output_chars": len(answer_text),
            },
        })
        return

    amsg = db.add("message", {
        "case_id": case_id, "turn_id": turn_id, "role": "assistant", "content": answer_text,
    })
    db.add("ledger", {
        "case_id": case_id, "turn_id": turn_id,
        "event_type": "answer_created", "payload": {"message_id": amsg["id"]},
    })

    # leak 감지 — 관찰만 하고 답변은 절대 고치지 않는다
    leak_marks = []
    if "update_investigation_brief" in answer_text:
        leak_marks.append("tool_name")
    if "[functions." in answer_text:
        leak_marks.append("bracket_call")
    if '"focus"' in answer_text and '"next_up"' in answer_text:
        leak_marks.append("brief_json")
    if leak_marks:
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "answer_leak_suspected",
            "payload": {"marks": leak_marks, "answer_chars": len(answer_text)},
        })

    # 기록자 2차 호출 — 브리프는 여기서만 갱신된다 (불변식 12 개정)
    _run_recorder(db, llm, case_id, turn_id, context_core, answer_text, lang)

    # 9.0 finalize 직전 한 번 더 — 브리프 구간에 recovery 가 들어온 경우
    turn_now2 = db.get("turn", turn_id)
    if turn_now2["status"] != "pending":
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "model_call",
            "payload": {
                "role": "troubleshoot", "effort": effort, "web": web, "status": "discarded_not_pending_late",
                "latency_ms": db.now_ms() - t0, "output_chars": len(answer_text),
            },
        })
        return

    claimed = db.edit_where(
        "turn", turn_id, {"status": "pending"},
        {
            "status": "finalized", "answer_status": "stored",
            "assistant_message_id": amsg["id"], "finished_at": db.now_ms(),
        },
    )
    if not claimed:
        return
    db.add("ledger", {
        "case_id": case_id, "turn_id": turn_id,
        "event_type": "turn_status_changed",
        "payload": {"to": "finalized", "answer_status": "stored"},
    })
    db.add("ledger", {
        "case_id": case_id, "turn_id": turn_id,
        "event_type": "model_call",
        "payload": {
            "role": "troubleshoot", "effort": effort, "web": web, "status": "completed",
            "latency_ms": db.now_ms() - t0, "output_chars": len(answer_text),
            # 확장 3호 — 모델이 스스로 한 검색과 인용. evidence 가 아니라 원장(참고 기록)이다.
            **({"web_queries": web_info.get("queries", []), "web_citations": web_info.get("citations", [])}
               if web else {}),
        },
    })


def web_lookup_worker(db, search, turn_id: int, query: str, engine: str) -> None:
    turn = db.get("turn", turn_id)
    if turn is None or turn["status"] != "pending":
        return  # 진입 클레임
    case_id = turn["case_id"]
    try:
        api = search.lookup(query, engine)
        raw = (api or {}).get("results")
        if not isinstance(raw, list):
            raise RuntimeError("Results are not an array")

        content = WEB_CONTENT_PREFIX + query + "\n"
        source_results = []
        idx = 1
        for item in raw:
            if idx <= 10:
                link = item.get("link")
                title = item.get("title")
                snippet = item.get("snippet")
                content += (
                    f"{idx}. {'' if title is None else title} — {'' if link is None else link}\n"
                    f"   {'' if snippet is None else snippet}\n"
                )
                source_results.append({
                    "position": item["position"] if item.get("position") is not None else idx,
                    "title": title, "link": link, "snippet": snippet,
                })
                idx += 1

        now = db.now_ms()
        source = {"engine": engine, "query": query, "fetched_at": now, "results": source_results}
        ev = db.add("evidence", {
            "case_id": case_id, "turn_id": turn_id, "kind": "web_lookup",
            "content": content, "source": source, "created_at": now,
        })
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "canonical_evidence_created", "payload": {"evidence_id": ev["id"]},
        })
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "web_lookup_completed",
            "payload": {"result_count": len(source_results)},
        })
        claimed = db.edit_where(
            "turn", turn_id, {"status": "pending"},
            {"status": "finalized", "evidence_id": ev["id"], "finished_at": db.now_ms()},
        )
        if claimed:
            db.add("ledger", {
                "case_id": case_id, "turn_id": turn_id,
                "event_type": "turn_status_changed", "payload": {"to": "finalized"},
            })
    except Exception:  # noqa: BLE001 — .xs catch: 조용히 실패로 종결
        claimed = db.edit_where(
            "turn", turn_id, {"status": "pending"},
            {
                "status": "finalized", "error": {"stage": "web_lookup"},
                "finished_at": db.now_ms(),
            },
        )
        if not claimed:
            return
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "web_lookup_failed", "payload": {},
        })
        db.add("ledger", {
            "case_id": case_id, "turn_id": turn_id,
            "event_type": "turn_status_changed", "payload": {"to": "finalized"},
        })


def record_worker(db, llm, record_id: int) -> None:
    rec = db.get("record", record_id)
    if rec is None or rec["status"] != "pending":
        return  # 진입 클레임
    case = db.get("case", rec["case_id"])
    if case is None:
        return

    turns = db.query("turn", where={"case_id": rec["case_id"]})
    msgs = db.query("message", where={"case_id": rec["case_id"]})
    evs = db.query("evidence", where={"case_id": rec["case_id"]})
    msg_map = {m["id"]: m for m in msgs}
    ev_map = {e["id"]: e for e in evs}

    dossier = ""
    for t in turns:
        if t["status"] == "abandoned":
            continue
        if t["turn_kind"] in ("input", "recovery", "external"):
            user_msg = msg_map.get(t["user_message_id"]) or {}
            ev_id_str = str(t["evidence_id"]) if t["evidence_id"] is not None else "none"
            dossier += (
                f"[turn {t['sequence']}] investigator (Evidence #{ev_id_str}): "
                f"{user_msg.get('content') or ''}\n"
            )
            if t["assistant_message_id"] is not None:
                asst = msg_map.get(t["assistant_message_id"]) or {}
                # MCP 턴(확장 4호)의 결론은 호스트 모델의 해석이다 — 조사자 보고가 아니므로 레코드가
                # 사실로 승격하지 않도록 표기한다(case 442 v1 에서 승격 관측).
                label = "host interpretation (not evidence)" if t["turn_kind"] == "external" else "copilot"
                dossier += f"[turn {t['sequence']}] {label}: {asst.get('content') or ''}\n"
        elif t["turn_kind"] == "web_lookup" and t["evidence_id"] is not None:
            ev = ev_map.get(t["evidence_id"]) or {}
            dossier += (
                f"[turn {t['sequence']}] web lookup (Evidence #{t['evidence_id']}): "
                f"{ev.get('content') or ''}\n"
            )

    brief_text = "none"
    if case["brief_cache"] is not None:
        brief_text = json.dumps(case["brief_cache"], ensure_ascii=False, separators=(",", ":"))
    dossier += "=== CURRENT BRIEF ===\n" + brief_text

    t0 = db.now_ms()
    try:
        out = llm.assemble_record(prompt_ext.RECORD_SYSTEM, dossier)  # 확장 5호 — 정정 절에 근거 필수
        content = (out or {}).get("content")
        if not isinstance(content, str) or len(content) == 0:
            raise RuntimeError("Missing output text")
        claimed = db.edit_where(
            "record", record_id, {"status": "pending"},
            {"content": content, "status": "created", "finished_at": db.now_ms()},
        )
        if not claimed:
            return
        db.add("ledger", {
            "case_id": case["id"], "turn_id": rec["turn_id"],
            "event_type": "record_created",
            "payload": {
                "record_id": record_id, "version": rec["version"],
                "output_chars": len(content), "latency_ms": db.now_ms() - t0,
            },
        })
    except Exception as exc:  # noqa: BLE001 — .xs catch: 조용히 실패로 종결
        claimed = db.edit_where(
            "record", record_id, {"status": "pending"},
            {
                "status": "failed",
                "error": {"stage": "record", "reason": str(exc), "http_status": None},
                "finished_at": db.now_ms(),
            },
        )
        if not claimed:
            return
        db.add("ledger", {
            "case_id": case["id"], "turn_id": rec["turn_id"],
            "event_type": "record_failed",
            "payload": {
                "record_id": record_id, "reason": str(exc),
                "latency_ms": db.now_ms() - t0,
            },
        })


def draft_query_fn(db, llm, case_id: int, candidate: str) -> dict[str, str]:
    from .errors import ApiError

    if not candidate.strip():
        raise ApiError("candidate is empty")

    user_messages = db.query(
        "message", where={"case_id": case_id, "role": "user"}, order="created_at"
    )
    reports = ""
    for m in user_messages:
        reports = reports + m["content"] + "\n\n"
    reports = reports.strip()

    lang = _pick_lang(bool(KO_RE.search(candidate)), bool(JA_RE.search(candidate)))

    t0 = db.now_ms()
    draft = ""
    try:
        user_prompt = (
            prompts.DRAFT_REPORTS_HEADER + reports
            + prompts.DRAFT_CANDIDATE_HEADER + candidate
            + prompts.DRAFT_TASK_PREFIX + lang + DRAFT_TASK_SUFFIX
        )
        out = llm.draft_query(prompts.DRAFT_QUERY_SYSTEM, user_prompt)
        text = (out or {}).get("query")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("No valid message returned by model")
        draft = text.strip()
        db.add("ledger", {
            "case_id": case_id, "turn_id": None,
            "event_type": "model_call",
            "payload": {
                "role": "draft_query", "status": "completed",
                "latency_ms": db.now_ms() - t0, "output_chars": len(draft),
            },
        })
    except Exception as exc:  # noqa: BLE001 — 실패 시 빈 문자열, 화면이 후보 원문으로 되돌린다 (불변식 7)
        db.add("ledger", {
            "case_id": case_id, "turn_id": None,
            "event_type": "model_call",
            "payload": {"role": "draft_query", "status": "failed", "reason": str(exc)},
        })
        draft = ""
    return {"query": draft, "lang": lang}
