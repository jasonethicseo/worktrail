"""이벤트 명세 — 이건 지금도 통과해야 한다(.xs 실측에서 온 사실)."""
from casebook.core import ledger


def test_열여섯_종에_확장_10호_셋이_더해졌다():
    assert len(ledger.EVENT_TYPES) == 20  # .xs 16종 + decision_recorded · constraint_recorded · hypothesis_ruled_out + term_defined(55호)


def test_코퍼스의_모든_이벤트가_명세_안에_있다(replay_turns):
    seen = {e for t in replay_turns for e in t["expected_events"]}
    assert seen <= ledger.EVENT_TYPES, f"명세에 없는 이벤트: {sorted(seen - ledger.EVENT_TYPES)}"


def test_brief_absent_는_더_이상_유효한_사유가_아니다():
    # 답변 호출에 tools 가 없으므로 구조적으로 발생할 수 없다(2026-08-25 죽은 블록 제거).
    assert "brief_absent" not in ledger.BRIEF_REJECT_REASONS


def test_기대값에_brief_rejected_가_없다(replay_turns):
    # 748건은 전부 죽은 블록의 산물이라 정규화 때 제거했다.
    offenders = [t["turn_id"] for t in replay_turns if "brief_rejected" in t["expected_events"]]
    assert not offenders, f"결함 시퀀스가 기대값에 남아 있다: {offenders[:5]}"
