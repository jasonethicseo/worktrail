"""확장 153호 (D17255) — 필요할 때 당겨 오는 과거 맥락. 보장할 것:
(1) resume 은 지금만: 저장소 공통 결정·제약은 제목과 번호만, 스레드 번호 목록은 개수로
(2) find 는 스레드를 가로질러 한 줄씩, 번호와 함께 — 효력 있는 것이 대체된 것보다 앞
(3) trail 은 스레드 하나를 시간순 한 줄씩, 노트는 해석으로 표시되고 차례(owner)가 붙는다
(4) inspect 는 번호 하나를 전문으로 — 결정은 무엇이 대체했는지까지, 노트는 관찰 원문 + 해석 표시
(5) 옛 inspect(evidence_id) 는 그대로 된다."""
from __future__ import annotations

import json

import pytest

from casebook.adapters.mcp_server import Tools
from casebook.core.errors import InputError, NotFoundError
from tests.drive import USER
from tests.test_threads import _git_repo


def _setup(app, tmp_path):
    wt = _git_repo(tmp_path / "w")
    t = Tools(app, USER)
    a = app.open_thread(USER, "pagination for the orders list", wt)["case_id"]
    b = app.open_thread(USER, "checkout timeout", wt)["case_id"]
    old = app.decide(USER, a, "use offset pagination", reason="simplest", authority="user", scope="repo")["decision_id"]
    new = app.decide(USER, b, "use cursor pagination\n\noffset skipped rows under concurrent writes",
                     reason="rows were skipped", authority="user", scope="repo", supersedes=old)["decision_id"]
    for i in range(30):
        app.constrain(USER, a, f"repo rule {i}\n\n" + "long body " * 40, authority="user", scope="repo")
    t.note_turn(a, "EXPLAIN shows a full scan on offset 90000", "offset pagination is slow past page 900",
                kind="finding", next="Decide between cursor and keyset", owner="user")
    return t, a, b, old, new


def test_resume_은_지금만_주고_저장소_공통은_제목과_번호만(app, tmp_path):
    t, a, b, old, new = _setup(app, tmp_path)
    full = app.resume(USER, a)                     # 화면·인계가 쓰는 코어 resume 은 전문 그대로
    r = t.resume(a)
    rs = r["repo_state"]
    assert "threads" not in rs and rs["thread_count"] == 2
    assert rs["decisions"] == [{"ref": f"D{new}", "title": "use cursor pagination", "authority": "user", "case_id": b}]
    assert all(set(x) == {"ref", "title", "authority", "case_id"} for x in rs["constraints"])
    assert f"trail({a})" in r["note"]
    assert len(json.dumps(r, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False)) / 3
    assert "offset pagination is slow" not in json.dumps(r, ensure_ascii=False)   # 지난 해석은 resume 에 없다


def test_find_는_스레드를_가로질러_한_줄씩_번호와_함께(app, tmp_path):
    t, a, b, old, new = _setup(app, tmp_path)
    f = t.find("pagination")
    refs = [x["ref"] for x in f["records"]]
    assert refs.index(f"D{new}") < refs.index(f"D{old}")                    # 효력 있는 것이 먼저
    by = {x["ref"]: x for x in f["records"]}
    assert by[f"D{old}"]["superseded_by"] == f"D{new}" and by[f"D{old}"]["case_id"] == a
    assert by[f"D{new}"]["line"] == "use cursor pagination" and by[f"D{new}"]["thread"] == "checkout timeout"
    note = by[f"{a}:1"]
    assert note["kind"] == "note" and note["interpretation"] is True and note["note_kind"] == "finding"
    assert [x["ref"] for x in t.find("concurrent writes")["records"]] == [f"D{new}"]   # 본문도 찾는다(줄은 제목만)
    assert t.find("pagination nonexistentword")["records"] == []
    with pytest.raises(InputError):
        t.find("   ")


