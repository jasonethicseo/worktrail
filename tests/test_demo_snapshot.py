"""확장 63호 — 읽기 전용 데모 폴더(D14800). 보장할 것:
(1) 기계 치환은 홈 경로·스크래치 경로·호스트 이름·사설 IP·syncflo 호스트·SSM 이름·계정 번호를 지우고, 뜻은 남긴다
(2) 한글이 바로 뒤에 붙어도 호스트 이름이 잡힌다(\\b 는 한글 앞에서 안 선다)
(3) --mask 문자열은 통째로 [가림] 이 된다, 치환은 문자열 값 안에서만 일어난다(키·숫자는 그대로)
(4) 화면은 CFG.SNAPSHOT 이 있으면 서버 대신 경로별 JSON 을 읽고, 쓰기·로그인·과거 기록을 끈다"""
from __future__ import annotations

import importlib.util
import json

import pytest
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("demo_snapshot", ROOT / "tools/demo_snapshot.py")
ds = importlib.util.module_from_spec(spec); spec.loader.exec_module(ds)   # type: ignore[union-attr]
PAGE = (ROOT / "web/worktrail/index.html").read_text(encoding="utf-8")


def test_기계_치환():
    s = ds.scrub("cd /Users/kim/Desktop/x; ls /private/tmp/claude-501/-Users-kim-Desktop-x/scratchpad; "
                 "host kim-MacBookPro.local 172.18.0.27 10.0.1.5 casebook-api.syncflo.cloud에 올렸다 "
                 "/casebook/prod/openai-api-key 계정 123456789012 존 Z0123456789ABC 시각 1789167475559", [])
    assert "/Users/" not in s and "-Users-kim" not in s and "MacBookPro" not in s
    assert "172.18" not in s and "10.0.1.5" not in s and "syncflo.cloud" not in s          # (1)
    assert "host.example에 올렸다" in s                                                      # (2)
    assert "openai-api-key" not in s and "123456789012" not in s and "Z0123456789ABC" not in s
    assert "1789167475559" in s                                                             # ms 시각(13자리)은 그대로


def test_mask_와_deep():
    doc = {"case_id": 457, "text": "kim@naver.com 가 보냈다 /Users/kim/a", "n": [1, {"e": "kim@naver.com"}]}
    out = ds.deep(doc, lambda t: ds.scrub(t, ["kim@naver.com"]))
    assert out == {"case_id": 457, "text": "[가림] 가 보냈다 ~/a", "n": [1, {"e": "[가림]"}]}                  # (3)


def test_화면의_스냅샷_모드():
    assert 'const SNAP = CFG.SNAPSHOT || ""' in PAGE
    assert 'if (SNAP) throw new Error("읽기 전용 데모라 바꿀 수 없다")' in PAGE            # 쓰기 끔
    assert 'const token = () => SNAP ? "snapshot"' in PAGE                                   # 로그인 끔
    assert "if (m && !SNAP) return { prior" in PAGE                                          # 과거 기록 끔
    # 69호 — 주제 관리도 숨긴다. 108호 — 가입 신청도(데모에는 운영자가 없다).
    assert ".snapshot #flipStatus,.snapshot #moveTo,.snapshot .tidy,.snapshot .topic-tools,.snapshot #navPrior,.snapshot #navJoin{display:none;}" in PAGE
    assert 'if (h === "#/join" && !SNAP)' in PAGE                                            # 주소로 쳐도 데모에서는 안 열린다
    assert ds.CONFIG_TAG in PAGE                                                             # 스크립트가 갈아 끼울 자리


