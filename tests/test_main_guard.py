"""서버 기동 가드 — 자리표시자 키('…')를 실제 키처럼 받아 모든 모델 호출이 조용히 실패한 사고(2026-09-05) 재발 방지."""
import pytest
import main


@pytest.mark.parametrize("bad", ["", "   ", "…", "sk-…", "abc", "키를넣으세요"])
def test_빈_키와_자리표시자는_기동을_막는다(bad):
    with pytest.raises(SystemExit):
        main.check_api_key(bad)


def test_실제처럼_보이는_키는_통과():
    assert main.check_api_key("sk-" + "a" * 40) == "sk-" + "a" * 40
