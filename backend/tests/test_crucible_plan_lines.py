"""Which investigations a tenant can support, given what it has connected.

The property under test is the one that makes the whole feature safe: a line
exists ONLY when the evidence for it exists. Get this wrong and the plan
proposes an investigation into spend for a tenant that measures no spend, which
is an invitation for the prose layer above it to invent the numbers.

The fixtures below are real source profiles, counted off `kg_signal` on
2026-08-21, because calibrating a threshold against imagined data is how you
end up with a gate that passes everything.
"""
from __future__ import annotations

from app.crucible.plan_lines import (
    LINE_KINDS, MIN_FOR_SHAPE, plan_lines,
)

# Real tenants, real counts.
XOMETRY = {"project_mgmt": 27582, "communication": 380, "analytics": 3, "revenue": 1}
CHAOSTRACK = {"communication": 3924, "customer_voice": 1600, "project_mgmt": 490,
              "analytics": 0, "revenue": 0}
TESSELLATE = {"analytics": 149, "customer_voice": 97, "project_mgmt": 27,
              "communication": 12, "revenue": 12, "pm_manual": 1}


def _keys(kinds):
    return {k.key for k in kinds}


def test_volume_is_not_evidence():
    """Xometry has 27,998 signals and can support exactly one investigation.

    This is the fact the whole design rests on. A generator asked for eight
    numbered lines here would have to invent seven of them, and they would read
    exactly as confidently as the one real one.
    """
    runnable, blocked = plan_lines(XOMETRY)

    assert _keys(runnable) == {"shipped_levers"}
    assert len(blocked) == len(LINE_KINDS) - 1
    # And the tracker line IS available — 27,582 tracker items are real
    # evidence about what shipped. Thin is not the same as empty.
    assert "shipped_levers" in _keys(runnable)


def test_a_tenant_with_no_numbers_gets_no_numeric_lines():
    """Chaostrack measures nothing, so nothing may be sized or trended."""
    runnable, _ = plan_lines(CHAOSTRACK)

    for key in ("decompose_metric", "adoption_shape", "cohort_contrast",
                "dated_intervention", "external_timing", "leading_indicator"):
        assert key not in _keys(runnable), f"{key} ran without numbers"
    # What it CAN do, it still does. The gate narrows the plan; it does not
    # withhold it.
    assert _keys(runnable) == {"shipped_levers", "reason_concentration"}


def test_an_instrumented_tenant_gets_the_whole_plan():
    """All eight, and every one of them grounded."""
    runnable, blocked = plan_lines(TESSELLATE)

    assert len(runnable) == len(LINE_KINDS)
    assert blocked == []


def test_present_but_too_thin_is_treated_as_absent():
    """Three tickets cannot show whether reasons concentrate or scatter.

    The dangerous middle: a source present in name, too small to carry the
    claim. Silently allowing it produces the most confident-sounding and least
    supported line in the plan.
    """
    thin = dict(CHAOSTRACK)
    thin["customer_voice"] = MIN_FOR_SHAPE["customer_voice"] - 1
    runnable, _ = plan_lines(thin)
    assert "reason_concentration" not in _keys(runnable)

    enough = dict(thin)
    enough["customer_voice"] = MIN_FOR_SHAPE["customer_voice"]
    runnable, _ = plan_lines(enough)
    assert "reason_concentration" in _keys(runnable)


def test_every_blocked_line_says_why_and_what_would_fix_it():
    """A gap without a remedy is a shrug.

    This is the property that makes a thin tenant's plan USEFUL rather than
    merely honest — the blocked list is the only place the product tells the
    user what to connect.
    """
    _, blocked = plan_lines(CHAOSTRACK)
    assert blocked
    for kind in blocked:
        assert kind.absent_because and kind.remedy, kind.key
        # Names the missing thing, rather than shrugging about "data".
        assert "no data" not in kind.absent_because.lower()
        # `blocked_by` must name the source, so the prose layer can be specific.
        assert kind.blocked_by(CHAOSTRACK)


def test_every_line_carries_a_reading_not_just_a_chore():
    """"Pull seat-level adoption" is a task. "Bimodal means a named-account
    play, uniformly low means a product problem" is why anyone should care."""
    for kind in LINE_KINDS:
        assert kind.question and kind.reading, kind.key
        assert len(kind.reading) > 40, f"{kind.key}: reading is too thin to be a reading"


def test_no_line_is_unconditional():
    """Every investigation rests on something. A line with no requirements
    would run on an empty tenant, which is precisely the fabrication case."""
    for kind in LINE_KINDS:
        assert kind.requires_all or kind.requires_any, kind.key

    assert plan_lines({})[0] == [], "a tenant with nothing connected got a line"
