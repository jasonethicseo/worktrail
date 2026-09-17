"""스레드 층 — 확장 15호 (2026-09-04). "사건"에서 "저장소 위의 작업 상태 원장"으로.

실사용(case 442)이 드러낸 것: Claude Code 작업은 한 저장소 안에서 반나절짜리 주제가 하루에 여럿 지나간다.
"세션을 넘어 살아남을 질문 하나"라는 케이스 단위는 그 모양과 맞지 않아 케이스 하나가 만능 통이 됐다.
그래서 단위를 바꾼다 — 데이터는 그대로(케이스 = 스레드), 그릇만 바뀐다:

  repository  정규화한 remote(없으면 git common dir, 그것도 없으면 경로). **branch 는 identity 가 아니다** —
              branch 를 넣으면 브랜치를 바꾸는 순간 같은 프로젝트가 다른 프로젝트가 된다. branch/HEAD/dirty 는 Anchor.
  thread      case 한 건. lifecycle 은 case.status 로: open | resolved(=closed). 짧은 수정도 스레드다.
  worktree → current thread   "지금 무엇을 하는가"는 스레드의 속성이 아니라 **작업 공간의 바인딩**이다.
              Claude 와 Codex 가 같은 DB 를 쓰므로 전역 active 는 서로를 덮어쓴다. pause = 바인딩 해제,
              switch = 바인딩 변경, close = closed + 그 스레드의 바인딩 전부 해제.
  focus       스레드를 열 때 명시적으로 선언한다(브리프의 focus 는 모델 문장이라 durable 이 아니다).
              선언은 append-only 원장 이벤트(state.declare)이고 현재값은 projection 이다.

12테이블 밖 부속 테이블 셋(read_log 와 같은 방식) — 원장 시퀀스 오라클은 그대로다.
"""
from __future__ import annotations

import contextvars
import os
import re
import subprocess
from typing import Any

from . import phase, state
from .errors import InputError, NotFoundError

_DDL = """
CREATE TABLE IF NOT EXISTS repo (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  identity TEXT NOT NULL UNIQUE,
  hint TEXT
);
CREATE TABLE IF NOT EXISTS thread (
  case_id INTEGER PRIMARY KEY,
  repo_id INTEGER NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_thread_repo ON thread(repo_id);
CREATE TABLE IF NOT EXISTS worktree_binding (
  user_id INTEGER NOT NULL,
  worktree TEXT NOT NULL,
  repo_id INTEGER NOT NULL,
  case_id INTEGER NOT NULL,
  bound_at INTEGER NOT NULL,
  PRIMARY KEY (user_id, worktree)
);
"""

CLOSED = "resolved"   # thread "closed" 는 case.status 의 resolved 다 — archived 는 "delete me" 용으로 남긴다

# 확장 27호 (2026-09-06) — 가상 worktree. 원격 MCP 문으로 들어오는 채팅 클라이언트(Claude.ai·ChatGPT)에는 작업
# 디렉터리가 없다. "chat" 또는 "chat:<이름>" 은 git 을 묻지 않는 가상 작업 공간이다: 어느 저장소의 스레드에나
# 붙을 수 있고(switch 의 저장소 일치 검사 면제), 거기서 새로 연 스레드는 identity "chat" 저장소에 놓인다.
# anchor 는 live git 이 없으므로 과거 관찰(anchor_history)만 — Tracker 는 실제 worktree 바인딩을 우선한다.
VIRTUAL_IDENTITY = "chat"


def is_virtual(worktree: str | None) -> bool:
    return bool(worktree) and (worktree == VIRTUAL_IDENTITY or worktree.startswith(VIRTUAL_IDENTITY + ":"))


# 확장 118호 — 데스크톱 앱은 프로젝트를 안 열면 임시 작업공간을 만들고 그것을 cwd 로 프록시를 띄운다.
# 프록시는 사실대로 그 경로를 보내고, git 이 아니니 identity 가 path:<그 폴더> 가 된다. 폴더 이름에
# 날짜와 난수가 들어 있어 세션마다 다른 저장소가 서고, 거기 연 스레드는 다음 세션에서 사라진다 —
# 이어짐이 제품의 전부인데 그 자리에서 깨진다(#518 open #15252: "데스크톱 앱 사용자 누구에게나
# 일어난다"). 임시 작업공간은 프로젝트가 아니라 대화 맥락이므로 chat 으로 다룬다: 이름이 바뀌어도
# 같은 곳에 모이고, 유령 저장소가 목록에 쌓이지 않는다.
_SCRATCH_MARK = "/claude/scratch-workspaces/"


def is_scratch(worktree: str | None) -> bool:
    """데스크톱 앱의 임시 작업공간인가. 세션마다 새로 생기는 폴더라 저장소로 삼으면 안 된다."""
    return bool(worktree) and _SCRATCH_MARK in worktree.replace("\\", "/").lower()


