"""화면 JS 가 없는 이름을 부르지 않는가 (확장 82호).

왜 필요한가: 확장 79호가 번역 함수를 t→tr 로 바꾸며 호출부 두 자리를 빠뜨렸다. 그 결과
threadView 가 ReferenceError 로 죽고 render 의 catch 가 그것을 삼켜 **스레드 상세 화면이
카드 한 장으로 대체됐다** — 한국어·영어 모두, 데모 9개 스레드 전부. 스위트 421건이 통과했는데,
화면을 실제로 실행하는 테스트가 하나도 없었고 회귀 테스트는 정의부 문자열만 grep 했기 때문이다.

여기서는 브라우저 없이 같은 부류를 잡는다: 템플릿 안에서 호출되는 이름이 파일 어딘가에
선언돼 있는지 본다. 완전한 정적 분석은 아니지만, 이름을 바꾸며 호출부를 빠뜨리는 실수는 잡는다.
"""
from __future__ import annotations

import pathlib
import re

PAGE = pathlib.Path("web/worktrail/index.html")

# 브라우저·표준 전역. 여기 없는 이름은 파일 안에 선언이 있어야 한다.
BUILTINS = {
    "document", "window", "location", "navigator", "localStorage", "sessionStorage", "history",
    "fetch", "setTimeout", "setInterval", "clearTimeout", "clearInterval", "requestAnimationFrame",
    "console", "alert", "confirm", "prompt", "JSON", "Object", "Array", "String", "Number", "Boolean",
    "Math", "Date", "RegExp", "Map", "Set", "WeakMap", "Promise", "Error", "URL", "URLSearchParams",
    "encodeURIComponent", "decodeURIComponent", "parseInt", "parseFloat", "isNaN", "Intl",
    "IntersectionObserver", "MutationObserver", "EventSource", "AbortController", "getComputedStyle",
    "structuredClone", "queueMicrotask", "performance", "CustomEvent", "Event", "Blob",
}


def _js() -> str:
    html = PAGE.read_text(encoding="utf-8")
    return html[html.index("<script>") + len("<script>"):html.rindex("</script>")]


