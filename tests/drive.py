"""오라클 구동기 — 한 경로를 실행하고 그동안 원장에 쌓인 event_type 순서를 돌려준다.

실제 API 모양에 맞춘 판. 테스트 본문은 안 바뀐다.
- action_key 는 호출마다 고유해야 한다 — 같은 키는 멱등 반환이라 이벤트가 0개가 된다.
- recovery 는 복구 가능한 원본이 필요하다. 골든 recovery 시퀀스는 abandoned 이벤트가
  없으므로 원본은 pending 이 아니라 **답변 실패로 종결된 턴**이어야 한다. 답변 실패를
  주입해 원본을 만들고, 그 이벤트는 관측 창(before) 밖에 둔다.
- case_status_changed 는 유효 상태(open/resolved/archived) 중 resolved 로 보낸다.
"""
from __future__ import annotations
import itertools
from tests.conftest import PLACEHOLDER_INPUT

USER, CASE = 1, 1
_keys = itertools.count()


def _key(prefix: str = "k") -> str:
    return f"{prefix}-{next(_keys)}"


def observed(app, user_id: int, case_id: int, before: int) -> list[str]:
    return [e["event_type"] for e in app.list_ledger(user_id, case_id)[before:]]


def drive(app, kind: str, *, user_id: int = USER, case_id: int = CASE,
          source_turn_id: int | None = None, with_record: bool = False,
          fail: tuple[str, ...] = (), expected_events: list[str] | None = None) -> list[str]:
    # 리플레이 코퍼스 턴은 기대 시퀀스가 곧 재현 지시다 — 실패 주입과 기록 회차를 유도한다.
    record_plan = [True] if with_record else []
    if expected_events is not None:
        fail = tuple(fail)
        if "web_lookup_failed" in expected_events:
            fail += ("search",)
        record_plan = [ev == "record_created"
                       for ev in expected_events if ev in ("record_created", "record_failed")]

    if kind == "recovery" and source_turn_id is None:
        app.llm.fail_next.add("answer")
        src = app.submit_turn(user_id, case_id, PLACEHOLDER_INPUT, action_key=_key("rsrc"))
        app.run_workers()
        source_turn_id = src["turn_id"]

    for role in fail:
        if role == "search":
            app.search.fail_next = True
        else:
            app.llm.fail_next.add(role)

    before = len(app.list_ledger(user_id, case_id))
    if kind == "input":
        app.submit_turn(user_id, case_id, PLACEHOLDER_INPUT, action_key=_key())
    elif kind == "web_lookup":
        app.web_lookup(user_id, case_id, "placeholder query", engine="google",
                       action_key=_key())
    elif kind == "recovery":
        app.recover(user_id, case_id, source_turn_id, action_key=_key())
    elif kind == "case_status_changed":
        app.set_status(user_id, case_id, "resolved")
        return observed(app, user_id, case_id, before)
    else:
        raise AssertionError(f"알 수 없는 경로: {kind}")
    app.run_workers()
    for record_ok in record_plan:
        if not record_ok:
            app.llm.fail_next.add("record")
        app.generate_record(user_id, case_id)
        app.run_workers()
    return observed(app, user_id, case_id, before)