# 확장 29호 (2026-09-06) — 얇은 클라이언트. 서버는 git 을 모른다: 맥의 프록시가 local_facts() 로 읽은 git 사실을
# 도구 호출에 실어 보내고, 서버는 요청 동안 provided() 로 그것을 걸어 둔다. identify / anchor / record_commit 은
# 걸린 사실이 그 worktree 의 것이면 git 을 부르지 않고 그것을 쓴다. 사실은 evidence 가 아니라 참조다(확장 17호와 같음).
_FACTS: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("casebook_git_facts", default=None)


class provided:
    """with threads.provided(facts): ... — 이 블록 안의 identify/anchor/record_commit 은 facts 를 쓴다."""
    def __init__(self, facts: dict[str, Any] | None) -> None:
        self.facts = facts
        self.token = None

    def __enter__(self):
        self.token = _FACTS.set(self.facts)
        return self.facts

    def __exit__(self, *exc):
        _FACTS.reset(self.token)


def _facts_for(worktree: str) -> dict[str, Any] | None:
    f = _FACTS.get()
    return f if f and f.get("worktree") == worktree else None


def local_facts(worktree: str) -> dict[str, Any]:
    """클라이언트(맥)에서 읽는 git 사실 — identity 와 anchor 와 HEAD 커밋. 프록시·훅이 원격 도구에 실어 보낸다."""
    ident = identify(worktree)
    out: dict[str, Any] = {k: ident.get(k) for k in ("worktree", "identity", "hint", "aliases")}
    if ident.get("virtual"):
        out["virtual"] = True
    a = anchor(worktree)
    out.update({k: a.get(k) for k in ("branch", "head", "dirty", "dirty_files")})
    wt = ident["worktree"]
    full = _git(wt, "rev-parse", "HEAD") if a.get("head") else None
    if full:
        stat = (_git(wt, "show", "--shortstat", "--format=", "HEAD") or "").strip().splitlines()
        out["commit"] = {"head": full, "committed_at": int(_git(wt, "log", "-1", "--format=%ct") or 0),
                         "message": _git(wt, "log", "-1", "--format=%s") or "", "stat": stat[-1] if stat else ""}
    return out


# 확장 39호 (2026-09-08) — 스레드가 어느 문으로 들어왔는가. mcp = 사람이 에이전트와 한 작업,
# intake = 러너가 밀어 넣은 조사. **도출하지 않고 저장한다** — 증거의 source.via 로 매번 도출하면
# 랩 스레드에 판정을 note_turn 으로 남기는 순간(랩의 정상 흐름이다) 분류가 작업으로 뒤집힌다.
AGENT_ORIGIN, INTAKE_ORIGIN = "agent", "intake"


def ensure(db) -> None:
    with db._lock:
        db.conn.executescript(_DDL)
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(thread)").fetchall()}
        if "origin" not in cols:
            db.conn.execute("ALTER TABLE thread ADD COLUMN origin TEXT")
            # 기존 스레드는 이 시점의 증거로 한 번만 판정하고 굳힌다(뒤집히지 않게).
            db.conn.execute(
                "UPDATE thread SET origin = COALESCE((SELECT CASE WHEN COUNT(*) > 0 AND "
                "SUM(CASE WHEN json_extract(e.source, '$.via') = 'mcp' THEN 1 ELSE 0 END) = 0 "
                f"THEN '{INTAKE_ORIGIN}' ELSE '{AGENT_ORIGIN}' END FROM evidence e "
                f"WHERE e.case_id = thread.case_id), '{AGENT_ORIGIN}')")
            db.conn.commit()


def set_origin(db, case_id: int, origin: str) -> None:
    ensure(db)
    with db._lock:
        db.conn.execute("UPDATE thread SET origin = ? WHERE case_id = ?", (origin, case_id))
        db.conn.commit()


def origins(db, user_id: int, case_ids: list[int]) -> dict[int, str]:
    """스레드별 origin. 열 때 정해져 그대로 남는다."""
    if not case_ids:
        return {}
    ensure(db)
    ids = [int(c) for c in case_ids]
    q = ",".join("?" * len(ids))
    out = {c: AGENT_ORIGIN for c in ids}
    with db._lock:
        rows = db.conn.execute(
            f'SELECT t.case_id AS cid, t.origin AS origin FROM thread t JOIN "case" c ON c.id = t.case_id '
            f"WHERE c.user_id = ? AND t.case_id IN ({q})", (user_id, *ids)).fetchall()
    for r in rows:
        out[r["cid"]] = r["origin"] or AGENT_ORIGIN
    return out


