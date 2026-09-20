#!/usr/bin/env python3
"""가상 기록만 든 데모용 DB 를 짓는다 (원티드 출품 데모).

    python tools/demo_seed.py --out /tmp/demo.db
    python tools/demo_snapshot.py --out demo/site --db /tmp/demo.db --email demo@example.com

실제 기록을 가려서 내는 대신, 지어낸 팀의 지어낸 저장소 기록을 제품 코어 API 로 그대로 쌓는다.
화면(web/worktrail/index.html)도 스냅샷 도구도 한 줄 고치지 않는다 — DB 만 다르다.
가릴 것을 고르는 일(REVIEW.txt·demo_masks.txt)이 통째로 없어진다.

어떻게: 시계(SqliteDB.now_ms)를 시드가 적은 시각으로 돌리고, 진짜 git 저장소를 임시로 만들어
커밋을 얹는다 — 그래서 스레드의 저장소 식별·커밋 연결·anchor 가 실제와 같은 길로 생긴다.
마지막에 임시 경로를 보기 좋은 경로로 바꿔 DB 를 다시 쓴다.
"""
from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import zoneinfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from casebook.core.db import SqliteDB          # noqa: E402
from casebook.core.threads import ensure_change  # noqa: E402
from casebook.core.worktrail import Worktrail   # noqa: E402
from tools.demo_shift import days_to_shift, shift_all  # noqa: E402

SEOUL = zoneinfo.ZoneInfo("Asia/Seoul")
DISPLAY_HOME = "~/work"      # 기록에 남을 작업 폴더의 겉모습. 스냅샷의 SCRUB 이 홈 경로를 ~ 로 바꾸는 것과 같은 자리.
EMAIL = "demo@example.com"
NAME = "데모"


class Clock:
    """가상 시계 — 시드가 적은 시각으로 기록이 찍힌다.

    시드의 시각은 한국 시간이다. 짓는 기계의 시간대로 읽으면 안 된다 — 서버 컨테이너는 UTC 라 "9/14 16:50" 이
    화면(KST)에서 "9/15 01:50" 으로 보였다(실측 2026-09-18, 실제 제작 기록의 시각까지 틀리게 나왔다)."""
    now = 0

    @classmethod
    def set(cls, stamp: str) -> int:
        at = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=SEOUL)
        cls.now = int(at.timestamp()) * 1000
        return cls.now

    @classmethod
    def bump(cls, ms: int = 1000) -> int:
        cls.now += ms
        return cls.now


