"""Framework selection is CODE reasoning over the source inventory, never a
model's opinion (I2). These tests exercise `select_framework` and
`questions_for` in isolation — no DB, no LLM."""
from app.crucible.framework import (
    MAX_PLAN_QUESTIONS,
    display_name,
    questions_for,
    select_framework,
)
from app.crucible.plan import SourceInventory


def _src(source_type: str, n: int = 10) -> SourceInventory:
    return SourceInventory(source_type, n, source_type, "witnesses")


def test_no_numeric_source_and_no_declared_preference_chooses_moscow():
    """The corpus this exists for: no analytics, no revenue, no
    measured outcome. RICE cannot derive Reach or Impact from it (see the
    reasoning spike's real-pipeline run — 26/26 findings scored None)."""
    choice = select_framework([_src("customer_voice"), _src("communication")])
    assert choice.framework == "moscow"
    assert choice.declared is None
    assert "nothing connected here carries a number" in choice.reason


def test_a_numeric_source_chooses_rice():
    choice = select_framework([_src("customer_voice"), _src("analytics")])
    assert choice.framework == "rice"
    assert "analytics" in choice.reason


def test_declared_rice_is_honoured_when_numeric_data_backs_it():
    choice = select_framework([_src("revenue")], declared="rice")
    assert choice.framework == "rice"
    assert choice.declared == "rice"
    assert choice.honoured_declared is True


def test_declared_rice_falls_back_to_moscow_without_numeric_data():
    """The company said RICE at onboarding; nothing connected here can size
    it. The choice still has to be usable — MoSCoW, not a None-scoring
    RICE table — and the fallback is disclosed, not silent."""
    choice = select_framework([_src("customer_voice")], declared="rice")
    assert choice.framework == "moscow"
    assert choice.declared == "rice"
    assert choice.honoured_declared is False
    assert "RICE" in choice.reason
    assert choice.remedy


def test_declared_moscow_is_always_honoured():
    """MoSCoW needs no numeric source, so declaring it is always usable."""
    choice = select_framework([_src("customer_voice")], declared="moscow")
    assert choice.framework == "moscow"
    assert choice.honoured_declared is True


def test_declared_but_unsupported_framework_falls_back_and_says_so():
    """`wsjf`/`kano`/`volume-severity`/`goal-based` are real DB values this
    build cannot score yet. The company's setting is not silently ignored —
    the run says it could not honour it and what it did instead."""
    choice = select_framework([_src("analytics")], declared="wsjf")
    assert choice.framework == "rice"          # falls back to the data choice
    assert choice.declared == "wsjf"
    assert choice.honoured_declared is False
    assert "WSJF" in choice.reason


def test_selection_is_case_and_whitespace_insensitive():
    choice = select_framework([_src("customer_voice")], declared="  MoSCoW  ")
    assert choice.framework == "moscow"
    assert choice.honoured_declared is True


def test_display_name_covers_every_db_value():
    for code in ("rice", "moscow", "wsjf", "kano", "volume-severity", "goal-based"):
        assert display_name(code)


def test_questions_for_rice_asks_account_value_and_the_decision_pair():
    qs = questions_for("rice")
    ids = [q.id for q in qs]
    assert "account_value" in ids
    assert "decision_owner" in ids
    assert "needed_by" in ids
    assert len(qs) <= MAX_PLAN_QUESTIONS


def test_questions_for_moscow_never_asks_for_a_dollar_value():
    """MoSCoW's ranking has no arithmetic that reads account_value — asking
    for it would collect an input nothing downstream uses, which is exactly
    the dishonest ask this function exists to avoid."""
    qs = questions_for("moscow")
    ids = [q.id for q in qs]
    assert "account_value" not in ids
    assert "decision_owner" in ids
    assert "needed_by" in ids


def test_every_question_states_why_it_is_asked():
    for framework in ("rice", "moscow"):
        for q in questions_for(framework):
            assert q.why.strip()
            assert q.prompt.strip()