# ── identity ───────────────────────────────────────────────────────────────
def _git(worktree: str, *args: str) -> str | None:
    try:
        p = subprocess.run(["git", "-C", worktree, *args], capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001 — git 이 없거나 느리면 identity 는 경로로 떨어진다
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def normalize_remote(url: str) -> str:
    """git@github.com:a/b.git · ssh://git@host/a/b · https://host/a/b.git → host/a/b (host 소문자)."""
    u = url.strip()
    m = re.match(r"^[\w.-]+@([\w.-]+):(.+)$", u)          # scp 꼴
    if m:
        host, path = m.group(1), m.group(2)
    else:
        m = re.match(r"^[a-z+]+://(?:[^@/]+@)?([^/:]+)(?::\d+)?/(.+)$", u)
        if m:
            host, path = m.group(1), m.group(2)
        else:
            return u
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path}"


def identify(worktree: str) -> dict[str, Any]:
    """worktree 경로 → {worktree(정규화한 루트), identity, hint, aliases}. git 이 아니면 경로가 identity 다.

    aliases = 같은 디렉터리가 **예전에** 가졌을 identity 들(약한 것부터). 폴더가 git init 되면 path: → local: 로,
    remote 가 붙으면 local: → host/path 로 identity 가 바뀌는데, 그때 이전 identity 로 열린 스레드를 잃지 않으려면
    승격(upgrade)이 필요하다 — Codex Phase 0(case 447)이 이 구멍에 빠졌다. _resolve_repo 가 aliases 로 승격·병합한다.
    """
    if is_scratch(worktree):
        worktree = VIRTUAL_IDENTITY          # 확장 118호 — 임시 작업공간은 저장소가 아니다
    if is_virtual(worktree):
        return {"worktree": worktree, "identity": VIRTUAL_IDENTITY, "hint": "AI chat clients (no repository)",
                "aliases": [], "virtual": True}
    f = _facts_for(worktree)
    if f:
        return {"worktree": f["worktree"], "identity": f["identity"], "hint": f.get("hint") or f["identity"],
                "aliases": list(f.get("aliases") or []), "provided": True}
    wt = os.path.realpath(os.path.expanduser(worktree))
    top = _git(wt, "rev-parse", "--show-toplevel")
    if not top:
        return {"worktree": wt, "identity": f"path:{wt}", "hint": wt, "aliases": []}
    top = os.path.realpath(top)
    common = _git(top, "rev-parse", "--git-common-dir") or ".git"
    common = os.path.realpath(os.path.join(top, common)) if not os.path.isabs(common) else os.path.realpath(common)
    remote = _git(top, "remote", "get-url", "origin")
    if remote:
        return {"worktree": top, "identity": normalize_remote(remote), "hint": remote,
                "aliases": [f"local:{common}", f"path:{top}"]}
    return {"worktree": top, "identity": f"local:{common}", "hint": common, "aliases": [f"path:{top}"]}


def _repo_has_user(db, repo_id: int, user_id: int) -> bool:
    """An alias is usable only if this user already has records under it."""
    if db.conn.execute(
        'SELECT 1 FROM thread t JOIN "case" c ON c.id=t.case_id '
        'WHERE t.repo_id=? AND c.user_id=? LIMIT 1', (repo_id, user_id)
    ).fetchone():
        return True
    if db.conn.execute('SELECT 1 FROM worktree_binding WHERE repo_id=? AND user_id=? LIMIT 1',
                       (repo_id, user_id)).fetchone():
        return True
    if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prior_session'").fetchone():
        return bool(db.conn.execute('SELECT 1 FROM prior_session WHERE repo_id=? AND user_id=? LIMIT 1',
                                    (repo_id, user_id)).fetchone())
    return False


def _repo_has_any_records(db, repo_id: int) -> bool:
    if db.conn.execute('SELECT 1 FROM thread WHERE repo_id=? LIMIT 1', (repo_id,)).fetchone():
        return True
    if db.conn.execute('SELECT 1 FROM worktree_binding WHERE repo_id=? LIMIT 1', (repo_id,)).fetchone():
        return True
    if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prior_session'").fetchone():
        return bool(db.conn.execute('SELECT 1 FROM prior_session WHERE repo_id=? LIMIT 1', (repo_id,)).fetchone())
    return False


