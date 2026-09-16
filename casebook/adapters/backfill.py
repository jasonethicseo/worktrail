"""설치 전 기록 끌어오기 — 확장 42호. 맥에 이미 쌓인 세션 로그에서 **사람이 친 말만** 골라 서버로 보낸다.

    casebook-backfill              # 작업공간 목록을 보여 주고 고르게 한다 (로컬 모드면 이 맥의 DB 로)
    casebook-backfill --dry-run    # 무엇이 갈지만 본다 (아무것도 보내지 않는다)
    casebook-backfill --all        # 묻지 않고 전부 (스크립트용)
    casebook-backfill --only casebook,bot   # 이름에 그 말이 든 작업공간만

단위는 저장소가 아니라 **사용자**다 — 원래 문제가 "이 저장소의 기억"이 아니라 "여러 디렉터리·여러 AI
세션에 파편화"였다. 그래서 디렉터리를 가리지 않고 한 번 회수한다.

무엇을 고르게 하나: 어느 작업공간을 올릴지 사람이 정한다. 두 가지 이유다. (1) 회사 코드·비밀이 섞인
디렉터리를 서버로 올릴지는 사람만 정할 수 있다. (2) 자동화가 보낸 프롬프트("You are …" 로 시작하는
442건을 실측)가 user 메시지로 남는데 메타데이터로는 안 갈린다 — 어느 디렉터리가 파이프라인인지도
사람만 안다. 자동 판별을 더 똑똑하게 만들려다 틀리느니 고르게 한다.

서버는 이 맥의 디스크를 볼 수 없다(확장 29호와 같은 이유). 그래서 읽고 고르는 일만 여기 남긴다:
- Claude Code `~/.claude/projects/*/*.jsonl` · Codex `~/.codex/sessions/**/*.jsonl`
- 저장소는 **디렉터리 이름이 아니라 기록 안의 cwd** 로 가린다 — 이름 규칙(슬러그)은 되돌릴 수 없어
  `casebook` 과 `casebook-copilot` 이 같은 폴더로 뭉친다.
- 세션 식별자는 **파일 이름**이다. 기록 안의 session_id 는 파일마다 유일하지 않다 — 이어받기와 서브에이전트가
  같은 id 를 여러 파일에 쓴다(이 저장소 실측: 파일 32 → id 15, 그래서 절반 넘게 서로를 덮어썼다).
- 사람이 친 말만 남긴다. 도구 결과, 하네스가 끼워 넣는 알림·명령 출력·환경 안내, 에이전트의 말은 전부 뺀다.
  에이전트의 말을 들이면 다음 세션이 과거의 폐기된 추론을 사실로 읽는다(search.py 계약 1번과 같은 이유).

무엇을 만들지 않는가: 스레드·주제·focus·결정·제약·next. 여기서 오는 것은 검색 가능한 원자료뿐이다(C13615).
저장소마다 Worktrail 이 처음 기록한 때가 컷오프다 — 그 뒤는 native 기록이 있으므로 서버가 버린다.
"""
from __future__ import annotations

import contextlib
import json
import os
import pathlib
import sys
from typing import Any, Iterator

from casebook.adapters.mcp_proxy import default_worktree
from casebook.core import threads
from casebook.core.clientmode import say

CLAUDE_ROOT = "~/.claude/projects"
CODEX_ROOT = "~/.codex/sessions"

# 하네스가 사용자 메시지 자리에 끼워 넣는 것들 — 사람이 친 말이 아니다.
NOT_HUMAN_PREFIX = (
    "<command-name>", "<command-message>", "<local-command-stdout>", "<local-command-stderr>",
    "<bash-input>", "<bash-stdout>", "<bash-stderr>", "<user-prompt-submit-hook>",
    "Caveat: The messages below were generated",
    "# AGENTS.md instructions", "<environment_context>", "<user_instructions>",
    "<INSTRUCTIONS>", "<recommended_plugins>",
    # 에이전트가 스스로 만든 검토 요청 — 세션 메타로도 거르지만 메타가 없는 형식을 대비한다.
    "The following is the Codex agent history",
)
NOT_HUMAN_CONTAINS = ("<system-reminder>", "<task-notification>", "<ci-monitor-event>")


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """한 세션 안에서 똑같은 말이 여러 번 적히는 것은 로그의 흔적이다 — 이어받기·재전송으로 같은 메시지가
    uuid 만 다르게 다시 쓰인다(실측: 한 파일에 같은 문장이 ref 두 개로). 처음 것만 남긴다."""
    seen: set[str] = set()
    out = []
    for i in items:
        key = i["text"]
        if key in seen:
            continue
        seen.add(key)
        out.append(i)
    return out


