"""테스트 공용 — 오라클 로더와 가짜 어댑터.

모델을 실제로 부르지 않는다. 오라클이 보는 것은 **이벤트 시퀀스**이고
그건 모델 텍스트와 무관하다(인수 조건: 답변 텍스트는 비교 대상이 아니다).

실패 주입: FakeLLM.fail_next 에 역할("answer"/"recorder"/"record"/"draft_query")을,
FakeSearch.fail_next 에 True 를 넣으면 다음 호출 1회가 실패한다 — 골든 실패 경로와
불변식 5 가 이 스위치로 돈다.
"""
from __future__ import annotations
import json, os, pathlib, pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

# 리플레이 코퍼스에는 입력 원문이 없다(글자 수만). 사건 내용을 테스트 repo 로
# 옮기지 않기 위해서다 — 이벤트 시퀀스는 입력 내용에 의존하지 않는다.
PLACEHOLDER_INPUT = "replay placeholder input"


@pytest.fixture(scope="session", autouse=True)
def _isolated_home(tmp_path_factory):
    """HOME 격리 — 맥의 ~/.casebook/remote-url(전환 스위치, 확장 29호)이 있으면 훅·프록시 서브프로세스가
    tmp sqlite 대신 실제 원격 문으로 가서 14건이 결정적으로 깨진다(2026-09-06). 테스트는 git 을 -c 로 쓰므로
    전역 gitconfig 에도 기대지 않는다."""
    home = tmp_path_factory.mktemp("home")
    prev = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    yield
    if prev is None:
        os.environ.pop("HOME", None)
    else:
        os.environ["HOME"] = prev


@pytest.fixture(scope="session")
def golden() -> list[dict]:
    return json.loads((FIXTURES / "golden_turn_sequences.json").read_text(encoding="utf-8"))["paths"]


@pytest.fixture(scope="session")
def replay_turns() -> list[dict]:
    d = json.loads((FIXTURES / "replay_corpus.json").read_text(encoding="utf-8"))
    return [t for t in d["turns"] if t.get("era") in ("current", "era_independent")]


class FakeLLM:
    """기본은 항상 성공. fail_next 에 넣은 역할은 다음 호출 1회가 실패한다."""

    def __init__(self) -> None:
        self.fail_next: set[str] = set()

    def _maybe_fail(self, role: str) -> None:
        if role in self.fail_next:
            self.fail_next.discard(role)
            raise RuntimeError(f"injected {role} failure")

    def answer(self, system, prompt, effort="none", web=False):
        self._maybe_fail("answer")
        out = {"text": "fake answer", "usage": {}}
        if web:  # 확장 3호 — 실어댑터가 돌려주는 모양 그대로
            out["web"] = {"queries": ["fake query"],
                          "citations": [{"url": "https://example.test/doc", "title": "Doc"}]}
        return out

    def record_brief(self, system, prompt):
        self._maybe_fail("recorder")
        return {"brief": {"focus": "f", "evidence": [], "considering": [],
                          "recent_updates": [], "next_up": []}}

    def draft_query(self, system, prompt):
        self._maybe_fail("draft_query")
        return {"query": "fake query"}

    def assemble_record(self, system, prompt):
        self._maybe_fail("record")
        return {"content": "# fake record"}


class FakeSearch:
    def __init__(self) -> None:
        self.fail_next = False

    def lookup(self, query, engine):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected search failure")
        return {"results": [{"title": "t", "link": "https://example.test/1", "snippet": "s"}]}


# ── 확장 130호 (D16054) — 공개 트리에는 조사·티켓 모듈이 없다 ────────────────────────────────
# private 에서는 시험 전부가 조사 객체(Casebook)로 돈다. 공개 트리(tools/release.sh 가 만드는 것)에는 그 모듈이
# 없으므로 app 픽스처는 Worktrail 로 서고, 조사 기능을 부르는 시험은 실패가 아니라 "private 전용" 으로 건너뛴다.
# 좁게 잡는다: 그 모듈들의 ModuleNotFoundError, 그리고 Worktrail 에 없는 속성을 부르는 AttributeError 만.
# 그 밖의 실패는 그대로 실패다. private 저장소의 전체 실행이 진짜 관문이다.
INVESTIGATION_MODULES = frozenset({
    "casebook.core.app", "casebook.core.tickets", "casebook.core.workers", "casebook.core.prompts",
    "casebook.core.prompt_ext", "casebook.core.ledger", "casebook.adapters.openai_llm",
    "casebook.adapters.no_search", "casebook.adapters.http_api_legacy",
})


def investigation_available() -> bool:
    try:
        import casebook.core.app  # noqa: F401
        return True
    except ImportError:
        return False


def _needs_investigation(exc: BaseException) -> str | None:
    import re
    if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", "") in INVESTIGATION_MODULES:
        return f"needs {exc.name} (private only)"
    if isinstance(exc, AttributeError):
        m = re.search(r"'Worktrail' object has no attribute '(\w+)'", str(exc))
        if m:
            return f"needs Casebook.{m.group(1)} (private only)"
    return None


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    outcome = yield
    if investigation_available():
        return
    try:
        outcome.get_result()
    except BaseException as exc:  # noqa: BLE001 — 아래에서 좁게 고른다
        why = _needs_investigation(exc)
        if why is None:
            return
        outcome.force_exception(pytest.skip.Exception(why))


def _build_app(llm, search):
    from casebook.core.db import SqliteDB
    try:
        from casebook.core.app import Casebook
    except ImportError:                      # 공개 트리에는 조사 모듈이 없다(확장 130호) — 기록만으로 세운다
        Casebook = None

    db = SqliteDB(":memory:")
    # drive.USER/CASE(=1/1) 과 맞춘 시드
    user = db.add("user", {"name": "tester", "email": "tester@example.test", "password": "x"})
    case = db.add("case", {"user_id": user["id"], "title": "replay case",
                           "status": "open", "schema_version": 1})
    assert user["id"] == 1 and case["id"] == 1
    if Casebook is None:
        from casebook.core.worktrail import Worktrail
        return Worktrail(db)
    return Casebook(db=db, llm=llm, search=search)


@pytest.fixture
def app():
    return _build_app(FakeLLM(), FakeSearch())


class RecordingLLM(FakeLLM):
    """모델 호출을 전부 기록한다. 불변식 1·3·4·7·12·15 를 여기서 검사한다."""
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str, str]] = []   # (role, system, prompt)
        self.answer_efforts: list[str] = []           # 답변 호출이 받은 effort 순서대로
        self.answer_webs: list[bool] = []             # 답변 호출이 받은 web 순서대로

    def _rec(self, role, system, prompt, out):
        self.calls.append((role, system, prompt)); return out

    def answer(self, system, prompt, effort="none", web=False):
        self.answer_efforts.append(effort); self.answer_webs.append(web)
        return self._rec("answer", system, prompt, super().answer(system, prompt, effort, web))
    def record_brief(self, system, prompt):    return self._rec("recorder", system, prompt, super().record_brief(system, prompt))
    def draft_query(self, system, prompt):     return self._rec("draft_query", system, prompt, super().draft_query(system, prompt))
    def assemble_record(self, system, prompt): return self._rec("record", system, prompt, super().assemble_record(system, prompt))


@pytest.fixture
def rec_app():
    """호출을 기록하는 앱. (app, llm) 튜플."""
    llm = RecordingLLM()
    return _build_app(llm, FakeSearch()), llm
