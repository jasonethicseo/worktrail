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


def test_사전_열쇠가_망가지지_않았다():
    """일괄 치환이 사전의 열쇠 자체를 tr(\"…\") 로 바꿔 문법 오류를 낸 적이 있다."""
    js = _js()
    block = js[js.index("const EN = {"):js.index("\n  };", js.index("const EN = {"))]
    assert "tr(" not in block and "${" not in block
    assert len(re.findall(r'"[^"]+":', block)) > 100
