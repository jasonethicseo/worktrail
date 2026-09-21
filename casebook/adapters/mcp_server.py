"""MCP 문 — 확장 4호 (2026-09-03). 코어 위의 두 번째 문(첫 번째는 http_api).

    python -m casebook.adapters.mcp_server          # stdio. Claude Code / Codex 가 띄운다.
    python -m casebook.adapters.mcp_server http     # 확장 27호 — 원격 문(streamable-http). Claude.ai·ChatGPT 커넥터용.
    python -m casebook.adapters.mcp_server mint EMAIL   # 원격 문 토큰 발급 → 커넥터 URL 은 https://<host>/mcp/<token>

원격 문 (확장 27호, 2026-09-06):
    같은 build_server 를 HTTP 로 연다. 사용자는 요청마다 정한다 — 경로 /mcp/<token> 의 토큰(또는 Authorization: Bearer)을
    웹 로그인과 같은 비밀(db meta auth_secret)로 검증해 user.id 를 되찾는다. 채팅 클라이언트에는 작업 디렉터리가 없으므로
    worktree 인자를 안 주면 가상 worktree "chat"(core.threads.is_virtual)이 된다. add_evidence_file 은 서버 파일을 읽는
    도구라 원격에서는 거부한다. CASEBOOK_MCP_HOST/PORT(기본 127.0.0.1:8790)로 바인드, 공개는 터널(Tailscale Funnel 등)이 맡는다.

환경변수:
    CASEBOOK_DB          sqlite 경로 (기본 ~/.casebook/casebook.db). 같은 파일을 main.py --db 로
                         열면 웹 앱에서 그 케이스들을 그대로 읽는다 — sqlite 하나, 문 두 개.
    CASEBOOK_MCP_EMAIL   케이스 소유자 계정 (기본 mcp@local, 없으면 비밀번호 없이 만든다)
    모델 키 없이 기록·검색·인계를 모두 수행한다.

원칙 (인계문 §5·§6):
- 추론은 호스트가 한다. Worktrail은 원문과 호스트의 결론을 저장하고, 선언된 상태로 인계한다.
  조사 답변·모델 요약·레코드 생성은 Casebook HTTP 문에 남는다.
- 도구는 확인만 돌려준다. 브리프·레코드는 사람이 handoff 를 명시적으로 부를 때만 나간다
  (불변식 3·15 의 정신 — 요약을 모델 컨텍스트에 되먹이지 않는다).
- 호스트 모델이 case_id 를 보는 것은 불변식 4 의 경계 밖이다(Casebook 의 추론 모델이 아니다).
- 동기 실행. 워커는 도구 호출 안에서 끝까지 돈다(드레이너 스레드 없음).
"""
from __future__ import annotations

import os
import pathlib
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from casebook.core import access, auth, headline, hookctx, oauth, prior, reads, recall, state, threads
from casebook.core.worktrail import Worktrail
from casebook.core.db import SqliteDB
from casebook.core.errors import AccessDeniedError, ApiError, InputError

try:   # 도구 함수의 `ctx: Context` 주석을 SDK 가 평가해야 한다(from __future__ annotations) — 모듈 수준에 있어야 보인다
    from mcp.server.mcpserver import Context
except ModuleNotFoundError:   # 코어 테스트는 mcp 없이 돈다; build_server 가 불릴 때만 필요하다
    Context = Any  # type: ignore[misc,assignment]

INSTRUCTIONS = """Worktrail keeps a durable, byte-exact record of engineering work so the next session or agent can
pick it up without guessing from the code.

Unit of work: a thread — one question or change in one repository, followed to the end. Deciding and
building are ONE thread (re-declare focus); open a new one only when the question changes. list_threads
first. open_thread(focus, topic) starts and binds one; switch_thread / close_thread change it;
declare(focus|open|next) keeps it current.

When to record:
- A turn is when the machine told you something that changed what you would do next — a test result,
  an error, a measurement, a diff. Record the raw output as evidence and your conclusion from it. If
  nothing new was observed, there is no turn.
- Keep raw evidence sparse: what grounded a decision or is hard to reproduce; if git or a file holds
  it, reference it.
- Your conclusions are interpretation, not evidence — evidence is only what the machine or engineer
  actually said.

add_evidence / add_evidence_file take raw material VERBATIM — never paraphrase or trim.
note_turn(kind=change|verified|finding|thought) after each step: what was observed, what you concluded.
handoff only when the engineer asks where things stand — never to refresh your own memory.
First line of every statement = its title: ≤120 columns (CJK=2), no ' — ', no record numbers (#517,
D15635, turn 80, commit hash) — numbers go in the body or evidence_ids, else refused.
A next for the user is written to them, not about them.

Durable state — the boundary is "what could overturn it": a hypothesis (the world can still contradict
it) stays in note_turn, never in decide. A decision or constraint (only the engineer can change it) goes
to decide/constrain with authority, and is superseded, not edited. rule_out needs the evidence that
killed the hypothesis AND the scope where that holds.

resume = the state now. Pull the past when needed: find(query) → trail(case_id) → inspect(ref).
Notes there are interpretation; a hit is retrieval, not conclusion."""

def _first_line(text: str | None, limit: int = 100) -> str:
    """기록의 첫 줄(= 제목). 규칙이 서기 전의 옛 기록은 길어서 자른다."""
    line = (text or "").strip().split("\n", 1)[0].strip()
    return line if len(line) <= limit else line[: limit - 1] + "…"


