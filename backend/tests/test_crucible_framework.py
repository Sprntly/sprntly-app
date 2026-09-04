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


# ─── Questions derived from what the reconnaissance pass saw ────────────────
#
# The old set was a function of the FRAMEWORK alone, so every RICE run asked
# the same three things whatever was connected. The bar for asking is now
# deliberately high: a wrong guess must silently corrupt a NUMBER, and the
# answer must not be derivable from what is connected. Everything that fails
# either half is a default the run should take and disclose, or a fact it
# should derive and say it derived.

from tests import _tabular_recon_fixtures as _fx  # noqa: E402

from app.crucible import recon as _recon  # noqa: E402
from app.crucible.framework import derived_account_value  # noqa: E402


def _observations():
    return _recon.observe(_fx.full_pack()).observations


def test_with_no_observations_the_question_set_is_exactly_what_it_always_was():
    """Every caller that has not run a reconnaissance pass, and every plan
    stored before one existed. The parameter must be fully optional."""
    assert [q.id for q in questions_for("rice")] == [
        "account_value", "decision_owner", "needed_by"]
    assert [q.id for q in questions_for("moscow")] == [
        "decision_owner", "needed_by"]


def test_the_account_value_question_is_not_asked_when_the_contracts_answer_it():
    """Asking for an estimate the customer's own contracts already contain is
    the engine requesting a guess it can measure — and then labelling that
    guess an assumption in a document whose data contradicts it."""
    ids = [q.id for q in questions_for("rice", _observations())]
    assert "account_value" not in ids


def test_it_is_still_asked_when_nothing_connected_carries_a_per_account_value():
    """The suppression must be about the EVIDENCE, not about observations
    existing at all — otherwise any reconnaissance pass silently drops the
    question."""
    without = tuple(
        o for o in _observations() if o.kind != "unit_value_derivable")
    assert "account_value" in [q.id for q in questions_for("rice", without)]


def test_the_derived_value_is_the_median_and_says_where_it_came_from():
    """A question that stops being asked with no explanation reads as a
    feature that broke. The median rather than the mean so a handful of very
    large accounts do not set the price of a typical one."""
    value, note = derived_account_value(_observations())
    assert value == 283526
    assert "total_acv_usd" in note
    assert "median" in note


def test_nothing_is_derived_when_there_is_nothing_to_derive_it_from():
    assert derived_account_value(()) == (None, "")


def test_a_derived_question_carries_what_was_seen_and_what_happens_if_skipped():
    by_id = {q.id: q for q in questions_for("rice", _observations())}
    q = by_id["value_column_choice"]
    assert "base_acv_usd" in q.what_i_saw and "total_acv_usd" in q.what_i_saw
    assert q.options == ("total_acv_usd", "base_acv_usd")
    # NEVER "we will guess": a default is a stated behaviour the run carries
    # out and discloses.
    assert q.default_if_skipped


def test_the_same_finding_seen_several_times_is_asked_about_once():
    """The pass emits up to eight observations of a kind. Asking a reader the
    same thing three times with different percentages behind it is how a gate
    stops being answered."""
    doubled = _observations() + _observations()
    ids = [q.id for q in questions_for("rice", doubled)]
    assert len(ids) == len(set(ids))


def test_the_decision_box_questions_can_never_be_crowded_out():
    """Neither is derivable from any corpus, and both are the ones the gate
    already renders — so losing one to make room for a derived question would
    be a visible regression in exchange for a question nothing renders yet."""
    many = _observations() * 6
    ids = [q.id for q in questions_for("rice", many)]
    assert ids[-2:] == ["decision_owner", "needed_by"]


def test_the_cap_truncates_rather_than_raising():
    """It used to be a hard `assert`, which on a derived set is a 500 on the
    plan gate — the reader loses the whole plan because the run found too much
    worth clarifying. Strictly worse than asking one question fewer."""
    many = _observations() * 6
    assert len(questions_for("rice", many)) <= MAX_PLAN_QUESTIONS