def _resolve_repo(db, user_id: int, ident: dict[str, Any], create: bool) -> dict[str, Any] | None:
    """identity 로 repo 행을 찾되, 옛 identity(aliases)로 열린 행이 있으면 **승격**한다:
    현재 identity 행이 없으면 옛 행의 identity 를 제자리에서 바꾸고(id 유지 — 스레드·바인딩 그대로),
    있으면 옛 행의 스레드·바인딩을 현재 행으로 옮기고 옛 행을 지운다(병합)."""
    with db._lock:
        row = db.get_by("repo", "identity", ident["identity"])
        for alias in ident.get("aliases", []):
            old = db.get_by("repo", "identity", alias)
            if old is None or old["id"] == (row or {}).get("id") or not _repo_has_user(db, old["id"], user_id):
                continue
            if row is None and not _repo_has_any_other_user(db, old["id"], user_id):
                # Sole owner: keep the old id, as older clients expect on path -> git upgrades.
                db.conn.execute("UPDATE repo SET identity=?, hint=? WHERE id=?",
                                (ident["identity"], ident["hint"], old["id"]))
                row = db.get("repo", old["id"])
            else:
                if row is None:
                    row = db.add("repo", {"identity": ident["identity"], "hint": ident["hint"]})
                db.conn.execute('UPDATE thread SET repo_id=? WHERE repo_id=? AND case_id IN '
                                '(SELECT id FROM "case" WHERE user_id=?)', (row["id"], old["id"], user_id))
                db.conn.execute('UPDATE worktree_binding SET repo_id=? WHERE repo_id=? AND user_id=?',
                                (row["id"], old["id"], user_id))
                if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prior_session'").fetchone():
                    db.conn.execute('UPDATE prior_session SET repo_id=? WHERE repo_id=? AND user_id=?',
                                    (row["id"], old["id"], user_id))
                if not _repo_has_any_records(db, old["id"]):
                    db.conn.execute('DELETE FROM repo WHERE id=?', (old["id"],))
        if row is None and create:
            row = db.add("repo", {"identity": ident["identity"], "hint": ident["hint"]})
        return row


def _repo_has_any_other_user(db, repo_id: int, user_id: int) -> bool:
    if db.conn.execute('SELECT 1 FROM thread t JOIN "case" c ON c.id=t.case_id '
                       'WHERE t.repo_id=? AND c.user_id!=? LIMIT 1', (repo_id, user_id)).fetchone():
        return True
    if db.conn.execute('SELECT 1 FROM worktree_binding WHERE repo_id=? AND user_id!=? LIMIT 1',
                       (repo_id, user_id)).fetchone():
        return True
    if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prior_session'").fetchone():
        return bool(db.conn.execute('SELECT 1 FROM prior_session WHERE repo_id=? AND user_id!=? LIMIT 1',
                                    (repo_id, user_id)).fetchone())
    return False


def _repo_for(db, user_id: int, ident: dict[str, Any]) -> dict[str, Any]:
    return _resolve_repo(db, user_id, ident, create=True)  # type: ignore[return-value]


def reconcile_repo(db, user_id: int, worktree: str) -> dict[str, Any]:
    """읽기 경로(resume 등)에서도 승격이 일어나게 — identity 가 바뀐 뒤 첫 접근이 어디서 오든 같은 결과."""
    ensure(db)
    ident = identify(worktree)
    _resolve_repo(db, user_id, ident, create=False)
    return ident


# ── bindings ───────────────────────────────────────────────────────────────
def _binding(db, user_id: int, worktree: str) -> dict[str, Any] | None:
    with db._lock:
        row = db.conn.execute("SELECT * FROM worktree_binding WHERE user_id=? AND worktree=?",
                              (user_id, worktree)).fetchone()
    return dict(row) if row else None


def facts_from_binding(db, user_id: int, worktree: str) -> dict[str, Any] | None:
    """확장 48호 (2026-09-10, D14179) — 원격 문에 facts 없이 경로만 왔을 때. 서버는 git 을 모르므로 **이미 묶인 경로**의
    저장소 정체성만 돌려준다(anchor·commit 은 없다 — 지어내지 않는다). 바인딩이 없으면 None — 호출자가 거절한다.
    #492: 커넥터(프록시 없음)가 넘긴 맥 경로에 서버 컨테이너가 git 을 돌리다 실패해 path:/Users/… 를 지어냈다."""
    ensure(db)
    b = _binding(db, user_id, worktree)
    repo = db.get("repo", b["repo_id"]) if b else None
    if repo is None:
        return None
    return {"worktree": worktree, "identity": repo["identity"], "hint": repo["hint"], "aliases": [], "from_binding": True}


def _bind(db, user_id: int, worktree: str, repo_id: int, case_id: int) -> None:
    with db._lock:
        db.conn.execute(
            "INSERT INTO worktree_binding(user_id, worktree, repo_id, case_id, bound_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(user_id, worktree) DO UPDATE SET repo_id=excluded.repo_id, case_id=excluded.case_id, "
            "bound_at=excluded.bound_at",
            (user_id, worktree, repo_id, case_id, db.now_ms()))
        db.conn.commit()


def _unbind(db, user_id: int, worktree: str | None = None, case_id: int | None = None) -> int:
    with db._lock:
        if worktree is not None:
            cur = db.conn.execute("DELETE FROM worktree_binding WHERE user_id=? AND worktree=?", (user_id, worktree))
        else:
            cur = db.conn.execute("DELETE FROM worktree_binding WHERE user_id=? AND case_id=?", (user_id, case_id))
        db.conn.commit()
        return cur.rowcount


def _thread_row(db, case_id: int) -> dict[str, Any] | None:
    with db._lock:
        row = db.conn.execute("SELECT * FROM thread WHERE case_id=?", (case_id,)).fetchone()
    return dict(row) if row else None


