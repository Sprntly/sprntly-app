"""`crucible/plan.py`'s framework-mismatch gap. Pure — no DB, no LLM. Live
`build_plan(...)` coverage (framework selection over a real company's real
source inventory) lives in `test_routes_crucible.py`, which already owns the
fake-Supabase `ctx` fixture this needs."""
from app.crucible.framework import FrameworkChoice
from app.crucible.plan import SourceInventory, derive_gaps_and_promises


def _src(source_type: str, n: int = 10) -> SourceInventory:
    return SourceInventory(source_type, n, source_type, "witnesses")


# ─── AC-2 / AC-3: the chosen framework, and the gap when it could not honour
#     the company's own onboarding setting ───────────────────────────────────


def test_a_declared_framework_mismatch_becomes_a_gap_not_a_silent_swap():
    """AC-3: the plan calls out what THIS run needs and cannot get — including
    the company's own stated framework, when the data cannot support it."""
    choice = FrameworkChoice(
        framework="moscow",
        reason="your team set RICE at onboarding, but nothing connected "
               "here carries a number",
        declared="rice", honoured_declared=False,
        remedy="connect an analytics or revenue source",
    )
    gaps, _ = derive_gaps_and_promises([_src("customer_voice")], (), framework_choice=choice)
    questions = " ".join(f"{g.question} {g.because} {g.remedy}" for g in gaps)
    assert "RICE" in questions
    assert "connect an analytics or revenue source" in questions


def test_an_honoured_declared_framework_adds_no_extra_gap():
    choice = FrameworkChoice(
        framework="rice", reason="numeric data connected",
        declared="rice", honoured_declared=True,
    )
    gaps, _ = derive_gaps_and_promises([_src("analytics")], (), framework_choice=choice)
    assert not any("isn't this ranked by" in g.question for g in gaps)


def test_no_declared_framework_at_all_adds_no_mismatch_gap():
    choice = FrameworkChoice(framework="moscow", reason="no numeric source")
    gaps, _ = derive_gaps_and_promises([_src("customer_voice")], (), framework_choice=choice)
    assert not any("isn't this ranked by" in g.question for g in gaps)


def test_no_framework_choice_at_all_is_still_a_valid_call():
    """`derive_gaps_and_promises` predates `framework_choice` and every other
    existing caller/test omits it — the parameter must be fully optional."""
    gaps, produce = derive_gaps_and_promises([_src("customer_voice")])
    assert isinstance(gaps, tuple)
    assert isinstance(produce, tuple)