def _is_human(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith(NOT_HUMAN_PREFIX):
        return False
    head = t[:400]
    return not any(m in head for m in NOT_HUMAN_CONTAINS)


def _ms(stamp: Any) -> int | None:
    """ISO8601 → epoch ms. 못 읽으면 None — 시간은 있으면 좋고 없어도 기록은 들어간다."""
    if isinstance(stamp, (int, float)):
        return int(stamp)
    if not isinstance(stamp, str) or not stamp:
        return None
    import datetime
    try:
        return int(datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _lines(path: pathlib.Path) -> Iterator[dict]:
    try:
        with path.open(errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue                      # 쓰는 중이던 마지막 줄 — 건너뛴다
    except OSError:
        return


def _claude_text(message: dict) -> str | None:
    """user 항목에서 사람이 친 말을 꺼낸다. 문자열이면 그대로, 배열이면 도구 결과가 아닌 text 블록만.
    배열을 통째로 버리면 이미지와 함께 쓴 말이 사라진다(이 저장소 실측 16건)."""
    c = message.get("content")
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        return None
    if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return None                                   # 도구 결과 — 사람의 말이 아니다
    parts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
    joined = "\n".join(x for x in parts if x)
    return joined or None


def read_claude(path: pathlib.Path) -> dict[str, Any] | None:
    """Claude Code 세션 하나. 사람이 친 말만 — 도구 결과와 하네스가 끼워 넣는 것,
    그리고 압축 요약(isCompactSummary)은 뺀다. 압축 요약은 모델이 쓴 글이다."""
    out: list[dict[str, Any]] = []
    cwd = branch = None
    for r in _lines(path):
        if r.get("type") != "user" or r.get("isMeta") or r.get("isCompactSummary"):
            continue
        content = _claude_text(r.get("message") or {})
        if content is None or not _is_human(content):
            continue
        cwd = cwd or r.get("cwd")
        branch = branch or r.get("gitBranch")
        out.append({"at": _ms(r.get("timestamp")), "ref": r.get("uuid"), "text": content})
    out = _dedupe(out)
    if not out:
        return None
    return {"source": "claude-code", "external_id": path.stem, "source_path": str(path),
            "cwd": cwd, "branch": branch,
            "started_at": out[0]["at"], "ended_at": out[-1]["at"], "instructions": out}


def _codex_is_subagent(meta: dict) -> bool:
    """에이전트가 스스로 띄운 세션(검토 guardian 등) — 여기 담긴 user 메시지는 사람이 친 말이 아니라
    에이전트가 만든 요청문이다. 이 저장소 실측: cwd 가 맞는 codex 파일 19개가 전부 이것이었고,
    자동 생성 검토 요청이 971건 중 454건·바이트로는 96% 였다."""
    src = meta.get("source")
    if isinstance(src, dict) and "subagent" in src:
        return True
    return bool(meta.get("thread_source") or meta.get("parent_thread_id") not in (None, meta.get("session_id")))


def read_codex(path: pathlib.Path) -> dict[str, Any] | None:
    """Codex 세션 하나. 사람이 띄운 세션의 role=user 메시지만."""
    out: list[dict[str, Any]] = []
    cwd = branch = None
    started = None
    for r in _lines(path):
        payload = r.get("payload") or {}
        if r.get("type") == "session_meta":
            if _codex_is_subagent(payload):
                return None                           # 에이전트가 띄운 세션 — 통째로 들이지 않는다
            cwd = cwd or payload.get("cwd")
            started = started or _ms(r.get("timestamp"))
            git = payload.get("git") or {}
            branch = branch or (git.get("branch") if isinstance(git, dict) else None)
            continue
        if r.get("type") != "response_item" or payload.get("type") != "message":
            continue
        if payload.get("role") != "user":
            continue
        text = "\n".join(b.get("text", "") for b in (payload.get("content") or [])
                         if isinstance(b, dict) and b.get("type") in ("input_text", "text"))
        if not _is_human(text):
            continue
        out.append({"at": _ms(r.get("timestamp")), "ref": payload.get("id"), "text": text})
    out = _dedupe(out)
    if not out:
        return None
    return {"source": "codex", "external_id": path.stem, "source_path": str(path),
            "cwd": cwd, "branch": branch,
            "started_at": started or out[0]["at"], "ended_at": out[-1]["at"], "instructions": out}


def collect_all() -> list[dict[str, Any]]:
    """디렉터리를 가리지 않고 사람이 띄운 세션 전부. cwd 는 세션이 적어 둔 값을 그대로 쓴다."""
    found: list[dict[str, Any]] = []
    for root, reader in ((CLAUDE_ROOT, read_claude), (CODEX_ROOT, read_codex)):
        base = pathlib.Path(root).expanduser()
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            session = reader(path)
            if session is not None and session.get("cwd"):
                found.append(session)
    found.sort(key=lambda s: s.get("started_at") or 0)
    return found


def workspaces(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """작업공간(cwd)별로 묶는다 — 사람이 고르는 단위."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in sessions:
        groups.setdefault(os.path.realpath(s["cwd"]), []).append(s)
    out = []
    for cwd, items in groups.items():
        out.append({"cwd": cwd, "sessions": items, "exists": os.path.isdir(cwd),
                    "instructions": sum(len(x["instructions"]) for x in items),
                    "bytes": sum(len(i["text"].encode()) for x in items for i in x["instructions"])})
    out.sort(key=lambda w: -w["instructions"])
    return out


def facts_for(cwd: str) -> dict[str, Any]:
    """그 작업공간의 git 사실. 폴더가 사라졌으면 경로만으로 식별한다(기록은 버리지 않는다)."""
    if os.path.isdir(cwd):
        try:
            return threads.local_facts(cwd)
        except Exception:                                  # noqa: BLE001 — git 이 아니어도 들인다
            pass
    return {"worktree": cwd, "identity": f"path:{cwd}", "hint": cwd, "aliases": []}


def collect(worktree: str) -> list[dict[str, Any]]:
    """이 worktree 에서 한 세션만 모은다 — 판별은 기록 안의 cwd 로 한다."""
    target = os.path.realpath(worktree)
    found: list[dict[str, Any]] = []
    for root, reader in ((CLAUDE_ROOT, read_claude), (CODEX_ROOT, read_codex)):
        base = pathlib.Path(root).expanduser()
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.jsonl")):
            session = reader(path)
            if session is None:
                continue
            cwd = session.get("cwd")
            if not cwd or os.path.realpath(cwd) != target:
                continue
            found.append(session)
    found.sort(key=lambda s: s.get("started_at") or 0)
    return found


BATCH_BYTES = 700_000        # 요청 하나의 상한. 이 저장소 실측 전체 10.7MB · 가장 큰 세션 3MB — 한 번에 보내면 끊긴다.


def _parts(s: dict[str, Any], budget: int) -> Iterator[tuple[dict[str, Any], int]]:
    """세션 하나를 상한에 맞는 조각들로. 두 번째 조각부터 append 를 단다."""
    head = {k: v for k, v in s.items() if k != "instructions"}
    overhead = len(json.dumps(head, ensure_ascii=False).encode()) + 32
    room = max(budget - overhead, 1)
    cur: list[dict[str, Any]] = []
    cur_size = 0
    first = True
    for item in s["instructions"]:
        n = len(json.dumps(item, ensure_ascii=False).encode())
        if cur and cur_size + n > room:
            yield {**head, "instructions": cur, **({} if first else {"append": True})}, overhead + cur_size
            cur, cur_size, first = [], 0, False
        cur.append(item)
        cur_size += n
    if cur:
        yield {**head, "instructions": cur, **({} if first else {"append": True})}, overhead + cur_size


def batches(sessions: list[dict[str, Any]], budget: int = BATCH_BYTES) -> Iterator[list[dict[str, Any]]]:
    """요청 크기에 맞춰 쪼갠다. 한 세션이 혼자 상한을 넘으면 지시를 나누고 두 번째 조각부터 append 를 단다
    (서버가 지우지 않고 뒤에 붙인다). 다시 돌리면 첫 조각이 지우고 다시 쓰므로 결과는 같다.
    지시 하나가 상한보다 클 때만 조각이 상한을 넘는다 — 말을 자르지는 않는다."""
    batch: list[dict[str, Any]] = []
    size = 0
    for s in sessions:
        for piece, piece_size in _parts(s, budget):
            if batch and size + piece_size > budget:
                yield batch
                batch, size = [], 0
            batch.append(piece)
            size += piece_size
    if batch:
        yield batch


def import_local(worktree: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """로컬 모드 — 기록이 이 맥에만 있을 때. 서버를 거치지 않고 그 DB 에 바로 적는다.
    서버 쪽 import_prior 와 같은 순서다: 저장소를 정하고, 그 저장소의 컷오프를 구하고, 갈아 끼운다."""
    from casebook.adapters.mcp_server import build_from_env
    from casebook.core import prior

    cb, uid = build_from_env()
    facts = facts_for(worktree)
    with threads.provided(facts):
        threads.ensure(cb.db)
        repo = threads._repo_for(cb.db, threads.identify(facts["worktree"]))
        cut = prior.cutoff_for(cb.db, uid, repo["id"])
        out = prior.import_sessions(cb.db, uid, repo["id"], sessions, reset=True, cutoff=cut)
    out["repository"] = repo["identity"]
    return out


@contextlib.asynccontextmanager
async def _maybe(client):
    """client 가 있으면 그 수명을 여기서 닫고, 없으면 아무것도 하지 않는다."""
    if client is None:
        yield None
        return
    async with client:
        yield client


def send(url: str, worktree: str, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    """원격 문의 import_prior 도구로 보낸다. git 사실은 여기서 읽어 실어 보낸다(확장 29호와 같은 길)."""
    import anyio
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    facts = facts_for(worktree)
    chunks = list(batches(sessions))

    # 확장 96호 — 로그인해 두었으면 주소에서 토큰을 떼고 머리로 붙는다.
    # 확장 103호 — 머리를 한 번 박지 않고 요청마다 다는 인증기를 쓴다. 조각이 많으면 한 연결이
    # 300초(토큰 수명)를 넘고, 박아 둔 머리는 그 뒤로 전부 401 이 된다.
    from casebook.adapters.device_login import route_auth
    to, auth = route_auth(url)

    async def go():
        total = {"added": 0, "updated": 0, "skipped": 0, "stored_instructions": 0}
        client = None
        if auth is not None:
    # 확장 102호 — SDK 의 create_mcp_http_client 로 만든다. 맨 httpx2.AsyncClient 는 timeout 5초에
    # follow_redirects 가 꺼져 있고, SDK 것은 connect 30초·read 300초에 redirect 를 따라간다.
            from mcp.shared._httpx_utils import create_mcp_http_client
            client = create_mcp_http_client(auth=auth)
        async with _maybe(client), streamable_http_client(to, http_client=client) as (r, w):
            async with ClientSession(r, w, read_timeout_seconds=180) as sess:
                await sess.initialize()
                for n, chunk in enumerate(chunks, 1):
                    # 첫 조각이 그 저장소의 설치 전 기록을 비운다 — 디스크에 있는 것이 진실이다.
                    res = await sess.call_tool("import_prior",
                                               {"facts": facts, "sessions": chunk, "reset": n == 1})
                    text = res.content[0].text if res.content else "{}"
                    if text.startswith("casebook error:"):
                        raise RuntimeError(text)
                    out = json.loads(text)
                    for k in ("added", "updated", "skipped"):
                        total[k] += out.get(k, 0)
                    total["stored_instructions"] += out.get("stored_instructions", 0)
                    print(say(f"  보냄 {n}/{len(chunks)}", f"  sent {n}/{len(chunks)}"), end="\r", flush=True)
        print(" " * 24, end="\r")
        return total
    return anyio.run(go)


def _fmt(w: dict[str, Any]) -> str:
    kb = w["bytes"] // 1024
    mark = "" if w["exists"] else say("  (폴더 없음)", "  (folder is gone)")
    # 폭을 맞춰 목록이 줄맞춤된다 — 단위 말이 길이가 달라도 자리는 같다
    body = say(f'{len(w["sessions"]):4d}세션 {w["instructions"]:5d}건',
               f'{len(w["sessions"]):4d} sess {w["instructions"]:5d} typed')
    return f'{body} {kb:6d}KB  {w["cwd"]}{mark}'


def choose(ws: list[dict[str, Any]], argv: list[str]) -> list[dict[str, Any]] | None:
    """무엇을 올릴지 사람이 정한다. 고르지 않으면 아무것도 보내지 않는다."""
    only = next((a.split("=", 1)[1] for a in argv if a.startswith("--only=")), None)
    if only is None and "--only" in argv:
        i = argv.index("--only")
        only = argv[i + 1] if i + 1 < len(argv) else ""
    if only is not None:
        keys = [k.strip() for k in only.split(",") if k.strip()]
        return [w for w in ws if any(k in w["cwd"] for k in keys)]
    if "--all" in argv:
        return ws

    print(say("\n올릴 작업공간을 고른다. 번호를 쉼표로, 범위는 1-3, 전부는 a, 그만두려면 빈 줄.",
              "\nPick the workspaces to upload. Numbers separated by commas, ranges like 1-3, "
              "a for all, empty line to stop."))
    print(say("  (여기 있는 말은 서버로 올라간다 — 회사 코드나 비밀이 섞인 곳은 빼는 게 좋다.)\n",
              "  (What you typed in these folders is uploaded to the server. Leave out anything with\n"
              "   company code or secrets in it.)\n"))
    for n, w in enumerate(ws, 1):
        print(f"  {n:2d}) {_fmt(w)}")
    if not sys.stdin.isatty():
        print(say("\n고를 수 없는 곳에서 실행됐다 — --all 또는 --only 로 명시한다. 아무것도 보내지 않았다.",
                  "\nNo terminal to ask in — name them with --all or --only. Nothing was sent."),
              file=sys.stderr)
        return None
    try:
        raw = input(say("\n고른다 > ", "\npick > ")).strip()
    except EOFError:
        raw = ""
    if not raw:
        print(say("아무것도 보내지 않았다.", "Nothing was sent."))
        return None
    if raw.lower() in ("a", "all", "전부"):
        return ws
    picked: list[int] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                picked += list(range(int(a), int(b) + 1))
        elif part.isdigit():
            picked.append(int(part))
    chosen = [ws[i - 1] for i in sorted(set(picked)) if 1 <= i <= len(ws)]
    if not chosen:
        print(say("고른 것이 없다 — 아무것도 보내지 않았다.", "Nothing picked — nothing was sent."))
        return None
    return chosen


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in argv or "-n" in argv
    ws = workspaces(collect_all())
    if not ws:
        print(say("들일 것이 없다 — Claude Code·Codex 세션 기록을 찾지 못했다.",
                  "Nothing to bring in — no Claude Code or Codex session logs were found."))
        return 0
    total = sum(w["instructions"] for w in ws)
    print(say(f"과거 세션 {sum(len(w['sessions']) for w in ws)}개 · 사람이 친 말 {total}건 · 작업공간 {len(ws)}곳",
              f"{sum(len(w['sessions']) for w in ws)} past sessions · {total} things you typed · "
              f"{len(ws)} workspaces"))

    chosen = choose(ws, argv)
    if not chosen:
        return 0
    print(say(f"\n고른 것: {len(chosen)}곳 · 세션 {sum(len(w['sessions']) for w in chosen)} · "
              f"말 {sum(w['instructions'] for w in chosen)}건",
              f"\nPicked: {len(chosen)} folders · {sum(len(w['sessions']) for w in chosen)} sessions · "
              f"{sum(w['instructions'] for w in chosen)} things you typed"))
    if dry:
        for w in chosen:
            print(say(f"  (보내지 않음) {_fmt(w)}", f"  (not sent) {_fmt(w)}"))
        return 0
    from casebook.core.clientmode import ModeError, resolve
    try:
        _mode, url = resolve()          # server 인데 주소가 없으면 멈춘다 — 로컬 DB 로 들이지 않는다
    except ModeError as exc:
        print(f"  {exc}")
        return 1
    if not url:
        print(say("  기록이 이 맥에만 있다 — 서버를 거치지 않고 바로 들인다.",
                  "  Records live on this machine only — bringing them in directly, no server."))
    total_added = total_updated = total_cut = 0
    for w in chosen:
        out = send(url, w["cwd"], w["sessions"]) if url else import_local(w["cwd"], w["sessions"])
        total_added += out.get("added", 0)
        total_updated += out.get("updated", 0)
        got = out.get("stored_instructions")
        cut = w["instructions"] - got if isinstance(got, int) else 0
        total_cut += max(cut, 0)
        print(f"  {w['cwd']} — " +
              say(f"세션 {out.get('added', 0) + out.get('updated', 0)}",
                  f"{out.get('added', 0) + out.get('updated', 0)} sessions") +
              (say(f" · Worktrail 기록이 있는 기간 {cut}건 제외",
                   f" · {cut} skipped, already covered by Worktrail records") if cut > 0 else ""))
    print(say(f"\n들임: 새로 {total_added} · 갱신 {total_updated}",
              f"\nBrought in: {total_added} new · {total_updated} updated") +
          (say(f" · 컷오프로 제외 {total_cut}건", f" · {total_cut} skipped by the cutoff") if total_cut else ""))
    print(say("스레드·주제·결정은 만들지 않았다 — 검색되는 원자료로만 들어갔다.",
              "No threads, topics or decisions were created — this went in as searchable source text only."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