def _ensure_thread(db, case: dict, repo_id: int) -> dict[str, Any]:
    """케이스를 스레드로 만든다. 이미 스레드면 그대로(다른 repo 로 옮기지 않는다 — 스레드는 한 저장소의 것)."""
    row = _thread_row(db, case["id"])
    if row:
        return row
    with db._lock:
        db.conn.execute("INSERT INTO thread(case_id, repo_id, created_at) VALUES (?,?,?)",
                        (case["id"], repo_id, db.now_ms()))
        db.conn.commit()
    return _thread_row(db, case["id"])  # type: ignore[return-value]


# ── operations (app.py 가 소유권 검사 뒤 부른다) ───────────────────────────
def open_thread(db, user_id: int, case: dict, focus: str, worktree: str, authority: str) -> dict[str, Any]:
    ensure(db)
    if not (focus or "").strip():
        raise InputError("focus is empty — say in one sentence what this thread is for")
    ident = identify(worktree)
    repo = _repo_for(db, user_id, ident)
    _ensure_thread(db, case, repo["id"])
    d = state.declare(db, case, "focus", focus, authority)
    _bind(db, user_id, ident["worktree"], repo["id"], case["id"])
    return {"case_id": case["id"], "repo": repo["identity"], "worktree": ident["worktree"],
            "focus": focus, "focus_id": d["declaration_id"]}


def switch_thread(db, user_id: int, case: dict | None, worktree: str) -> dict[str, Any]:
    """case=None 이면 pause(바인딩 해제). 스레드가 아니었던 케이스(예: 옛 사건)도 이 저장소의 스레드로 받는다."""
    ensure(db)
    ident = identify(worktree)
    if case is None:
        n = _unbind(db, user_id, worktree=ident["worktree"])
        return {"worktree": ident["worktree"], "current": None, "paused": bool(n)}
    if case["status"] != "open":
        raise InputError(f"thread {case['id']} is {case['status']} — reopen it before switching to it")
    repo = _repo_for(db, user_id, ident)
    row = _ensure_thread(db, case, repo["id"])
    if row["repo_id"] != repo["id"]:
        other = db.get("repo", row["repo_id"])
        if ident.get("virtual") and other is not None:
            # 채팅 클라이언트는 저장소가 없다 — 스레드가 속한 저장소 그대로 두고 바인딩만 잡는다
            _bind(db, user_id, ident["worktree"], other["id"], case["id"])
            return {"worktree": ident["worktree"], "current": case["id"], "repo": other["identity"]}
        raise InputError(f"thread {case['id']} belongs to {other['identity'] if other else '?'}, not to {repo['identity']}")
    _bind(db, user_id, ident["worktree"], repo["id"], case["id"])
    return {"worktree": ident["worktree"], "current": case["id"], "repo": repo["identity"]}


def close_thread(db, user_id: int, case: dict) -> dict[str, Any]:
    ensure(db)
    n = _unbind(db, user_id, case_id=case["id"])
    return {"case_id": case["id"], "unbound_worktrees": n}


def current_thread(db, user_id: int, worktree: str) -> dict[str, Any] | None:
    ensure(db)
    ident = identify(worktree)
    b = _binding(db, user_id, ident["worktree"])
    if not b:
        return None
    case = db.get("case", b["case_id"])
    if case is None or case["status"] != "open":
        _unbind(db, user_id, worktree=ident["worktree"])   # 닫힌 스레드에 매인 바인딩은 무효
        return None
    return {"case_id": case["id"], "title": case["title"], "worktree": ident["worktree"],
            "repo": ident["identity"], "focus": state.current(db, case["id"], "focus"), "bound_at": b["bound_at"]}


