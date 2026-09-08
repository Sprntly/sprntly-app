"""WHY a deal is blocked — the classifier that breaks apart the checklist's
single `deal blockers` theme.

No network and no model call: `_offline()` holds under pytest, and the tests
that exercise the real path stub the gateway — same convention as
`test_crucible_figure_class.py`, which this file mirrors throughout.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.crucible.blocker_reason import (
    BLOCKER_REASON_KEYS,
    BLOCKER_REASON_LABELS,
    BLOCKER_REASONS,
    CHUNK,
    CLASSIFY_SCHEMA,
    CLUSTERABLE_REASONS,
    PROPERTY_KEY,
    apply_reasons,
    classify_blocker_reasons,
    estimate_cost,
)
from app.crucible.pipeline import _cluster, _label

from tests.test_crucible_pipeline import claim as _claim


def a_claim(text, *, cid="c1", blocker_reason=None, accounts=("Northwind",)):
    """One `constraint`-typed (deal-blocker) claim, optionally already
    classified."""
    base = _claim(cid, assertion=text, ctype="constraint", subject="deal_blocker",
                  source="customer_voice", accounts=accounts)
    return replace(base, blocker_reason=blocker_reason)


# ── The closed vocabulary ────────────────────────────────────────────────────

def test_the_vocabulary_is_closed_small_and_the_schema_matches_it():
    """A value outside the vocabulary must be impossible to request and
    impossible to accept — the schema constrains the model, and the reader
    in `_classify_chunk` re-checks rather than trusting it. Small, per the
    module's own reasoning: a free-text field is a closed vocabulary that
    quietly stopped being closed."""
    enum = CLASSIFY_SCHEMA["properties"]["classifications"]["items"][
        "properties"]["blocker_reason"]["enum"]
    assert tuple(enum) == BLOCKER_REASON_KEYS
    assert "other" in BLOCKER_REASON_KEYS
    assert 3 <= len(BLOCKER_REASON_KEYS) <= 10


def test_the_schema_returns_a_category_and_nothing_else():
    """I2. The model may return a reason and an index — never a score, a
    rank, a severity, or which account it concerns."""
    props = CLASSIFY_SCHEMA["properties"]["classifications"]["items"][
        "properties"]
    assert set(props) == {"idx", "blocker_reason"}
    from app.crucible.invariants import assert_llm_schema_returns_no_decision

    assert_llm_schema_returns_no_decision(CLASSIFY_SCHEMA, "classify_blocker_reason")


def test_other_is_a_valid_draw_but_not_clusterable():
    """`other` is a real answer the model may give, and it is deliberately
    excluded from the set that overrides the cluster key — see
    `CLUSTERABLE_REASONS`'s own docstring: a second catch-all bucket under a
    new name is the exact failure this module exists to avoid."""
    assert "other" in BLOCKER_REASON_KEYS
    assert "other" not in CLUSTERABLE_REASONS
    assert CLUSTERABLE_REASONS == frozenset(BLOCKER_REASON_KEYS) - {"other"}


# ── Property tests on the LLM-facing category descriptions (I3 standing rule) ─

@pytest.mark.parametrize("key,description,label", BLOCKER_REASONS)
def test_every_category_description_is_a_real_sentence(key, description, label):
    """A vague description is how a closed vocabulary quietly becomes a
    free-text field. Each description must be long enough to distinguish the
    category from its neighbours and must not just repeat the key."""
    assert len(description) >= 40, (key, description)
    assert len(description) <= 400, (key, description)
    assert description[0].isupper() or key == "other", (key, description)
    assert description.strip().endswith("."), (key, description)
    # Not merely the key restated with spaces — a real sentence, not a label.
    assert description.lower() != key.replace("_", " "), (key, description)


@pytest.mark.parametrize("key,description,label", BLOCKER_REASONS)
def test_every_report_label_is_short_and_human_readable(key, description, label):
    """The label is what a reader sees as a finding's headline — bounded so
    it reads as a phrase, not a sentence, and never just the raw key."""
    assert 5 <= len(label) <= 60, (key, label)
    assert "_" not in label, (key, label)
    assert label == label.lower(), (key, label)


def test_the_vocabulary_has_no_duplicate_keys_or_labels():
    keys = [k for k, _d, _l in BLOCKER_REASONS]
    labels = [l for _k, _d, l in BLOCKER_REASONS]
    assert len(keys) == len(set(keys))
    assert len(labels) == len(set(labels))


def test_every_key_has_a_label_mapping():
    assert set(BLOCKER_REASON_LABELS) == set(BLOCKER_REASON_KEYS)


def test_the_system_prompt_mentions_every_category_and_its_description():
    from app.crucible.blocker_reason import _SYSTEM

    for key, description, _label in BLOCKER_REASONS:
        assert key in _SYSTEM
        assert description in _SYSTEM


# ── Scoped to constraint claims only — the other eleven checklist themes ────
# ── are never candidates. ────────────────────────────────────────────────────

def test_only_constraint_typed_claims_are_candidates(monkeypatch):
    """`product_gap`/`legal`/`timeline`/... claims must never be sent —
    scoping to `type == constraint` is what keeps this change from touching
    any of the other eleven checklist themes."""
    import app.crucible.blocker_reason as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)
    seen: list[int] = []

    def _capture(*, enterprise_id, candidates):
        seen.append(len(candidates))
        return {}

    monkeypatch.setattr(mod, "_classify_chunk", _capture)
    claims = [
        a_claim("no budget until Q4", cid="blocker"),
        _claim("pg", ctype="preference", subject="product gaps"),
        _claim("legal", ctype="mechanism", subject="legal & compliance"),
    ]
    classify_blocker_reasons(claims, enterprise_id="co")
    assert seen == [1]


def test_classification_is_skipped_entirely_under_pytest():
    """No test may spend money by accident."""
    claims = [a_claim("no budget until Q4", cid=f"c{i}") for i in range(5)]
    assert classify_blocker_reasons(claims, enterprise_id="co") == {}


def test_a_failed_chunk_loses_only_its_own_claims(monkeypatch):
    import app.crucible.blocker_reason as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)
    calls = {"n": 0}

    def _boom(**kwargs):
        calls["n"] += 1
        raise RuntimeError("simulated model failure")

    monkeypatch.setattr(mod, "_classify_chunk", _boom)
    claims = [a_claim("no budget until Q4", cid=f"c{i}") for i in range(5)]
    assert classify_blocker_reasons(claims, enterprise_id="co") == {}
    assert calls["n"] == 1, "five rows is one chunk"


def test_a_reason_outside_the_vocabulary_is_dropped_not_passed_through(monkeypatch):
    """Enforced in the reader, not trusted from the schema."""
    import app.crucible.blocker_reason as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)

    class _Result:
        output = {"classifications": [
            {"idx": 1, "blocker_reason": "budget"},
            {"idx": 2, "blocker_reason": "definitely_a_real_reason_trust_me"},
        ]}

    monkeypatch.setattr("app.graph.gateway.llm_call",
                        lambda **kw: _Result(), raising=False)
    claims = [a_claim("first", cid="a"), a_claim("second", cid="b")]
    out = classify_blocker_reasons(claims, enterprise_id="co")
    assert out == {"a": "budget"}


def test_the_fast_model_is_used_for_this_shape(monkeypatch):
    import app.crucible.blocker_reason as mod
    from app.llm import FAST_MODEL

    monkeypatch.setattr(mod, "_offline", lambda: False)
    captured: dict = {}

    class _Result:
        output = {"classifications": []}

    def _capture(**kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr("app.graph.gateway.llm_call", _capture, raising=False)
    classify_blocker_reasons([a_claim("x")], enterprise_id="co")
    assert captured["model"] == FAST_MODEL
    assert captured["json_schema"] is CLASSIFY_SCHEMA
    assert captured["agent"] == "crucible"


# ── Applying the result ────────────────────────────────────────────────────

def test_apply_reasons_touches_only_the_claims_it_has_an_answer_for():
    claims = [a_claim("first", cid="a"), a_claim("second", cid="b")]
    out = apply_reasons(claims, {"a": "budget"})
    assert out[0].blocker_reason == "budget"
    assert out[1].blocker_reason is None
    assert out[0] is not claims[0]
    assert out[1] is claims[1]
    assert out[0].assertion == "first"


# ── Classify once. Persist before use, take the first sample. ──────────────

def test_a_row_that_already_has_a_reason_is_never_re_sent(monkeypatch):
    """THE REPRODUCIBILITY CLAIM. If someone reintroduces per-run
    classification this fails loudly."""
    import app.crucible.blocker_reason as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)
    sent: list[list[str]] = []

    def _capture(*, enterprise_id, candidates):
        sent.append([c.id for c in candidates])
        return {}

    monkeypatch.setattr(mod, "_classify_chunk", _capture)
    claims = [
        a_claim("already judged", cid="stored", blocker_reason="budget"),
        a_claim("not yet judged", cid="fresh"),
    ]
    classify_blocker_reasons(claims, enterprise_id="co")
    assert sent == [["fresh"]], "a stored reason must never be re-drawn"


def test_a_fully_classified_corpus_costs_nothing_and_calls_nothing(monkeypatch):
    import app.crucible.blocker_reason as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)

    def _explode(**kwargs):
        raise AssertionError("no call should be made")

    monkeypatch.setattr(mod, "_classify_chunk", _explode)
    claims = [a_claim("judged", cid=f"c{i}", blocker_reason="budget")
              for i in range(5)]
    assert classify_blocker_reasons(claims, enterprise_id="co") == {}
    assert estimate_cost(claims)["candidates"] == 0
    assert estimate_cost(claims)["calls"] == 0


def test_the_second_run_over_identical_input_produces_the_identical_answer(
    monkeypatch,
):
    """Run once against a model that answers differently each time, store the
    result, and the second run must reuse the stored reason rather than take
    the new draw — never re-roll a stored class to check it."""
    import app.crucible.blocker_reason as mod

    monkeypatch.setattr(mod, "_offline", lambda: False)
    draws = iter(["budget", "timing_priority"])

    def _flaky(*, enterprise_id, candidates):
        verdict = next(draws)
        return {c.id: verdict for c in candidates}

    monkeypatch.setattr(mod, "_classify_chunk", _flaky)

    claims = [a_claim("no budget approved yet", cid="row")]
    first = classify_blocker_reasons(claims, enterprise_id="co")
    assert first == {"row": "budget"}

    stored = apply_reasons(claims, first)
    second = classify_blocker_reasons(stored, enterprise_id="co")

    assert second == {}, "the stored reason must be reused, not re-drawn"
    assert stored[0].blocker_reason == "budget"


def test_a_stored_reason_is_read_back_off_the_signal():
    """The read half of the round trip, through the real projection."""
    from app.crucible.claims import project_signal

    claim = project_signal({
        "id": "s-1", "kind": "deal_blocker", "source_type": "customer_voice",
        "content": "no budget until next fiscal year",
        "valid_at": "2026-08-01T12:00:00+00:00",
        "properties": {PROPERTY_KEY: "budget"},
    }, {})
    assert claim is not None
    assert claim.blocker_reason == "budget"


def test_a_stored_reason_outside_the_vocabulary_is_ignored():
    from app.crucible.claims import project_signal

    claim = project_signal({
        "id": "s-1", "kind": "deal_blocker", "source_type": "customer_voice",
        "content": "no budget until next fiscal year",
        "valid_at": "2026-08-01T12:00:00+00:00",
        "properties": {PROPERTY_KEY: "definitely_a_real_reason"},
    }, {})
    assert claim is not None
    assert claim.blocker_reason is None


# ── Costing a run before paying for it ──────────────────────────────────────

def test_the_estimate_is_counts_only_and_never_a_stale_price():
    claims = [a_claim("no budget until Q4", cid=f"c{i}") for i in range(11)]
    est = estimate_cost(claims)
    assert est["candidates"] == 11
    assert est["calls"] == 1
    assert est["estimated_input_tokens"] > 0
    assert est["estimated_output_tokens"] > 0
    assert not any("cost" in k or "usd" in k or "price" in k for k in est)


def test_the_estimate_chunks_the_way_the_run_does():
    claims = [a_claim("no budget until Q4", cid=f"c{i}")
              for i in range(CHUNK * 2 + 1)]
    assert estimate_cost(claims)["calls"] == 3


# ── The defect this module fixes: `_cluster`/`_label` on a real shape ──────
# ── (a fake, mixed corpus mirroring the real one's dominating structure) ───

def test_a_single_constant_theme_splits_into_reason_clusters():
    """THE DEFECT, reproduced and fixed. Without a classified reason every
    one of these claims keys to the same cluster (`subject="deal_blocker"`,
    the checklist's constant fallback); with one, they split by why."""
    claims = (
        [a_claim(f"no budget approved, account {i}", cid=f"b{i}",
                 blocker_reason="budget", accounts=(f"Acct{i}",))
         for i in range(4)]
        + [a_claim(f"legal review pending, account {i}", cid=f"l{i}",
                   blocker_reason="legal_security_compliance",
                   accounts=(f"Acct{i+10}",))
           for i in range(3)]
        + [a_claim("model did not answer for this one", cid="unclassified",
                   accounts=("AcctX",))]
    )
    clusters = _cluster(claims)
    assert set(clusters) >= {"budget", "legal_security_compliance", "deal_blocker"}
    assert len(clusters["budget"]) == 4
    assert len(clusters["legal_security_compliance"]) == 3
    # The unclassified claim still lands on the constant fallback — degrade,
    # never lose the claim.
    assert clusters["deal_blocker"] == [claims[-1]]


def test_other_and_unclassified_share_the_same_fallback_cluster():
    claims = [
        a_claim("does not fit any category", cid="other1", blocker_reason="other"),
        a_claim("no reason drawn", cid="none1", blocker_reason=None),
    ]
    clusters = _cluster(claims)
    assert set(clusters) == {"deal_blocker"}
    assert len(clusters["deal_blocker"]) == 2


def test_a_graph_supplied_cluster_id_still_wins_over_a_classified_reason():
    """`subject_cluster_id` is the graph's own, more specific answer and must
    keep outranking a reason classification — same precedence order as the
    pre-existing `subject_cluster_id or subject` chain."""
    a = replace(a_claim("x", cid="a", blocker_reason="budget"),
                subject_cluster_id="graph-cluster-9")
    b = replace(a_claim("y", cid="b", blocker_reason="budget"),
                subject_cluster_id="graph-cluster-9")
    clusters = _cluster([a, b])
    assert set(clusters) == {"graph-cluster-9"}


def test_the_label_of_a_reason_cluster_is_the_human_readable_display_label():
    """Without this, every reason cluster would still display "deal_blocker"
    — every claim in it shares that constant `subject` — defeating the split
    at the one place a reader actually sees it."""
    claims = [a_claim(f"no budget, {i}", cid=f"b{i}", blocker_reason="budget")
              for i in range(3)]
    assert _label(claims, "budget") == BLOCKER_REASON_LABELS["budget"]
    assert _label(claims, "budget") != "deal_blocker"


def test_an_ordinary_subject_cluster_label_is_unaffected():
    """The reason-label short-circuit must only fire for an actual reason
    key — an unrelated cluster whose members share a real subject keeps
    picking the most-common-subject behaviour untouched."""
    claims = [
        _claim("c1", subject="export latency"),
        _claim("c2", subject="export latency"),
    ]
    assert _label(claims, "export latency") == "export latency"