def test_db_모드는_HTTP_없이_같은_다섯_길을_낸다(tmp_path):
    """서버 안의 밤샘 갱신(compose 의 demo 서비스)이 쓰는 길 — 코어를 직접 불러 폴더를 통째로 바꿔친다."""
    from casebook.adapters.mcp_server import Tools
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    from tests.test_threads import _git_repo
    db = tmp_path / "w.db"
    wt = Worktrail(SqliteDB(str(db)))
    wt.signup("사람", "d@x.test", "pw")
    t = Tools(wt, 1)
    cid = t.open_thread("데모 폴더를 만든다", _git_repo(tmp_path / "r"), topic="검증")["case_id"]
    t.note_turn(cid, "$ ls /Users/kim/x\nkim@naver.com", "홈 경로가 섞였다", kind="finding", next="다음 할 일\n\n시험이 세운 자리다.", owner="user")
    get = ds.db_getter(str(db), "d@x.test")
    out = tmp_path / "demo" / "site"
    s = ds.build(get, PAGE, out, ["kim@naver.com"])
    assert s["threads"] == 1 and (out / "index.html").exists() and (out / "data/meta.json").exists()
    assert {p.name for p in (out / "data").iterdir()} == {"status.json", "review.json", "topic.json", "thread", "case", "prior.json", "meta.json"}
    case = (out / f"data/case/{cid}.json").read_text(encoding="utf-8")
    assert "/Users/kim" not in case and "kim@naver.com" not in case and "[가림]" in case
    assert "kim@naver.com" not in (out / "REVIEW.txt").read_text(encoding="utf-8")       # 가린 것은 목록에서도 빠진다
    assert 'window.CASEBOOK_CONFIG={SNAPSHOT:"data",LANG:""}' in (out / "index.html").read_text(encoding="utf-8")
    # 두 번째 실행은 통째로 바꿔친다 — 옆에 .new/.old 가 남지 않는다
    ds.build(get, PAGE, out, [])
    assert {p.name for p in out.parent.iterdir()} == {"site"}


def test_topic_허용_목록(tmp_path):
    """--topic 은 그 주제의 스레드만 남긴다 — 현황·회고·주제 목록·저장소 스레드 목록 전부에서. 없는 이름은 예외."""
    from casebook.adapters.mcp_server import Tools
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    from tests.test_threads import _git_repo
    db = tmp_path / "w.db"
    wt = Worktrail(SqliteDB(str(db)))
    wt.signup("사람", "d@x.test", "pw")
    t = Tools(wt, 1)
    repo = _git_repo(tmp_path / "r")
    a = t.open_thread("낼 것", repo, topic="공개")["case_id"]
    b = t.open_thread("숨길 것: 인스턴스 i-0123456789abcdef0 --profile opsprofile", repo,
                      topic="비공개", new_topic=True)["case_id"]      # 118호 — 정말 다른 주제다
    get = ds.db_getter(str(db), "d@x.test")
    out = tmp_path / "site"
    s = ds.build(get, PAGE, out, [], ["공개"])
    assert s["threads"] == 1
    st = json.loads((out / "data/status.json").read_text(encoding="utf-8"))
    assert [x["name"] for x in st["topics"]] == ["공개"] and st["counts"]["open"] == 1 and st["counts"]["topics"] == 1
    rv = json.loads((out / "data/review.json").read_text(encoding="utf-8"))
    assert set(rv["cases"]) == {str(a)} and [x["name"] for x in rv["topics"]] == ["공개"]
    th = json.loads((out / f"data/thread/{a}.json").read_text(encoding="utf-8"))
    assert th["repo_state"]["threads"] == [a]
    assert not (out / f"data/thread/{b}.json").exists() and not (out / f"data/case/{b}.json").exists()
    with pytest.raises(ValueError, match="그런 주제가 없다"):
        ds.build(get, PAGE, out, [], ["공개", "오타"])
    # 인스턴스 ID·프로필 이름은 주제와 무관하게 기계가 가린다
    assert ds.scrub("i-0123456789abcdef0 AWS_PROFILE=opsprofile --profile opsprofile", []) == "i-[가림] AWS_PROFILE=[가림] --profile [가림]"


def test_skip_은_주제_안에서_몇_개를_더_뺀다(tmp_path):
    from casebook.adapters.mcp_server import Tools
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    from tests.test_threads import _git_repo
    db = tmp_path / "w.db"
    wt = Worktrail(SqliteDB(str(db)))
    wt.signup("사람", "d@x.test", "pw")
    t = Tools(wt, 1)
    repo = _git_repo(tmp_path / "r")
    a = t.open_thread("낼 것", repo, topic="공개")["case_id"]
    b = t.open_thread("원본만 쌓인 것", repo, topic="공개")["case_id"]
    get = ds.db_getter(str(db), "d@x.test")
    out = tmp_path / "site"
    s = ds.build(get, PAGE, out, [], ["공개"], {b})
    assert s["threads"] == 1 and not (out / f"data/thread/{b}.json").exists()
    tp = json.loads((out / "data/topic.json").read_text(encoding="utf-8"))
    assert tp["topics"][0]["case_ids"] == [a]
    st = json.loads((out / "data/status.json").read_text(encoding="utf-8"))
    assert (st["topics"][0]["open_count"], st["topics"][0]["closed_count"]) == (1, 0)   # 주제 머리 수도 뺀 만큼 준다
    rv = json.loads((out / "data/review.json").read_text(encoding="utf-8"))
    assert rv["topics"][0]["case_ids"] == [a] and set(rv["cases"]) == {str(a)}
    with pytest.raises(ValueError, match="--topic 과 함께만"):
        ds.build(get, PAGE, out, [], None, {b})


