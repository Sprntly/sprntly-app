"""What kind of money a recovered figure is — the classifier that replaced
five rounds of phrase patterns.

THE ROWS BELOW ARE REAL. Every case in `LIVE_ROWS` is a paraphrase from a
live run's committed head, spot-checked against its source text. Nine of the
eleven were wrong, and the wrongness was not one category error repeated —
it was four separate conversation genres the phrase families had never
modelled: compensation, cost avoidance, hypotheticals, and a job candidate's
track record at a previous employer.

No network and no model call: `_offline()` holds under pytest, and the tests
that exercise the real path stub the gateway.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.crucible.figure_class import (
    CHUNK,
    CLASSIFY_SCHEMA,
    FIGURE_CLASSES,
    RANGE_CLASS,
    SUMMABLE_CLASS,
    apply_classes,
    classify_figures,
    estimate_cost,
)
from app.crucible.pipeline import _figure_is_committed, _figure_is_list_price

from tests.test_crucible_pipeline import claim as _claim


def a_claim(text, amount, *, cid="c1", figure_class=None, kind="commercial_term"):
    """One figure-bearing claim, optionally already classified."""
    base = _claim(cid, assertion=text, magnitude=amount, ctype="magnitude",
                  source="revenue", accounts=("Northwind",),
                  raw={"currency": "USD"}, artifact_type=kind)
    return replace(base, figure_class=figure_class)


#: The eleven rows spot-checked against source text on a live run, with the
#: category each one actually is. Two were real deals; nine were not.
LIVE_ROWS: tuple[tuple[str, float, str], ...] = (
    # ── The two that were real ──────────────────────────────────────────
    ("deals nearing closure with two accounts, together valued at $165k",
     165_000.0, "deal_value"),
    ("a $150K payment was agreed for the initial phase",
     150_000.0, "deal_value"),
    # ── Compensation: an entire conversation genre, four of the nine ────
    ("their previous OTE was $260K at the last company",
     260_000.0, "compensation"),
    ("the role offers a $150K base salary plus equity",
     150_000.0, "compensation"),
    ("hiring a BDR at a $50k base plus $100 per meeting booked",
     50_000.0, "compensation"),
    ("the candidate was previously on a $170,000 package",
     170_000.0, "compensation"),
    # ── Money deliberately not spent ────────────────────────────────────
    ("they avoided spending $75K on a booth at the conference this year",
     75_000.0, "cost_avoidance"),
    # ── Money that never happened ───────────────────────────────────────
    ("a potential deal size of $200k if a prospect were to buy the add-on",
     200_000.0, "hypothetical"),
    ("that would be worth about $80k to us if it landed",
     80_000.0, "hypothetical"),
    # ── Somebody else's money ───────────────────────────────────────────
    ("the candidate directly influenced $3.7M TCV with a 43% close rate",
     3_700_000.0, "third_party"),
    ("customers currently pay around $3,000,000 to a competing vendor",
     3_000_000.0, "third_party"),
)


# ── The closed vocabulary and the consequence table ─────────────────────────

def test_the_vocabulary_is_closed_and_the_schema_matches_it():
    """A value outside the vocabulary must be impossible to request and
    impossible to accept — the schema constrains the model, and the reader
    in `_classify_chunk` re-checks rather than trusting it."""
    enum = CLASSIFY_SCHEMA["properties"]["classifications"]["items"][
        "properties"]["figure_class"]["enum"]
    assert tuple(enum) == FIGURE_CLASSES
    assert SUMMABLE_CLASS in FIGURE_CLASSES
    assert RANGE_CLASS in FIGURE_CLASSES
    assert "other" in FIGURE_CLASSES


def test_the_schema_returns_a_category_and_nothing_else():
    """I2. The model may return a class and an index. It may not return a
    score, a rank, a confidence, or an amount — the amount stays the
    parser's."""
    props = CLASSIFY_SCHEMA["properties"]["classifications"]["items"][
        "properties"]
    assert set(props) == {"idx", "figure_class"}
    from app.crucible.invariants import assert_llm_schema_returns_no_decision

    assert_llm_schema_returns_no_decision(CLASSIFY_SCHEMA, "classify_figure")


@pytest.mark.parametrize("figure_class", FIGURE_CLASSES)
def test_only_deal_value_is_ever_summed(figure_class):
    """THE CONSEQUENCE TABLE, and it lives in deterministic code. The model
    proposes a class; which class may enter a total is settled here and is
    not something the model can move."""
    c = a_claim("some text", 50_000.0, figure_class=figure_class)
    assert _figure_is_committed(c, 50_000.0, frozenset()) is (
        figure_class == "deal_value"
    )


@pytest.mark.parametrize("figure_class", FIGURE_CLASSES)
def test_only_list_price_is_ever_ranged(figure_class):
    c = a_claim("some text", 50_000.0, figure_class=figure_class)
    assert _figure_is_list_price(c, 50_000.0, frozenset()) is (
        figure_class == "list_price"
    )


@pytest.mark.parametrize("text,amount,figure_class", LIVE_ROWS)
def test_every_spot_checked_row_lands_where_it_belongs(text, amount, figure_class):
    """All eleven, by their real category. The two deals survive; the nine
    others are refused, each with its own category as the reason."""
    c = a_claim(text, amount, figure_class=figure_class)
    committed = _figure_is_committed(c, amount, frozenset())
    ranged = _figure_is_list_price(c, amount, frozenset())
    assert committed is (figure_class == "deal_value"), text
    assert ranged is (figure_class == "list_price"), text


