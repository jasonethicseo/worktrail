#!/usr/bin/env python3
"""읽기 전용 데모 폴더를 만든다 (확장 63호, D14800).

    python tools/demo_snapshot.py --out demo/site --db /data/casebook.db --email you@example.com   # 서버 안에서
    python tools/demo_snapshot.py --out demo/site --base http://127.0.0.1:8788/api                  # 맥의 로컬 창으로
    python tools/demo_snapshot.py --out demo/site                                                   # ~/.casebook/remote-url 의 문으로
    … [--mask deploy/demo_masks.txt] [--every 86400]

Worktrail 화면이 읽는 길을 전부 긁어 경로별 JSON 으로 저장하고, web/worktrail/index.html 을 스냅샷 모드
(CFG.SNAPSHOT)로 바꿔 함께 놓는다. 폴더째 정적으로 올리면 화면이 그대로 뜬다 — 실행 프로그램·DB·로그인 없이.
--out 은 통째로 바꿔치기한다(옆에 만들고 이름을 바꾼다) — 서빙 중에 반쯤 쓰인 파일이 보이지 않게.
--every 초를 주면 그 간격으로 되풀이한다(컨테이너 안의 밤샘 갱신).

긁는 길: /status /review /topic /thread/{id} /case/{id}.  prior(사람이 친 말)는 빼고 빈 목록을 둔다.
--topic 이름 을 주면(여러 번 가능) 그 주제의 스레드만 낸다 — 현황·회고·찾기·저장소 스레드 목록에서도 나머지는 없는 것이 된다.
--skip 450,455 는 그 안에서 스레드 몇 개를 더 뺀다(같은 자리 전부에서).

걸러내기 — 기계가 하는 것: 홈 경로 → ~, 스크래치 경로, *.local 호스트, 사설 IP → 0.0.0.0,
*.syncflo.cloud → host.example, SSM 이름, 12자리 계정 번호, Route53 존, 이 맥의 로그인 이름.
기계가 못 하는 것: 이메일. 발견한 이메일은 out/REVIEW.txt 에 어디서 몇 번인지 적는다 — 사람이 보고 가릴 것을
--mask 파일(한 줄에 하나, # 은 주석, 그 문자열이 통째로 [가림] 으로 바뀐다)에 적어 다시 돌린다.
토큰 꼴(sk-, ghp_, cm_pro_, Bearer …)이 하나라도 남으면 폴더를 쓰지 않고 멈춘다.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import re
import shutil
import sys
import time
import urllib.request
from typing import Callable

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from casebook.adapters.mcp_proxy import remote_url  # noqa: E402
from casebook.adapters.ui_server import CONFIG_TAG, split_remote  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
PAGE = ROOT / "web/worktrail/index.html"
LANDING = ROOT / "web/landing/index.html"      # 소개 — 한국어 데모 폴더의 intro/ 로 간다
TIMEOUT = 60
CASE_KEYS = ("case", "conversation", "evidence", "turns")   # http_api.get_case 가 내보내는 부분집합

# 기계 치환 — 원문의 뜻은 남기고 자리만 지운다
SCRUB = [
    (re.compile(r"/Users/[A-Za-z0-9_.-]+"), "~"),
    (re.compile(r"/private/tmp/claude-\d+/-Users-[A-Za-z0-9_.-]+?(?=/|\b)"), "~/tmp"),   # 스크래치 폴더
    (re.compile(r"/-Users-[A-Za-z0-9_.]+?-"), "/-home-"),                                   # ~/.claude/projects/-Users-… 꼴
    (re.compile(r"\b[A-Za-z0-9-]+\.local(?![A-Za-z0-9-])"), "mac.local"),                   # 호스트 이름
    (re.compile(r"\b(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)(?:\.\d{1,3}){2,3}\b"), "0.0.0.0"),
    (re.compile(r"\b[A-Za-z0-9-]+\.syncflo\.cloud(?![A-Za-z0-9-])"), "host.example"),      # \b 는 뒤에 한글이 오면 안 선다
    (re.compile(r"/casebook/prod/[A-Za-z0-9_-]+"), "/casebook/prod/[가림]"),
    (re.compile(r"(?<!\d)\d{12}(?!\d)"), "[계정]"),                                          # AWS 계정 번호(12자리)
    (re.compile(r"\bZ[0-9A-Z]{12,}\b"), "[존]"),                                             # Route53 존 ID
    (re.compile(r"\bi-0[0-9a-f]{16}\b"), "i-[가림]"),                                         # EC2 인스턴스 ID
    (re.compile(r"(AWS_PROFILE=|--profile[ =])[A-Za-z0-9_-]+"), r"\1[가림]"),                  # AWS 프로필 이름
]
# 이 맥의 로그인 이름 — ls·lsof 출력 같은 데 홀로 남는다. 넉 자 미만이면 오인이 많아 안 건드린다.
LOGIN = os.environ.get("USER") or ""
if len(LOGIN) >= 4:
    SCRUB.append((re.compile(r"(?<![A-Za-z0-9_])" + re.escape(LOGIN) + r"(?![A-Za-z0-9_])"), "me"))
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")
SECRET = re.compile(r"\b(?:sk-|ghp_|gho_|cm_pro_|xox[bp]-|AKIA)[A-Za-z0-9_-]{6,}|[Bb]earer [A-Za-z0-9._-]{16,}")
# 사람 아닌 주소 — 목록에 안 올린다
NOBODY = re.compile(r"@(?:example\.(?:com|test)|x\.test|github\.com|.*\.local)$|^(?:git|noreply|no-reply)@")

Getter = Callable[[str], object]   # "/thread/457" → 그 길이 주는 JSON 값


def http_getter(base: str, headers: dict[str, str]) -> Getter:
    def get(path: str) -> object:
        req = urllib.request.Request(base + "/app" + path, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read())
    return get


def db_getter(db_path: str, email: str) -> Getter:
    """서버 안에서 — HTTP 도 토큰도 없이 같은 코어 함수를 부른다(http_api 의 GET 다섯 개와 같은 것)."""
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    wt = Worktrail(SqliteDB(db_path))
    user = wt.db.get_by("user", "email", email.strip().lower())
    if user is None:
        raise SystemExit(f"그 이메일의 사용자가 없다: {email}")
    uid = user["id"]

    # 이 도구는 토큰도 HTTP 도 거치지 않으므로 서버의 관문을 지나지 않는다 — 세 번째 문이다.
    # 차단된 계정의 기록을 정적 폴더로 내보내면 회수해도 그 사람 기록이 계속 공개된다.
    # 그래서 여기서 한 번 더 본다. 허용 표가 아직 없는 DB(74호 이전)에서는 그냥 지난다.
    from casebook.core import access
    if access.row(wt.db, uid) is not None:
        state = access.state(wt.db, uid)
        if state["state"] != access.STATE_ALLOWED:
            raise SystemExit(f"{email} 은 이 서버에서 차단된 계정이다 — 기록을 데모로 내보내지 않는다.\n"
                             f"  내보내려면 먼저: python -m casebook.adapters.mcp_server allow {email}")

    def get(path: str) -> object:
        if path == "/status":
            return wt.status(uid)
        if path == "/review":
            return wt.review(uid)
        if path == "/topic":
            return {"topics": wt.list_topics(uid)}
        if path.startswith("/thread/"):
            return wt.thread_state(uid, int(path.rsplit("/", 1)[1]))
        if path.startswith("/case/"):
            out = wt.get_case(uid, int(path.rsplit("/", 1)[1]))
            return {k: out[k] for k in CASE_KEYS}
        raise ValueError(path)
    return get


def case_ids(*docs: object) -> list[int]:
    ids: set[int] = set()

    def walk(o: object) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k == "case_id" and isinstance(v, int):
                    ids.add(v)
                walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    for d in docs:
        walk(d)
    return sorted(ids)


def deep(o: object, f: Callable[[str], str]) -> object:
    if isinstance(o, str):
        return f(o)
    if isinstance(o, list):
        return [deep(x, f) for x in o]
    if isinstance(o, dict):
        return {k: deep(v, f) for k, v in o.items()}
    return o


def scrub(text: str, masks: list[str]) -> str:
    for m in masks:
        if m:
            text = text.replace(m, "[가림]")
    for pat, to in SCRUB:
        text = pat.sub(to, text)
    return text


def read_masks(path: str | None) -> list[str]:
    if not path:
        return []
    out = []
    for ln in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(ln)
    return out


def restrict(raw: dict[str, object], topics: list[str], skip: set[int] = frozenset()) -> list[int]:
    """허용한 주제의 스레드만(그중 skip 은 빼고) 남긴다. status·review·topic 을 제자리에서 고치고 남은 id 를 돌려준다.
    없는 주제 이름은 오타일 가능성이 높으니 예외 — 조용히 빈 데모를 내지 않는다."""
    st, rv, tp = raw["/status"], raw["/review"], raw["/topic"]
    names = {t["name"] for t in tp["topics"]}
    missing = [n for n in topics if n not in names]
    if missing:
        raise ValueError(f"그런 주제가 없다: {missing} — 있는 것: {sorted(names)}")
    tp["topics"] = [t for t in tp["topics"] if t["name"] in topics]
    for t in tp["topics"]:
        t["case_ids"] = [i for i in t["case_ids"] if i not in skip]
    keep = {i for t in tp["topics"] for i in t["case_ids"]}
    st["topics"] = [t for t in st["topics"] if t["name"] in topics]
    for t in st["topics"]:
        t["threads"] = [x for x in t["threads"] if x["case_id"] in keep]
        t["open_count"] = sum(x["state"] == "open" for x in t["threads"])      # 머리의 "열림 n · 종료 m" 이 여기서 나온다
        t["closed_count"] = sum(x["state"] != "open" for x in t["threads"])
    st["unassigned"] = []
    st["latest"] = [x for x in st.get("latest", []) if x.get("case_id") in keep]
    st["stream"] = [x for x in st.get("stream", []) if x.get("case_id") in keep]
    st["prior"] = {"sessions": 0, "instructions": 0, "repos": 0, "first_at": None, "last_at": None}
    rows = [x for t in st["topics"] for x in t["threads"]]
    c = st.get("counts", {})
    c.update(open=sum(x["state"] == "open" for x in rows), topics=len(st["topics"]),
             repos=len({x.get("repo") for x in rows if x.get("repo")}),
             mine=sum(x["state"] == "open" and x.get("owner") == "user" for x in rows),
             watching=sum(x["state"] == "open" and x.get("owner") == "watch" for x in rows),
             agent=sum(x["state"] == "open" and x.get("owner") not in (None, "user", "watch") for x in rows),
             unset=sum(x["state"] == "open" and not x.get("owner") for x in rows),
             closed_recent=sum(x["state"] != "open" for x in rows), intake=sum(x.get("origin") == "intake" for x in rows))
    rv["topics"] = [t for t in rv["topics"] if t["name"] in topics]
    for t in rv["topics"]:
        t["case_ids"] = [i for i in t["case_ids"] if i in keep]
    rv["cases"] = {k: v for k, v in rv["cases"].items() if int(k) in keep}
    return sorted(keep)


def build(get: Getter, page: str, out: pathlib.Path, masks: list[str], topics: list[str] | None = None,
          skip: set[int] = frozenset(), lang: str = "") -> dict:
    """긁고 걸러서 out 을 통째로 바꿔친다. 토큰 꼴이 남으면 아무것도 쓰지 않고 예외."""
    if CONFIG_TAG not in page:
        raise ValueError("화면에서 config 스크립트를 찾지 못했다")
    raw: dict[str, object] = {p: get(p) for p in ("/status", "/review", "/topic")}
    if skip and not topics:
        raise ValueError("--skip 은 --topic 과 함께만 — 주제 없이 몇 개만 빼는 데모는 없다")
    ids = restrict(raw, topics, skip) if topics else case_ids(raw["/status"], raw["/review"])
    for i in ids:
        raw[f"/thread/{i}"] = get(f"/thread/{i}")
        raw[f"/case/{i}"] = get(f"/case/{i}")
        rs = raw[f"/thread/{i}"].get("repo_state") if isinstance(raw[f"/thread/{i}"], dict) else None
        if topics and isinstance(rs, dict) and isinstance(rs.get("threads"), list):
            rs["threads"] = [x for x in rs["threads"] if x in ids]       # 저장소의 다른 스레드 목록도 허용한 것만

    # 걸러내기 — 문자열 값마다 한다(원문을 고치는 유일한 자리다). 텍스트째 치환하면 이스케이프가 깨진다.
    clean: dict[str, str] = {}
    emails: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    secrets: list[str] = []
    for p, doc in raw.items():
        t = json.dumps(deep(doc, lambda s: scrub(s, masks)), ensure_ascii=False)
        secrets += [f"{p}: {m[:12]}…" for m in SECRET.findall(t)]
        for e in EMAIL.findall(t):
            if not NOBODY.search(e):
                emails[e][p.split("/")[1]] += 1
        clean[p] = t
    if secrets:
        raise ValueError("토큰 꼴이 남아 있다 — 폴더를 쓰지 않는다:\n  " + "\n  ".join(secrets[:20]))

    tmp = out.with_name(out.name + ".new")
    shutil.rmtree(tmp, ignore_errors=True)
    data = tmp / "data"
    (data / "thread").mkdir(parents=True)
    (data / "case").mkdir(parents=True)
    for p, t in clean.items():
        (data / (p.lstrip("/") + ".json")).write_text(t, encoding="utf-8")
    (data / "prior.json").write_text(json.dumps({"workspaces": [], "totals": {}}), encoding="utf-8")
    total = sum(len(t.encode("utf-8")) for t in clean.values())
    summary = {"generated_at": int(time.time() * 1000), "threads": len(ids), "files": len(clean) + 1, "bytes": total,
               "emails": {e: dict(w) for e, w in emails.items()}, "masks": len(masks)}
    (data / "meta.json").write_text(json.dumps({k: summary[k] for k in ("generated_at", "threads", "files", "bytes")},
                                               ensure_ascii=False), encoding="utf-8")
    # 이 데모가 어느 언어의 기록인지 화면에 알린다. 없으면 방문자의 브라우저 언어로 떨어져,
    # 영어 기록에 한국어 상단바가 얹히는 화면이 된다(레딧에서 올 사람이 보는 것이 정확히 이것이다).
    (tmp / "index.html").write_text(page.replace(CONFIG_TAG, '<script>window.CASEBOOK_CONFIG={SNAPSHOT:"data",LANG:"%s"};</script>' % lang, 1),
                                    encoding="utf-8")
    # 소개(랜딩) — 처음 온 사람이 "이게 무엇이고 왜 필요한가" 부터 읽는 자리. /demo/intro/ 로 나가고
    # 데모 띠가 그리로 잇는다. 한국어로 쓰여 있으므로 한국어 데모에만 둔다(영어판에는 놓지 않는다).
    if lang == "ko" and LANDING.is_file():
        (tmp / "intro").mkdir()
        shutil.copyfile(LANDING, tmp / "intro" / "index.html")
    lines = [f"# 사람이 볼 것 — {time.strftime('%Y-%m-%d %H:%M')}", "",
             "이메일 (기계는 안 가린다. 가릴 것은 --mask 파일에 한 줄씩 적고 다시 돌린다):"]
    for e, where in sorted(emails.items(), key=lambda kv: -sum(kv[1].values())):
        lines.append(f"  {e}  " + " · ".join(f"{k} {n}" for k, n in where.most_common()))
    if not emails:
        lines.append("  없음")
    lines += ["", f"가린 문자열 {len(masks)}개 · 스레드 {len(ids)}개 · 파일 {len(clean) + 1}개"]
    (tmp / "REVIEW.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    old = out.with_name(out.name + ".old")
    shutil.rmtree(old, ignore_errors=True)
    if out.exists():
        os.rename(out, old)
    os.rename(tmp, out)
    shutil.rmtree(old, ignore_errors=True)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="데모 폴더 (index.html + data/). 통째로 바꿔친다")
    ap.add_argument("--db", help="서버 안: sqlite 경로 (--email 과 함께). HTTP 없이 코어를 직접 부른다")
    ap.add_argument("--email", help="--db 와 함께: 누구의 기록인가")
    ap.add_argument("--base", help="로컬 창 프록시(예: http://127.0.0.1:8788/api). 없으면 ~/.casebook/remote-url 의 문")
    ap.add_argument("--mask", help="통째로 [가림] 으로 바꿀 문자열 목록 파일(한 줄에 하나, # 주석)")
    ap.add_argument("--page", default=str(PAGE), help="화면 파일 (기본: 저장소의 web/worktrail/index.html)")
    ap.add_argument("--every", type=int, default=0, help="초 — 주면 그 간격으로 되풀이한다(밤샘 갱신)")
    ap.add_argument("--topic", action="append", help="이 주제의 스레드만 낸다(여러 번 가능). 없으면 전부")
    ap.add_argument("--skip", default="", help="--topic 안에서 더 뺄 스레드 번호, 콤마 구분 (예: 450,455)")
    ap.add_argument("--lang", default="", choices=("", "ko", "en"),
                    help="이 데모가 어느 언어의 기록인가. 화면이 이 값으로 열린다 (비우면 방문자 브라우저 언어)")
    a = ap.parse_args()

    if a.db:
        if not a.email:
            print("--db 에는 --email 이 필요하다", file=sys.stderr)
            return 2
        get = db_getter(a.db, a.email)
    elif a.base:
        get = http_getter(a.base.rstrip("/"), {"Accept": "application/json"})
    else:
        u = remote_url()
        if not u:
            print("원격 문이 없다 — ~/.casebook/remote-url 이나 --base 나 --db 가 필요하다", file=sys.stderr)
            return 2
        origin, token = split_remote(u)
        get = http_getter(origin, {"Authorization": f"Bearer {token}", "Accept": "application/json"})

    out = pathlib.Path(a.out)
    while True:
        try:
            s = build(get, pathlib.Path(a.page).read_text(encoding="utf-8"), out, read_masks(a.mask),
                      [t for t in (a.topic or []) if t.strip()] or None,   # compose 가 빈 값을 넘길 수 있다
                      {int(x) for x in a.skip.split(",") if x.strip()}, a.lang)
            print(f"{time.strftime('%Y-%m-%d %H:%M')} {out}/  스레드 {s['threads']} · 파일 {s['files'] + 1} · "
                  f"{s['bytes'] / 1e6:.1f} MB · 이메일 {len(s['emails'])}종 → {out}/REVIEW.txt", flush=True)
        except Exception as e:   # noqa: BLE001 — 되풀이 모드에서는 한 번 실패로 죽지 않는다
            print(f"{time.strftime('%Y-%m-%d %H:%M')} 실패: {e}", file=sys.stderr, flush=True)
            if not a.every:
                return 1
        if not a.every:
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    raise SystemExit(main())
