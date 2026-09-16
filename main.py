"""서버 진입점 — Xano 대체 실행형. 로컬과 서빙(컨테이너)이 같은 파일이다.

    OPENAI_API_KEY=… .venv/bin/python main.py [--db casebook.db] [--port 8787]

환경변수 (인자보다 낮은 우선순위):
    CASEBOOK_DB            sqlite 경로 (기본 casebook.db · 컨테이너는 /data/casebook.db)
    CASEBOOK_HOST          바인드 주소 (기본 127.0.0.1 · 컨테이너는 0.0.0.0)
    CASEBOOK_PORT          포트 (기본 8787)
    CASEBOOK_WORKERS       드레이너 스레드 수 (기본 4)
    CASEBOOK_CORS_ORIGINS  프론트 origin 허용목록, 콤마 구분 (기본 * — 로컬 개발)
    CASEBOOK_EFFORT        답변 호출 reasoning effort 기본값 (none/low/medium)
    CASEBOOK_INVITE_CODES  가입 초대 코드, 콤마 구분 (확장 2호). 비우면 열린 가입 — 로컬 전용
    CASEBOOK_WEB_SEARCH    답변 호출 내장 web_search 기본값 0/1 (확장 3호). 턴 요청의 web 이 우선

프론트 연결(config 만 바꾼다):
    web/app/config.js            APP_BASE:  http://localhost:8787/app
                                 AUTH_BASE: http://localhost:8787/auth
    web/tickets/tickets.config.js TICKETS_BASE: http://localhost:8787/tickets (+ 위 둘)

Xano background task 의 자리는 드레이너 스레드 풀이다 — 엔드포인트는 즉시 응답하고
(202 의미) 워커는 뒤에서 돈다. 스레드가 여럿인 이유: 답변 호출은 p50 14초·꼬리 70초라
(stress50 실측) 한 줄로 돌리면 한 사용자의 긴 턴이 다른 사용자 전부를 막는다. 잡 하나는
한 스레드만 집고(큐 락), 같은 턴의 중복 처리는 워커 진입 클레임이 막는다(불변식 14).
재시도는 없다(불변식 7). 프로세스가 죽으면 큐도 죽는다 — 그 턴은 pending 으로 남고
사용자가 Re-run(recovery)으로 처리한다(불변식 8: 자동 복구 없음). AWS 로 가면 이
풀이 SQS+Lambda 가 되고, 진입 클레임이 at-least-once 중복 전달을 막는다(인계 §5).
"""
from __future__ import annotations

import argparse
import logging
import os
import threading
import time

from casebook.adapters.http_api import create_app
from casebook.adapters.openai_llm import OpenAILLM
from casebook.adapters.no_search import NoSearch
from casebook.core.app import Casebook
from casebook.core.db import SqliteDB
from casebook.core.tickets import Tickets

log = logging.getLogger("casebook")


def check_api_key(key: str | None) -> str:
    """빈 키뿐 아니라 자리표시자(예: 문서의 '…')도 기동 때 거른다.
    2026-09-05: 'OPENAI_API_KEY=… main.py' 를 그대로 붙여 띄운 서버가 모든 모델 호출을
    "'latin-1' codec can't encode character '\\u2026'" 로 조용히 실패시켰다(Record 생성·intake 첫 답변 전부)."""
    k = (key or "").strip()
    if not k:
        raise SystemExit("OPENAI_API_KEY 가 비어 있다 — 환경변수로 넘겨라")
    if not k.isascii() or len(k) < 20:
        raise SystemExit(f"OPENAI_API_KEY 가 자리표시자로 보인다({k[:3]!r}…, {len(k)}자) — 실제 키를 넘겨라")
    return k


def build(db_path: str) -> tuple[Casebook, Tickets]:
    check_api_key(os.environ.get("OPENAI_API_KEY"))
    db = SqliteDB(db_path)
    invites = frozenset(c.strip() for c in os.environ.get("CASEBOOK_INVITE_CODES", "").split(",") if c.strip())
    casebook = Casebook(
        db=db, llm=OpenAILLM(), search=NoSearch(),  # 검색 백엔드는 미정 — README
        default_effort=os.environ.get("CASEBOOK_EFFORT", "none"),
        invite_codes=invites,
        default_web=os.environ.get("CASEBOOK_WEB_SEARCH", "0").strip() in ("1", "true", "yes"),
    )
    return casebook, Tickets(db)


def start_drainers(casebook: Casebook, count: int) -> list[threading.Thread]:
    def _drain(name: str) -> None:
        while True:
            try:
                if not casebook.run_one():
                    time.sleep(0.05)
            except Exception:  # noqa: BLE001 — 워커 하나의 예외가 드레이너를 죽이면 안 된다
                # 워커는 모델·검색 실패를 스스로 원장에 남기고 정상 반환한다. 여기까지
                # 올라오는 건 DB 오류 같은 예상 밖 실패다 — 로그에 남기고 다음 잡으로 간다.
                # 그 턴은 pending 으로 남고, 사용자가 Re-run 으로 처리한다(불변식 8).
                log.exception("worker crashed (%s) — turn stays pending, user may Re-run", name)

    threads = []
    for i in range(count):
        t = threading.Thread(target=_drain, args=(f"drainer-{i}",), daemon=True, name=f"drainer-{i}")
        t.start()
        threads.append(t)
    return threads


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=os.environ.get("CASEBOOK_DB", "casebook.db"))
    parser.add_argument("--host", default=os.environ.get("CASEBOOK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CASEBOOK_PORT", "8787")))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("CASEBOOK_WORKERS", "4")))
    args = parser.parse_args()

    cors = os.environ.get("CASEBOOK_CORS_ORIGINS", "").strip()
    cors_origins = [o.strip() for o in cors.split(",") if o.strip()] or None
    if cors_origins is None and args.host != "127.0.0.1":
        log.warning("CASEBOOK_CORS_ORIGINS 가 비어 있다 — 모든 origin 을 허용한다(서빙에 부적합)")

    casebook, tickets = build(args.db)
    if not casebook.invite_codes and args.host != "127.0.0.1":
        log.warning("CASEBOOK_INVITE_CODES 가 비어 있다 — 누구나 가입해 OpenAI 키를 쓸 수 있다(서빙에 부적합)")
    start_drainers(casebook, max(1, args.workers))
    log.info("db=%s host=%s port=%s workers=%s cors=%s",
             args.db, args.host, args.port, args.workers, cors_origins or "*")

    import uvicorn

    # 이용 허용 검사 (인계서 4절). 기본은 꺼 둔다 — 켜는 것은 운영 적용이고, 그 판단은 사용자 몫이다.
    # 켤 때 표가 처음 생기면 그때 있던 사용자를 허용으로 적는다(D15081).
    enforce_access = os.environ.get("CASEBOOK_ENFORCE_ACCESS", "").strip() in ("1", "true", "yes")
    if enforce_access:
        from casebook.core import access
        n = access.grandfather(casebook.db)
        log.info("access enforcement on (grandfathered %s existing users)", n)

    uvicorn.run(create_app(casebook, tickets, cors_origins=cors_origins,
                           enforce_access=enforce_access),
                host=args.host, port=args.port)


if __name__ == "__main__":
    main()