def test_스냅샷이_자기_언어를_들고_간다(tmp_path):
    """영어 기록에 한국어 상단바가 얹히면 안 된다 — 레딧에서 올 사람이 보는 것이 정확히 이 화면이다.
    --lang 을 안 주면 빈 값이고, 그때는 방문자의 브라우저 언어로 열린다(종전과 같다).

    확장 86호: 전에는 이 자리가 제품의 치환 한 줄을 테스트 본문에 베껴 놓고 자기가 넣은 것을
    자기가 찾았다 — build() 가 lang 을 아예 버려도 통과하는 시험이었다. 실제로 build() 를
    부르고 나온 파일을 읽는다."""
    from casebook.adapters.mcp_server import Tools
    from casebook.core.db import SqliteDB
    from casebook.core.worktrail import Worktrail
    from tests.test_threads import _git_repo
    db = tmp_path / "w.db"
    wt = Worktrail(SqliteDB(str(db)))
    wt.signup("사람", "d@x.test", "pw")
    t = Tools(wt, 1)
    t.open_thread("데모 폴더를 만든다", _git_repo(tmp_path / "r"), topic="검증")
    get = ds.db_getter(str(db), "d@x.test")
    for lang, want in (("en", 'LANG:"en"'), ("ko", 'LANG:"ko"'), ("", 'LANG:""')):
        out = tmp_path / ("site_" + (lang or "none"))
        ds.build(get, PAGE, out, [], lang=lang)
        html = (out / "index.html").read_text(encoding="utf-8")
        assert f'window.CASEBOOK_CONFIG={{SNAPSHOT:"data",{want}}}' in html
        assert ds.CONFIG_TAG not in html                       # 자리표가 남으면 화면이 서버 설정을 다시 읽는다
        assert html.count("window.CASEBOOK_CONFIG={") == 1     # 설정 script 는 하나뿐이다
        assert "../app/config" not in html                     # 데모 폴더 밖을 가리키는 script 가 없다


# ── 확장 105호 — 공개 데모는 지어낸 기록으로만 짓는다 ───────────────────────
def test_데모_컨테이너가_실기록_DB_를_붙이지_않는다():
    """D14988: 데모에 낼 기록은 지어서 만들고 실기록은 내지 않는다. 그런데 compose 의 demo 서비스는
    옛 길(D14800) 그대로 실서버 sqlite 를 읽어 마스킹하고 있었다 — 결정만 바뀌고 배포가 안 따라오면
    /demo/ 를 여는 순간 실기록이 공개된다. 붙이지 않는 것이 가장 확실한 보장이다."""
    import pathlib, re
    compose = (pathlib.Path(__file__).resolve().parent.parent / "compose.yml").read_text(encoding="utf-8")
    demo = compose.split("\n  demo:", 1)[1].split("\n  caddy:", 1)[0]

    assert "casebook-data" not in demo, "데모가 실서버 sqlite 를 붙인다 — 실기록이 새어 나갈 길이다"
    assert "/data/casebook.db" not in demo, "데모가 실기록 DB 를 읽는다"
    assert "--mask" not in demo, "가릴 것이 있다는 뜻이다 — 지어낸 기록에는 가릴 것이 없다"
    assert "CASEBOOK_DEMO_EMAIL" not in demo, "실기록 주인을 가리키는 설정이 남아 있다"
    assert "demo_seed.py" in demo, "지어낸 기록을 짓는 단계가 없다"


