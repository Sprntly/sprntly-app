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
    constant_checklist_theme_labels,
    estimate_cost,
)
from app.crucible.pipeline import _cluster, _label

from tests.test_crucible_pipeline import claim as _claim

#: The checklist's own constant theme label for the 'objection' category
#: (`app.graph.extractor._CHECKLIST_CATEGORIES`, row 3 of the "objection"
#: entry) — what `assign_themes` actually writes to `subject` for every
#: checklist blocker claim, NOT the raw `kind` fallback ("deal_blocker")
#: a claim only ever carries before the graph has themed it.
CHECKLIST_THEME_SUBJECT = "deal blockers"

#: The graph theme entity id every checklist blocker claim shares in
#: production — `assign_themes` themes every 'objection' claim onto the
#: SAME entity, because there is only one "deal blockers" theme node. A
#: fixed, shared id here (rather than a fresh one per claim) is what makes
#: `a_claim`'s default the real shape: many claims, one cluster id.
CHECKLIST_THEME_SUBJECT_CLUSTER_ID = "kg:deal-blockers-theme-entity"


def a_claim(
    text, *, cid="c1", blocker_reason=None, accounts=("Northwind",),
    subject=CHECKLIST_THEME_SUBJECT,
    subject_cluster_id=CHECKLIST_THEME_SUBJECT_CLUSTER_ID,
):
    """One `constraint`-typed (deal-blocker) claim, optionally already
    classified.

    DEFAULTS MIRROR WHAT PRODUCTION ACTUALLY PRODUCES, not a pre-theming
    state. `execute_run` always runs `kg_themes.assign_themes` before
    `build_findings` — measured on a real 11,567-claim tenant: 0 claims with
    a falsy `subject_cluster_id`. For a checklist 'objection' claim,
    `assign_themes` overwrites BOTH `subject` (to the theme's own constant
    label, "deal blockers" — not the raw `kind` fallback) AND
    `subject_cluster_id` (to the graph's theme entity id, shared by every
    claim themed onto that one entity). A fixture built without either is a
    state the run path cannot reach, and testing only that state is how a
    change with zero production effect shipped green.
    """
    base = _claim(cid, assertion=text, ctype="constraint", subject=subject,
                  source="customer_voice", accounts=accounts)
    base = replace(base, subject_cluster_id=subject_cluster_id)
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
# ── (fixtures now carry the `subject_cluster_id` production always sets —  ─
# ── see `a_claim`'s own docstring for why an id-less fixture is dishonest) ─

def test_every_checklist_blocker_claim_shares_one_real_cluster_id_by_default():
    """The premise the rest of this section depends on, asserted rather than
    assumed: in production every 'objection' claim themes onto the SAME
    graph entity, so they all arrive at `_cluster` with an IDENTICAL,
    already-set `subject_cluster_id` — never `None`."""
    a, b = a_claim("x", cid="a"), a_claim("y", cid="b")
    assert a.subject_cluster_id == b.subject_cluster_id == CHECKLIST_THEME_SUBJECT_CLUSTER_ID
    assert a.subject == CHECKLIST_THEME_SUBJECT


def test_a_single_constant_theme_splits_into_reason_clusters():
    """THE DEFECT, reproduced with the shape production actually produces —
    every claim already carrying the SAME real `subject_cluster_id` — and
    fixed: a claim with a clusterable reason keys on the reason instead,
    because it is still sitting on the checklist's own constant theme
    (`subject == "deal blockers"`)."""
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
    fallback_key = CHECKLIST_THEME_SUBJECT_CLUSTER_ID.lower()
    assert set(clusters) >= {"budget", "legal_security_compliance", fallback_key}
    assert len(clusters["budget"]) == 4
    assert len(clusters["legal_security_compliance"]) == 3
    # The unclassified claim still lands on the checklist theme's own real
    # cluster id — degrade, never lose the claim.
    assert clusters[fallback_key] == [claims[-1]]


def test_other_and_unclassified_share_the_same_fallback_cluster():
    claims = [
        a_claim("does not fit any category", cid="other1", blocker_reason="other"),
        a_claim("no reason drawn", cid="none1", blocker_reason=None),
    ]
    clusters = _cluster(claims)
    fallback_key = CHECKLIST_THEME_SUBJECT_CLUSTER_ID.lower()
    assert set(clusters) == {fallback_key}
    assert len(clusters[fallback_key]) == 2