def test_find_의_증거_발췌는_여러_줄이어도_맞은_낱말을_보인다(app, tmp_path):
    # 발췌(FTS snippet)는 여러 줄로 온다 — 첫 줄만 자르면 맞은 낱말이 빠지고 "… None" 같은 앞 문맥만 남았다.
    t, a, b, old, new = _setup(app, tmp_path)
    t.add_evidence(a, "binding after screen close: None\n  #3 closed needs_result=True  result=None")
    ev = t.find("needs_result")["evidence"]
    assert len(ev) == 1 and "[needs_result]" in ev[0]["excerpt"]
    assert "\n" not in ev[0]["excerpt"]
    t.add_evidence(a, "prefix " * 40 + "\n" + "x" * 150 + " rarewordzz " + "suffix " * 40)
    long = t.find("rarewordzz")["evidence"][0]["excerpt"]
    assert "[rarewordzz]" in long and len(long) <= 160


def test_trail_은_시간순_한_줄씩_노트는_해석으로(app, tmp_path):
    t, a, b, old, new = _setup(app, tmp_path)
    tr = t.trail(a)
    kinds = [e["kind"] for e in tr["events"]]
    assert kinds[0] == "opened" and "decision" in kinds and kinds.count("constraint") == 30
    note = next(e for e in tr["events"] if e["kind"] == "note")
    assert note["interpretation"] is True and note["line"] == "offset pagination is slow past page 900"
    nxt = next(e for e in tr["events"] if e["kind"] == "next")
    assert nxt["owner"] == "user" and nxt["ref"].startswith("N")
    assert "interpretation" in tr["note"]
    short = t.trail(a, limit=5)
    assert len(short["events"]) == 5 and short["earlier"] == len(tr["events"]) - 5
    older = t.trail(a, limit=200, before=short["events"][0]["at"])
    assert all(e["at"] < short["events"][0]["at"] for e in older["events"])
    with pytest.raises(NotFoundError):
        Tools(app, 999).trail(a)                                              # 남의 스레드는 안 보인다


def test_inspect_는_번호_하나를_전문으로(app, tmp_path):
    t, a, b, old, new = _setup(app, tmp_path)
    d = t.inspect(ref=f"D{old}")
    assert d["kind"] == "decision" and d["statement"] == "use offset pagination" and d["reason"] == "simplest"
    assert d["status"] == "superseded" and d["superseded_by"] == {"ref": f"D{new}", "case_id": b, "title": "use cursor pagination"}
    n = t.inspect(ref=f"D{new}")
    assert n["status"] == "in force" and n["supersedes"]["ref"] == f"D{old}"
    assert n["statement"].endswith("offset skipped rows under concurrent writes")
    turn = t.inspect(ref=f"{a}:1")
    assert turn["observed"]["content"] == "EXPLAIN shows a full scan on offset 90000"
    assert turn["conclusion"] == "offset pagination is slow past page 900" and turn["interpretation"] is True
    ev = turn["observed"]["evidence_id"]
    assert t.inspect(ref=f"E{ev}")["content"] == "EXPLAIN shows a full scan on offset 90000"
    assert t.inspect(evidence_id=ev)["content"] == "EXPLAIN shows a full scan on offset 90000"    # 옛 호출
    nx = t.inspect(ref=next(e["ref"] for e in t.trail(a)["events"] if e["kind"] == "next"))
    assert nx["kind"] == "next" and nx["owner"] == "user"
    with pytest.raises(InputError):
        t.inspect(ref="pagination")
    with pytest.raises(NotFoundError):
        Tools(app, 999).inspect(ref=f"D{old}")


def test_초점을_쓰는_도구는_설명을_먼저_출처_인용은_끝에_쓰라고_한다():
    # 초점 본문이 사용자 인용으로 시작해 화면에서 무엇을 하는지가 안 보였다(사용자 2026-09-22) — 쓰는 쪽에서 순서를 잡는다.
    pytest.importorskip("mcp")
    import anyio
    from casebook.adapters.mcp_server import build_server

    async def tools():
        return {x.name: x for x in await build_server(app_for_tools(), USER).list_tools()}

    got = anyio.run(tools)
    for name in ("open_thread", "declare"):
        assert "explain first" in got[name].description and "quoted) last" in got[name].description, name


def app_for_tools():
    from tests.conftest import FakeLLM, FakeSearch, _build_app
    return _build_app(FakeLLM(), FakeSearch())