def git(cwd: str, *args: str, at: int | None = None) -> str:
    env = dict(os.environ)
    env.update(GIT_AUTHOR_NAME="데모", GIT_AUTHOR_EMAIL=EMAIL,
               GIT_COMMITTER_NAME="데모", GIT_COMMITTER_EMAIL=EMAIL)
    if at is not None:
        stamp = f"{at // 1000} +0900"
        env.update(GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
    r = subprocess.run(("git", *args), cwd=cwd, env=env, capture_output=True, text=True)
    return r.stdout.strip()


def make_repo(root: pathlib.Path, name: str) -> str:
    """origin 이 붙은 진짜 git 저장소 — identify() 가 host/path 로 알아본다."""
    wt = root / name.split("/")[-1]
    wt.mkdir(parents=True)
    git(str(wt), "init", "-q", "-b", "main")
    git(str(wt), "remote", "add", "origin", f"https://github.com/{name}.git")
    (wt / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    git(str(wt), "add", "-A")
    git(str(wt), "commit", "-q", "-m", "첫 커밋", at=Clock.now)
    return str(wt)


def path_pairs(real: str, shown: str) -> list[tuple[str, str]]:
    """치환할 경로 짝 — 심볼릭 링크를 푼 것까지 함께. 긴 것부터 바꿔야 조각이 남지 않는다."""
    both = {real, os.path.realpath(real)}
    return sorted(((p, shown) for p in both), key=lambda x: -len(x[0]))


def commit(wt: str, message: str, files: dict[str, str], at: int) -> None:
    for rel, text in files.items():
        p = pathlib.Path(wt) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", message, at=at)


def rewrite_paths(db_path: str, pairs: list[tuple[str, str]]) -> None:
    """임시 작업 폴더 경로를 보기 좋은 경로로 바꿔 DB 를 다시 쓴다."""
    src = sqlite3.connect(db_path)
    text = "\n".join(src.iterdump())
    src.close()
    for real, shown in sorted(pairs, key=lambda x: -len(x[0])):
        text = text.replace(real, shown)
    os.remove(db_path)
    dst = sqlite3.connect(db_path)
    dst.executescript(text)
    dst.commit()
    dst.close()


def now_seoul(at: datetime.datetime | None = None) -> datetime.datetime:
    """짓는 순간을 한국 시간으로 (시드의 시각이 한국 시간이므로).

    서버 컨테이너는 UTC 로 돈다. 거기서 naive datetime.now() 로 재면 한국보다 9시간 뒤처져서,
    밀 날수가 하루 늦게 7일로 넘어간다(실측 2026-09-19 16:05 재빌드: 한국 기준 7일인데 0 이 나왔다)."""
    return (at or datetime.datetime.now(datetime.timezone.utc)).astimezone(SEOUL).replace(tzinfo=None)


def seed(db_path: str, work: pathlib.Path, lang: str = "ko", real: bool = True,
         now: datetime.datetime | None = None, shift: bool = True) -> dict:
    # 시드 데이터는 따로 산다. 언어마다 한 벌씩 — 번역이 아니라 그 언어의 팀이 적었을 기록이다.
    # 원티드에는 한국어 데모를, 레딧에는 영어 데모를 낸다(D15188).
    if lang == "en":
        from tools.demo_seed_data_en import TOPICS, THREADS   # noqa: PLC0415
    else:
        from tools.demo_seed_data import TOPICS, THREADS      # noqa: PLC0415
    # 가상 기록은 짓는 날에 맞춰 통째로 민다(7의 배수 — 요일이 남는다). 심사 기간에 열어도 열린 스레드가
    # 몇 주 전에 멈춘 일로 보이지 않게. 글 안의 날짜도 같이 밀린다(tools/demo_shift.py).
    moved = days_to_shift(THREADS, now or now_seoul()) if shift else 0
    TOPICS, THREADS = shift_all(TOPICS, moved), shift_all(THREADS, moved)
    start_stamp = shift_all("2026-08-17 09:00", moved)
    if lang != "en":
        if real:
            # 한국어 데모에만 실제 기록을 다시 쓴 주제 하나를 얹는다(D16604). 실제 기록의 날짜는 사실이라 밀지 않는다.
            from tools.demo_seed_data_real import TOPICS as REAL_TOPICS, THREADS as REAL_THREADS  # noqa: PLC0415
            TOPICS, THREADS = [*TOPICS, *REAL_TOPICS], {**THREADS, **REAL_THREADS}

    SqliteDB.now_ms = staticmethod(lambda: Clock.now)     # 모든 created_at 이 가상 시계를 탄다
    Clock.set(start_stamp)
    wt = Worktrail(SqliteDB(db_path))
    ensure_change(wt.db)
    wt.signup(NAME, EMAIL, "demo-password-not-used")
    uid = wt.db.get_by("user", "email", EMAIL)["id"]

    repos: dict[str, str] = {}
    cases: dict[str, int] = {}
    made: list[tuple[str, str]] = []

    for key, th in THREADS.items():
        Clock.set(th["start"])
        name = th["repo"]
        if name not in repos:
            repos[name] = make_repo(work, name)
            made += path_pairs(repos[name], f"{DISPLAY_HOME}/{name.split('/')[-1]}")
        path = repos[name]
        r = wt.open_thread(uid, th["focus"], path, authority=th.get("by", "user"))
        cid = r["case_id"]
        cases[key] = cid
        evs: list[int] = []          # 이 스레드가 쌓은 증거 번호 — 결정·기각이 뒤를 가리킨다

        for step in th["steps"]:
            Clock.set(step["t"])
            if "turn" in step:
                s = step["turn"]
                out = wt.external_turn(uid, cid, s["ev"], f"{key}-{step['t']}", note=s["note"],
                                       note_kind=s.get("kind", "finding"), source={"via": "mcp"})
                if out.get("evidence_id"):
                    evs.append(out["evidence_id"])
                wt.run_workers()
            elif "decide" in step:
                s = step["decide"]
                wt.decide(uid, cid, s["s"], s.get("why"), evidence_ids=evs[-s.get("ev", 1):] or None,
                          authority=s.get("by", "user"), scope=s.get("scope", "thread"))
            elif "constrain" in step:
                s = step["constrain"]
                wt.constrain(uid, cid, s["s"], s.get("why"), authority=s.get("by", "user"), scope=s.get("scope", "thread"))
            elif "rule_out" in step:
                s = step["rule_out"]
                wt.rule_out(uid, cid, s["s"], s["scope"], evidence_ids=evs[-s.get("ev", 1):])
            elif "define" in step:
                s = step["define"]
                wt.define(uid, cid, s["term"], s["meaning"], authority=s.get("by", "agent"))
            elif "declare" in step:
                s = step["declare"]
                wt.declare(uid, cid, s["kind"], s["s"], authority=s.get("by", "user"), owner=s.get("owner"))
            elif "commit" in step:
                s = step["commit"]
                commit(path, s["m"], s["files"], Clock.now)
                wt.record_commit(uid, path)
            elif "close" in step:
                wt.close_thread(uid, cid, step["close"])
            Clock.bump()

    for t in TOPICS:
        Clock.set(t["at"])
        topic = wt.create_topic(uid, t["name"], t.get("summary", ""))
        ids = [cases[k] for k in t["threads"] if k in cases]
        wt.assign_topic(uid, topic["topic_id"], ids)
        if t.get("conclusion"):
            wt.conclude_topic(uid, topic["topic_id"], t["conclusion"])

    wt.run_workers()
    counts = {"threads": len(cases), "topics": len(TOPICS), "repos": len(repos), "moved": moved}
    wt.db.conn.close()
    rewrite_paths(db_path, made)
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="만들 sqlite 경로 (있으면 지우고 다시 만든다)")
    ap.add_argument("--lang", default="ko", choices=("ko", "en"),
                    help="기록의 언어 (기본 ko). 원티드는 ko, 레딧은 en")
    ap.add_argument("--no-shift", action="store_true", help="가상 기록의 날짜를 밀지 않는다 (시드에 적힌 날짜 그대로)")
    ap.add_argument("--now", default="", help="짓는 시각을 이것으로 본다: 'YYYY-MM-DD HH:MM' (시험·미리보기용)")
    ap.add_argument("--no-real", action="store_true",
                    help="한국어 데모에서 실제 기록을 다시 쓴 주제(demo_seed_data_real.py)를 뺀다")
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    work = pathlib.Path(tempfile.mkdtemp(prefix="demo-seed-"))
    try:
        now = datetime.datetime.strptime(a.now, "%Y-%m-%d %H:%M") if a.now else None
        c = seed(str(out), work, a.lang, real=not a.no_real, now=now, shift=not a.no_shift)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"{out}  [{a.lang}] 스레드 {c['threads']} · 주제 {c['topics']} · 저장소 {c['repos']}  ({out.stat().st_size / 1e6:.1f} MB) · 가상 기록 {c['moved']}일 밈")
    print(f"다음: python tools/demo_snapshot.py --out demo/site --db {out} --email {EMAIL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