def test_a_classified_reason_overrides_the_checklist_themes_own_cluster_id():
    """THE FIX, and the reason it has to be a fix rather than a reorder.
    `assign_themes` sets `subject_cluster_id` on EVERY checklist claim it
    themes, to the one shared entity for that constant label — measured on
    a real 11,567-claim tenant: 0 claims arrive at `_cluster` with a falsy
    `subject_cluster_id`. Ranking the reason BELOW `subject_cluster_id` (the
    original shape) is therefore unreachable on every tenant, always: this
    is the inversion of `test_a_graph_supplied_cluster_id_still_wins_over_a_
    classified_reason`, which pinned exactly that unreachable precedence."""
    a = a_claim("no budget approved", cid="a", blocker_reason="budget")
    b = a_claim("still no budget", cid="b", blocker_reason="budget")
    assert a.subject_cluster_id == b.subject_cluster_id, (
        "both must start from the SAME real theme entity — the production shape"
    )
    clusters = _cluster([a, b])
    assert set(clusters) == {"budget"}


def test_a_graph_supplied_cluster_id_still_wins_when_not_on_a_checklist_theme():
    """The case the override must NOT touch: a `constraint` claim already
    sitting on a real, SPECIFIC graph theme rather than the checklist's
    constant one — the 38 `business_context`-sourced claims measured on a
    real tenant ("Budget & procurement", 8 accounts; "FedRAMP / compliance",
    2 accounts; …), never rewritten to the checklist's constant label. A
    reason must not pull these into a generic bucket: the unconditional
    version of this change was measured to destroy 5 real findings carrying
    >=2 accounts this exact way, including this one's own shape."""
    a = a_claim("a specific, already-themed business constraint", cid="a",
                blocker_reason="budget", subject="Budget & procurement",
                subject_cluster_id="kg:budget-procurement-entity")
    b = a_claim("another one, same real theme", cid="b", blocker_reason="budget",
                subject="Budget & procurement",
                subject_cluster_id="kg:budget-procurement-entity")
    clusters = _cluster([a, b])
    assert set(clusters) == {"kg:budget-procurement-entity"}


def test_the_label_of_a_reason_cluster_is_the_human_readable_display_label():
    """Without this, every reason cluster would still display "deal
    blockers" — every claim in it shares that constant `subject` — defeating
    the split at the one place a reader actually sees it."""
    claims = [a_claim(f"no budget, {i}", cid=f"b{i}", blocker_reason="budget")
              for i in range(3)]
    assert _label(claims, "budget") == BLOCKER_REASON_LABELS["budget"]
    assert _label(claims, "budget") != CHECKLIST_THEME_SUBJECT


def test_the_checklist_themes_own_residual_still_labels_as_the_theme():
    """The `other`/unclassified residual keeps the checklist theme's own
    cluster id as its key, and `_label` falls through to its ordinary
    most-common-subject behaviour for it — which, for these claims, IS the
    constant label, matching what a real run renders for the residual
    (measured: a 30-account finding still labelled `deal blockers` at
    rank #6)."""
    claims = [a_claim("does not fit any category", cid="o1", blocker_reason="other"),
              a_claim("no reason drawn", cid="n1", blocker_reason=None)]
    fallback_key = CHECKLIST_THEME_SUBJECT_CLUSTER_ID.lower()
    assert _label(claims, fallback_key) == CHECKLIST_THEME_SUBJECT


def test_an_ordinary_subject_cluster_label_is_unaffected():
    """The reason-label short-circuit must only fire for an actual reason
    key — an unrelated cluster whose members share a real subject keeps
    picking the most-common-subject behaviour untouched."""
    claims = [
        _claim("c1", subject="export latency"),
        _claim("c2", subject="export latency"),
    ]
    assert _label(claims, "export latency") == "export latency"


# ── The vocabulary of constant checklist theme labels itself ───────────────

def test_constant_checklist_theme_labels_includes_deal_blockers_and_is_small():
    labels = constant_checklist_theme_labels()
    assert CHECKLIST_THEME_SUBJECT in labels
    # Eleven "mint_signal" categories mint a Signal onto a theme
    # ("stakeholders" does not — see `_CHECKLIST_CATEGORIES`'s own comment).
    assert 3 <= len(labels) <= 12


def test_constant_checklist_theme_labels_is_cached():
    """Computed once — the checklist's own category table does not change
    during a process lifetime, so re-deriving it on every `_cluster` call
    would be pure waste."""
    assert constant_checklist_theme_labels() is constant_checklist_theme_labels()
