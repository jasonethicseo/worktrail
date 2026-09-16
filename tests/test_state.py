"""확장 10호 — durable state. 이벤트로 쌓고 resume 이 projection 한다. 해석은 resume 에 없다."""
from __future__ import annotations
import pytest

from casebook.core.errors import InputError, NotFoundError
from tests.conftest import _build_app, FakeLLM, FakeSearch
from tests.drive import USER, CASE, drive


@pytest.fixture
def app2():
    app = _build_app(FakeLLM(), FakeSearch())
    app.db.add("user", {"name": "o", "email": "o@x.test", "password": None})
    r = app.external_turn(USER, CASE, "GOAL: intermittent 401 behind ALB\nlogs…", action_key="g", note="host guesses stale key"); app.run_workers()
    e2 = app.external_turn(USER, CASE, "10.0.1.7 uptime 9 days; others replaced 23:10", action_key="e2"); app.run_workers()
    return app, r["evidence_id"], e2["evidence_id"]


def test_decide_supersede_projection(app2):
    app, e1, e2 = app2
    d1 = app.decide(USER, CASE, "rotate keys via restart", reason="fastest", evidence_ids=[e2])["decision_id"]
    d2 = app.decide(USER, CASE, "add JWKS refresh interval", authority="user", supersedes=d1)["decision_id"]
    res = app.resume(USER, CASE)
    assert [d["decision_id"] for d in res["decisions"]] == [d2] and res["decisions"][0]["authority"] == "user"
    with pytest.raises(NotFoundError):
        app.decide(USER, CASE, "x", supersedes=999)
    with pytest.raises(InputError):
        app.decide(USER, CASE, "x", authority="deity")


def test_rule_out_needs_scope_and_evidence(app2):
    app, e1, e2 = app2
    with pytest.raises(InputError, match="scope"):
        app.rule_out(USER, CASE, "session sharing", scope="", evidence_ids=[e2])
    with pytest.raises(InputError, match="evidence_ids"):
        app.rule_out(USER, CASE, "session sharing", scope="prod, 2026-09-03", evidence_ids=[])
    with pytest.raises(NotFoundError):
        app.rule_out(USER, CASE, "x", scope="s", evidence_ids=[999])
    r = app.rule_out(USER, CASE, "session sharing between app servers", scope="prod, token auth, 2026-09-03", evidence_ids=[e2])
    ro = app.resume(USER, CASE)["ruled_out"]
    assert ro[0]["ruled_out_id"] == r["ruled_out_id"] and ro[0]["scope"].startswith("prod") and ro[0]["evidence_ids"] == [e2]


def test_resume_는_durable_state_만_해석_없음(app2):
    app, e1, e2 = app2
    app.constrain(USER, CASE, "do not rotate keys during business hours", authority="user")
    app.decide(USER, CASE, "restart 10.0.1.7 first")
    app.rule_out(USER, CASE, "session sharing", scope="prod", evidence_ids=[e2])
    c = app.resume(USER, CASE, "continuity")
    assert c["goal"]["title"] == "replay case" and c["goal"]["first_input_head"].startswith("GOAL: intermittent 401")  # 사람이 쓴 것만
    assert c["evidence_count"] == 2
    assert [x["evidence_id"] for x in c["evidence_index"]] == [e1, e2] and c["evidence_index"][1]["turn"] == 2
    assert c["constraints"][0]["statement"].startswith("do not rotate") and len(c["decisions"]) == 1 and len(c["ruled_out"]) == 1
    text = str(c)
    assert "host guesses stale key" not in text and "brief" not in c and "record" not in c   # 해석·브리프·레코드 없음
    f = app.resume(USER, CASE, "fresh")
    assert "decisions" not in f and "ruled_out" not in f and f["constraints"] and f["goal"] == c["goal"]
    with pytest.raises(InputError):
        app.resume(USER, CASE, "vibes")
    with pytest.raises(NotFoundError):
        app.resume(2, CASE)


def test_이벤트는_원장에_남고_턴_시퀀스_오라클은_불변(app2, golden):
    app, e1, e2 = app2
    app.decide(USER, CASE, "d"); app.rule_out(USER, CASE, "h", scope="s", evidence_ids=[e2])
    types = [e["event_type"] for e in app.list_ledger(USER, CASE)]
    assert types[-2:] == ["decision_recorded", "hypothesis_ruled_out"]
    expected = next(p["events"] for p in golden if p["path"] == "input_turn")
    assert drive(app, kind="input") == expected                       # 상태 이벤트는 턴 시퀀스에 끼지 않는다