def test_the_nine_wrong_rows_are_refused_outright_not_reclassified_as_prices():
    """THE THIRD STATE IS THE POINT. Before the classifier, anything that
    failed the committed test became a price — so a candidate's "$3.7M TCV"
    at a previous employer became the pricing MAXIMUM and the range rendered
    as $1,000 – $3,700,000. A figure can now be neither."""
    for text, amount, figure_class in LIVE_ROWS:
        if figure_class == "deal_value":
            continue
        c = a_claim(text, amount, figure_class=figure_class)
        assert not _figure_is_committed(c, amount, frozenset()), text
        assert not _figure_is_list_price(c, amount, frozenset()), text


def test_a_track_record_figure_can_no_longer_become_the_pricing_maximum():
    """The specific rendering failure, named."""
    c = a_claim("the candidate directly influenced $3.7M TCV with a 43% "
                "close rate", 3_700_000.0, figure_class="third_party")
    assert not _figure_is_list_price(c, 3_700_000.0, frozenset())


# ── Falling back, rather than falling over ─────────────────────────────────

def test_an_unclassified_claim_falls_back_to_the_deterministic_rules():
    """A model outage must degrade the answer, not empty it. `None` is not a
    category and never means "assume the good one" — the fallback still
    requires a positive committed signal."""
    quoted = a_claim("A $9,000 quote was issued", 9_000.0, figure_class=None)
    neutral = a_claim("the figure came up", 260_000.0, figure_class=None)
    assert _figure_is_committed(quoted, 9_000.0, frozenset())
    assert not _figure_is_committed(neutral, 260_000.0, frozenset())


def test_classification_is_skipped_entirely_under_pytest():
    """No test may spend money by accident."""
    claims = [a_claim(t, a) for t, a, _ in LIVE_ROWS]
    assert classify_figures(claims, enterprise_id="co") == {}


def test_a_failed_chunk_loses_only_its_own_claims(monkeypatch):
    import app.crucible.figure_class as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("simulated model failure")

    monkeypatch.setattr(mod, "_classify_chunk", _boom)
    claims = [a_claim(t, a, cid=f"c{i}") for i, (t, a, _) in enumerate(LIVE_ROWS)]
    assert classify_figures(claims, enterprise_id="co") == {}
    assert calls["n"] == 1, "eleven rows is one chunk"


def test_only_claims_carrying_a_figure_are_sent(monkeypatch):
    """Cost is proportional to figures, not to corpus size."""
    import app.crucible.figure_class as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)
    seen: list[int] = []

    def _capture(*, enterprise_id, candidates):
        seen.append(len(candidates))
        return {}

    monkeypatch.setattr(mod, "_classify_chunk", _capture)
    claims = [a_claim("a deal closed at this value", 50_000.0, cid="has")]
    claims.append(_claim("none", magnitude=None))
    classify_figures(claims, enterprise_id="co")
    assert seen == [1]


def test_a_class_outside_the_vocabulary_is_dropped_not_passed_through(monkeypatch):
    """Enforced in the reader, not trusted from the schema, so an
    unrecognised label can never reach the consequence table."""
    import app.crucible.figure_class as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)

    class _Result:
        output = {"classifications": [
            {"idx": 1, "figure_class": "deal_value"},
            {"idx": 2, "figure_class": "definitely_a_deal_trust_me"},
        ]}

    monkeypatch.setattr("app.graph.gateway.llm_call",
                        lambda **kw: _Result(), raising=False)
    claims = [a_claim("first", 1000.0, cid="a"), a_claim("second", 2000.0, cid="b")]
    out = classify_figures(claims, enterprise_id="co")
    assert out == {"a": "deal_value"}


def test_the_fast_model_is_used_for_this_shape(monkeypatch):
    """Closed set, one enum per item — the shape the fast model's own
    charter names, not the reasoning-depth job."""
    import app.crucible.figure_class as mod
    from app.llm import FAST_MODEL

    monkeypatch.setattr(mod, "_offline", lambda: False)
    captured: dict = {}

    class _Result:
        output = {"classifications": []}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr("app.graph.gateway.llm_call", _capture, raising=False)
    classify_figures([a_claim("x", 1000.0)], enterprise_id="co")
    assert captured["model"] == FAST_MODEL
    assert captured["json_schema"] is CLASSIFY_SCHEMA
    assert captured["agent"] == "crucible"


# ── Applying the result ────────────────────────────────────────────────────

def test_apply_classes_touches_only_the_claims_it_has_an_answer_for():
    claims = [a_claim("first", 1000.0, cid="a"), a_claim("second", 2000.0, cid="b")]
    out = apply_classes(claims, {"a": "compensation"})
    assert out[0].figure_class == "compensation"
    assert out[1].figure_class is None
    # Frozen: new objects, and nothing else moved.
    assert out[0] is not claims[0]
    assert out[1] is claims[1]
    assert out[0].magnitude == 1000.0
    assert out[0].assertion == "first"


# ── Costing a run before paying for it ─────────────────────────────────────

def test_the_estimate_is_counts_only_and_never_a_stale_price():
    """Rates change. A price constant in here would be quoted as fact long
    after it stopped being true, so this returns volumes and leaves the
    arithmetic to whoever holds the current numbers."""
    claims = [a_claim(t, a, cid=f"c{i}") for i, (t, a, _) in enumerate(LIVE_ROWS)]
    est = estimate_cost(claims)
    assert est["candidates"] == len(LIVE_ROWS)
    assert est["calls"] == 1
    assert est["estimated_input_tokens"] > 0
    assert est["estimated_output_tokens"] > 0
    assert not any("cost" in k or "usd" in k or "price" in k for k in est)


def test_the_estimate_chunks_the_way_the_run_does():
    claims = [a_claim("a deal closed", 1000.0 + i, cid=f"c{i}")
              for i in range(CHUNK * 2 + 1)]
    assert estimate_cost(claims)["calls"] == 3
