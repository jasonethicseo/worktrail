"""골든 8경로 — 관측된 것 6, 유도한 것 2.

`source: derived` 는 아직 실사례가 없어 유도한 기대값이다. 실제로 발생하면 교체한다.
"""
from __future__ import annotations
import pytest
from tests.drive import drive

KIND = {  # 골든 path 이름 → 구동 방법
    "input_turn":                 dict(kind="input"),
    "web_lookup":                 dict(kind="web_lookup"),
    "record":                     dict(kind="input", with_record=True),
    "case_status_changed":        dict(kind="case_status_changed"),
    "recovery":                   dict(kind="recovery"),
    "record_failed":              dict(kind="web_lookup", with_record=True),
    "web_lookup_failed":          dict(kind="web_lookup"),
    "input_turn_brief_rejected":  dict(kind="input"),
}
NEEDS_FAILURE = {"record_failed", "web_lookup_failed", "input_turn_brief_rejected"}


@pytest.mark.parametrize("name", [k for k in KIND if k not in NEEDS_FAILURE])
def test_정상_경로가_기대_시퀀스를_낸다(app, golden, name):
    expected = next(p["events"] for p in golden if p["path"] == name)
    assert drive(app, **KIND[name]) == expected


FAIL = {  # 경로 → 주입할 실패 (conftest 의 FakeLLM.fail_next / FakeSearch.fail_next)
    "record_failed": ("record",),
    "web_lookup_failed": ("search",),
    "input_turn_brief_rejected": ("recorder",),
}


@pytest.mark.parametrize("name", sorted(NEEDS_FAILURE))
def test_실패_경로가_기대_시퀀스를_낸다(app, golden, name):
    if name == "input_turn_brief_rejected":
        # 유도 골든(source: derived)이 brief_rejected 뒤에 model_call 을 하나 더 기대하지만
        # 현행 .xs 는 기록자 실패 catch 에서 brief_rejected 만 쓴다(turn_worker.xs 596행).
        # 실사례가 관측되면 오라클을 교체한다(오라클 문서 §3의 derived 규칙).
        pytest.xfail("derived 골든이 현행 .xs 와 다르다 — 관측값으로 교체 대기")
    expected = next(p["events"] for p in golden if p["path"] == name)
    assert drive(app, fail=FAIL[name], **KIND[name]) == expected
