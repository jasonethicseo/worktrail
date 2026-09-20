"""데모에 얹는 실제 기록 한 주제 (원티드 출품용, 한국어 데모에만).

tools/demo_seed_data.py 의 가상 커머스 기록 옆에 넷째 주제로 붙는다. 이 제품을 만들며 이 제품에 남긴
실제 스레드 셋을 **다시 쓴** 것이다 — 긁어서 가린 것이 아니다. 그래서 여기 적은 문장만 나간다.

넣은 것: 제목·초점·기계 출력 발췌(시험 결과·실측 숫자)·결정과 이유·커밋 메시지·결과.
넣지 않은 것: 사람이 친 말의 원문, 대회·모집 이야기, 서버 주소·계정·경로, 시험 사용자, 다른 제품 분석.
증거는 제품에서는 바이트 그대로지만 여기서는 줄였다 — 줄인 것은 첫 줄에 "(실제 기록에서 발췌)" 라고 적는다.
숫자와 시험 이름, 커밋 메시지는 원본 그대로다(메시지 앞의 확장 번호만 뗐다).

형식은 demo_seed_data.py 와 같다. 첫 줄은 제목이다: 표시폭 120칸 이내, ' — ' 로 잇지 않고, 기록 번호를 쓰지 않는다.
닫힌 스레드만 싣는다 — 지금 열려 있는 실제 작업은 데모에 내지 않는다(사용자 2026-09-18).
날짜는 사실이라 밀지 않는다(tools/demo_shift.py 는 가상 기록만 민다). 빼려면 demo_seed.py 를 --no-real 로 돌린다.
"""

WORKTRAIL = "jasonethicseo/worktrail"

TOPICS = [
    {
        "name": "실제 제작 기록",
        "at": "2026-09-13 19:00",
        "summary": "이 제품을 만들며 이 제품에 남긴 실제 기록 셋을 줄여 옮겼다. 첫 줄 상한 실측(9/13) → 5분마다 매달리던 호출(9/14) → next 강제(9/15).",
        "conclusion": "규칙 셋이 전부 실제로 막힌 자리에서 나왔다\n\n상한은 영어로 쓴 기록이 통과하지 못한다는 실측에서, 시험 규칙은 시험이 전부 초록인데 같은 증상이 재발한 데서, next 강제는 다음 할 일이 일곱 시간 낡아 있던 스레드에서 나왔다. 셋 다 추측으로 고치지 않고 먼저 쟀고, 잰 것이 증거로 남아 있어 왜 그렇게 정했는지를 지금도 되짚을 수 있다.",
        "threads": ["W1", "W2", "W3"],
    },
]

THREADS: dict[str, dict] = {}

# ── 첫 줄 상한: 60자 → 표시폭 120칸 (실제 2026-09-13) ─────────────────────────

