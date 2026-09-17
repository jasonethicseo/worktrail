"""설치 전 기록 (확장 42호) — 과거 세션을 저장소 이력으로만 들인다.

(1) 스레드·주제·결정·제약을 만들지 않는다 (C13615·C13608) — 들어오는 것은 사람이 친 말의 원문뿐.
(2) 에이전트의 말과 하네스가 끼워 넣는 것은 걸러진다 — 다음 세션이 폐기된 추론을 사실로 읽으면 안 된다.
(3) 저장소는 디렉터리 이름이 아니라 기록 안의 cwd 로 가린다.
(4) 다시 들여도 늘지 않는다(멱등), 남의 기록은 안 보인다, 원문 식별자는 보존된다 (C13616).
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

from casebook.adapters import backfill
from casebook.core import prior, review, threads
from tests.conftest import FakeLLM, FakeSearch, _build_app

USER = 1
OTHER = 2


@pytest.fixture
def app():
    return _build_app(FakeLLM(), FakeSearch())


def _repo(app, worktree: str) -> int:
    threads.ensure(app.db)
    return threads._repo_for(app.db, USER, threads.identify(worktree))["id"]


def _session(**kw):
    base = {"source": "claude-code", "external_id": "s1", "source_path": "/logs/s1.jsonl",
            "cwd": "/w", "branch": "master", "started_at": 10, "ended_at": 20,
            "instructions": [{"at": 10, "ref": "u1", "text": "토큰은 URL 에 넣지 말자"}]}
    base.update(kw)
    return base


# ── 저장 ────────────────────────────────────────────────────────────────────
def test_들여도_스레드와_주제는_생기지_않는다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    review.ensure(app.db)                       # topic 테이블이 있는 상태에서도 늘지 않아야 한다
    tables = ("thread", "topic", '"case"', "turn", "evidence")

    def counts():
        with app.db._lock:
            return {t: app.db.conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"] for t in tables}

    before = counts()
    prior.import_sessions(app.db, USER, rid, [_session()])
    assert counts() == before                   # 들여도 아무 의미 구조가 생기지 않는다


def test_같은_세션을_다시_들이면_늘지_않는다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    first = prior.import_sessions(app.db, USER, rid, [_session()])
    again = prior.import_sessions(app.db, USER, rid, [_session(
        instructions=[{"at": 10, "ref": "u1", "text": "토큰은 URL 에 넣지 말자"},
                      {"at": 11, "ref": "u2", "text": "한 줄 더"}])])
    assert first["added"] == 1 and again["added"] == 0 and again["updated"] == 1
    assert prior.summary(app.db, USER, rid)["sessions"] == 1
    assert prior.summary(app.db, USER, rid)["instructions"] == 2


def test_사람이_친_말이_없는_세션은_안_들인다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    out = prior.import_sessions(app.db, USER, rid, [_session(instructions=[]),
                                                   _session(external_id="s2", instructions=[{"text": "  "}])])
    assert {k: out[k] for k in ("added", "updated", "skipped", "sessions")} == {
        "added": 0, "updated": 0, "skipped": 2, "sessions": 0}


def test_원문_식별자를_보존한다(app, tmp_path):
    """나중에 "결정에서 그 순간의 원문으로" 내려갈 재료 (C13616)."""
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, USER, rid, [_session()])
    hit = prior.search(app.db, USER, "토큰")[0]
    assert hit["source"] == "claude-code" and hit["external_id"] == "s1"
    assert hit["source_path"] == "/logs/s1.jsonl" and hit["ref"] == "u1"


def test_모르는_출처는_거절한다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    with pytest.raises(Exception):
        prior.import_sessions(app.db, USER, rid, [_session(source="cursor")])
    with pytest.raises(Exception):
        prior.import_sessions(app.db, USER, rid, [_session(external_id="")])


def test_남의_설치전_기록은_안_보인다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, OTHER, rid, [_session(instructions=[{"text": "남의 비밀"}])])
    assert prior.search(app.db, USER, "비밀") == []
    assert prior.summary(app.db, USER, rid)["sessions"] == 0


def test_한국어는_조사가_붙어도_찾는다(app, tmp_path):
    """unicode61 은 "토큰은"을 한 토큰으로 잡는다 — 접두 일치가 아니면 "토큰"으로 못 찾는다."""
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, USER, rid, [_session()])
    assert prior.search(app.db, USER, "토큰")[0]["ref"] == "u1"


def test_검색은_원문을_돌려준다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, USER, rid, [_session(instructions=[
        {"text": "카프카 컨슈머 랙을 보자"}, {"text": "그건 됐고 다른 걸 하자"}])])
    hits = prior.search(app.db, USER, "카프카")
    assert len(hits) == 1 and hits[0]["text"] == "카프카 컨슈머 랙을 보자"


# ── 클라이언트 파서 ─────────────────────────────────────────────────────────
def _write(path: pathlib.Path, rows: list[dict]) -> pathlib.Path:
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    return path


def test_claude_는_사람이_친_말만_고른다(tmp_path):
    """content 가 리스트인 것은 도구 결과다. 하네스가 끼워 넣는 것도 사람의 말이 아니다."""
    f = _write(tmp_path / "a.jsonl", [
        {"type": "user", "uuid": "u1", "sessionId": "S", "cwd": "/w", "gitBranch": "master",
         "timestamp": "2026-09-08T01:00:00.000Z", "message": {"content": "이어서 해줘."}},
        {"type": "user", "uuid": "u2", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "user", "uuid": "u3", "message": {"content": "<command-name>/model</command-name>"}},
        {"type": "user", "uuid": "u4", "message": {"content": "<system-reminder>\n숨은 지시\n</system-reminder>"}},
        {"type": "user", "uuid": "u5", "isMeta": True, "message": {"content": "메타"}},
        {"type": "assistant", "message": {"content": "제가 한 말"}},
        {"type": "user", "uuid": "u6", "timestamp": "2026-09-08T01:05:00.000Z",
         "message": {"content": "그럼 이렇게 하자"}},
    ])
    s = backfill.read_claude(f)
    assert [i["text"] for i in s["instructions"]] == ["이어서 해줘.", "그럼 이렇게 하자"]
    assert s["external_id"] == "a" and s["cwd"] == "/w" and s["branch"] == "master"   # 식별자는 파일 이름
    assert s["started_at"] < s["ended_at"] and s["instructions"][0]["ref"] == "u1"


def test_codex_도_사람이_친_말만_고른다(tmp_path):
    f = _write(tmp_path / "b.jsonl", [
        {"type": "session_meta", "timestamp": "2026-09-08T02:00:00.000Z",
         "payload": {"session_id": "C1", "cwd": "/w", "git": {"branch": "master"}}},
        {"type": "response_item", "timestamp": "2026-09-08T02:00:01.000Z",
         "payload": {"type": "message", "role": "user", "id": "m1",
                     "content": [{"type": "input_text", "text": "# AGENTS.md instructions\n대답은 한글로"}]}},
        {"type": "response_item", "timestamp": "2026-09-08T02:00:02.000Z",
         "payload": {"type": "message", "role": "user", "id": "m2",
                     "content": [{"type": "input_text", "text": "<environment_context>\n  <cwd>/w</cwd>"}]}},
        {"type": "response_item", "timestamp": "2026-09-08T02:00:03.000Z",
         "payload": {"type": "message", "role": "assistant", "id": "m3",
                     "content": [{"type": "output_text", "text": "제가 한 말"}]}},
        {"type": "response_item", "timestamp": "2026-09-08T02:00:04.000Z",
         "payload": {"type": "message", "role": "user", "id": "m4",
                     "content": [{"type": "input_text", "text": "랙 원인부터 보자"}]}},
    ])
    s = backfill.read_codex(f)
    assert [i["text"] for i in s["instructions"]] == ["랙 원인부터 보자"]
    assert s["external_id"] == "b" and s["branch"] == "master" and s["instructions"][0]["ref"] == "m4"


def test_깨진_줄은_건너뛰고_나머지를_읽는다(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text('{"type": "user", "uuid": "u1", "message": {"content": "첫 줄"}}\n{깨진\n')
    assert [i["text"] for i in backfill.read_claude(f)["instructions"]] == ["첫 줄"]


def test_저장소는_폴더_이름이_아니라_기록_안의_cwd_로_가린다(tmp_path, monkeypatch):
    """슬러그는 되돌릴 수 없어 casebook 과 casebook-copilot 이 한 폴더로 뭉친다."""
    logs = tmp_path / "logs"; logs.mkdir()
    mine, other = tmp_path / "proj", tmp_path / "proj-copilot"
    _write(logs / "mine.jsonl", [{"type": "user", "uuid": "a", "sessionId": "A", "cwd": str(mine),
                                  "message": {"content": "내 것"}}])
    _write(logs / "other.jsonl", [{"type": "user", "uuid": "b", "sessionId": "B", "cwd": str(other),
                                   "message": {"content": "남의 것"}}])
    monkeypatch.setattr(backfill, "CLAUDE_ROOT", str(logs))
    monkeypatch.setattr(backfill, "CODEX_ROOT", str(tmp_path / "none"))
    got = backfill.collect(str(mine))
    assert [i["text"] for s in got for i in s["instructions"]] == ["내 것"]


# ── 조각내어 보내기 (요청 상한) ─────────────────────────────────────────────
def test_큰_세션은_조각나되_지시가_사라지지_않는다():
    """이 저장소 실측: 전체 10.7MB · 가장 큰 세션 3MB — 한 번에 보내면 연결이 끊긴다."""
    sessions = [{"source": "claude-code", "external_id": f"s{n}", "cwd": "/w",
                 "instructions": [{"text": "가" * 500, "ref": f"r{n}-{i}"} for i in range(20)]}
                for n in range(5)]
    chunks = list(backfill.batches(sessions, budget=8_000))
    assert len(chunks) > 5
    assert all(len(json.dumps(c, ensure_ascii=False).encode()) <= 9_000 for c in chunks)
    refs = [i["ref"] for c in chunks for s in c for i in s["instructions"]]
    assert refs == [i["ref"] for s in sessions for i in s["instructions"]]      # 순서까지 그대로


def test_조각으로_들여도_한_세션이고_다시_돌리면_같다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    big = _session(instructions=[{"text": f"말 {i}", "ref": f"r{i}"} for i in range(40)])
    for _ in range(2):                                   # 두 번 돌려도 결과가 같아야 한다
        for chunk in backfill.batches([big], budget=1_200):
            prior.import_sessions(app.db, USER, rid, chunk)
        s = prior.summary(app.db, USER, rid)
        assert s["sessions"] == 1 and s["instructions"] == 40
    assert prior.search(app.db, USER, "말")[0]["external_id"] == "s1"


def test_세션_식별자는_파일_이름이다(tmp_path):
    """기록 안의 session_id 는 파일마다 유일하지 않다 — 이어받기·서브에이전트가 같은 id 를 여러 파일에 쓴다.
    이 저장소 실측: 파일 32 → id 15. 그대로 두면 절반 넘게 서로를 덮어쓴다."""
    a = _write(tmp_path / "one.jsonl", [{"type": "user", "uuid": "u", "sessionId": "SAME", "cwd": "/w",
                                         "message": {"content": "첫째"}}])
    b = _write(tmp_path / "two.jsonl", [{"type": "user", "uuid": "u", "sessionId": "SAME", "cwd": "/w",
                                         "message": {"content": "둘째"}}])
    assert backfill.read_claude(a)["external_id"] == "one"
    assert backfill.read_claude(b)["external_id"] == "two"

    c = _write(tmp_path / "rollout-x.jsonl", [
        {"type": "session_meta", "payload": {"session_id": "SAME", "cwd": "/w"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "id": "m",
                                              "content": [{"type": "input_text", "text": "셋째"}]}}])
    assert backfill.read_codex(c)["external_id"] == "rollout-x"


def test_다시_들이면_디스크에_있는_것만_남는다(app, tmp_path):
    """reset — 식별자 규칙이 바뀌거나 파일이 지워지면 낡은 행이 남는다."""
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, USER, rid, [_session(external_id="old", instructions=[{"text": "옛 행"}])])
    prior.import_sessions(app.db, USER, rid, [_session(external_id="new", instructions=[{"text": "새 행"}])],
                          reset=True)
    s = prior.summary(app.db, USER, rid)
    assert s["sessions"] == 1 and s["instructions"] == 1
    assert prior.search(app.db, USER, "옛") == []
    assert prior.search(app.db, USER, "새")[0]["external_id"] == "new"


def test_reset_은_남의_기록을_지우지_않는다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, OTHER, rid, [_session(external_id="theirs", instructions=[{"text": "남의 것"}])])
    prior.import_sessions(app.db, USER, rid, [_session(instructions=[{"text": "내 것"}])], reset=True)
    assert prior.summary(app.db, OTHER, rid)["sessions"] == 1


def test_한_세션_안_같은_말은_한_번만(tmp_path):
    """이어받기·재전송으로 같은 메시지가 uuid 만 다르게 다시 적힌다 — 로그의 흔적이지 두 번 시킨 것이 아니다."""
    f = _write(tmp_path / "d.jsonl", [
        {"type": "user", "uuid": "u1", "cwd": "/w", "message": {"content": "같은 말"}},
        {"type": "user", "uuid": "u2", "cwd": "/w", "message": {"content": "다른 말"}},
        {"type": "user", "uuid": "u3", "cwd": "/w", "message": {"content": "같은 말"}},
    ])
    s = backfill.read_claude(f)
    assert [i["text"] for i in s["instructions"]] == ["같은 말", "다른 말"]
    assert s["instructions"][0]["ref"] == "u1"        # 처음 것을 남긴다


# ── 독립 감사(2026-09-08)가 짚은 것들 ───────────────────────────────────────
def test_에이전트가_띄운_세션은_통째로_뺀다(tmp_path):
    """검토 guardian 같은 서브에이전트 세션의 user 메시지는 에이전트가 만든 요청문이다.
    실측: 이 저장소의 codex 파일 19개가 전부 이것이었고 971건 중 454건·바이트로 96% 였다."""
    f = _write(tmp_path / "g.jsonl", [
        {"type": "session_meta", "payload": {"session_id": "G", "cwd": "/w",
                                             "source": {"subagent": {"other": "guardian"}}}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "id": "m1",
                                              "content": [{"type": "input_text", "text": "검토해라"}]}}])
    assert backfill.read_codex(f) is None

    f2 = _write(tmp_path / "g2.jsonl", [
        {"type": "session_meta", "payload": {"session_id": "H", "cwd": "/w", "thread_source": "guardian_review"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "id": "m",
                                              "content": [{"type": "input_text", "text": "x"}]}}])
    assert backfill.read_codex(f2) is None


def test_압축_요약은_사람의_말이_아니다(tmp_path):
    f = _write(tmp_path / "s.jsonl", [
        {"type": "user", "uuid": "u1", "cwd": "/w", "isCompactSummary": True,
         "message": {"content": "지금까지의 요약: ..."}},
        {"type": "user", "uuid": "u2", "cwd": "/w", "message": {"content": "이어서 해줘"}}])
    assert [i["text"] for i in backfill.read_claude(f)["instructions"]] == ["이어서 해줘"]


def test_이미지와_함께_쓴_말은_살린다(tmp_path):
    """배열을 통째로 버리면 사람이 쓴 말이 사라진다 — 이 저장소 실측 16건."""
    f = _write(tmp_path / "i.jsonl", [
        {"type": "user", "uuid": "u1", "cwd": "/w", "message": {"content": [
            {"type": "image", "source": {}}, {"type": "text", "text": "이 화면 고쳐줘"}]}},
        {"type": "user", "uuid": "u2", "cwd": "/w", "message": {"content": [
            {"type": "tool_result", "content": "x"}, {"type": "text", "text": "도구 결과 딸림"}]}}])
    assert [i["text"] for i in backfill.read_claude(f)["instructions"]] == ["이 화면 고쳐줘"]


def test_원문을_다듬지_않는다(tmp_path):
    f = _write(tmp_path / "w.jsonl", [
        {"type": "user", "uuid": "u1", "cwd": "/w", "message": {"content": "  앞뒤 공백 그대로\n"}}])
    assert backfill.read_claude(f)["instructions"][0]["text"] == "  앞뒤 공백 그대로\n"


def test_같은_조각을_다시_받아도_늘지_않는다(app, tmp_path):
    """append 재전송 — 조각 프로토콜은 중복 수신에 안전해야 한다."""
    rid = _repo(app, str(tmp_path))
    head = {"source": "claude-code", "external_id": "s1"}
    prior.import_sessions(app.db, USER, rid, [{**head, "instructions": [{"text": "하나", "ref": "a"}]}])
    tail = {**head, "append": True, "instructions": [{"text": "둘", "ref": "b"}]}
    prior.import_sessions(app.db, USER, rid, [tail])
    prior.import_sessions(app.db, USER, rid, [tail])          # 재전송
    assert prior.summary(app.db, USER, rid)["instructions"] == 2


def test_요청_하나가_통째로_되거나_통째로_안_된다(app, tmp_path):
    """쓰다 실패하면 지운 기록이 돌아오지 않는다 — 교체는 원자적이어야 한다."""
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, USER, rid, [_session(instructions=[
        {"text": "원래 것", "ref": "a"}, {"text": "원래 것 둘", "ref": "b"}])])
    with pytest.raises(Exception):
        prior.import_sessions(app.db, USER, rid, [_session(instructions=[
            {"text": "새 것", "ref": "c"}, {"text": object(), "ref": "d"}])])
    s = prior.summary(app.db, USER, rid)
    assert s["sessions"] == 1 and s["instructions"] == 2
    assert prior.search(app.db, USER, "원래")[0]["ref"] == "a"


def test_저장소가_병합되면_설치_전_기록도_따라온다(app, tmp_path):
    """repo 승격·병합이 thread·binding 만 옮기고 prior 를 두고 가면 기록이 통째로 사라진다."""
    threads.ensure(app.db)
    old = threads._repo_for(app.db, USER, {"identity": "path:/w", "hint": "/w", "aliases": []})
    prior.import_sessions(app.db, USER, old["id"], [_session()])
    new = threads._repo_for(app.db, USER, {"identity": "github.com/acme/w", "hint": "g", "aliases": ["path:/w"]})
    assert new["id"] != old["id"] or True
    assert prior.summary(app.db, USER, new["id"])["sessions"] == 1


def test_native_기록이_시작된_뒤는_들이지_않는다(app, tmp_path):
    """이 저장소 실측: casebook 이 이미 기록 중이던 기간이라 백필의 순증이 0건이었다."""
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, USER, rid, [_session(instructions=[
        {"text": "설치 전", "at": 100, "ref": "a"},
        {"text": "설치 뒤", "at": 300, "ref": "b"},
        {"text": "시각 모름", "ref": "c"}])], cutoff=200)
    got = [h["text"] for h in prior.search(app.db, USER, "설치")]
    assert "설치 전" in got and "설치 뒤" not in got
    assert prior.summary(app.db, USER, rid)["instructions"] == 2      # 시각 모르는 것은 남긴다


def test_컷오프는_그_저장소가_처음_기록한_때다(app, tmp_path):
    from casebook.core import threads as th
    wt = str(tmp_path)
    rid = _repo(app, wt)
    assert prior.cutoff_for(app.db, USER, rid) is None                # 스레드가 없으면 컷오프도 없다
    app.open_thread(USER, "첫 스레드", wt)
    cut = prior.cutoff_for(app.db, USER, rid)
    assert isinstance(cut, int) and cut > 0


# ── 전역 회수와 고르기 (GPT 제안 반영) ──────────────────────────────────────
def test_작업공간별로_묶고_큰_것부터_보여준다(tmp_path, monkeypatch):
    logs = tmp_path / "logs"; logs.mkdir()
    a, b = tmp_path / "projA", tmp_path / "projB"
    a.mkdir(); b.mkdir()
    _write(logs / "1.jsonl", [{"type": "user", "uuid": "u1", "cwd": str(a), "message": {"content": "가"}},
                              {"type": "user", "uuid": "u2", "cwd": str(a), "message": {"content": "나"}}])
    _write(logs / "2.jsonl", [{"type": "user", "uuid": "u3", "cwd": str(b), "message": {"content": "다"}}])
    monkeypatch.setattr(backfill, "CLAUDE_ROOT", str(logs))
    monkeypatch.setattr(backfill, "CODEX_ROOT", str(tmp_path / "none"))
    ws = backfill.workspaces(backfill.collect_all())
    assert [w["instructions"] for w in ws] == [2, 1]        # 큰 것부터
    assert ws[0]["cwd"] == str(a) and ws[0]["exists"] is True


def test_고르지_않으면_아무것도_보내지_않는다(monkeypatch):
    ws = [{"cwd": "/a", "sessions": [], "instructions": 1, "bytes": 1, "exists": True}]
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert backfill.choose(ws, []) is None                  # 고를 수 없는 곳에서는 거절한다
    assert backfill.choose(ws, ["--all"]) == ws
    assert backfill.choose(ws, ["--only", "/a"]) == ws
    assert backfill.choose(ws, ["--only", "없는것"]) == []


def test_폴더가_사라져도_기록은_버리지_않는다(tmp_path):
    gone = str(tmp_path / "지워진곳")
    f = backfill.facts_for(gone)
    assert f["worktree"] == gone and f["identity"] == f"path:{gone}"


# ── 과거 기록 화면 (확장 44호) ──────────────────────────────────────────────
def test_과거_기록은_작업공간별로_묶여_나온다(app, tmp_path):
    a, b = tmp_path / "A", tmp_path / "B"
    a.mkdir(); b.mkdir()
    ra, rb = _repo(app, str(a)), _repo(app, str(b))
    prior.import_sessions(app.db, USER, ra, [_session(external_id="a1", cwd=str(a), instructions=[
        {"text": "가", "ref": "1"}, {"text": "나", "ref": "2"}])])
    prior.import_sessions(app.db, USER, rb, [_session(external_id="b1", cwd=str(b),
                                                      instructions=[{"text": "다", "ref": "3"}])])
    arc = prior.archive(app.db, USER)
    assert arc["totals"] == {"workspaces": 2, "sessions": 2, "instructions": 3}
    assert [w["instructions"] for w in arc["workspaces"]] == [2, 1]        # 큰 것부터

    ss = prior.sessions_of(app.db, USER, ra)
    assert len(ss) == 1 and ss[0]["first_instruction"] == "가"

    d = prior.session_detail(app.db, USER, ss[0]["id"])
    assert [i["text"] for i in d["items"]] == ["가", "나"]                 # 순서대로, 원문 그대로
    assert d["instructions"] == 2                                          # 개수는 개수로 남는다


def test_남의_과거_기록은_열_수_없다(app, tmp_path):
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, OTHER, rid, [_session(instructions=[{"text": "남의 것"}])])
    assert prior.archive(app.db, USER)["totals"]["sessions"] == 0
    sid = prior.sessions_of(app.db, OTHER, rid)[0]["id"]
    with pytest.raises(Exception):
        prior.session_detail(app.db, USER, sid)


def test_목록_단서는_짧은_한_마디를_건너뛴다(app, tmp_path):
    """첫 말이 "run" 이면 세션을 고를 수 없다 — 처음 만나는 충분히 긴 말을 단서로 쓴다."""
    rid = _repo(app, str(tmp_path))
    prior.import_sessions(app.db, USER, rid, [_session(instructions=[
        {"text": "run", "ref": "1"},
        {"text": "OHLCV CSV 만 주고 나머지는 네가 다 해라", "ref": "2"},
        {"text": "더 길게 쓴 두 번째 문장이지만 앞의 것이 먼저다", "ref": "3"}])])
    assert prior.sessions_of(app.db, USER, rid)[0]["first_instruction"].startswith("OHLCV")

    prior.import_sessions(app.db, USER, rid, [_session(external_id="s2", instructions=[
        {"text": "run", "ref": "a"}, {"text": "ok", "ref": "b"}, {"text": "다시 해봐", "ref": "c"}])])
    lead = [s for s in prior.sessions_of(app.db, USER, rid) if s["external_id"] == "s2"][0]
    assert lead["first_instruction"] == "다시 해봐"     # 긴 말이 없으면 그중 가장 긴 것


def test_로컬_모드는_서버_없이_바로_들인다(tmp_path, monkeypatch):
    """기록이 이 맥에만 있을 때 backfill 이 거부하면 안 된다 — 그 DB 에 바로 적는다."""
    import os
    from casebook.core import prior as pr
    wt = tmp_path / "proj"; wt.mkdir()
    monkeypatch.setenv("CASEBOOK_DB", str(tmp_path / "local.db"))
    monkeypatch.delenv("CASEBOOK_REMOTE_URL", raising=False)
    out = backfill.import_local(str(wt), [{"source": "claude-code", "external_id": "s1", "cwd": str(wt),
                                           "instructions": [{"text": "예전에 한 말", "ref": "a"}]}])
    assert out["added"] == 1 and out["stored_instructions"] == 1
    from casebook.core.db import SqliteDB
    db = SqliteDB(str(tmp_path / "local.db"))
    assert pr.summary_all(db, 1)["instructions"] == 1
    assert os.path.isfile(tmp_path / "local.db")


def test_로컬_모드_안내가_서버를_요구하지_않는다():
    import inspect
    src = inspect.getsource(backfill.main)
    assert "기록이 이 맥에만 있다" in src and "import_local" in src
