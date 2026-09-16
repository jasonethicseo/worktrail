"""확장 5호·7호 — 레코드 프롬프트 덮어쓰기. 추출본(prompts.py)은 불변, 덮어쓰기는 되돌릴 수 있어야 한다."""
from casebook.core import prompt_ext, prompts


def test_덮어쓰기는_되돌리면_추출본이_된다():
    back = prompt_ext.RECORD_SYSTEM
    for old, new in reversed(prompt_ext.OVERLAY):
        assert back.count(new) == 1
        back = back.replace(new, old)
    assert back == prompts.RECORD_SYSTEM


def test_결과_모양():
    s = prompt_ext.RECORD_SYSTEM
    assert "exactly these six sections" in s and "## Actions and outcomes" not in s
    assert s.count("\n## ") == 6
    assert "Evidence #n that overturned it" in s and "including actions that had no effect, each with its Evidence #n" in s
    assert "never drop items" not in s and "not a transcript" in s


def test_record_worker_가_확장본을_쓴다(rec_app):
    from tests.conftest import PLACEHOLDER_INPUT
    from tests.drive import USER, CASE
    app, llm = rec_app
    app.submit_turn(USER, CASE, PLACEHOLDER_INPUT, action_key="k"); app.run_workers()
    app.generate_record(USER, CASE); app.run_workers()
    system = next(s for (role, s, _p) in llm.calls if role == "record")
    assert system == prompt_ext.RECORD_SYSTEM and system != prompts.RECORD_SYSTEM
