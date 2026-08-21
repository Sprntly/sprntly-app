"""Numbers the plan may state, and the guard that stops it stating others.

Two distinct failures are covered here, and the second is the subtle one:

  1. A FABRICATED NUMBER — the model writes "$27M" because the shape of the
     sentence wants a figure. `unstated_numbers` catches it.
  2. A FABRICATED RELATIONSHIP — every number is real and traceable, and the
     sentence is still false because it asserts a connection the evidence does
     not make. No digit-level check can see this one; it has to be prevented
     at extraction.
"""
from __future__ import annotations

from app.crucible.plan_facts import (
    Fact, extract_facts, unstated_numbers, _numerals,
)


def sig(sid, props, **kw):
    return {"id": sid, "properties": props, "source_type": kw.get("source_type", "analytics"),
            "content": kw.get("content", "")}


def test_a_number_with_no_fact_behind_it_is_caught():
    facts = [Fact(key="m", statement="interchange in 2026-08 was 1,829,384", value=1829384)]
    assert unstated_numbers("interchange in 2026-08 was 1,829,384", facts) == set()
    # The shape of the sentence wants a figure; the evidence does not have one.
    assert unstated_numbers("costing you $27M across 1,900 accounts", facts) == {"27", "1900"}


def test_the_same_number_written_differently_still_passes():
    """1,900 and 1900 are one claim. A guard that cannot see that would push
    authors toward removing separators, which makes the output worse."""
    facts = [Fact(key="n", statement="1,900 customers", value=1900)]
    assert unstated_numbers("1900 customers", facts) == set()
    assert unstated_numbers("1,900 customers", facts) == set()


def test_list_numbering_and_years_are_never_fabrication():
    """Otherwise the plan cannot write "1." or refer to line 7 or say 2026."""
    assert unstated_numbers("1. do this. see line 7. in 2026.", []) == set()


def test_attribution_needs_a_RECORDED_split_not_any_percentage():
    """The fabricated-relationship case, found live on the seeded tenant.

    Collecting every `*_pct` property produced "debit interchange cap accounts
    for 0% of the movement" — a real percentage, lifted from an unrelated
    market-research signal, asserted to explain a decline it says nothing
    about. Every digit traceable, sentence false.
    """
    stray = [
        sig("a", {"debit_interchange_cap_pct": 0.5}),
        sig("b", {"yield_pct": 4.0}),
        sig("c", {"us_corporate_card_share_pct": 3.0}),
    ]
    assert extract_facts(stray, ["decompose_metric"]) == []

    # A real decomposition: parts on ONE signal that account for the whole.
    real = [sig(f"s{i}", {"reduced_spend_pct": 54, "never_ramped_pct": 31,
                          "category_mix_pct": 15}) for i in range(3)]
    facts = extract_facts(real, ["decompose_metric"])
    keys = {f.key for f in facts}
    assert keys == {"attribution.reduced_spend", "attribution.never_ramped",
                    "attribution.category_mix"}


def test_parts_that_do_not_account_for_the_whole_are_not_a_split():
    """Two percentages summing to 12% are not a decomposition of anything."""
    partial = [sig("a", {"foo_pct": 7, "bar_pct": 5})]
    assert extract_facts(partial, ["decompose_metric"]) == []


def test_every_fact_carries_provenance():
    """A number without provenance is indistinguishable from an invented one,
    and this feature's credibility is exactly that difference."""
    sigs = [sig(f"s{i}", {"seat_adoption_pct": 5 if i % 3 else 80}) for i in range(40)]
    facts = extract_facts(sigs, ["adoption_shape"])
    assert facts
    for f in facts:
        assert f.signal_ids, f.key


def test_a_missing_property_yields_no_fact_rather_than_a_zero():
    """I3: unmeasured is not zero.

    Clustering once keyed on `properties.subject`, which was present on 0 of
    400 real signals, and produced nine taxonomy findings before anyone
    noticed. Absence must be silent, never confident.
    """
    blank = [sig(f"s{i}", {}) for i in range(50)]
    assert extract_facts(blank, ["adoption_shape", "decompose_metric",
                                 "shipped_levers", "reason_concentration"]) == []


def test_too_few_signals_state_nothing_about_shape():
    """Nineteen accounts cannot establish that adoption is bimodal."""
    few = [sig(f"s{i}", {"seat_adoption_pct": 5 if i % 2 else 80}) for i in range(19)]
    assert extract_facts(few, ["adoption_shape"]) == []


def test_an_extractor_that_raises_narrows_the_plan_rather_than_failing_it():
    """A missing fact means less may be said, which is the safe direction."""
    import app.crucible.plan_facts as pf

    def boom(_): raise RuntimeError("kaboom")
    orig = pf.EXTRACTORS["shipped_levers"]
    pf.EXTRACTORS["shipped_levers"] = (boom,)
    try:
        good = [sig(f"s{i}", {"seat_adoption_pct": 5 if i % 3 else 80}) for i in range(40)]
        facts = extract_facts(good, ["shipped_levers", "adoption_shape"])
        assert {f.key for f in facts} and all(not f.key.startswith("rollout") for f in facts)
    finally:
        pf.EXTRACTORS["shipped_levers"] = orig