def test_데모는_한국어판과_영어판을_함께_낸다():
    """D15188 — 원티드에는 한국어 링크, 레딧에는 영어 링크. 화면 토글은 UI 문구만 바꾸고
    기록 자체는 못 바꾼다. 그래서 시드를 두 벌 짓는다."""
    import pathlib
    compose = (pathlib.Path(__file__).resolve().parent.parent / "compose.yml").read_text(encoding="utf-8")
    demo = compose.split("\n  demo:", 1)[1].split("\n  caddy:", 1)[0]
    assert "--lang ko" in demo and "--lang en" in demo, "두 언어를 다 짓지 않는다"
    assert "/demo/build/en" in demo, "영어판이 /demo/en/ 으로 가지 않는다"
    # 옆에 짓고 마지막에 바꿔친다 — 서빙 중에 반쯤 지어진 폴더가 보이면 안 된다
    assert demo.index("--out /demo/build") < demo.index("mv /demo/build /demo/site")
    assert "REVIEW.txt" in demo, "사람이 볼 내부 메모가 공개 폴더에 남는다"


def test_이미지에_git_이_있다():
    """demo_seed.py 는 임시 저장소를 만들어 커밋을 얹는다 — 그래야 데모 스레드의 저장소 식별과
    커밋 연결이 실제와 같은 길로 생긴다. python:slim 에는 git 이 없어 그대로면 데모가 서지 않는다."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    seed = (root / "tools" / "demo_seed.py").read_text(encoding="utf-8")
    assert '("git", *args)' in seed, "시드가 git 을 쓰지 않는다면 이 시험은 지킬 것이 없다"
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "install -y --no-install-recommends git" in dockerfile, "이미지에 git 이 없다"


# ── 확장 106호 — 데모는 판마다 제 언어로, 현황으로 연다 ──────────────────────
def test_데모는_판마다_언어를_따로_기억한다():
    """/demo/ 와 /demo/en/ 은 같은 출처다. 언어 열쇠가 하나면 한국어 데모에서 고른 것이 영어
    데모까지 바꾼다 — 영어 기록 위에 한국어 상단바가 뜬다(실측 2026-09-14: 사용자가 en 으로
    들어갔는데 한국어로 보였다). 판마다 따로 기억하고, 처음 열면 그 판의 CFG.LANG 으로 연다."""
    src = PAGE
    assert 'const LANG_KEY = CFG.SNAPSHOT' in src, "언어 열쇠가 판마다 갈리지 않는다"
    assert 'location.pathname' in src.split("const LANG_KEY", 1)[1][:300], "열쇠가 판을 가리키지 않는다"
    # 맨 열쇠를 그대로 쓰는 자리가 남아 있으면 한쪽이 여전히 남의 선택을 읽는다
    assert 'getItem("wt_lang")' not in src, "아직 판을 가리지 않는 열쇠로 읽는다"
    assert 'setItem("wt_lang",' not in src, "아직 판을 가리지 않는 열쇠로 쓴다"


def test_데모의_첫_화면은_현황이다():
    """사용자 2026-09-14: 처음 보이는 화면은 현황이어야 한다. 전에는 스냅샷이면 회고로 열었다 —
    제품이 무엇인지 먼저 보여 주는 것은 지금 무슨 일이 어디까지 왔는지이지 지나간 회고가 아니다."""
    src = PAGE
    router = src.split("function route() {", 1)[1].split("\n  }", 1)[0]
    assert "SNAP" not in router.split("location.hash", 1)[1].split("const qs", 1)[0], \
        "라우터가 데모를 따로 갈라 연다"
    assert "return { list: true };" in router, "기본 화면이 현황이 아니다"


def test_데모_도장도_언어를_따라간다():
    """상단바의 "읽기 전용 데모 · 갱신 …" 은 meta.json 을 받은 뒤 한 번만 그려져, 언어를 바꿔도
    이 한 줄만 옛 언어로 남았다(실측 2026-09-14: 영어 데모에서 한국어로 바꾸니 상단바가 전부
    한국어인데 이 줄만 read-only demo 였다). paintChrome 이 부를 때마다 다시 그린다."""
    assert "paintStamp();" in PAGE.split("function paintChrome() {", 1)[1].split("\n  }", 1)[0], \
        "언어를 바꿀 때 도장을 다시 그리지 않는다"
    # 받은 시각을 들고 있어야 다시 그릴 수 있다 — fetch 안에서 직접 쓰면 그때뿐이다
    assert "let snapAt = null;" in PAGE and "snapAt = (m && m.generated_at)" in PAGE
    fetched = PAGE.split('fetch(SNAP + "/meta.json")', 1)[1][:260]
    assert '$("who").textContent' not in fetched, "도장을 받은 자리에서 바로 그린다 — 다시 그릴 수 없다"


def test_영어_화면에_한국어_원문이_새지_않는다():
    """사전은 한국어 원문을 열쇠로 쓴다 — tr() 을 안 거친 자리는 영어 화면에 한국어로 나온다.
    사용자 2026-09-14: "en demo 에서 open (열었다) 이렇게 보임". 브라우저로 다섯 화면을 훑어
    열넷을 찾았고(현황 2 · 회고 4 · 스레드 8) 여기서 그 자리를 묶어 둔다.

    화면을 실제로 그려 보는 것이 제일 좋지만 이 저장소에는 JS 실행기가 없다. 대신 새던 자리마다
    tr() 을 거치는지 본다 — 되돌리면 이 시험이 깨진다."""
    must = [
        ('<span class="vt">${tr("목록")}</span>', "접힌 목록 띠"),
        ('? tr("닫았다") : e.kind === "opened" && e.text === e.title ? tr("열었다")', "현황 행의 열었다·닫았다"),
        ('$("crumbText").textContent = tr("회고"); $("crumbChip").textContent = tr("주제별");', "회고 머리빵"),
        ('mc.textContent = moreN(hidden);', "닫힘 띠의 더보기 N"),
        ('tr("다른 주제로 옮기기 ▾") : tr("주제에 넣기 ▾")', "주제 옮기기 고르개"),
        ('tr("관찰: ") + lastNote.observed', "SO FAR 의 관찰 머리"),
        ('tr("관찰: ") + e.observed', "노트 줄의 관찰 머리"),
        ('${tr("마지막 노트")} · turn ', "마지막 노트 꼬리"),
        ('${tr("결정이 없어 focus 에서")} · #', "focus 에서 온 WHY"),
        ('<summary>${tr("저장소 공통")} ', "저장소 공통 블록"),
        ('<span class="n">${tr("다른 스레드에서 정함")}</span>', "저장소 공통 부제"),
        ('${tr("이 스레드 것과 충돌하면 화면이 고르지 않는다.")}', "저장소 공통 경고"),
        ('<div class="empty">${tr("커밋 없음")}</div>', "커밋 없음"),
    ]
    for needle, what in must:
        assert needle in PAGE, f"{what} 가 tr() 을 안 거친다"
    # 수분류사는 사전으로 안 된다 — 어순이 바뀐다
    assert 'const moreN = (n) => LANG === "en"' in PAGE
    # 새 열쇠가 사전에 있어야 tr() 이 영어를 낸다
    for key in ("관찰: ", "결정이 없어 focus 에서", "다른 주제로 옮기기 ▾", "주제에 넣기 ▾", "목록 펼치기",
                "이 스레드 것과 충돌하면 화면이 고르지 않는다."):
        assert f'"{key}":' in PAGE, f"사전에 {key} 가 없다"


def test_데모는_되돌아온_사람에게_옛_화면을_주지_않는다():
    """정적 폴더에 캐시 머리가 없으면 브라우저가 제 어림짐작으로 오래 쥔다. 첫 화면을 현황으로
    바꿔 올렸는데 사용자에게는 회고가 그대로 보였다(실측 2026-09-14). no-cache 는 "쓰지 말라"가
    아니라 "쓰기 전에 물어보라"다 — ETag 가 있어 대개 304 로 끝난다."""
    import pathlib
    caddy = (pathlib.Path(__file__).resolve().parent.parent / "deploy/Caddyfile").read_text(encoding="utf-8")
    block = caddy.split("handle_path /demo/*", 1)[1].split("\n\t}", 1)[0]
    assert 'header Cache-Control "no-cache"' in block, "데모에 캐시 머리가 없다"


def test_화면이_낡은_next_를_말해_준다():
    """확장 110호(D15573) — 도구가 턴마다 next 를 다시 말하게 하므로 새 기록은 낡지 않는다.
    그런데 이미 쌓인 옛 기록에는 낡은 next 가 그대로 있다(스레드 520 이 그랬다). 낡았는지 모르면
    다음 세션이 그것을 읽고 이미 끝난 일을 하러 간다 — 그래서 화면이 말해 준다."""
    assert "const nextStale = s.next && lastNote && s.next.declared_at < lastNote.at;" in PAGE
    assert 'nextStale ? { cls: "sup", text: tr("마지막 노트보다 낡음") }' in PAGE
    assert '"마지막 노트보다 낡음":' in PAGE, "영어 화면에서 한국어로 뜬다"