def compact_threads(db, rows: list[dict[str, Any]], include_closed: bool = False
                    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """확장 151호 — 에이전트가 받는 스레드 목록을 한 줄씩으로 줄인다.

    예전에는 저장소의 스레드 전부를 초점 전문·결과 전문째 냈다. 스레드가 90개를 넘자 56,958자가 되어 도구
    한도를 넘었고, 에이전트는 매번 파일로 빠진 것을 다시 읽어야 했다(사용자 2026-09-21 "해봐"). 고르는 데
    필요한 것은 제목과 누구 차례뿐이다 — 한 스레드의 전부는 resume 이 준다.
    열린 스레드: 제목 · 턴 수 · 마지막 활동 · 다음 차례(선언된 next 의 첫 줄과 owner) · 묶인 worktree.
    닫힌 스레드: 기본은 개수만 — 결과 줄이 비어 정리가 필요한 것(needs_result)도 개수로만 센다. 처음에는 그것들을
    늘 한 줄씩 냈는데 실기록에서 17개가 목록의 절반을 차지했다. "정리해줘" 는 사람이 시킬 때만 하는 일이라
    그때 include_closed=True 로 부르면 된다 — 닫힌 것 전부가 결과 첫 줄(또는 needs_result)과 함께 온다.
    핵심 함수(threads.list_threads)는 그대로 둔다 — 세션 시작 훅이 전문을 쓴다."""
    out: list[dict[str, Any]] = []
    n_closed = n_need = 0
    for t in rows:
        title = _first_line(t.get("focus") or t.get("title"))
        if t["state"] == "open":
            row: dict[str, Any] = {"case_id": t["case_id"], "title": title, "state": "open",
                                   "turns": t.get("turn_count", 0), "updated_at": t.get("updated_at")}
            nxt = state.current_declaration(db, t["case_id"], "next")
            if nxt:
                row["next"] = _first_line(nxt["statement"])
                row["owner"] = nxt.get("owner")
            if t.get("bound_worktrees"):
                row["bound_worktrees"] = t["bound_worktrees"]
            out.append(row)
            continue
        n_closed += 1
        n_need += bool(t.get("needs_result"))
        if include_closed:
            row = {"case_id": t["case_id"], "title": title, "state": "closed"}
            if t.get("needs_result"):
                row["needs_result"] = True
            elif t.get("result"):
                row["result"] = _first_line(t["result"])
            out.append(row)
    return out, {"count": n_closed, "needs_result": n_need, "listed": include_closed}


CLIENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")

EVIDENCE_INDEX_IN_RESUME = 20


def compact_resume(out: dict[str, Any]) -> dict[str, Any]:
    """확장 153호 — 에이전트가 받는 resume 은 지금만. 화면(thread_state)과 인계는 전문을 그대로 쓴다.
    저장소 공통 결정·제약은 제목과 번호만 — 전문과 이유는 inspect(ref). 스레드 번호 목록은 개수로."""
    rs = out.get("repo_state")
    if rs:
        rs["thread_count"] = len(rs.pop("threads", []) or [])
        for key, pre in (("constraints", "C"), ("decisions", "D")):
            if key in rs:
                rs[key] = [{"ref": f"{pre}{x[key[:-1] + '_id']}", "title": _first_line(x["statement"], 120),
                            "authority": x["authority"], "case_id": x["case_id"]} for x in rs[key]]
        rs["note"] = "titles only; inspect(ref) gives the statement and reason in full"
    idx = out.get("evidence_index") or []
    if len(idx) > EVIDENCE_INDEX_IN_RESUME:
        out["evidence_index"] = idx[-EVIDENCE_INDEX_IN_RESUME:]
    out["note"] = ("durable state as it stands now, not how it got here. When you need the past: "
                   f"trail({out['case_id']}) walks this thread line by line, find(query) searches every thread, "
                   "inspect(ref) returns one record in full. Notes there are marked interpretation, not evidence.")
    return out


def normalize_client(name: str | None) -> str | None:
    """확장 40호 — 호스트가 initialize 에서 댄 이름(clientInfo.name). 받은 그대로 저장하고 화면이 사람 말로 옮긴다.
    실측(2026-09-08): Claude Code "claude-code" · Codex "codex-mcp-client". 못 받으면 None — 미상이라고 꾸미지 않는다."""
    n = (name or "").strip()
    return n if n and CLIENT_RE.match(n) else None

def client_from_ctx(ctx: Any) -> str | None:
    """프록시를 거치지 않는 직결 stdio 문에서는 여기서 직접 읽는다(프록시 경로는 인자로 받는다)."""
    try:
        p = ctx.session.client_params
        return normalize_client(p.client_info.name if p and p.client_info else None)
    except Exception:      # noqa: BLE001 — 이름을 못 읽어도 기록은 된다
        return None

_REQUEST_ID: ContextVar[str | None] = ContextVar("casebook_mcp_request_id", default=None)
# 재시도 열쇠는 요청의 _meta 로만 받는다. 도구 입력칸에 두면 모델이 그 칸을 채울 수 있고, 같은 값을 두 번 쓰면
# 채팅 커넥터·옛 프록시에서 두 번째 기록이 합쳐진다. 모델은 _meta 를 보지도 쓰지도 않는다.
REQUEST_ID_META = "casebook/request_id"


def request_id_from(ctx: Any) -> str | None:
    try:
        meta = ctx.request_context.meta
    except Exception:      # noqa: BLE001 — 요청 밖(시험의 직접 호출)이면 열쇠가 없는 것이다
        return None
    value = meta.get(REQUEST_ID_META) if isinstance(meta, dict) else None
    return value if isinstance(value, str) else None


@contextmanager
def _request_context(request_id: str | None):
    """Pass the transport retry key without adding a bypass argument to Tools."""
    token = _REQUEST_ID.set(request_id)
    try:
        yield
    finally:
        _REQUEST_ID.reset(token)


def _key() -> str:
    key = (_REQUEST_ID.get() or "").strip() or uuid.uuid4().hex
    if len(key) > 128:
        raise InputError("request_id is too long")
    return key

class Tools:
    """도구 본체 — MCP 프레임워크와 무관한 평범한 메서드. 테스트는 이걸 직접 부른다."""

    def __init__(self, casebook: Worktrail, user_id: int, remote: bool = False) -> None:
        self.cb = casebook     # Worktrail 이면 충분하다 — 확장 39호 3단계. Casebook 이면 handoff 가 레코드까지 만든다.
        self.uid = user_id
        self.remote = remote   # 확장 27호 — 원격 문: worktree 기본값은 가상 "chat", 서버 파일 읽기 없음

    def _source(self, client: str | None) -> dict[str, Any]:
        """확장 40호 — 증거에 어느 클라이언트가 남겼는지 한 칸. 못 받으면 종전과 같은 {"via": "mcp"} 다."""
        c = normalize_client(client)
        return {"via": "mcp", **({"client": c} if c else {})}

    @property
    def _can_write_record(self) -> bool:
        """레코드(산문 요약)는 조사 도구의 물건이다. 기록 제품만 있으면 선언된 상태로 답한다."""
        return hasattr(self.cb, "generate_record")

    def open_case(self, title: str) -> dict[str, Any]:
        r = self.cb.create_case(self.uid, title)
        return {"case_id": r["case_id"], "title": title}

    # ── 스레드 층 (확장 15호) ──
    @staticmethod
    def _worktree(worktree: str | None) -> str:
        # 우선순위: 호출 인자 → CASEBOOK_WORKTREE(등록 래퍼가 cd 하기 전의 $PWD = 호스트의 프로젝트 디렉터리)
        # → CLAUDE_PROJECT_DIR → 서버 프로세스 cwd. 래퍼가 casebook 저장소로 cd 하므로 cwd 는 마지막 수단이다 —
        # Codex 가 다른 저장소(casebook-lab)에서 열 때 이 저장소에 잘못 묶이지 않게(2026-09-05).
        return (worktree or os.environ.get("CASEBOOK_WORKTREE") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())

    def _wt(self, worktree: str | None, facts: dict[str, Any] | None = None) -> str:
        # 확장 29호: 얇은 클라이언트가 git 사실(facts)을 실어 보내면 그 worktree 가 곧 작업 공간이다.
        # 원격(채팅 클라이언트)에서 worktree 도 facts 도 없으면 서버 프로세스의 cwd 가 아니라 가상 작업 공간이다.
        if facts and facts.get("worktree"):
            wt = facts["worktree"]
        elif self.remote:
            wt = worktree or threads.VIRTUAL_IDENTITY
        else:
            wt = self._worktree(worktree)
        # 확장 118호 — 데스크톱 앱의 임시 작업공간은 저장소가 아니라 대화 맥락이다.
        return threads.VIRTUAL_IDENTITY if threads.is_scratch(wt) else wt

    def _facts(self, worktree: str | None, facts: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """확장 48호 (2026-09-10, D14179) — 원격 문은 git 사실을 지어내지 않는다. facts 가 왔으면 그것(프록시·훅이
        클라이언트의 git 에서 읽은 값), 가상 작업 공간이면 없음, 경로만 왔으면 이미 묶인 경로의 정체성(바인딩)뿐이다.
        처음 보는 경로는 거절한다 — 종전에는 threads.identify 가 서버 컨테이너에서 git 을 돌리다 실패해 path: 정체성을
        새로 만들었고, 같은 워크트리가 두 저장소로 갈렸다(#492). stdio 문은 종전 그대로(서버가 곧 그 기계다)."""
        if facts and facts.get("worktree"):
            # 확장 118호 — 임시 작업공간의 git 사실은 버린다. 그 경로로 저장소를 세우지 않는다.
            return None if threads.is_scratch(facts["worktree"]) else facts
        if not self.remote:
            return None if threads.is_scratch(self._wt(worktree, facts)) else facts
        wt = self._wt(worktree, facts)
        if threads.is_virtual(wt):
            return None
        known = threads.facts_from_binding(self.cb.db, self.uid, wt)
        if known is None:
            raise ApiError(f"worktree {wt!r} came without git facts and is not bound to any thread — the remote door "
                           "does not read git. Call through the Worktrail proxy (it fills `facts` from the client's git), "
                           "or omit worktree from a chat client.")
        return known

    UNCLASSIFIED = "미분류"   # 확장 30호: 이제 거절된다 — 주제 없는 스레드는 열리지 않는다

    @staticmethod
    def _same_topic(name: str) -> str:
        """주제 이름 비교용 열쇠. 띄어쓰기와 대소문자만 다른 것은 같은 이름으로 본다 —
        "기록 규칙" 과 "기록규칙" 이 두 주제가 되면 그 둘을 나중에 사람이 합쳐야 한다."""
        return "".join((name or "").split()).lower()

    def open_thread(self, focus: str, worktree: str | None = None, title: str | None = None,
                    authority: str = "agent", topic: str | None = None, facts: dict[str, Any] | None = None,
                    new_topic: bool = False) -> dict[str, Any]:
        """확장 28호 (2026-09-06): topic 은 필수다. D11098("여는 순간 assign_topic")이 에이전트 기억에 의존해 깨졌다
        (#458 이 미분류로 열림) — 결정을 도구가 강제한다(engineer_asked=true 와 같은 계열). 거절 문구에 기존 주제를
        실어 새 이름을 지어내지 않고 고르게 한다. 자동 배정(현재 스레드의 주제 물려주기)은 하지 않는다 — 대화 주제가
        바뀌어 새 스레드를 여는 때가 많아, 틀린 분류가 조용히 쌓인다.
        확장 30호 (2026-09-07): "미분류" 도망길도 닫는다 — 강제가 있는데도 #464 가 그 길로 미분류가 됐다(사용자: "미분류가
        계속 등장하는 건 피할 수 없나"). 정말 안 맞으면 새 주제를 만든다(이름만 대면 생긴다)."""
        topic = (topic or "").strip()
        names = [t["name"] for t in self.cb.list_topics(self.uid)]
        listed = "; ".join(names) if names else "(none yet)"
        if not topic or topic == self.UNCLASSIFIED:
            raise ApiError("topic is required — pass the topic this thread belongs to (an existing name below, or a new one). "
                           f"\"{self.UNCLASSIFIED}\" is not accepted: every thread belongs to a topic. Existing topics: {listed}")
        # 확장 118호 — 처음 보는 이름은 한 번 거절하고 목록을 보인다. 종전에는 빈 값과 "미분류" 만
        # 걸러서, 그럴듯한 새 이름을 대면 목록을 한 번도 안 보고 주제가 하나 더 생겼다. 실측: 주제
        # "원격 MCP 문"(7스레드)과 "원격 MCP 연결"(2스레드)이 같은 일로 갈렸고, #517 이 앞쪽에 있는데
        # 같은 일인 #523·#524 가 새 주제에 따로 섰다. 확장 30호가 "미분류" 도망길을 닫은 것과 같은
        # 자리이고 같은 이유다 — 강제가 없으면 에이전트는 기억에 기대고, 기억은 새 세션마다 비어 있다.
        # 띄어쓰기·대소문자만 다르면 기존 이름으로 맞춘다 — 통과시켜 놓고 topic_by_name 이 정확한
        # 이름으로만 찾으면, 거절도 안 되고 주제는 갈리는 최악이 된다.
        same = next((n for n in names if self._same_topic(n) == self._same_topic(topic)), None)
        if same:
            topic = same
        # names 가 비면 거르지 않는다 — 갓 깐 사람의 첫 기록이 거절로 시작하면 안 되고, 나눌 것도 없다.
        if names and not new_topic and same is None:
            raise ApiError(
                f"\"{topic}\" is a new topic — no topic by that name exists yet. Existing topics: {listed}. "
                "Pick the one this work belongs to. If none of them fits, call again with new_topic=true. "
                "A new name that means the same as an existing one splits the record in two, and only the "
                "engineer can merge them back (merge_topic).")
        with threads.provided(self._facts(worktree, facts)):
            out = self.cb.open_thread(self.uid, focus, self._wt(worktree, facts), title, authority)
        t = self.cb.topic_by_name(self.uid, topic, "", None, [out["case_id"]])
        out["topic"] = {"topic_id": t["topic_id"], "name": t["name"]}
        return out

    def switch_thread(self, case_id: int | None = None, worktree: str | None = None,
                      facts: dict[str, Any] | None = None) -> dict[str, Any]:
        with threads.provided(self._facts(worktree, facts)):
            return self.cb.switch_thread(self.uid, case_id, self._wt(worktree, facts))

    def close_thread(self, case_id: int, result: str = "") -> dict[str, Any]:
        return self.cb.close_thread(self.uid, case_id, result or None)

    def list_threads(self, worktree: str | None = None, facts: dict[str, Any] | None = None,
                     include_closed: bool = False) -> dict[str, Any]:
        wt = self._wt(worktree, facts)
        with threads.provided(self._facts(worktree, facts)):
            out = self.cb.list_threads(self.uid, wt)
        out["threads"], out["closed"] = compact_threads(self.cb.db, out["threads"], include_closed)
        if threads.is_virtual(wt):
            # 채팅에는 저장소가 없다 — "chat" 저장소의 스레드만 보이면 쓸모가 없으니 열린 작업 전부를 함께 낸다
            out["other_repositories"] = [r for r in self.cb.overview(self.uid) if r["identity"] != threads.VIRTUAL_IDENTITY]
        return out

    def overview(self) -> list[dict[str, Any]]:
        return self.cb.overview(self.uid)

    def declare(self, case_id: int, kind: str, statement: str, authority: str = "agent", owner: str | None = None) -> str:
        r = self.cb.declare(self.uid, case_id, kind, statement, authority, owner)
        sup = f", supersedes #{r['supersedes']}" if r["supersedes"] else ""
        who = f", owner: {r['owner']}" if r.get("owner") else ""
        return f"{kind} #{r['declaration_id']} declared for thread {case_id} (authority: {authority}{who}{sup})."

    def assign_topic(self, name: str, case_ids: list[int], summary: str = "", repos: list[str] | None = None,
                     conclusion: str = "") -> str:
        r = self.cb.topic_by_name(self.uid, name, summary, repos, case_ids, conclusion=conclusion or None)
        tail = f" Conclusion: {r['conclusion']}" if r.get("conclusion") else ""
        return f"topic #{r['topic_id']} '{r['name']}' now holds threads {r['case_ids']}.{tail}"

    def list_topics(self) -> list[dict[str, Any]]:
        """확장 47호 — 주제 목록: 이름·설명·스레드. 겹치는 이름을 사람이 판정할 재료다(읽기 전용)."""
        return [{"topic_id": t["id"], "name": t["name"], "summary": t.get("summary") or "", "threads": t["case_ids"],
                 **({"conclusion": t["conclusion"]} if t.get("conclusion") else {})} for t in self.cb.list_topics(self.uid)]

    def merge_topic(self, topic: str, into: str, engineer_asked: bool = False) -> str:
        """확장 47호 (2026-09-09, D13981) — 주제 합치기는 사람이 시킬 때만(handoff 와 같은 계열). 에이전트가 스스로
        주제를 합치면 사람이 나눠 둔 것이 조용히 사라진다 — 분류는 사람의 판단이다(D13955)."""
        if not engineer_asked:
            return ("merge_topic is for the engineer's explicit request, not for you. Call it only when they asked to "
                    "merge or tidy topics — then pass engineer_asked=true. To see what overlaps, use list_topics.")
        names = [t["name"] for t in self.cb.list_topics(self.uid)]
        src = self.cb.find_topic(self.uid, topic)
        dst = self.cb.find_topic(self.uid, into)
        missing = [n for n, t in ((topic, src), (into, dst)) if t is None]
        if missing:
            raise ApiError(f"topic not found: {'; '.join(repr(m) for m in missing)} — pass an existing name exactly. "
                           f"Existing topics: {'; '.join(names) if names else '(none yet)'}")
        r = self.cb.merge_topic(self.uid, src["id"], dst["id"])
        return (f"topic #{r['merged']['topic_id']} '{r['merged']['name']}' merged into #{r['topic_id']} '{r['name']}': "
                f"{r['merged']['moved']} thread(s) moved, it now holds {r['case_ids']}. '{r['merged']['name']}' no longer exists.")

    def rate_handoff(self, case_id: int | None = None, constraints_retained: bool | None = None,
                     decisions_retained: bool | None = None, duplicate_investigation: bool | None = None,
                     wrong_next_step: bool | None = None, re_explained: int | None = None,
                     retrieval_used: bool | None = None, retrieval_changed_action: bool | None = None,
                     note: str = "") -> str:
        r = reads.review_handoff(self.cb.db, self.uid, case_id, constraints_retained=constraints_retained,
                                 decisions_retained=decisions_retained, duplicate_investigation=duplicate_investigation,
                                 wrong_next_step=wrong_next_step, re_explained=re_explained, retrieval_used=retrieval_used,
                                 retrieval_changed_action=retrieval_changed_action, note=note or None)
        return f"Handoff review #{r['review_id']} recorded" + (f" for thread {case_id}." if case_id else ".")

    def list_cases(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.cb.list_cases(self.uid)[: max(1, min(limit, 100))]
        return [{"case_id": c["id"], "title": c["title"], "status": c["status"],
                 "focus": c["brief_focus"],
                 # 확장 12호 vitals — 목록이 마지막 활동 순이라 "이어서 할 케이스"가 위에 온다
                 "turn_count": c["turn_count"], "updated_at": c["updated_at"],
                 "last_turn_status": c["last_turn_status"]} for c in rows]

    _SUMMARY_HEAD = re.compile(r"^\s*(evidence\s*#\s*\d+|e\d+|summary|요약|정리)\b", re.I)

    def _reject_summary(self, text: str) -> str | None:
        """첫 실사용(442 turn 17)에서 호스트가 evidence 의 요약문을 evidence 로 넣었다 — 기계 출력이 아니다.
        기존 evidence 를 가리키며 시작하거나 'Evidence #N: …' 꼴이면 note_turn 으로 돌려보낸다."""
        head = (text or "").lstrip()[:80]
        if self._SUMMARY_HEAD.match(head) or re.search(r"Evidence\s*#\d+\s*[:：]", head):
            return ("rejected: this reads like a summary of existing evidence, not raw material. Evidence is what the "
                    "machine or the engineer actually produced, verbatim. Put your reading of it in note_turn(conclusion).")
        return None

    def add_evidence(self, case_id: int, text: str, client: str | None = None) -> str:
        bad = self._reject_summary(text)
        if bad:
            # 거부도 측정한다 — Codex 2회차가 짚은 구멍: handoff_refused 는 남는데 이건 안 남았다
            reads.log(self.cb.db, self.uid, "evidence_refused", case_id, (text or "").strip()[:80])
            return bad
        r = self.cb.external_turn(self.uid, case_id, text, action_key=_key(), note=None,
                                  source=self._source(client))
        self.cb.run_workers()
        return f"Evidence #{r['evidence_id']} stored byte-exact as turn {r['sequence']} of case {case_id}."

    def add_evidence_file(self, case_id: int, path: str, max_bytes: int = 200_000) -> str:
        """파일을 서버가 직접 읽는다 — 호스트가 로그를 출력 토큰으로 다시 쓰지 않아도 된다."""
        if self.remote:
            raise ApiError("add_evidence_file is not available over the remote door — it reads files on the server, "
                           "not on your machine. Paste the text with add_evidence instead.")
        fp = pathlib.Path(path).expanduser()
        if not fp.is_file():
            raise ApiError(f"not a file: {path}")
        raw = fp.read_bytes()
        clipped = len(raw) > max_bytes
        text = raw[:max_bytes].decode("utf-8", errors="replace")
        head = f"[file] {fp}" + (f" (first {max_bytes} of {len(raw)} bytes)" if clipped else f" ({len(raw)} bytes)") + "\n"
        r = self.cb.external_turn(self.uid, case_id, head + text, action_key=_key(), note=None,
                                  source={"via": "mcp", "file": str(fp), "bytes": len(raw), "clipped": clipped})
        self.cb.run_workers()
        return (f"Evidence #{r['evidence_id']} stored from {fp.name} ({len(raw)} bytes"
                + (", clipped" if clipped else "") + f") as turn {r['sequence']} of case {case_id}.")

    FOCUS_TURN_CAP = 15   # 확장 46호 — 마지막 focus 선언 뒤 이만큼 쌓이면 note_turn 은 거절된다

    def _check_focus_cap(self, case_id: int) -> None:
        """확장 46호 (2026-09-09): 스레드가 주제에서 미끄러지는 것을 도구가 잡는다(#484). D11098("주제가 바뀌면 새 스레드")은
        repo 범위인데도 #446(87턴)에 이어 #483(33턴)에서 다시 깨졌다 — 28호처럼 안내문은 실패한 것이 측정됐으니 거절한다.
        세는 것은 마지막 focus 선언 뒤의 턴이고(D13874), 문턱 15 는 2026-09-09 분포에서 미끄러진 둘만 걸고 건강한
        스레드(4~20턴)는 안 건드리는 값이다(D13873). 빠져나가는 길은 둘뿐(D13875): 같은 일이면 declare(focus) 로 확인하고
        계속(append-only 라 그 확인이 남는다), 아니면 open_thread. 우회 인자는 두지 않는다 — 30호에서 도망길은 그리로 가는
        것이 측정됐다."""
        c = state.turns_since_focus(self.cb.db, case_id)
        if c["turns"] < self.FOCUS_TURN_CAP:
            return
        f = c["focus"]
        where = (f"since its focus was declared (#{f['id']}: \"{f['statement'][:80]}\")" if f
                 else "and no focus has ever been declared")
        raise ApiError(f"thread {case_id} has {c['turns']} turns {where} — the cap is {self.FOCUS_TURN_CAP}. "
                       "A thread is one subject; this many turns usually means the conversation has moved on (D11098). "
                       "If it has not: restate the focus WORD FOR WORD — that is recorded as a confirmation, not a "
                       "change, and it does not show up as a new focus in the review or the timeline. Do NOT fold "
                       "progress into it: where you got to belongs in your notes (the screen reads the last one as "
                       "SO FAR) and what happens next belongs in next. A focus that carries progress competes with "
                       "both and then none of the three can be trusted. "
                       "Two ways forward, nothing else: if this is still the same work, confirm it with "
                       "declare(case_id, \"focus\", <the focus, restated>) and then record the turn; if the subject has "
                       "changed, open_thread(focus, topic) for the new one. Nothing was recorded.")

    # 확장 60호 — 노트의 갈래(D14720, #508). 실서버 노트 45개 표본이 이 넷으로 나뉘었다(바꿈 44 · 발견 24 · 생각 18 · 확인 13 %).
    # 붙이는 것은 에이전트다 — next 의 owner 처럼 없으면 거절한다. 사후 AI 분류는 하지 않는다. 옛 노트는 갈래 없이 "노트".
    NOTE_KINDS = {"change": "바꿈", "verified": "확인", "finding": "발견", "thought": "생각"}
    _NOTE_ALIASES = {**{v: k for k, v in NOTE_KINDS.items()}, **{k: k for k in NOTE_KINDS}}

    @classmethod
    def normalize_note_kind(cls, kind: str | None) -> str:
        k = cls._NOTE_ALIASES.get((kind or "").strip().lower()) or cls._NOTE_ALIASES.get((kind or "").strip())
        if not k:
            raise InputError(
                "note_turn needs kind — one of change(바꿈: 코드·화면·설정이 바뀜) · verified(확인: 테스트·실서버·재현으로 검증됨) · "
                "finding(발견: 재거나 원인을 알아낸 사실) · thought(생각: 제안·가설·판단, 뒤집힐 수 있는 것). "
                "둘이 섞이면 노트를 둘로 쪼갠다. Decisions are decide(), not a note kind.")
        return k

    def note_turn(self, case_id: int, observed: str, conclusion: str, kind: str | None = None,
                  next: str | None = None, owner: str | None = None, client: str | None = None,
                  evidence_ids: list[int] | None = None) -> str:
        """확장 110호 (D15573) — 턴마다 next 를 다시 말한다.

        전에는 next 에 아무 강제가 없어 낡은 채로 남았다: 스레드 520 에서 마지막 노트보다 7시간·
        턴 10개만큼 낡은 next 가 이미 끝난 일을 가리키고 있었다. focus 에는 15턴 상한이 있는데
        사람이 실제로 읽고 움직이는 줄에는 없었다. 따로 부르게 하지 않고 여기서 받는 이유는
        도구 호출을 늘리지 않고도 매 턴 다시 생각하게 만들기 때문이다. 글이 그대로면 state.declare
        가 새 줄을 쓰지 않으므로, "매 턴 갱신" 은 실제로 "매 턴 재확인" 이 된다."""
        note_kind = self.normalize_note_kind(kind)
        if not (next or "").strip():
            raise InputError(
                "note_turn needs next — after this turn, what happens next and whose turn is it? "
                "Pass next=<one line, then a blank line, then the detail> and owner=<\"user\" | "
                "\"watch\" | an agent name such as \"claude\" / \"codex\">. Say it every turn, even when "
                "nothing changed: if it is the same as before, nothing new is written (D15573). "
                "That line is what the next session reads to know what to do.")
        # 턴을 먼저 쌓고 next 를 쓰다 거절당하면 그 턴만 next 없이 남는다 — 먼저 재어 본다.
        headline.check(next, "next")
        if not state.normalize_owner(owner):
            raise InputError('next needs an owner — who takes the next action: "user" (the engineer '
                             'must act), "watch" (nothing to do, only watching), or an agent name '
                             'such as "codex" / "claude"')
        headline.check_addressed(next, owner, "next")          # 확장 123호 — 사람의 차례면 사람에게 쓴다
        refs = state.owned_evidence(self.cb.db, self.uid, evidence_ids)   # 확장 123호 — 남의 증거는 못 단다
        self._check_focus_cap(case_id)
        r = self.cb.external_turn(self.uid, case_id, observed, action_key=_key(), note=conclusion,
                                  source=self._source(client), note_kind=note_kind, refs=refs)
        # next 는 턴 뒤에 선언한다 — 앞에 두면 원장 순서에서 next 가 노트보다 먼저가 되어
        # 화면이 "이 next 는 마지막 노트보다 낡았다" 로 읽는다.
        d = self.cb.declare(self.uid, case_id, "next", next, "agent", owner)
        tail = (f" next #{d['declaration_id']} unchanged." if d.get("unchanged")
                else f" next #{d['declaration_id']} declared (owner: {d['owner']}).")
        self.cb.run_workers()
        cites = f" Cites evidence {', '.join('#' + str(i) for i in refs)}." if refs else ""
        return (f"Turn {r['sequence']} recorded in case {case_id}: Evidence #{r['evidence_id']} "
                f"+ your conclusion.{cites}{tail}")

    def search_evidence(self, query: str, limit: int = 10, include_archived: bool = False) -> list[dict[str, Any]]:
        """기록된 증거 먼저, 그 뒤에 설치 전 기록(D13627 — 검색은 포함, 목록은 섞지 않음)."""
        rows = self.cb.search_evidence(self.uid, query, limit, include_archived)
        reads.log(self.cb.db, self.uid, "search", None, query, len(rows))
        hits = [{"evidence_id": r["evidence_id"], "case_id": r["case_id"], "case_title": r["case_title"],
                 "turn": r["turn"], "kind": r["kind"], "created_at": r["created_at"],
                 "source": r["source"], "excerpt": r["excerpt"]} for r in rows]
        room = max(int(limit) - len(hits), 0)
        if room:
            hits += [self._as_hit(r) for r in prior.search(self.cb.db, self.uid, query, room)]
        return hits
    def inspect(self, evidence_id: int | None = None, ref: str | None = None) -> dict[str, Any]:
        if ref is None and evidence_id is None:
            raise InputError("give ref (D17255, C11100, R42, N17252, E2513, 564:3) or evidence_id")
        target = ref if ref is not None else evidence_id
        out = recall.inspect(self.cb.db, self.uid, target)
        reads.log(self.cb.db, self.uid, "inspect", out["case_id"], str(target))
        return out

    def find(self, query: str, limit: int = recall.FIND_LIMIT, include_closed: bool = True) -> dict[str, Any]:
        out = recall.find(self.cb.db, self.uid, query, limit, include_closed)
        reads.log(self.cb.db, self.uid, "find", None, query, len(out["records"]) + len(out["evidence"]))
        return out

    def trail(self, case_id: int, limit: int = recall.TRAIL_LIMIT, before: int | None = None) -> dict[str, Any]:
        out = recall.trail(self.cb.db, self.uid, case_id, limit, before)
        reads.log(self.cb.db, self.uid, "trail", case_id, str(before or ""))
        return out

    def decide(self, case_id: int, statement: str, reason: str = "", evidence_ids: list[int] | None = None,
               authority: str = "agent", supersedes: int | None = None, scope: str = "thread",
               client: str | None = None) -> str:
        r = self.cb.decide(self.uid, case_id, statement, reason, evidence_ids, authority, supersedes, scope,
                           client=normalize_client(client))
        sup = f", supersedes D{r['supersedes']}" if r["supersedes"] else ""
        return f"Decision D{r['decision_id']} recorded in case {case_id} (scope: {scope}, authority: {authority}{sup})."

    def constrain(self, case_id: int, statement: str, reason: str = "", authority: str = "agent",
                  supersedes: int | None = None, scope: str = "thread") -> str:
        r = self.cb.constrain(self.uid, case_id, statement, reason, authority, supersedes, scope)
        sup = f", supersedes C{r['supersedes']}" if r["supersedes"] else ""
        return f"Constraint C{r['constraint_id']} recorded in case {case_id} (scope: {scope}, authority: {authority}{sup})."

    def define(self, case_id: int, term: str, meaning: str, authority: str = "agent",
               supersedes: int | None = None, scope: str = "thread") -> str:
        r = self.cb.define(self.uid, case_id, term, meaning, authority, supersedes, scope)
        sup = f", supersedes T{r['supersedes']}" if r["supersedes"] else ""
        return f"Term T{r['term_id']} '{r['term']}' defined in case {case_id} (scope: {scope}, authority: {authority}{sup})."

    def rule_out(self, case_id: int, hypothesis: str, scope: str, evidence_ids: list[int]) -> str:
        r = self.cb.rule_out(self.uid, case_id, hypothesis, scope, evidence_ids)
        return f"Ruled out R{r['ruled_out_id']} in case {case_id}, within scope: {scope}"

    def resume(self, case_id: int, mode: str = "continuity", worktree: str | None = None,
               facts: dict[str, Any] | None = None) -> dict[str, Any]:
        with threads.provided(self._facts(worktree, facts)):
            out = self.cb.resume(self.uid, case_id, mode, self._wt(worktree, facts))
        reads.log(self.cb.db, self.uid, "resume", case_id, mode)
        return compact_resume(out)

    def hook(self, event: str, facts: dict[str, Any], why: str = "", reason: str = "") -> dict[str, Any]:
        """확장 29호 — 훅 스크립트 전용(에이전트용 아님). 맥이 읽은 git 사실로 SessionStart 본문·commit·checkpoint 를 서버에서."""
        if not facts or not facts.get("worktree"):
            raise ApiError("hook needs facts with a worktree — the hook script builds them with threads.local_facts()")
        with threads.provided(facts):
            return hookctx.run(self.cb, self.uid, event, facts["worktree"], why, reason)

    def import_prior(self, facts: dict[str, Any], sessions: list, reset: bool = False) -> dict[str, Any]:
        """확장 42호 — backfill 스크립트 전용(에이전트용 아님). 설치 전 세션을 저장소 이력으로만 들인다.
        스레드·주제·결정은 만들지 않는다(C13615) — 여기 오는 것은 사람이 친 말의 원문뿐이다."""
        if not facts or not facts.get("worktree"):
            raise ApiError("import_prior needs facts with a worktree — the backfill script builds them")
        with threads.provided(facts):
            threads.ensure(self.cb.db)
            repo = threads._repo_for(self.cb.db, self.uid, threads.identify(facts["worktree"]))
            cut = prior.cutoff_for(self.cb.db, self.uid, repo["id"])
            out = prior.import_sessions(self.cb.db, self.uid, repo["id"], sessions, reset=reset, cutoff=cut)
        out["repository"] = repo["identity"]
        return out

    def search_prior(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return prior.search(self.cb.db, self.uid, query, limit)

    @staticmethod
    def _as_hit(row: dict[str, Any]) -> dict[str, Any]:
        """설치 전 기록 한 건을 검색 결과 모양으로. evidence 와 같은 칸을 쓰되 kind 로 구분한다 —
        기록된 증거가 아니라 원자료이고, 그때 폐기된 이야기일 수도 있다."""
        return {"evidence_id": None, "case_id": None, "case_title": "설치 전 기록", "kind": "pre_install",
                "created_at": row.get("at"), "turn": None, "excerpt": row.get("text"),
                "source": {"client": row.get("source"), "session": row.get("external_id"),
                           "path": row.get("source_path"), "ref": row.get("ref")}}

    def handoff(self, case_id: int, engineer_asked: bool = False) -> str:
        # 첫 실사용(442 turn 18)에서 호스트가 resume 직후 자기 기억 갱신용으로 handoff 를 불렀다.
        # 설명문("do not call it to refresh your own memory")은 무시됐다 → 명시적 단언을 요구한다.
        if not engineer_asked:
            reads.log(self.cb.db, self.uid, "handoff_refused", case_id)
            return ("handoff is for the engineer, not for you. Call it only when they asked for a summary, "
                    "a handover, or where the case stands — then pass engineer_asked=true. "
                    "To orient yourself, use resume(case_id).")
        reads.log(self.cb.db, self.uid, "handoff", case_id)
        case = self.cb.get_case(self.uid, case_id)
        head = f"# Handoff — case {case_id} · {case['case']['title']}\n\n"
        # 인계의 라벨은 이 스레드가 쓰인 언어로 붙인다(D15186) — 묻지 않고 focus 에서 읽는다.
        # 저장하지 않으므로 focus 를 다시 선언하면 라벨도 따라온다. 고칠 자리가 따로 없다.
        st = self.cb.resume(self.uid, case_id, "continuity")
        # focus 의 **제목 줄**로 본다 — 본문에는 인용한 로그·코드가 섞여 판정을 흔든다.
        # focus 가 없는 스레드(선언 전)는 케이스 제목으로 떨어진다: 한국어 단서를 손에 쥐고도
        # 영어로 판정하던 자리다.
        L = HANDOFF_LABELS[headline.lang_of(
            headline.title_of((st.get("focus") or {}).get("statement")) or case["case"]["title"])]
        if not self._can_write_record:
            body = L["intro"]
        else:
            r = self.cb.generate_record(self.uid, case_id)
            self.cb.run_workers()
            recs = [x for x in self.cb.list_records(self.uid, case_id) if x["id"] == r["record_id"]]
            rec = recs[0] if recs else None
            if rec and rec["status"] == "created":
                body = rec["content"]
            else:
                reason = ((rec or {}).get("error") or {}).get("reason", "unknown")
                body = f"_Record generation failed ({reason}). Declared state below is the latest._"
        # 확장 39호 — 꼬리는 모델이 만든 brief 가 아니라 선언된 상태에서 온다. 기록 도구가 가진
        # 것으로 답한다: 무엇을 위한 스레드이고, 무엇이 안 풀렸고, 다음은 누구 차례인가.
        lines = []
        if st.get("focus"):
            lines.append(f"- focus: {st['focus']['statement']}")
        if st.get("open"):
            lines.append(f"- open: {st['open']['statement']}")
        if st.get("next"):
            owner = st["next"].get("owner")
            lines.append(f"- next: {st['next']['statement']}" + (f"  ({L['owner']}: {owner})" if owner else ""))
        if not self._can_write_record:
            for label, group in ((L["decision"], st.get("decisions", [])), (L["constraint"], st.get("constraints", [])),
                                 (L["repo_decision"], st.get("repo_state", {}).get("decisions", [])),
                                 (L["repo_constraint"], st.get("repo_state", {}).get("constraints", []))):
                for item in group:
                    lines.append(f"- {label} ({item.get('authority', 'agent')}): {item['statement']}")
                    if item.get("reason"):
                        lines.append(f"  {L['reason']}: {item['reason']}")
            for item in st.get("ruled_out", []):
                lines.append(f"- {L['ruled_out']}: {item['hypothesis']} "
                             f"({L['scope']}: {item['scope']}, {L['grounds']}: {item['evidence_ids']})")
            for item in st.get("evidence_index", []):
                lines.append(f"- {L['evidence']} #{item['evidence_id']}: {item.get('head', '')}")
        tail = ("\n\n---\n## Declared state\n" + "\n".join(lines) + "\n") if lines else ""
        return head + body + tail

# 인계의 라벨 — 스레드가 쓰인 언어로 붙는다(D15186). 기록 본문은 손대지 않는다: 그것은 사람이
# 쓴 글이고, 둘레의 라벨만 읽는 사람 쪽으로 맞춘다.
HANDOFF_LABELS = {
    "ko": {"intro": "_Worktrail — 선언된 상태와 근거로 구성한 인계입니다._",
           "owner": "차례", "decision": "결정", "constraint": "제약",
           "repo_decision": "저장소 공통 결정", "repo_constraint": "저장소 공통 제약",
           "reason": "이유", "ruled_out": "배제", "scope": "범위", "grounds": "근거", "evidence": "증거"},
    "en": {"intro": "_Worktrail — a handoff built from the declared state and what grounded it._",
           "owner": "turn", "decision": "decision", "constraint": "constraint",
           "repo_decision": "repository decision", "repo_constraint": "repository constraint",
           "reason": "reason", "ruled_out": "ruled out", "scope": "scope", "grounds": "grounds",
           "evidence": "evidence"},
}

REMOTE_TOKEN_TTL = 10 * 365 * 86400   # 커넥터에 한 번 넣는 토큰 — 회수는 auth_secret 교체(웹 로그인도 함께 풀린다)

# 확장 134호 — 인자 경계가 깨진 호출(quant-events 545). 모델이 observed 를 잘못 닫자 conclusion·next 가 observed 안에
# 글자로 들어갔고, SDK 의 pydantic 에러는 input_value 로 그 원문 마크업을 모델에게 되돌려줬다 — 그 대화는 곧 안전
# 분류기에 막혔다. 그래서 에러는 인자 이름과 할 일만 말하고 값은 한 글자도 싣지 않는다.
# 흔적으로 보는 것은 "이 도구의 **다른** 인자 이름을 단 parameter 여는 태그" 뿐이다: 이 사건을 증거로 남기는
# add_evidence(text) 의 conclusion·next 는 그 도구의 인자가 아니므로 걸리지 않는다.
_PARAM_OPEN = re.compile(r"<(?:[\w-]+:)?parameter\s+name\s*=\s*[\"']?([A-Za-z_]\w*)")


def boundary_leak(tool: str, arguments: dict[str, Any], params: set[str]) -> str | None:
    """문자열 인자 안에 같은 도구의 다른 인자가 섞여 들어갔으면 원문 없는 거절문, 아니면 None."""
    for key, value in arguments.items():
        if not isinstance(value, str):
            continue
        leaked = sorted({n for n in _PARAM_OPEN.findall(value) if n in params and n != key})
        if leaked:
            names = ", ".join(leaked)
            return (f"{tool}: {key} contains other arguments ({names}) as text — the {key} value was closed "
                    f"wrongly and swallowed the ones after it. Send {names} as separate arguments and call "
                    f"{tool} again. Nothing was recorded.")
    return None


def argument_error(tool: str, exc: Any) -> str:
    """pydantic ValidationError 를 인자 이름과 할 일로만 다시 쓴다 — input_value(원문)는 버린다."""
    missing, other = [], []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "arguments"
        if err.get("type") == "missing":
            missing.append(loc)
        else:
            other.append(f"{loc}: {err.get('msg', 'invalid')}")
    parts = []
    if missing:
        names = ", ".join(missing)
        parts.append(f"missing required argument(s): {names}. Each is its own argument — if you did write them, "
                     f"check they did not end up inside another argument's value (a wrongly closed argument "
                     f"swallows the ones after it), then send them separately")
    parts.extend(other)
    return f"{tool}: " + "; ".join(parts) + ". Nothing was recorded."

def build_server(casebook: Worktrail, user_id: int | None = None, *, resolve_user=None):
    """MCPServer 조립. mcp 는 여기서만 임포트한다 — 코어 테스트는 mcp 없이 돈다.

    user_id: stdio 처럼 프로세스가 한 사용자의 것일 때. resolve_user(ctx) -> int: 원격 문처럼 요청마다 사용자가
    다를 때 — 그 경우 Tools 는 remote=True 로 만들어진다. 둘 중 하나는 있어야 한다."""
    from mcp.server.mcpserver import MCPServer

    if (user_id is None) == (resolve_user is None):
        raise ValueError("pass exactly one of user_id, resolve_user")
    server = MCPServer("casebook", instructions=INSTRUCTIONS)
    fixed = Tools(casebook, user_id) if user_id is not None else None

    def T(ctx) -> Tools:
        return fixed if fixed is not None else Tools(casebook, resolve_user(ctx), remote=True)

    def _guard(fn):
        def wrapped(*a, **kw):
            try:
                return fn(*a, **kw)
            except ApiError as exc:
                return f"casebook error: {exc.message}"
        wrapped.__name__ = fn.__name__
        wrapped.__doc__ = fn.__doc__
        return wrapped

    @server.tool(name="open_case", description="Start a new investigation case. Title = one line, the first symptom as the engineer stated it. Returns case_id; reuse it for the rest of this problem.")
    def open_case(title: str, ctx: Context) -> dict:
        return T(ctx).open_case(title)

    # ── 스레드 층 (확장 15호) ──
    @server.tool(name="list_threads", description="This worktree's current thread and the threads of its repository, one line each (open first, most recent activity first): title (the focus's first line), turns, updated_at, and for open threads the declared next's first line with its owner. Closed threads are only counted (closed.count, closed.needs_result) unless include_closed=true. needs_result means the engineer closed it from the screen without a result line; when they ask you to tidy up, call list_threads(include_closed=true), read each thread marked needs_result and fill it with close_thread(case_id, result). For one thread's full state use resume(case_id). Call it first in a session; pass worktree (absolute path) when you are not in the server's cwd. From a chat client (no repository) it also lists open work across all repositories.")
    def list_threads(ctx: Context, worktree: str | None = None, facts: dict | None = None, include_closed: bool = False) -> dict:
        return T(ctx).list_threads(worktree, facts, include_closed)

    @server.tool(name="overview", description="Browse your open work across ALL repositories before choosing a directory, including in Codex where there is no SessionStart hook. Returns repositories with open threads: focus, next, updated_at (last turn, record or commit activity), and bound_worktrees. Repositories and threads are ordered by recent activity; closed/archived threads and repositories without open work are omitted. No worktree argument or model call. Use resume(case_id) for constraints and decisions before continuing a thread.")
    def overview(ctx: Context) -> list[dict]:
        return T(ctx).overview()

    @server.tool(name="open_thread", description="Start a thread — one question or change in one repository, followed to the end (deciding and building are one thread; re-declare focus to continue) — and bind it to this worktree. Open a new one only when the question changes. focus = one sentence, what this thread is for (durable declaration, not a summary). In the body, explain first — what the thread does and what would finish it — and put where it came from (the engineer's words, quoted) last, as its grounds: the screen shows the body in order, so a quote first hides the focus. topic (required) = the line of work this thread belongs to — it must be one of the engineer's existing topics; a name that is not one of them is refused once, with the list, so you choose instead of inventing. Only when none of them fits, call again with new_topic=true. \"Uncategorized\" (미분류) is refused — every thread belongs to a topic. Returns case_id; use it for evidence, note_turn, decide. FIRST LINE = TITLE: at most 120 columns (CJK counts as 2), no ' — ' joiner, and no record numbers (#517, D15635, 확장 122호, turn 80, commit hashes) — a person reads that line on the screen; put the rest, numbers included, after a blank line as the body (the server refuses otherwise).")
    def open_thread(focus: str, topic: str, ctx: Context, worktree: str | None = None, title: str | None = None, authority: str = "agent", facts: dict | None = None, new_topic: bool = False) -> dict:
        return _guard(T(ctx).open_thread)(focus, worktree, title, authority, topic, facts, new_topic)

    @server.tool(name="switch_thread", description="Bind this worktree to an existing open thread (case_id), or pause: case_id omitted unbinds the worktree. Threads are per repository; the binding is per worktree, so two agents in two worktrees never overwrite each other. From a chat client the binding may point at a thread of any repository.")
    def switch_thread(ctx: Context, case_id: int | None = None, worktree: str | None = None, facts: dict | None = None) -> dict:
        return _guard(T(ctx).switch_thread)(case_id, worktree, facts)

    @server.tool(name="close_thread", description="Close a thread (status resolved) and release every worktree bound to it. result = one sentence saying what came of it — it becomes the thread's closing line in Worktrail's Status screen and the retrospective. Also fills the result line of a thread the engineer already closed from the screen (needs_result in list_threads) — appended as a new ledger event, never an edit. Closed threads stay searchable; reopen from the screen or via set_status, then switch_thread to it. FIRST LINE = TITLE: at most 120 columns (CJK counts as 2), no ' — ' joiner, and no record numbers (#517, D15635, 확장 122호, turn 80, commit hashes) — a person reads that line on the screen; put the rest, numbers included, after a blank line as the body (the server refuses otherwise).")
    def close_thread(case_id: int, ctx: Context, result: str = "") -> dict:
        return _guard(T(ctx).close_thread)(case_id, result)

    @server.tool(name="declare", description="Declare the thread's current focus (what it is for), open (what is still unresolved: questions, blockers, uncertainty) or next (the 1–3 concrete actions to take next). kind = focus|open|next. focus says what the thread is FOR and what would finish it — not how far along you are. In the body, explain first — what the thread does and what would finish it — and put where it came from (the engineer's words, quoted) last, as its grounds: the screen shows the body in order, so a quote first hides the focus. Re-declaring it with exactly the same words is a confirmation (it resets the turn cap and is not shown as a change); re-declare with different words only when the direction itself changed. Append-only: the previous declaration of that kind is superseded, not edited. next REQUIRES owner — whose turn it is: \"user\" (the engineer must act), \"watch\" (nothing to do — observing only), or an agent name such as \"codex\" / \"claude\". It IS the thread's phase in Worktrail, and it is not carried over: a new next declaration without it is refused. A next owned by the user is written TO the engineer ('Decide X'), never about them ('The user decides X'). Declare open and next before pausing, switching, or when the engineer's direction changes; resume shows them as the starting point. FIRST LINE = TITLE: at most 120 columns (CJK counts as 2), no ' — ' joiner, and no record numbers (#517, D15635, 확장 122호, turn 80, commit hashes) — a person reads that line on the screen; put the rest, numbers included, after a blank line as the body (the server refuses otherwise).")
    def declare(case_id: int, kind: str, statement: str, ctx: Context, authority: str = "agent", owner: str | None = None) -> str:
        return _guard(T(ctx).declare)(case_id, kind, statement, authority, owner)

    @server.tool(name="assign_topic", description="Put threads under a topic for the engineer's Review (retrospective) screen — a topic is a line of work spanning threads and repositories (e.g. 'MCP for code agents', 'incident lab'). name finds or creates the topic; case_ids are added (idempotent). repos = repository identities whose pre-thread git commits belong to this topic. conclusion = one sentence to pin on the topic when the engineer wraps it up (a topic's phase is derived; only the conclusion is written by hand). Use it only when the engineer names the topic or a new thread clearly continues one. FIRST LINE = TITLE: at most 120 columns (CJK counts as 2), no ' — ' joiner, and no record numbers (#517, D15635, 확장 122호, turn 80, commit hashes) — a person reads that line on the screen; put the rest, numbers included, after a blank line as the body (the server refuses otherwise).")
    def assign_topic(name: str, case_ids: list[int], ctx: Context, summary: str = "", repos: list[str] | None = None, conclusion: str = "") -> str:
        return _guard(T(ctx).assign_topic)(name, case_ids, summary, repos, conclusion)

    @server.tool(name="list_topics", description="List the engineer's topics — id, name, summary, the threads under each, and the conclusion if pinned. Read-only. Use it to see what overlaps before the engineer decides what to merge, or to pick an existing topic name.")
    def list_topics(ctx: Context) -> list:
        return _guard(T(ctx).list_topics)()

    @server.tool(name="merge_topic", description="Merge one topic into another, FOR THE ENGINEER: every thread under `topic` moves to `into` (its assignment time kept), repositories are united, `into` keeps its own summary and conclusion, and `topic` is deleted. Thread records are untouched. Both are existing topic names, exactly. Requires engineer_asked=true, which you may pass only when the engineer asked to merge or tidy topics — never on your own judgment; say which goes into which before calling.")
    def merge_topic(topic: str, into: str, ctx: Context, engineer_asked: bool = False) -> str:
        return _guard(T(ctx).merge_topic)(topic, into, engineer_asked)

    @server.tool(name="rate_handoff", description="Record the ENGINEER's judgment of one handoff (a fresh session that picked up a thread) — never your own score. Ask them, then pass what they said: constraints_retained / decisions_retained (did the new session honour them without being told again), duplicate_investigation (re-did finished work), wrong_next_step, re_explained (how many times they had to re-explain past context), retrieval_used / retrieval_changed_action (was a past thread searched, and did it change what happened). Leave unknown fields out.")
    def rate_handoff(ctx: Context, case_id: int | None = None, constraints_retained: bool | None = None, decisions_retained: bool | None = None, duplicate_investigation: bool | None = None, wrong_next_step: bool | None = None, re_explained: int | None = None, retrieval_used: bool | None = None, retrieval_changed_action: bool | None = None, note: str = "") -> str:
        return _guard(T(ctx).rate_handoff)(case_id, constraints_retained, decisions_retained, duplicate_investigation, wrong_next_step, re_explained, retrieval_used, retrieval_changed_action, note)

    @server.tool(name="list_cases", description="List the engineer's recent cases (id, title, status, current focus) — to find the case to continue.")
    def list_cases(ctx: Context, limit: int = 20) -> list:
        return T(ctx).list_cases(limit)

    @server.tool(name="add_evidence", description="Store raw material VERBATIM as evidence: logs, error output, command results, config. Do not paraphrase or trim. Returns the evidence number.")
    def add_evidence(case_id: int, text: str, ctx: Context, client: str | None = None) -> str:
        with _request_context(request_id_from(ctx)):
            return _guard(T(ctx).add_evidence)(case_id, text, client or client_from_ctx(ctx))

    @server.tool(name="add_evidence_file", description="Store a file's contents as evidence, read directly by the server (no need to repeat the text). Use for log files, command output saved to a file, configs. Absolute or ~ path. Not available over the remote door (chat clients) — use add_evidence.")
    def add_evidence_file(case_id: int, path: str, ctx: Context) -> str:
        return _guard(T(ctx).add_evidence_file)(case_id, path)

    @server.tool(name="note_turn", description="Record one diagnostic step: `observed` = what was seen (verbatim where possible), `conclusion` = what you concluded, ruled out, or propose next. kind (required) = change(바꿈: code/screen/config changed) · verified(확인: test/prod/repro confirmed) · finding(발견: measured or found out, a fact) · thought(생각: proposal/hypothesis/judgment — can be overturned); if two apply, split into two notes. `next` (required) = after this turn, what happens next, and `owner` (required) = whose turn it is: \"user\" (the engineer must act), \"watch\" (nothing to do, only watching), or an agent name such as \"codex\" / \"claude\". Say next EVERY turn, even when nothing changed — if it is the same line as before, nothing new is written, so this costs you a sentence and keeps the thread honest. A stale next is the failure this product exists to prevent: the next session reads it and goes off to do work that is already done. Updates the case brief. Call at the end of each step, not every message. Refused once 15 turns have piled up since the thread's focus was last declared: re-declare the focus if it is still the same work, or open_thread for the new subject. `evidence_ids` (optional) = the evidence numbers this conclusion rests on — cite them here, not in the text; the screen renders them as links. A next owned by the user is written TO the engineer ('Decide X'), never about them ('The user decides X'). FIRST LINE = TITLE: at most 120 columns (CJK counts as 2), no ' — ' joiner, and no record numbers (#517, D15635, 확장 122호, turn 80, commit hashes) — a person reads that line on the screen; put the rest, numbers included, after a blank line as the body (the server refuses otherwise).")
    def note_turn(case_id: int, observed: str, conclusion: str, kind: str, next: str, owner: str,
                  ctx: Context, evidence_ids: list[int] | None = None, client: str | None = None) -> str:
        with _request_context(request_id_from(ctx)):
            return _guard(T(ctx).note_turn)(case_id, observed, conclusion, kind, next, owner,
                                            client or client_from_ctx(ctx), evidence_ids)

    @server.tool(name="search_evidence", description="Search this engineer's recorded evidence across all their cases — the raw material, never past conclusions. When there is room left, results also include material from BEFORE casebook was installed (kind=\"pre_install\"): the engineer's own words imported from old session logs, with the source session. Treat those as raw material only — they are not decisions or constraints, and they may describe approaches that were later abandoned. A hit is retrieval, not a conclusion.")
    def search_evidence(query: str, ctx: Context, limit: int = 10, include_archived: bool = False) -> list:
        return _guard(T(ctx).search_evidence)(query, limit, include_archived)

    @server.tool(name="inspect", description="Return one record in full by its number (ref), as find, trail and resume print it: D<id> decision · C<id> constraint · R<id> ruled-out · T<id> term · F/O/N<id> focus/open/next declaration (with what superseded it, if anything) · E<id> evidence verbatim with provenance · <case>:<turn> a note (the observed evidence verbatim plus the conclusion, marked interpretation). evidence_id=<n> still works for evidence. Not summarized.")
    def inspect(ctx: Context, ref: str | None = None, evidence_id: int | None = None) -> dict:
        return _guard(T(ctx).inspect)(evidence_id, ref)

    @server.tool(name="find", description="Pull past context across every thread when you need it: decisions, constraints, ruled-outs, focus/open/next declarations, notes and close results whose text contains every word of query — one line each with its number (ref) and thread, in-force before superseded, newest first; evidence hits fill the remaining room. Use it when the engineer refers to something from before ('the one we rejected', 'why did we…') or you need a record's number. Notes are marked interpretation, not evidence. Then trail(case_id) or inspect(ref).")
    def find(query: str, ctx: Context, limit: int = recall.FIND_LIMIT, include_closed: bool = True) -> dict:
        return _guard(T(ctx).find)(query, limit, include_closed)

    @server.tool(name="trail", description="Walk one thread back in time, one line per event: opened, focus/open/next declarations (with owner), decisions, constraints, ruled-outs, notes, evidence, commits, closed. The most recent `limit` lines; pass before=<the earliest line's at> for older ones. Use it when you need how a thread got where it is — resume gives only where it is now. Notes are marked interpretation, not evidence. inspect(ref) for any line in full.")
    def trail(case_id: int, ctx: Context, limit: int = recall.TRAIL_LIMIT, before: int | None = None) -> dict:
        return _guard(T(ctx).trail)(case_id, limit, before)

    @server.tool(name="decide", description="Record a decision the work will follow from now on — a choice, not a truth (e.g. 'use a separate auth_credentials table'). authority='user' if the engineer stated it, 'agent' if you made it while implementing. To change it later, decide again with supersedes=<decision id>. scope='thread' (default: archived with this thread) or 'repo' (holds for every thread of this repository — promotion is by declaring the scope, never automatic). Not for hypotheses about causes — those stay in note_turn. FIRST LINE = TITLE: at most 120 columns (CJK counts as 2), no ' — ' joiner, and no record numbers (#517, D15635, 확장 122호, turn 80, commit hashes) — a person reads that line on the screen; put the rest, numbers included, after a blank line as the body (the server refuses otherwise). reason follows the same first-line rule — its first line is what the thread card shows as the reason.")
    def decide(case_id: int, statement: str, ctx: Context, reason: str = "", evidence_ids: list[int] | None = None, authority: str = "agent", supersedes: int | None = None, scope: str = "thread", client: str | None = None) -> str:
        return _guard(T(ctx).decide)(case_id, statement, reason, evidence_ids, authority, supersedes, scope, client or client_from_ctx(ctx))

    @server.tool(name="define", description="Give a coined name its meaning — a term the work uses that a reader who was not there would not know (e.g. '1층 조리법', '3회차', 'blinding'). term = the name as written (≤40 chars); meaning = one line (≤120 columns), details after a blank line. scope 'thread' (default) or 'repo' (every thread of the repository). Not enforced: use it when the engineer asks what a name meant or asks you to tidy up, or when you coin a name inside a record. Superseded (supersedes=<term id>), never edited. resume and the screens show current terms; the Find screen shows first use.")
    def define(case_id: int, term: str, meaning: str, ctx: Context, authority: str = "agent", scope: str = "thread", supersedes: int | None = None) -> str:
        return _guard(T(ctx).define)(case_id, term, meaning, authority, supersedes, scope)

    @server.tool(name="constrain", description="Record a constraint the work must respect (e.g. 'existing API contract must not break'). authority='user' or 'agent'. scope='thread' (default) or 'repo' (holds for every thread of this repository). Change later with supersedes=<constraint id>. FIRST LINE = TITLE: at most 120 columns (CJK counts as 2), no ' — ' joiner, and no record numbers (#517, D15635, 확장 122호, turn 80, commit hashes) — a person reads that line on the screen; put the rest, numbers included, after a blank line as the body (the server refuses otherwise). reason follows the same first-line rule.")
    def constrain(case_id: int, statement: str, ctx: Context, reason: str = "", authority: str = "agent", supersedes: int | None = None, scope: str = "thread") -> str:
        return _guard(T(ctx).constrain)(case_id, statement, reason, authority, supersedes, scope)

    @server.tool(name="rule_out", description="Record that a hypothesis was ruled out — ONLY with the evidence that killed it and the scope where that holds (repro path, commit, environment). Outside the scope it is a hypothesis again. Ruling-outs stop future investigation, so the bar is higher than for a finding.")
    def rule_out(case_id: int, hypothesis: str, scope: str, evidence_ids: list[int], ctx: Context) -> str:
        return _guard(T(ctx).rule_out)(case_id, hypothesis, scope, evidence_ids)

    @server.tool(name="resume", description="Pick a thread up in a new session. Durable state as it stands now, seven fields: focus · anchor (live git of this worktree: branch, HEAD, dirty) · constraints · decisions (with reason and authority) · open (unresolved) · next (actions) · evidence_refs — plus repo_state (repo-scope constraints/decisions shared by every thread of the repository, titles and numbers only — inspect(ref) for the full text; if one conflicts with the thread's own, both are shown, nothing is resolved for you). mode='fresh' omits decisions and ruled-outs. Never includes how it got here: pull that with trail(case_id) or find(query) when you need it.")
    def resume(case_id: int, ctx: Context, mode: str = "continuity", worktree: str | None = None, facts: dict | None = None) -> dict:
        return _guard(T(ctx).resume)(case_id, mode, worktree, facts)

    @server.tool(name="hook", description="For the Claude Code hook script only (thin client) — not for agents. event = session-start | post-compact | post-commit | checkpoint; facts = git facts of the worktree read on the client (threads.local_facts). Returns the hook's JSON output.")
    def hook(event: str, facts: dict, ctx: Context, why: str = "", reason: str = "") -> dict:
        return _guard(T(ctx).hook)(event, facts, why, reason)

    @server.tool(name="import_prior", description="For the casebook backfill script only (thin client) — not for agents. Imports pre-install session logs as repository history: verbatim engineer instructions only, no threads, topics, decisions or constraints are created from them.")
    def import_prior(facts: dict, sessions: list, ctx: Context, reset: bool = False) -> dict:
        return _guard(T(ctx).import_prior)(facts, sessions, reset)

    @server.tool(name="search_prior", description="Search what the engineer actually typed in sessions from BEFORE casebook was installed (repository history, imported by backfill). Returns their words verbatim with the source session — never a conclusion, and never anything an agent said. Nothing here is a decision or a constraint: it is raw material, and it may describe approaches that were later abandoned. Use it when the engineer asks what they were doing back then, or when you need the original wording of an old request; use search_evidence for recorded work.")
    def search_prior(query: str, ctx: Context, limit: int = 10) -> list:
        return _guard(T(ctx).search_prior)(query, limit)

    @server.tool(name="handoff", description="Read the declared state, decisions, constraints and evidence references as a handoff, FOR THE ENGINEER. Requires engineer_asked=true, which you may pass only when they actually asked for a summary, a handover, or where the case stands. Never call it to refresh your own memory — use resume for that.")
    def handoff(case_id: int, ctx: Context, engineer_asked: bool = False) -> str:
        return _guard(T(ctx).handoff)(case_id, engineer_asked)

    # 확장 134호 — 인자 검증은 SDK 가 도구 함수보다 먼저 하므로 입구(call_tool)를 감싼다. 프록시는 인자를 그대로
    # 넘기므로 여기 한 곳이 로컬·원격 문을 모두 덮는다.
    from mcp.server.mcpserver.exceptions import ToolError
    from pydantic import ValidationError

    sdk_call_tool = server.call_tool
    params_of: dict[str, set[str]] = {}

    async def call_tool(name: str, arguments: dict[str, Any], context: Any = None):
        if not params_of:
            params_of.update({t.name: set((t.input_schema or {}).get("properties", {})) for t in await server.list_tools()})
        bad = boundary_leak(name, arguments or {}, params_of.get(name, set()))
        if bad:
            raise ToolError(bad)
        try:
            return await sdk_call_tool(name, arguments, context)
        except ToolError as exc:
            if isinstance(exc.__cause__, ValidationError):
                raise ToolError(argument_error(name, exc.__cause__)) from exc.__cause__
            raise

    server.call_tool = call_tool
    return server

# ── 원격 문 (확장 27호) ─────────────────────────────────────────────────────────
MCP_PATH = "/mcp"

# RFC 9728 은 자원이 /mcp 면 메타데이터를 /.well-known/oauth-protected-resource/mcp 에 두라고 한다.
# 접미사 없는 자리도 함께 낸다 — 클라이언트마다 둘 중 하나만 보는 것이 있다. 첫 번째가 401 머리에
# 실리는 정본이다.
RESOURCE_METADATA_PATHS = ("/.well-known/oauth-protected-resource/mcp",
                           "/.well-known/oauth-protected-resource")

# 확장 98호 — 얇은 클라이언트가 받아 가는 것들. 경로 토큰이 있을 때는 /mcp/<토큰>/<이름> 이지만,
# 로그인해서 머리로 붙으면 경로에 토큰이 없어 /mcp/<이름> 이 된다. 토큰은 늘 점이 든 긴 문자열이라
# 이 셋과 헷갈리지 않는다.
DIST_NAMES = ("install.sh", "ui", "client.tar.gz", "windows.md")

# 확장 100호 (D15414) — 신청제의 공개 길 둘. 이용 허용 검사 **앞**에 선다: 신청한 사람은
# 정의상 아직 allowed 가 아니므로, 검사 뒤에 두면 자기 상태를 물어볼 수도 없다.
# 대신 OAuth 로 신원은 반드시 확인한다 — 아무나 남의 이메일로 줄을 설 수 없다.
JOIN_REQUEST = "/join/request"
JOIN_STATUS = "/join/status"
JOIN_EXCHANGE = "/join/exchange"     # 확장 111호 — 브라우저 대신 서버가 인가 코드를 바꾼다
JOIN_CONSENT = "/join/consent"       # 확장 115호 — 안내문에 동의한 것을 본인이 남긴다
JOIN_PATHS = (JOIN_REQUEST, JOIN_STATUS, JOIN_EXCHANGE, JOIN_CONSENT)
# 확장 101호 — 신청 페이지 자체. 인증 없이 누구나 본다: 여기서 나가는 것은 HTML 한 장이고
# 그 안에 비밀이 없다(client_id 는 공개 앱의 것, 나머지는 메타데이터에서 읽는다).
# /join/callback 은 인가 서버가 ?code= 를 달고 돌려보내는 자리라 같은 페이지를 낸다.
JOIN_PAGE_PATHS = ("/join", "/join/", "/join/callback")

# 확장 99호 — 구형 경로를 닫아도 이 셋은 경로 토큰으로 계속 받을 수 있다.
# 닭과 달걀이기 때문이다: 로그인하려면 casebook-login 이 있어야 하고, 그것은 install.sh 로 깔린다.
# 받은 설치 한 줄이 곧 초대장이고, 그 초대장을 닫으면 새 테스터가 들어올 길이 없어지며
# 이미 깐 사람도 갱신을 못 받는다(실측 2026-09-14: 닫자 install.sh 가 401 이 되어 재설치가
# 조용히 실패했다). 닫는 것은 **기록에 닿는 길**이지 클라이언트를 나눠 주는 길이 아니다.
# 여기서 나가는 것은 누구의 기록도 아니다 — 설치 스크립트·화면 파일·소스 묶음뿐이다.


def _public_base(request) -> str:
    """리버스 프록시 뒤에서도 밖에서 보이는 주소. request.url 은 Caddy 뒤에서 http:// 로 온다 —
    전달 헤더가 있으면 그것이 진짜다(2026-09-07 실측, _client_dist 와 같은 이유)."""
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.headers.get("host", "")
    return f"{proto}://{host}" if host else str(request.url).split("/mcp", 1)[0]

def mint_token(casebook: Worktrail, email: str, ttl: int = REMOTE_TOKEN_TTL) -> str:
    """커넥터에 넣을 토큰. 웹 로그인 토큰과 같은 서명·같은 비밀, 만료만 길다. 사용자가 없으면 만든다(stdio 문과 같은 규칙)."""
    email = email.strip().lower()
    user = casebook.db.get_by("user", "email", email)
    if user is None:
        user = casebook.db.add("user", {"name": email.split("@")[0], "email": email, "password": None})
    return auth.create_token(casebook._secret(), user["id"], ttl)

def user_from_request(casebook: Worktrail, request) -> int | None:
    """(옛 이름) 누구인지만 돌려준다. 어느 길로 들어왔는지가 필요하면 who_from_request 를 쓴다."""
    uid, _kind = who_from_request(casebook, request)
    return uid


AUTH_OAUTH = "oauth"        # 인가 서버가 내준 JWT
AUTH_LEGACY = "legacy"      # 주소에 실린 토큰, 또는 그 토큰을 Bearer 로 보낸 것


def who_from_request(casebook: Worktrail, request) -> tuple[int | None, str]:
    """경로 /mcp/<token> 또는 Authorization: Bearer <token> → user.id. 틀리면 None.

    확장 87호: Bearer 가 JWT 꼴이면 인가 서버가 내준 것으로 보고 oauth 로 검증한다. 둘을 섞어
    시도하지 않는다 — 꼴로 먼저 가르고 그 길에서만 판정한다. 구형 HMAC 토큰을 JWT 검증에 넣거나
    그 반대로 하면 실패 이유가 뒤섞여, 나중에 어느 쪽이 막힌 것인지 알 수 없게 된다.
    oauth 가 꺼져 있으면(기본) 이 갈래는 없는 것과 같다."""
    token = None
    path = request.url.path if hasattr(request, "url") else request.get("path", "")
    if path.startswith(MCP_PATH + "/"):
        head = path[len(MCP_PATH) + 1:].split("/", 1)[0]
        # 확장 98호 — /mcp/ui 의 "ui" 는 토큰이 아니라 받아 갈 것의 이름이다. 이것을 토큰으로
        # 집으면 뒤의 Authorization 을 보지 않아, 머리로 붙은 클라이언트가 401 을 받는다.
        token = None if head in DIST_NAMES else head
    if not token:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
    if not token:
        return None, AUTH_LEGACY
    if oauth.enabled() and oauth.looks_like_jwt(token):
        try:
            return oauth.user_of(casebook.db, oauth.verify(token)), AUTH_OAUTH
        except Exception:  # noqa: BLE001 — 서명이 틀렸든 계정이 없든 401 이다
            return None, AUTH_OAUTH
    try:
        return casebook.verify(token), AUTH_LEGACY
    except Exception:  # noqa: BLE001 — UnauthorizedError 든 뭐든 401 로 낸다
        return None, AUTH_LEGACY

def _join_page():
    """신청 페이지 한 장. 저장소의 web/join/index.html 을 그대로 낸다 — 서버가 진실이다."""
    from starlette.responses import PlainTextResponse, Response
    root = pathlib.Path(os.environ.get("CASEBOOK_CLIENT_SRC") or
                        pathlib.Path(__file__).resolve().parents[2])
    page = root / "web" / "join" / "index.html"
    if not page.is_file():
        return PlainTextResponse("join page is not installed on this server", status_code=404)
    return Response(page.read_bytes(), media_type="text/html; charset=utf-8",
                    headers={"Cache-Control": "no-store"})


async def _join(casebook: Worktrail, request, path: str):
    """신청제의 공개 길 (확장 100호, D15414).

        POST /join/request   로그인한 사람이 줄을 선다
        GET  /join/status    지금 내 상태와, 승인됐으면 설치 한 줄

    신원은 OAuth 로 확인하고 이용 허용은 보지 않는다 — 신청한 사람은 아직 allowed 가 아니다.
    설치 한 줄은 allowed 일 때만 준다: 그것이 곧 이 서버에 기록을 쌓는 열쇠이기 때문이다."""
    from starlette.responses import JSONResponse

    # 확장 111호 — 토큰 교환은 서버가 대신한다. 인가 서버(AuthKit)의 토큰 문이
    # Access-Control-Allow-Origin 을 주지 않아 브라우저가 직접 부르면 CORS 로 막힌다
    # (실측 2026-09-15: /join/callback 에서 "Failed to fetch"). 이 길은 아직 토큰이 없는
    # 사람이 부르므로 Authorization 검사 앞에 선다 — 신원은 받아 온 토큰으로 그 뒤에 본다.
    if path == JOIN_EXCHANGE:
        if request.method != "POST":
            return JSONResponse({"error": "POST only"}, status_code=405)
        try:
            form = await request.json()
        except Exception:  # noqa: BLE001
            form = {}
        code = (form.get("code") or "").strip()
        verifier = (form.get("code_verifier") or "").strip()
        if not code or not verifier:
            return JSONResponse({"error": "code and code_verifier are required"}, status_code=400)
        try:
            # redirect_uri 는 서버가 짓는다 — 브라우저가 대는 값을 그대로 인가 서버에 넘기지 않는다.
            got = oauth.exchange_code(code, verifier, _public_base(request) + "/join/callback")
        except Exception as exc:  # noqa: BLE001 — 교환 실패는 사람이 읽을 말로 낸다
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"access_token": got})

    auth = (request.headers.get("authorization") or "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token or not oauth.looks_like_jwt(token):
        return JSONResponse({"error": "sign in first"}, status_code=401)
    try:
        claims = oauth.verify(token)
        email = oauth.email_of(claims)
    except Exception as exc:  # noqa: BLE001 — 서명이든 이메일이든 401 이다
        return JSONResponse({"error": str(exc)}, status_code=401)

    name = (claims.get("name") or claims.get("given_name") or "").strip()
    if path == JOIN_REQUEST and request.method == "POST":
        try:
            out = access.request_access(casebook.db, email, name)
        except access.QueueFullError as exc:
            return JSONResponse({"error": exc.message}, status_code=429)
        return JSONResponse({"email": email, "state": out["state"], "new": out["new"]})

    user = casebook.db.get_by("user", "email", email)

    # 확장 115호 — 동의는 본인만 남길 수 있다. 신청 페이지가 안내문을 보인 뒤 이 길로 부른다.
    # 승인과는 무관하다: 줄을 선 사람도 미리 남겨 둘 수 있고, 그래야 승인되는 순간 바로 쓸 수 있다.
    # 판은 서버가 정한다 — 브라우저가 대는 값을 그대로 받으면 안 읽고도 아무 판이나 박을 수 있다.
    if path == JOIN_CONSENT:
        if request.method != "POST":
            return JSONResponse({"error": "POST only"}, status_code=405)
        if user is None:
            return JSONResponse({"error": "request access first"}, status_code=404)
        # 확장 126호 — 어느 안내문에 동의했는지 갈라 적는다. 로컬은 서버 보관·열람이 해당되지 않는다.
        try:
            form = await request.json()
        except Exception:  # noqa: BLE001
            form = {}
        access.record_consent(casebook.db, user["id"],
                              access.notice_version((form or {}).get("mode") or ""))
        st = access.state(casebook.db, user["id"])
        return JSONResponse({"email": email, "state": st["state"] if st["known"] else "none",
                             "consent": st["consent_version"], "notice": access.NOTICE_VERSION})

    if user is None:
        return JSONResponse({"email": email, "state": "none", "notice": access.NOTICE_VERSION})
    st = access.state(casebook.db, user["id"])
    body = {"email": email, "state": st["state"] if st["known"] else "none",
            "consent": st["consent_version"], "notice": access.NOTICE_VERSION,
            # 확장 129호 — 동의한 사실(consent)과 그것이 지금 판인가(consent_current)는 다른 질문이다.
            # 옛 판은 지우지 않는다. 언제 무엇에 동의했는지가 기록이고, 화면은 둘째 칸만 본다.
            "consent_current": access.notice_current(st["consent_version"]),
            "seats": access.seats(casebook.db)}      # 확장 119호 — 화면이 "몇 자리 남았나" 를 말한다
    # 설치 한 줄은 동의를 남긴 뒤에만 준다(확장 115호). 그 줄이 곧 기록에 닿는 열쇠인데 동의가
    # 없으면 그 열쇠로 첫 기록에서 403 을 받는다 — 쓸 수 없는 줄을 쥐여 주면 사람은 설치가
    # 고장 난 줄 알고, 거절문이 시키는 대로 다시 설치해도 풀리지 않는다.
    if body["state"] == access.STATE_ALLOWED and body["consent_current"]:
        u = f"{_public_base(request)}{MCP_PATH}/{mint_token(casebook, email)}"
        # 확장 126호 — 모드는 신청 화면에서 이미 골랐다. 설치기에게 그대로 넘겨 다시 묻지 않는다.
        # 윈도우는 sh 가 없어 지시문을 준다(확장 122호) — 없는 줄을 쥐여 주지 않는다.
        body["install"] = {"server": f"curl -fsSL {u}/install.sh | sh -s -- --server",
                           "local": f"curl -fsSL {u}/install.sh | sh -s -- --local",
                           "windows": f"{u}/windows.md"}
    return JSONResponse(body)


def _client_dist(casebook: Worktrail, request, what: str):
    """/mcp/<token>/install.sh · /mcp/<token>/client.tar.gz — 확장 34호. 토큰은 이미 확인된 뒤에 온다."""
    from starlette.responses import JSONResponse, PlainTextResponse, Response

    from casebook.adapters import client_dist

    # 리버스 프록시(Caddy) 뒤에서는 request.url 이 http:// 로 온다 — 전달 헤더가 있으면 그것이 진짜 주소다.
    # 안 그러면 install.sh 가 http URL 을 remote-url 에 적어 클라이언트가 평문으로 붙는다(2026-09-07 실측).
    proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip() or request.url.scheme
    host = request.headers.get("x-forwarded-host", "").split(",")[0].strip() or request.headers.get("host", "")
    path = request.url.path
    base = f"{proto}://{host}{path}" if host else str(request.url).split("?", 1)[0]
    base = base[: base.rfind("/")]                      # .../mcp/<token>
    try:
        if what == "install.sh":
            return PlainTextResponse(client_dist.install_script(base), media_type="text/x-shellscript")
        if what == "ui":
            return Response(client_dist.tracker_html(), media_type="text/html; charset=utf-8")
        if what == "windows.md":
            # 확장 122호 — 윈도우에는 sh 가 없다. 설치기 대신 에이전트에게 붙여넣을 지시문을 낸다.
            return PlainTextResponse(client_dist.windows_prompt(base), media_type="text/markdown; charset=utf-8")
        if what == "client.tar.gz":
            mode = "local" if "mode=local" in (request.url.query or "") else "thin"
            return Response(client_dist.build_tarball(mode=mode), media_type="application/gzip",
                            headers={"content-disposition": "attachment; filename=casebook-client.tar.gz"})
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"error": f"not found: {what}"}, status_code=404)

# ── 거절된 요청은 접어서 남긴다 ──────────────────────────────────────────────
# 전에는 토큰 없는 요청마다 read_log 에 한 줄을 썼다. 그 길은 인증보다 먼저 돌므로 아무나
# 공개 /mcp 로 DB 를 무제한 불릴 수 있었다(실측 5,411 req/s · 400건에 WAL +4.1MB).
# 신호는 남기되 쓰기는 묶는다: 출처마다 창(60초) 하나에 한 줄, 그 사이 건수는 hits 에 접는다.
# 상태는 앱 인스턴스마다 따로 둔다 — 전역으로 두면 한 프로세스의 두 문이 서로의 창을 먹는다.
_REFUSE_WINDOW_MS = 60_000
_REFUSE_MAX_KEYS = 1024


def _refusal_logger(casebook: Worktrail):
    """(kind, user_id, source) 마다 창 하나에 한 줄만 쓰는 기록기를 만든다."""
    import threading
    lock = threading.Lock()
    seen: dict[str, list[int]] = {}        # 열쇠 → [창 시작 ms, 접어 둔 건수]

    def log(kind: str, user_id: int, source: str) -> None:
        key = f"{kind}:{user_id}:{source}"
        now = casebook.db.now_ms()
        with lock:
            if len(seen) >= _REFUSE_MAX_KEYS and key not in seen:
                seen.pop(min(seen, key=lambda k: seen[k][0]), None)     # 표가 무한히 자라지 않게
            slot = seen.get(key)
            if slot is None or now - slot[0] >= _REFUSE_WINDOW_MS:
                folded = slot[1] if slot else 0
                seen[key] = [now, 0]
                write = True
            else:
                slot[1] += 1
                write, folded = False, 0
        if write:
            reads.log(casebook.db, user_id, kind, None, source, folded or None)

    return log


def build_http_app(casebook: Worktrail):
    """streamable-http ASGI 앱. 바깥: 토큰 → user.id 를 요청 state 에 놓고 경로를 /mcp 로 되돌리는 미들웨어.
    안쪽: build_server 의 MCPServer. json_response — 터널 뒤에서 오래 여는 SSE 를 피한다.
    DNS rebinding 보호는 끈다 — Host 는 터널의 공개 호스트명이고, 문지기는 토큰이다."""
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    # 이 문은 언제나 서버다(로컬은 stdio 문을 쓴다). 표가 처음 생기는 순간 그때 있던 사용자를
    # 허용으로 적어 둔다(D15081) — 그 뒤에 mint 되는 계정은 기본 차단이다.
    access.grandfather(casebook.db)
    # 확장 87호 — 켜져 있으면 지금 서 보고, 못 서면 여기서 죽는다(조용한 401 로 흩어지지 않게).
    if oauth.enabled():
        print(f"oauth: {oauth.issuer()} · aud {oauth.audience()} · jwks {oauth.preflight()}")
    log_refused = _refusal_logger(casebook)

    def resolve_user(ctx) -> int:
        req = ctx.request_context.request
        uid = getattr(getattr(req, "state", None), "casebook_user_id", None)
        if uid is None:
            raise ApiError("unauthenticated request reached a tool")   # 미들웨어가 막았어야 한다
        return uid

    server = build_server(casebook, resolve_user=resolve_user)
    inner = server.streamable_http_app(
        streamable_http_path=MCP_PATH, json_response=True, stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return await inner(scope, receive, send)
        path = scope.get("path", "")
        if path == "/health":
            return await JSONResponse({"ok": True, "door": "mcp"})(scope, receive, send)
        # RFC 9728 — 자원 서버 메타데이터. 인증 앞에 선다: 401 을 받은 클라이언트가 "어디로
        # 로그인하러 가야 하나" 를 여기서 읽는다. 비밀이 없는 공개 문서다.
        if oauth.enabled() and path in RESOURCE_METADATA_PATHS:
            return await JSONResponse(oauth.protected_resource_metadata())(scope, receive, send)
        if oauth.enabled() and path in JOIN_PAGE_PATHS:
            return await _join_page()(scope, receive, send)
        if oauth.enabled() and path in JOIN_PATHS:
            return await (await _join(casebook, Request(scope, receive), path))(scope, receive, send)
        if path != MCP_PATH and not path.startswith(MCP_PATH + "/"):
            return await JSONResponse({"error": "not found"}, status_code=404)(scope, receive, send)
        uid, how = who_from_request(casebook, Request(scope, receive))
        # 확장 34호 — 같은 토큰 뒤에서 얇은 클라이언트를 내준다(경로 꼴). 확장 99호: 닫아도 이것만은 지난다.
        rest = path[len(MCP_PATH) + 1:].split("/", 1) if path != MCP_PATH else [""]
        is_dist = (len(rest) == 2 and rest[1] in DIST_NAMES) or (len(rest) == 1 and rest[0] in DIST_NAMES)
        # 확장 92호 (D15352) — 구형 경로를 닫았으면 터미널 클라이언트는 로그인해야 한다.
        # 웹 커넥터는 기기 흐름을 못 쓰므로 그 계정만 운영자가 명시적으로 열어 둔다.
        if uid is not None and how == AUTH_LEGACY and not is_dist and not oauth.legacy_allowed(uid):
            log_refused("mcp_legacy_closed", uid, scope.get("client", ("?",))[0])
            return await JSONResponse(
                {"error": "the address-in-the-URL token is closed on this server. "
                          "Log in once from a terminal: casebook-login, then the client "
                          "sends the token in the Authorization header instead."},
                status_code=401)(scope, receive, send)
        if uid is None:
            log_refused("mcp_unauthorized", 0, scope.get("client", ("?",))[0])
            headers = {}
            if oauth.enabled():
                base = _public_base(Request(scope, receive))
                headers["WWW-Authenticate"] = oauth.www_authenticate(base + RESOURCE_METADATA_PATHS[0])
            return await JSONResponse({"error": "unauthorized — connector URL is https://<host>/mcp/<token>; "
                                                "mint one with: python -m casebook.adapters.mcp_server mint EMAIL"},
                                      status_code=401, headers=headers)(scope, receive, send)
        # 인계서 4절 — 토큰이 진짜인 것과 이 서버를 써도 되는 것은 별개다. 구형 토큰 경로에도 똑같이 건다.
        # need 를 주지 않는다 = worktrail 범위면 충분하다. 이 문에 얹힌 것은 Worktrail 도구뿐이고
        # tickets·모델 호출 도구는 없다. 운영자 것을 이 문에 붙이는 날에는 그 도구에서
        # access.check(..., need=access.SCOPE_OPERATOR) 를 따로 불러야 한다 — 여기 한 줄로는 안 걸린다.
        try:
            access.check(casebook.db, uid)
        except AccessDeniedError as exc:
            log_refused("mcp_forbidden", uid, scope.get("client", ("?",))[0])
            return await JSONResponse({"error": exc.message}, status_code=403)(scope, receive, send)
        # 설치는 curl 한 줄이 된다.
        if len(rest) == 2 and rest[1]:
            return await _client_dist(casebook, Request(scope, receive), rest[1])(scope, receive, send)
        # 확장 98호 — 머리로 붙은 클라이언트에는 경로 토큰이 없다. 창이 화면을 받아 가는 길이 여기다.
        if len(rest) == 1 and rest[0] in DIST_NAMES:
            return await _client_dist(casebook, Request(scope, receive), rest[0])(scope, receive, send)
        scope = dict(scope)
        scope["path"] = MCP_PATH                       # 토큰 조각을 떼고 SDK 가 아는 경로로
        scope["raw_path"] = MCP_PATH.encode()
        scope.setdefault("state", {})
        scope["state"]["casebook_user_id"] = uid
        return await inner(scope, receive, send)

    app.lifespan = inner   # uvicorn 은 scope["type"]=="lifespan" 도 app 으로 보낸다 — inner 로 그대로 흘러간다
    from casebook.adapters.request_limits import RequestSizeLimit
    return RequestSizeLimit(app)

def build_from_env() -> tuple[Worktrail, int]:
    from casebook.core import clientmode                  # 훅과 같은 기본값을 쓴다
    db_path = pathlib.Path(clientmode.local_db())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = SqliteDB(str(db_path))
    cb = Worktrail(db=db)
    email = clientmode.local_email()
    user = db.get_by("user", "email", email)
    if user is None:
        user = db.add("user", {"name": email.split("@")[0], "email": email, "password": None})
    return cb, user["id"]

def main(argv: list[str] | None = None) -> None:
    import sys
    args = list(sys.argv[1:] if argv is None else argv)
    # --db 를 받는다. 전에는 몰라서 조용히 무시했고, 그래서 임시 DB 를 만들려던 명령이
    # 실제 ~/.casebook/casebook.db 에 계정을 만들었다(2026-09-13 실사고). 모르는 -- 인자도 거절한다.
    if "--db" in args:
        i = args.index("--db")
        if i + 1 >= len(args):
            raise SystemExit("--db 뒤에 경로가 없다")
        os.environ["CASEBOOK_DB"] = args[i + 1]
        del args[i:i + 2]
    # 확장 116호 — forget 이 제 깃발을 들고 온다. 여기서 통째로 거절하면 그 깃발이 하위 명령까지
    # 닿지 못한다. 아는 것만 통과시키고 나머지는 그대로 막는다.
    SUBCOMMAND_FLAGS = ("--yes", "--records")
    unknown = [a for a in args if a.startswith("--") and a not in SUBCOMMAND_FLAGS]
    if unknown:
        raise SystemExit(f"모르는 인자다: {' '.join(unknown)} — 받는 것은 --db PATH 와 "
                         f"{' · '.join(SUBCOMMAND_FLAGS)} 뿐이다")
    cmd = args[0] if args else "stdio"
    cb, uid = build_from_env()
    if cmd == "stdio":
        build_server(cb, uid).run("stdio")
    elif cmd == "http":
        import uvicorn
        host = os.environ.get("CASEBOOK_MCP_HOST", "127.0.0.1")
        port = int(os.environ.get("CASEBOOK_MCP_PORT", "8790"))
        # access_log 끔 — 토큰이 경로에 있다. 인증 실패는 read_log(mcp_unauthorized)에 남는다.
        uvicorn.run(build_http_app(cb), host=host, port=port, access_log=False)
    elif cmd == "mint":
        if len(args) < 2:
            raise SystemExit("usage: python -m casebook.adapters.mcp_server [--db PATH] mint EMAIL")
        import sys as _sys
        print(f"DB: {os.environ.get('CASEBOOK_DB', '~/.casebook/casebook.db')}", file=_sys.stderr)
        print(mint_token(cb, args[1]))          # 토큰만 stdout — 파이프로 받는 쪽이 있다
    elif cmd in ("allow", "block", "access", "consent", "forget"):
        _access_cli(cb, cmd, args[1:])
    else:
        raise SystemExit(f"unknown command {cmd!r} — stdio | http | mint EMAIL | "
                         "access | allow EMAIL [scope] | block EMAIL | consent EMAIL VERSION | "
                         "forget EMAIL [--yes]")


def _access_cli(cb: Worktrail, cmd: str, rest: list[str]) -> None:
    """운영자가 손으로 켜고 끄는 자리. 개별 회수가 여기서 된다 — auth_secret 을 건드리지 않는다.

    access                     지금 상태 전부
    allow EMAIL [scope]        허용 (scope: worktrail 기본 · operator 는 조사·tickets 까지)
    block EMAIL                차단 — 그 사람의 기존 토큰도 그대로 막힌다
    consent EMAIL VERSION      동의한 안내문 판을 계정에 붙인다
    forget EMAIL [--yes]       그 계정의 것을 전부 지운다 (--yes 없으면 세어서 보여만 준다)
    """
    access.grandfather(cb.db)
    # 어느 DB 를 건드리는지 늘 말한다 — 이것이 없어서 위 사고를 아무도 알아채지 못했다.
    print(f"DB: {os.environ.get('CASEBOOK_DB', '~/.casebook/casebook.db')}")
    if cmd == "access":
        rows = cb.db.query("user", where={})
        print(f"{'id':>4}  {'email':<34} {'state':<8} {'scope':<10} consent")
        for u in rows:
            s = access.state(cb.db, u["id"])
            print(f"{u['id']:>4}  {(u['email'] or '-'):<34} {s['state']:<8} {s['scope']:<10} "
                  f"{s['consent_version'] or '-'}")
        return
    if not rest:
        raise SystemExit(f"usage: python -m casebook.adapters.mcp_server {cmd} EMAIL"
                         + (" VERSION" if cmd == "consent" else ""))
    email = rest[0].strip().lower()
    user = cb.db.get_by("user", "email", email)
    if user is None:
        raise SystemExit(f"그런 계정이 없다: {email}")
    if cmd == "allow":
        # scope 를 안 주면 지금 범위를 그대로 둔다 — 전에는 worktrail 을 밀어 넣어서
        # block → allow 왕복만으로 운영자가 조용히 강등됐다.
        scope = rest[1] if len(rest) > 1 else None
        before = access.state(cb.db, user["id"])["scope"] if access.row(cb.db, user["id"]) else None
        access.set_state(cb.db, user["id"], access.STATE_ALLOWED, scope)
        now_scope = access.state(cb.db, user["id"])["scope"]
        note = f" (scope {before} → {now_scope})" if before and before != now_scope else f" (scope={now_scope})"
        print(f"허용: {email}{note}")
    elif cmd == "block":
        access.set_state(cb.db, user["id"], access.STATE_BLOCKED)
        print(f"차단: {email} — 이 계정의 기존 토큰도 이제 막힌다")
    elif cmd == "consent":
        if len(rest) < 2:
            raise SystemExit("usage: … consent EMAIL VERSION")
        access.record_consent(cb.db, user["id"], rest[1])
        print(f"동의 기록: {email} → {rest[1]}")
    else:  # forget — D15637 의 "삭제". 되돌릴 수 없으므로 세어서 보이는 것이 기본이다.
        plan = access.forget_plan(cb.db, user["id"])
        total = sum(plan.values())
        print(f"{email} (user {user['id']}) 에 달린 행 {total}:")
        for t, n in sorted(plan.items(), key=lambda kv: -kv[1]):
            print(f"  {t:22} {n:>7}")
        if not plan:
            print("  (없음)")
        if "--yes" not in rest:
            print("\n지우려면 같은 줄 끝에 --yes 를 붙인다. repo 는 지우지 않는다 — 남과 공유한다.")
            return
        done = access.forget(cb.db, user["id"])
        print(f"\n지웠다: {sum(done.values())} 행. 되돌릴 수 없다.")

if __name__ == "__main__":
    main()