THREADS["W1"] = {
    "repo": WORKTRAIL,
    "start": "2026-09-13 19:10",
    "focus": """첫 줄 상한을 영어로 쓰는 사람도 통과하게 바꾼다

기록의 첫 줄은 사람이 읽는 제목이라 60자를 넘으면 서버가 거절한다. 영어권 시험 사용자를 받기 전에
이 상한이 영어로 쓴 기록을 얼마나 막는지 재고, 단위를 다시 정한다.""",
    "steps": [
        {"t": "2026-09-13 19:26", "turn": {
            "kind": "finding",
            "ev": """(실제 기록에서 발췌) 60자 상한이 영어로 쓴 기록을 얼마나 막나

자연 코퍼스(규칙 도입 이전 focus 444줄, 상한 없이 쓴 글) 영어 통과율:
  120칸 38.9% / 80자 0.6% / 72칸 0.6%
영어 focus 중앙값 128자 → 상한 60 에서 거절
한국어 통과율: width<=120 은 len<=60 의 상위집합이라 새 거절 0건
              width<=72 는 오늘 통과하는 한글 40자 줄(폭 80)을 거절 — 27.0% → 3.9%""",
            "note": """영어는 상한을 80자로 늘려도 0.6%만 통과한다

같은 뜻을 영어로 쓰면 글자 수가 두 배를 넘는다. 상한 없이 쓴 영어 초점 444줄의 중앙값이 128자라, 글자 수로 세는 한 영어권 사용자는 첫 선언에서 막힌다. 표시폭으로 세어 120칸을 주면 한국어는 지금과 똑같이 통과하고 영어는 18~20단어가 들어간다."""}},
        {"t": "2026-09-13 19:35", "decide": {
            "s": "첫 줄 상한을 글자 수 60자에서 표시폭 120칸으로 바꾼다",
            "why": "한국어에 새로 거절되는 문장이 0건이다\n\n한글은 2칸, 영문은 1칸으로 센다. 120칸은 한글 60자와 같은 값이라 지금 통과하는 한국어 기록은 전부 그대로 통과한다. 언어별 표(한국어 60자·영어 80자)는 언어라는 축이 새로 생겨서, 72칸은 지금 통과하는 한글 40자 줄을 거절해서 접었다.",
            "by": "user"}},
        {"t": "2026-09-13 19:46", "commit": {
            "m": "제목 상한을 표시폭 120칸으로: 영어도 한 줄이 들어간다",
            "files": {"casebook/core/headline.py": "import unicodedata\n\nWIDTH_LIMIT = 120\n\n\ndef width(text: str) -> int:\n    \"\"\"터미널 표시폭. 동아시아 넓은 글자는 2칸, 나머지는 1칸, 결합 문자는 0칸.\"\"\"\n    n = 0\n    for ch in text:\n        if unicodedata.combining(ch):\n            continue\n        n += 2 if unicodedata.east_asian_width(ch) in (\"W\", \"F\") else 1\n    return n\n"}}},
        {"t": "2026-09-13 23:04", "turn": {
            "kind": "verified",
            "ev": """(실제 기록에서 발췌)
$ .venv/bin/python -m pytest -q
434 passed, 1 skipped, 1 xfailed in 46.69s

$ 한국어 회귀 재확인
한글 60자: 120칸 통과
한글 61자: 122칸 거절
NFD=62 NFC=62""",
            "note": """한글 60자는 통과하고 61자는 거절된다, 예전과 같은 자리다

자모를 풀어 쓴 글(NFD)도 합친 글(NFC)과 같은 폭으로 센다. 거절문은 영어로 바꿨다. 그 글을 읽고 고쳐 쓰는 것은 에이전트이기 때문이다."""}},
        {"t": "2026-09-13 23:10", "close": """첫 줄 상한을 표시폭 120칸으로 바꿨고 한국어는 그대로다

60자 상한에서는 영어로 쓴 초점이 사실상 전부 거절됐다. 단위를 표시폭으로 바꿔 한글 60자와 같은 120칸을 주자 한국어는 새로 거절되는 문장 없이 영어가 한 줄 들어간다."""},
    ],
}

# ── 도구 호출이 5분마다 매달린다 (실제 2026-09-14) ───────────────────────────