def list_threads(db, user_id: int, worktree: str, vitals: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """이 worktree 의 저장소에 속한 스레드 전부(open 먼저, 마지막 활동 순) + 이 worktree 의 current."""
    ensure(db)
    ident = identify(worktree)
    repo = _resolve_repo(db, user_id, ident, create=False)  # 자기 별칭의 스레드만 승격한다
    out: dict[str, Any] = {"worktree": ident["worktree"], "repo": ident["identity"], "current": None, "threads": []}
    if repo is None:
        return out
    with db._lock:
        rows = db.conn.execute(
            "SELECT c.id, c.title, c.status FROM thread t JOIN \"case\" c ON c.id = t.case_id "
            "WHERE t.repo_id=? AND c.user_id=?", (repo["id"], user_id)).fetchall()
        bound = db.conn.execute("SELECT worktree, case_id FROM worktree_binding WHERE user_id=? AND repo_id=?",
                                (user_id, repo["id"])).fetchall()
    where = {}
    for b in bound:
        where.setdefault(b["case_id"], []).append(b["worktree"])
    _, results = closings(db)
    threads = []
    for r in rows:
        v = vitals.get(r["id"], {})
        st = "open" if r["status"] == "open" else "closed"
        threads.append({
            "case_id": r["id"], "title": r["title"], "state": st,
            # 확장 48호 — 화면에서 결과 없이 닫힌 스레드: 에이전트가 "정리해줘"에 골라 close_thread(result) 로 채운다
            "result": results.get(r["id"]), "needs_result": st == "closed" and not results.get(r["id"]),
            "focus": state.current(db, r["id"], "focus"),
            "turn_count": v.get("turn_count", 0), "updated_at": v.get("updated_at"),
            "last_turn_status": v.get("last_turn_status"),
            "bound_worktrees": where.get(r["id"], []),
        })
    threads.sort(key=lambda t: (t["state"] != "open", -(t["updated_at"] or 0), -t["case_id"]))
    out["threads"] = threads
    b = _binding(db, user_id, ident["worktree"])
    out["current"] = b["case_id"] if b and any(t["case_id"] == b["case_id"] and t["state"] == "open" for t in threads) else None
    return out


# ── 확장 16호 — 저장소 형제 스레드 · Anchor(live git) ─────────────────────
def repo_of_case(db, case_id: int) -> dict[str, Any] | None:
    ensure(db)
    row = _thread_row(db, case_id)
    return db.get("repo", row["repo_id"]) if row else None


def repo_case_ids(db, user_id: int, case_id: int) -> set[int] | None:
    """이 케이스가 스레드면 같은 저장소·같은 사용자의 스레드 case_id 전부(자기 포함). 아니면 None."""
    repo = repo_of_case(db, case_id)
    if repo is None:
        return None
    with db._lock:
        rows = db.conn.execute(
            "SELECT t.case_id FROM thread t JOIN \"case\" c ON c.id = t.case_id WHERE t.repo_id=? AND c.user_id=?",
            (repo["id"], user_id)).fetchall()
    return {r["case_id"] for r in rows}


def anchor(worktree: str) -> dict[str, Any]:
    """live git 상태 — 정본은 언제나 이것이다(checkpoint 는 과거 관찰). git 이 아니면 경로만."""
    ident = identify(worktree)
    wt = ident["worktree"]
    out: dict[str, Any] = {"worktree": wt, "repo": ident["identity"], "read_at": _now()}
    if ident.get("virtual"):
        out.update({"branch": None, "head": None, "dirty": None, "virtual": True})
        return out
    f = _facts_for(worktree)
    if f:
        out.update({"branch": f.get("branch"), "head": f.get("head"), "dirty": f.get("dirty"),
                    "dirty_files": list(f.get("dirty_files") or []), "provided": True})
        if f.get("from_binding"):
            out["from_binding"] = True      # 확장 48호 — 정체성은 바인딩에서, live git 은 없다
        return out
    branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        out.update({"branch": None, "head": None, "dirty": None})
        return out
    head = _git(wt, "rev-parse", "--short=12", "HEAD")
    status = _git(wt, "status", "--porcelain") or ""
    lines = [l for l in status.splitlines() if l.strip()]
    out.update({"branch": branch, "head": head, "dirty": len(lines), "dirty_files": [l[3:] for l in lines[:10]]})
    return out


def _now() -> int:
    import time
    return int(time.time() * 1000)


# ── 확장 17호 — git 자동 연결: change(commit | checkpoint). evidence 가 아니다 ────────
# evidence 는 "기계가 관찰해 출력한 사실"이고 git 은 그 자체가 canonical record 다. 여기엔 참조(hash·branch·
# message·shortstat)만 둔다. checkpoint 는 "그때 마지막으로 본 git 상태"이지 현재 상태의 정본이 아니다 —
# resume 의 anchor 가 live git 과 비교해 changed_since 를 낸다. 기록은 best-effort: 실패해도 commit 은 성공한다.
_DDL_CHANGE = """
CREATE TABLE IF NOT EXISTS change (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at INTEGER NOT NULL,
  user_id INTEGER NOT NULL,
  case_id INTEGER NOT NULL,
  kind TEXT NOT NULL,
  worktree TEXT,
  branch TEXT,
  head TEXT,
  message TEXT,
  stat TEXT,
  dirty INTEGER,
  dirty_files TEXT
);
CREATE INDEX IF NOT EXISTS ix_change_case ON change(case_id, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_change_commit ON change(case_id, head) WHERE kind = 'commit';
"""


def ensure_change(db) -> None:
    with db._lock:
        db.conn.executescript(_DDL_CHANGE)


def record_commit(db, user_id: int, case_id: int, worktree: str) -> dict[str, Any] | None:
    """worktree 의 HEAD 커밋을 스레드의 change 로. 이미 있으면(같은 case·hash) None — 멱등이라 훅이 매번 불러도 된다."""
    ensure_change(db)
    ident = identify(worktree); wt = ident["worktree"]
    f = _facts_for(worktree)
    c = (f or {}).get("commit") if f else None
    head = c["head"] if c else (None if f else _git(wt, "rev-parse", "HEAD"))
    if not head:
        return None
    # 커밋은 "그것이 만들어진 시점에 현재였던 스레드"의 것이다. 훅은 현재 바인딩만 알기 때문에, 바인딩보다
    # 오래된 커밋(스레드를 바꾸기 전에 한 커밋)은 여기 붙이지 않는다 — 2026-09-05 두 번 잘못 붙은 뒤 추가.
    b = _binding(db, user_id, wt)
    committed = str(c["committed_at"]) if c else _git(wt, "log", "-1", "--format=%ct")
    # git 의 %ct 는 초 단위(내림)라 "그 초가 끝난 뒤에 바인딩이 시작됐다"를 기준으로 한다 — 같은 초면 붙인다.
    if b and b["case_id"] == case_id and committed and (int(committed) + 1) * 1000 <= b["bound_at"]:
        return None
    if c:
        branch, message, stat = f.get("branch"), c.get("message") or "", [c.get("stat") or ""]
    else:
        branch = _git(wt, "rev-parse", "--abbrev-ref", "HEAD")
        message = _git(wt, "log", "-1", "--format=%s") or ""
        stat = (_git(wt, "show", "--shortstat", "--format=", "HEAD") or "").strip().splitlines()
    import json
    with db._lock:
        cur = db.conn.execute(
            "INSERT OR IGNORE INTO change(created_at, user_id, case_id, kind, worktree, branch, head, message, stat, dirty, dirty_files) "
            "VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)",
            (db.now_ms(), user_id, case_id, "commit", wt, branch, head, message, stat[-1] if stat else ""))
        db.conn.commit()
        if cur.rowcount == 0:
            return None
        row = db.conn.execute("SELECT * FROM change WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def record_checkpoint(db, user_id: int, case_id: int, worktree: str, why: str = "") -> dict[str, Any] | None:
    """지금 본 git 상태(branch·HEAD·dirty)를 스레드에 남긴다 — 압축 직전·세션 종료·pause/switch 의 마지막 관찰."""
    ensure_change(db)
    a = anchor(worktree)
    if a.get("head") is None:
        return None
    import json
    with db._lock:
        cur = db.conn.execute(
            "INSERT INTO change(created_at, user_id, case_id, kind, worktree, branch, head, message, stat, dirty, dirty_files) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (db.now_ms(), user_id, case_id, "checkpoint", a["worktree"], a["branch"], a["head"], why, None,
             a["dirty"], json.dumps(a.get("dirty_files", []), ensure_ascii=False)))
        db.conn.commit()
        row = db.conn.execute("SELECT * FROM change WHERE id=?", (cur.lastrowid,)).fetchone()
    return dict(row)


def anchor_history(db, case_id: int, limit: int = 10) -> dict[str, Any]:
    """스레드의 commit 참조(최근 순)와 마지막 checkpoint."""
    ensure_change(db)
    import json
    with db._lock:
        commits = db.conn.execute(
            "SELECT created_at, branch, head, message, stat FROM change WHERE case_id=? AND kind='commit' ORDER BY id DESC LIMIT ?",
            (case_id, limit)).fetchall()
        cp = db.conn.execute(
            "SELECT created_at, worktree, branch, head, dirty, dirty_files, message FROM change WHERE case_id=? AND kind='checkpoint' "
            "ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
    out: dict[str, Any] = {"commits": [dict(c) for c in commits], "last_checkpoint": None}
    if cp:
        out["last_checkpoint"] = {"at": cp["created_at"], "worktree": cp["worktree"], "branch": cp["branch"], "head": cp["head"],
                                  "dirty": cp["dirty"], "dirty_files": json.loads(cp["dirty_files"] or "[]"), "why": cp["message"]}
    return out


def compare_anchor(live: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    """live git(정본) 과 마지막 checkpoint(과거 관찰) 의 차이. 차이 자체가 handoff 에 유용한 정보다."""
    cp = history.get("last_checkpoint")
    out = dict(live); out.update(history)
    if cp is None or live.get("head") is None:
        out["changed_since_checkpoint"] = None
        return out
    same_head = (live["head"] or "").startswith(cp["head"][:12]) or (cp["head"] or "").startswith(live["head"][:12])
    out["changed_since_checkpoint"] = not (same_head and (live.get("dirty") or 0) == (cp.get("dirty") or 0)
                                           and (live.get("dirty_files") or []) == (cp.get("dirty_files") or []))
    return out


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _json_or_empty(v) -> dict:
    import json
    try:
        return json.loads(v) if isinstance(v, str) else dict(v or {})
    except Exception:
        return {}


def closings(db) -> tuple[dict[int, int], dict[int, str]]:
    """case_status_changed 원장을 순서대로 접어 (닫힌 시각, 결과 한 줄)을 낸다 — 확장 48호.
    닫힌 시각은 실제로 상태가 바뀐 이벤트의 것이고, 결과는 그 뒤 나중에 붙인 이벤트(from == to)까지
    최신 것이 이긴다. 다시 열리면 둘 다 지운다 — 다음 종료가 옛 결과를 물려받지 않게."""
    with db._lock:
        rows = db.conn.execute(
            "SELECT case_id, payload, created_at FROM ledger WHERE event_type='case_status_changed' ORDER BY id").fetchall()
    closed_at: dict[int, int] = {}; results: dict[int, str] = {}
    for c in rows:
        p = c["payload"] if isinstance(c["payload"], dict) else _json_or_empty(c["payload"])
        cid, to = c["case_id"], p.get("to")
        if to in ("resolved", "archived"):
            if p.get("from") != to:
                closed_at[cid] = c["created_at"]
            if p.get("result"):
                results[cid] = p["result"]
        elif to == "open":
            closed_at.pop(cid, None); results.pop(cid, None)
    return closed_at, results


# ── 확장 22호 — Tracker 읽기: 사용자의 모든 저장소·스레드 (웹은 worktree 를 모른다) ─────────
def all_threads(db, user_id: int, vitals: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    """저장소별 스레드 전부. 정렬: 저장소는 가장 최근 활동 순, 스레드는 open 먼저 · 최근 활동 순."""
    ensure(db); ensure_change(db)
    with db._lock:
        rows = db.conn.execute(
            "SELECT r.id AS repo_id, r.identity, r.hint, c.id AS case_id, c.title, c.status "
            "FROM thread t JOIN \"case\" c ON c.id = t.case_id JOIN repo r ON r.id = t.repo_id WHERE c.user_id=?",
            (user_id,)).fetchall()
        bound = db.conn.execute("SELECT worktree, case_id FROM worktree_binding WHERE user_id=?", (user_id,)).fetchall()
        last_commits = db.conn.execute(
            "SELECT case_id, head, message, created_at FROM change WHERE kind='commit' AND id IN "
            "(SELECT MAX(id) FROM change WHERE kind='commit' GROUP BY case_id)").fetchall()
    where: dict[int, list[str]] = {}
    for b in bound:
        where.setdefault(b["case_id"], []).append(b["worktree"])
    lc = {r["case_id"]: {"head": r["head"], "message": r["message"], "at": r["created_at"]} for r in last_commits}
    closed_at, results = closings(db)
    repos: dict[int, dict[str, Any]] = {}
    for r in rows:
        v = vitals.get(r["case_id"], {})
        rep = repos.setdefault(r["repo_id"], {"repo_id": r["repo_id"], "identity": r["identity"], "hint": r["hint"], "threads": []})
        commit = lc.get(r["case_id"])
        # Tracker 의 "마지막 활동" 은 턴·레코드(vitals) 뿐 아니라 커밋도 포함한다 — 작업 화면이니까
        updated = max(v.get("updated_at") or 0, (commit or {}).get("at") or 0) or None
        st = "open" if r["status"] == "open" else "closed"
        nd = state.current_declaration(db, r["case_id"], "next")
        owner = (nd or {}).get("owner")
        rep["threads"].append({
            "case_id": r["case_id"], "title": r["title"], "state": st,
            "focus": state.current(db, r["case_id"], "focus"),
            "next": (nd or {}).get("statement"), "next_at": (nd or {}).get("declared_at"), "owner": owner,
            "open": state.current(db, r["case_id"], "open"),
            "turn_count": v.get("turn_count", 0), "updated_at": updated,
            "last_turn_status": v.get("last_turn_status"), "record_count": v.get("record_count", 0),
            "bound_worktrees": where.get(r["case_id"], []), "last_commit": commit,
            # 확장 37호 — 단계는 선언된 차례 그대로, 결과는 닫을 때 남긴 한 줄
            "phase": phase.thread_phase(st, owner),
            "closed_at": closed_at.get(r["case_id"]) if st == "closed" else None,
            "result": results.get(r["case_id"]),
        })
    out = list(repos.values())
    for rep in out:
        rep["threads"].sort(key=lambda t: (t["state"] != "open", -(t["updated_at"] or 0), -t["case_id"]))
        rep["open_count"] = sum(1 for t in rep["threads"] if t["state"] == "open")
        rep["updated_at"] = max((t["updated_at"] or 0) for t in rep["threads"])
    out.sort(key=lambda r: -r["updated_at"])
    return out


def bound_worktree_of(db, user_id: int, case_id: int) -> str | None:
    ensure(db)
    with db._lock:
        rows = db.conn.execute("SELECT worktree FROM worktree_binding WHERE user_id=? AND case_id=? ORDER BY bound_at DESC",
                               (user_id, case_id)).fetchall()
    real = [r["worktree"] for r in rows if not is_virtual(r["worktree"])]
    return real[0] if real else (rows[0]["worktree"] if rows else None)