def _declared(js: str) -> set[str]:
    names: set[str] = set()
    names |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)", js))
    names |= set(re.findall(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)", js))
    names |= set(re.findall(r"\bclass\s+([A-Za-z_$][\w$]*)", js))
    # 구조분해와 매개변수는 촘촘히 잡기 어렵다 — 이름만 넉넉히 거둔다
    for chunk in re.findall(r"(?:const|let|var)\s*[\[{]([^\]}]*)[\]}]\s*=", js):
        names |= set(re.findall(r"[A-Za-z_$][\w$]*", chunk))
    for params in re.findall(r"\(([^()]*)\)\s*=>", js):
        names |= set(re.findall(r"[A-Za-z_$][\w$]*", params))
    for params in re.findall(r"function\s*\*?\s*[A-Za-z_$\w]*\s*\(([^()]*)\)", js):
        names |= set(re.findall(r"[A-Za-z_$][\w$]*", params))
    names |= set(re.findall(r"\bcatch\s*\(\s*([A-Za-z_$][\w$]*)", js))
    names |= set(re.findall(r"\bfor\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)", js))
    return names


def test_템플릿이_없는_이름을_부르지_않는다():
    js = _js()
    declared = _declared(js) | BUILTINS
    # `${ident(` 꼴 — 템플릿 안에서 호출되는 함수 이름
    called = set(re.findall(r"\$\{\s*([A-Za-z_$][\w$]*)\s*\(", js))
    missing = sorted(n for n in called if n not in declared)
    assert not missing, f"템플릿이 부르는데 선언이 없는 이름: {missing}"


def test_번역_함수_이름이_한_가지다():
    """t( 로 부르던 잔재가 남으면 스레드 화면이 통째로 죽는다."""
    js = _js()
    assert "const tr = (s) => LANG" in js
    assert not re.search(r"\$\{\s*t\s*\(", js), "템플릿에 ${t( 가 남아 있다"
    assert not re.search(r"[^\w$.]t\(\"[가-힣]", js), "t(\"한국어\") 꼴 호출이 남아 있다"


def test_노트_타임라인은_최신부터_쌓고_오래된_것을_뒤로_둔다():
    """새 노트는 위에 쌓고, 제한을 넘은 과거 노트만 처음에 숨긴다."""
    import json
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    js = _js()
    start = js.index("function noteTimeline")
    end = js.index("\n  }", start) + len("\n  }")
    helper = js[start:end]
    harness = helper + """
const notes = [
  { turn: 3, at: 30 },
  { turn: 1, at: 10 },
  { turn: 2, at: 20 }
];
console.log(JSON.stringify(noteTimeline(notes, 2).map((n) => [n.turn, n.older])));
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == [[3, False], [2, False], [1, True]]


def test_노트는_시간순과_종류별_보기를_오갈_수_있다():
    """기억 복원용 시간축과 예전 갈래별 화면을 같은 노트 원장에서 고른다."""
    js = _js()
    assert 'data-nview="timeline"' in js
    assert 'data-nview="grouped"' in js
    assert "const applyNoteView" in js
    assert '.meta .nk,.it summary > .nk{display:inline-flex' in PAGE.read_text()


def test_노트_종류_칩은_제목보다_먼저_읽힌다():
    """노트만 종류→내용→시점 순서이고, 다른 entry 메타 배치는 그대로 둔다."""
    page = PAGE.read_text()
    assert "leadTag: true" in page
    assert '<summary>${leadTag}<span class="hd ' in page
    assert '.it summary > .nk{box-sizing:border-box;width:34px' in page


def test_사전_열쇠가_망가지지_않았다():
    """일괄 치환이 사전의 열쇠 자체를 tr(\"…\") 로 바꿔 문법 오류를 낸 적이 있다."""
    js = _js()
    block = js[js.index("const EN = {"):js.index("\n  };", js.index("const EN = {"))]
    assert "tr(" not in block and "${" not in block
    assert len(re.findall(r'"[^"]+":', block)) > 100


def test_render_는_한_번에_하나만_돈다():
    """확장 135호 — 두 render 가 await 사이에 엇갈리면 공용 view 가 root 로 돌려진 뒤 늦은 목록이 화면 전체를 덮어
    #paneList 가 사라지고 "Cannot read properties of null (reading 'insertAdjacentHTML')" 이 떴다. 화면의 render
    직렬화 코드를 그대로 떼어 node 에서 돌린다: 느린 renderOnce 를 겹쳐 불러도 동시에 둘이 돌지 않고, 도는 중에
    들어온 요청은 끝난 뒤 한 번 더(그때의 주소로) 그려지며, 모든 호출자가 그 마지막 그리기까지 기다린다."""
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("node 없음")
    js = _js()
    start = js.index("let rendering = null")
    serializer = js[start:js.index("async function renderOnce", start)]
    harness = serializer + """
let running = 0, maxRunning = 0, drawn = [], route = "a";
async function renderOnce() {
  running++; maxRunning = Math.max(maxRunning, running);
  const at = route; await new Promise((r) => setTimeout(r, 20)); drawn.push(at); running--;
}
(async () => {
  const p1 = render(); route = "b"; const p2 = render(); route = "c"; const p3 = render();
  await Promise.all([p1, p2, p3]);
  const afterAll = drawn.slice();
  await render();
  console.log(JSON.stringify({ maxRunning, afterAll, last: drawn[drawn.length - 1], total: drawn.length }));
})();
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    import json
    r = json.loads(out.stdout)
    assert r["maxRunning"] == 1                     # 겹치지 않는다
    assert r["afterAll"] == ["a", "c"]              # 도는 중의 요청 둘은 하나로 모여, 마지막 주소로 그린다
    assert r["last"] == "c" and r["total"] == 3     # 끝난 뒤의 호출은 새로 한 번 돈다