THREADS["W2"] = {
    "repo": WORKTRAIL,
    "start": "2026-09-14 10:40",
    "focus": """기록 도구 호출이 5분마다 매달리던 것을 고친다

로그인 방식을 OAuth 로 바꾼 뒤부터 기록 도구 호출이 가끔 답 없이 매달린다. 에이전트는 30초를 기다리다
연결을 끊고, 그 턴의 기록은 사라진다. 원인을 찾아 고치고, 고쳐졌다는 것을 증상으로 확인한다.""",
    "steps": [
        {"t": "2026-09-14 11:00", "turn": {
            "kind": "finding",
            "ev": """(실제 기록에서 발췌) 첫 진단 때 잰 것
add_evidence 가 330초 넘게 running
같은 요청을 서버에 직접 보내면 0.03초

맨 httpx AsyncClient: timeout 5초, follow_redirects 꺼짐
SDK 의 create_mcp_http_client: connect 30초 · read 300초, redirect 를 따라감""",
            "note": """서버는 멀쩡하고 프록시 쪽 HTTP 클라이언트의 기본값이 달랐다

로그인 머리를 붙이려고 HTTP 클라이언트를 직접 만들면서 SDK 가 주던 타임아웃 기본값을 잃었다. 읽기 타임아웃 5초가 원인이라고 보고 SDK 의 클라이언트로 되돌린다."""}},
        {"t": "2026-09-14 11:22", "commit": {
            "m": "도구 호출이 매달리던 이유: 맨 httpx 클라이언트가 SDK 기본값을 지웠다",
            "files": {"casebook/adapters/mcp_proxy.py": "from mcp.shared._httpx_utils import create_mcp_http_client\n\n# 맨 AsyncClient 는 timeout 5초에 follow_redirects 가 꺼져 있다. SDK 것으로 만든다.\nasync def connect(url, headers):\n    async with create_mcp_http_client(headers=headers) as client:\n        ...\n",
                      "tests/test_device_login.py": "def test_맨_AsyncClient_를_만들지_않는다():\n    ...\n"}}},
        {"t": "2026-09-14 15:00", "turn": {
            "kind": "finding",
            "ev": """(실제 기록에서 발췌) 에이전트 쪽 MCP 로그를 "붙은 뒤 몇 초" 로 가른 측정

OAuth 이전 성공 호출 968건 — 붙은 뒤 최대 140075초
OAuth 이후 성공 호출 12건 — 붙은 뒤 최대 283초
OAuth 이후 매달림 5건 — 붙은 뒤 최소 362초

  매달림: 붙은 뒤    362초  add_evidence
  매달림: 붙은 뒤    421초  decide
  매달림: 붙은 뒤    515초  note_turn
  매달림: 붙은 뒤   1721초  add_evidence
  매달림: 붙은 뒤   2221초  note_turn

$ access token 을 디코드
iat 12:48:52 exp 12:53:52 TTL초 300""",
            "note": """첫 진단이 틀렸다, 매달림은 전부 붙은 뒤 300초를 넘겨서 났다

첫 수정은 시험 511개가 전부 초록이었는데 설치 71분 뒤 같은 증상이 재발했다. 호출을 붙은 뒤 몇 초로 갈라 보니 성공은 전부 283초 안, 매달림은 전부 362초 뒤다. access token 수명이 300초인데 프록시는 붙을 때 받은 토큰을 몇 시간 동안 그대로 들고 있었다."""}},
        {"t": "2026-09-14 15:30", "turn": {
            "kind": "finding",
            "ev": """(실제 기록에서 발췌) 매달리는 자리를 재현 — 연결은 부모 태스크, 호출은 자식 태스크

[A] connect OK 0.35s
[B] 예외 0.00s RuntimeError: Attempted to exit a cancel scope that isn't the current tasks's current cancel scope
[A] 태스크그룹이 터졌다: ExceptionGroup unhandled errors in a TaskGroup (1 sub-exception)

RuntimeError: Attempted to exit cancel scope in a different task than it was entered in""",
            "note": """만료 뒤 다시 붙는 길이 남의 태스크에서 연결을 닫아 답 없이 죽는다

토큰이 만료되면 호출하던 태스크가 재접속을 했는데, 연결은 그것을 연 태스크만 닫을 수 있다. 다른 태스크에서 닫으면 취소 범위가 깨지고 그 태스크는 오류도 못 돌려준 채 죽는다. 에이전트가 영원히 기다린 이유가 이것이다."""}},
        {"t": "2026-09-14 16:21", "commit": {
            "m": "5분마다 매달리던 진짜 이유: 토큰을 박아 두고, 남의 태스크에서 닫았다",
            "files": {"casebook/adapters/device_login.py": "def bearer():\n    \"\"\"요청마다 그때 유효한 토큰을 다는 httpx 인증기.\n\n    access token 수명이 300초다. 프록시는 몇 시간을 사는데 붙을 때 받은 머리를 그대로 들고 있으면\n    5분 뒤부터 서버가 전부 401 로 돌려보낸다.\"\"\"\n    ...\n",
                      "tests/test_mcp_remote.py": "def test_호출_태스크에서_재접속해도_매달리지_않는다(): ...\ndef test_호스트는_반드시_답을_받는다(): ...\ndef test_재접속이_매달려도_답은_온다(): ...\ndef test_붙다_실패해도_다음_기회에_회복한다(): ...\n"}}},
        {"t": "2026-09-14 16:40", "turn": {
            "kind": "verified",
            "ev": """(실제 기록에서 발췌) 증상 자체를 시계로 넘은 것 — 붙은 뒤 초 단위

  붙었다 (토큰 수명 300초)
  붙은 뒤    30초  list_cases  ok     0.08s  ✓
  붙은 뒤   150초  list_cases  ok     0.10s  ✓
  붙은 뒤   290초  list_cases  ok     0.57s  ✓
  붙은 뒤   320초  list_cases  ok     0.09s  ✓
  붙은 뒤   400초  list_cases  ok     0.10s  ✓
  붙은 뒤   620초  list_cases  ok     0.63s  ✓
판정: 통과 ✓ — 300초 경계가 사라졌다

$ 고치기 전 코드에 새 시험 넷을 걸어 본 것
FAILED tests/test_mcp_remote.py::test_호출_태스크에서_재접속해도_매달리지_않는다 - TimeoutError
FAILED tests/test_mcp_remote.py::test_호스트는_반드시_답을_받는다 - TimeoutError
FAILED tests/test_mcp_remote.py::test_재접속이_매달려도_답은_온다 - TimeoutError
FAILED tests/test_mcp_remote.py::test_붙다_실패해도_다음_기회에_회복한다 - RuntimeError: Attempted to exit cancel scope in a different task""",
            "note": """300초 경계가 사라졌고, 새 시험 넷은 고치기 전 코드에서 실패한다

토큰을 요청마다 달고 연결은 연 태스크만 닫게 고친 뒤, 붙은 뒤 620초까지 호출이 전부 1초 안에 돌아온다. 이번에는 시험이 증상을 본다. 고치기 전 코드에 걸면 셋이 TimeoutError, 하나가 취소 범위 오류로 실패한다."""}},
        {"t": "2026-09-14 16:50", "constrain": {
            "s": "고쳤다고 말하려면 증상을 보는 시험이 있어야 한다\n\n고친 코드의 모양을 지키는 시험만으로는 부족하다. 고치기 전 코드에 걸었을 때 실제로 실패하는 시험을 함께 두고, 실패하는 것을 눈으로 확인한 뒤 커밋한다.",
            "why": "첫 수정은 시험 511개가 초록인데도 증상을 못 고쳤다\n\n첫 수정에 붙인 시험 둘은 고친 모양을 지킬 뿐 증상이 사라졌는지 보지 않았다. 그래서 진단이 틀렸는데도 전부 초록이었고 설치 71분 뒤 같은 증상이 재발했다.",
            "by": "agent", "scope": "repo"}},
        {"t": "2026-09-14 16:52", "constrain": {
            "s": "오래 사는 연결에 토큰을 박지 않는다: 요청마다 단다",
            "why": "토큰 수명은 300초인데 프록시는 몇 시간을 산다\n\n붙을 때 받은 토큰을 연결에 박아 두자 300초를 넘긴 호출이 전부 매달렸다. 경로 토큰을 쓰던 때에는 같은 연결이 39시간까지 멀쩡했다.",
            "by": "agent"}},
        {"t": "2026-09-14 17:00", "close": """토큰을 요청마다 달고 연결은 연 태스크만 닫게 해서 매달림을 없앴다

첫 진단(HTTP 클라이언트 타임아웃)은 틀렸고 시험이 그것을 잡지 못했다. 호출을 붙은 뒤 몇 초로 갈라 재자 300초 경계가 드러났고, 원인은 수명 300초짜리 토큰을 연결에 박아 둔 것과 남의 태스크에서 연결을 닫은 것 둘이었다. 증상을 보는 시험 넷을 붙였다."""},
    ],
}

# ── next 가 낡지 않게 강제한다 (실제 2026-09-15) ─────────────────────────────

THREADS["W3"] = {
    "repo": WORKTRAIL,
    "start": "2026-09-15 00:42",
    "focus": """next 가 낡지 않게 강제한다

화면의 NEXT 줄은 사람이 읽고 움직이는 줄이다. 그런데 한 스레드에서 NEXT 가 SO FAR 보다 한참 낡은 채
남아 있는 것이 보였다. 왜 그렇게 됐는지 보고, 에이전트의 성실함이 아니라 서버가 지키게 만든다.""",
    "steps": [
        {"t": "2026-09-15 00:50", "turn": {
            "kind": "finding",
            "ev": """(실제 기록에서 발췌) 그 자리 — 한 스레드의 실제 값

next  declared_at 1789373285080
      "사용자가 배포를 승인하고, 그다음 승인 버튼을 만들지 정한다"
마지막 노트(SO FAR)  turn 12  at 1789400439339
그 사이에 쌓인 턴 10개. next 는 그 열 턴 동안 한 번도 다시 선언되지 않았고, 내용은 전부 끝난 일이다.

$ focus 에는 이미 강제가 있다 (note_turn 안내문)
Refused once 15 turns have piled up since the thread's focus was last declared: re-declare the
focus if it is still the same work, or open_thread for the new subject.

$ next 에는 그런 강제가 없다 (declare 안내문)
next REQUIRES owner — whose turn it is: "user" (the engineer must act), "watch" (nothing to do —
observing only), or an agent name such as "codex" / "claude".
→ owner 는 강제하지만 "언제 다시 선언해야 하는가" 는 아무 데도 없다.""",
            "note": """next 가 마지막 노트보다 7시간, 턴 10개만큼 낡아 있었다

사람이 읽고 움직이는 줄인데 내용은 이미 끝난 일이었다. 초점에는 15턴 상한이 있지만 next 에는 누구 차례인지를 요구하는 것 말고 아무 강제가 없다. 다음 세션이 이 줄을 읽으면 이미 끝난 일을 하러 간다. 이 제품이 막으려는 바로 그 실패다."""}},
        {"t": "2026-09-15 00:54", "decide": {
            "s": "턴마다 next 를 다시 말한다: 노트가 그것 없이는 거절된다",
            "why": "사람이 읽고 움직이는 줄에 강제가 없었다\n\n노트를 남길 때 next 와 누구 차례인지를 함께 받고, 없으면 서버가 거절한다. 같은 글이면 새 줄을 쓰지 않으므로 실제로는 매 턴 재확인이 되고 바뀐 때만 기록이 는다. 도구 호출을 늘리지 않고도 매 턴 다시 생각하게 만드는 자리라서 노트에 실었다. 화면은 이미 쌓인 옛 기록을 위해 next 가 마지막 노트보다 낡았으면 표시한다.",
            "by": "user"}},
        {"t": "2026-09-15 01:09", "commit": {
            "m": "턴마다 next 를 다시 말하게 한다",
            "files": {"tests/test_focus_cap.py": "def test_next_없이는_턴을_기록하지_못한다(wt, tmp_path): ...\ndef test_같은_next_는_새_줄을_쓰지_않는다(wt, tmp_path): ...\ndef test_next_는_그_턴_뒤에_선다(wt, tmp_path): ...\ndef test_화면이_낡은_next_를_말해_준다(): ...\n"}}},
        {"t": "2026-09-15 01:20", "turn": {
            "kind": "verified",
            "ev": """(실제 기록에서 발췌) 이 커밋이 붙인 시험
tests/test_focus_cap.py::test_next_없이는_턴을_기록하지_못한다
tests/test_focus_cap.py::test_같은_next_는_새_줄을_쓰지_않는다
tests/test_focus_cap.py::test_next_는_그_턴_뒤에_선다
tests/test_focus_cap.py::test_화면이_낡은_next_를_말해_준다""",
            "note": """next 없는 노트는 거절되고, 같은 글은 새 줄을 만들지 않는다

같은 글이면 기록이 늘지 않으므로 회고와 타임라인이 거의 같은 next 로 시끄러워지지 않는다. 화면은 옛 기록의 낡은 next 를 표시한다."""}},
        {"t": "2026-09-15 01:30", "close": """노트마다 next 를 함께 받게 했고 낡은 next 는 화면이 표시한다

next 가 일곱 시간 낡은 채 남은 스레드에서 시작했다. 이제 노트를 남기려면 다음에 무슨 일이 누구 차례인지를 함께 말해야 하고, 같은 글이면 기록이 늘지 않는다."""},
    ],
}
