"""Grouping claims by what they are ABOUT.

This module exists because a run over 2,777 real signals produced nine
"findings" that were the corpus taxonomy read back — "1200 claims concern
finding". Clustering keyed on `properties.subject`, which is present on exactly
zero real signals, so every group collapsed to the claim's `kind`. The tests
here are the ones that would have caught that: they assert on what a reader
sees, not on whether a function returned something.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.crucible.cluster import (
    DEFAULT_THRESHOLD,
    assign_clusters,
    label_for,
    parse_embedding,
)
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


def claim(cid: str, assertion: str) -> Claim:
    return Claim(
        id=cid, assertion=assertion, type="mechanism", subject="",
        source_id="customer_voice", artifact_id="a", artifact_type="t",
        strength="reported", observed_at=NOW, authoritative=True,
        population=PopulationFilter(), direction="neutral",
    )


def vec(*parts: float) -> list[float]:
    return list(parts)


# ── Grouping ─────────────────────────────────────────────────────────────────

def test_near_identical_claims_land_in_one_group():
    claims = [claim("a", "checkout fails at payment"),
              claim("b", "checkout fails at payment step")]
    out = assign_clusters(claims, {"a": vec(1.0, 0.0), "b": vec(0.99, 0.14)})
    assert out[0].subject_cluster_id == out[1].subject_cluster_id


def test_unrelated_claims_do_not():
    claims = [claim("a", "checkout fails"), claim("b", "onboarding is slow")]
    out = assign_clusters(claims, {"a": vec(1.0, 0.0), "b": vec(0.0, 1.0)})
    assert out[0].subject_cluster_id != out[1].subject_cluster_id


def test_the_group_is_named_in_the_corpus_own_words():
    """Not "c490". The whole point is that a reader recognises the theme."""
    claims = [claim("a", "checkout fails at the payment step"),
              claim("b", "checkout failing at payment")]
    out = assign_clusters(claims, {"a": vec(1.0, 0.0), "b": vec(0.99, 0.14)})
    assert "checkout" in out[0].subject.lower()
    assert not out[0].subject.startswith("c")  or "checkout" in out[0].subject


def test_the_same_input_gives_the_same_grouping():
    """Reproducibility is the claim this engine makes against asking a general
    model the same question. Anything fitted or randomly initialised cannot
    offer it."""
    claims = [claim(str(i), f"theme {i % 3}") for i in range(9)]
    emb = {str(i): vec(1.0 if i % 3 == 0 else 0.0,
                       1.0 if i % 3 == 1 else 0.0,
                       1.0 if i % 3 == 2 else 0.0) for i in range(9)}
    first = [c.subject_cluster_id for c in assign_clusters(claims, emb)]
    second = [c.subject_cluster_id for c in assign_clusters(claims, emb)]
    assert first == second


def test_a_claim_with_no_embedding_is_kept_untouched():
    """A missing vector says nothing about whether the claim matters, so it is
    never dropped and never forced into someone else's group."""
    claims = [claim("a", "one"), claim("b", "two")]
    out = assign_clusters(claims, {"a": vec(1.0, 0.0)})
    assert len(out) == 2
    assert out[1].subject_cluster_id is None


def test_a_zero_vector_does_not_poison_the_run():
    """Normalising it would divide by zero and produce NaNs, which compare
    false against everything and turn matching into a coin flip."""
    claims = [claim("a", "one"), claim("b", "two")]
    out = assign_clusters(claims, {"a": vec(0.0, 0.0), "b": vec(1.0, 0.0)})
    assert len(out) == 2


def test_no_embeddings_at_all_is_a_no_op_rather_than_a_crash():
    claims = [claim("a", "one")]
    assert assign_clusters(claims, {}) == claims


# ── Labels are topics, not assertions ────────────────────────────────────────

def test_a_label_stops_at_the_source_own_because():
    """The label is a topic. Carrying a source's reasoning into a finding's
    statement would put a causal claim in OUR mouth that the evidence does not
    support (I5)."""
    out = label_for("Checkout abandons at payment because Stripe times out")
    assert out == "Checkout abandons at payment"


def test_a_label_is_cut_on_a_word_boundary():
    out = label_for("x " * 200)
    assert len(out) <= 90
    assert not out.endswith(" ")


def test_a_label_survives_empty_input():
    assert label_for("") == "unlabelled"
    assert label_for(None) == "unlabelled"


def test_the_label_passes_the_causal_lint_it_is_built_for():
    from app.crucible.lint import lint_claim

    text = label_for("Latency drives churn because the queue backs up")
    assert lint_claim(f'3 claims concern “{text}”.', "reported").ok


# ── pgvector arrives as a string ─────────────────────────────────────────────

def test_an_embedding_from_postgrest_is_a_json_string():
    assert parse_embedding("[1.0, 2.0]") == [1.0, 2.0]


def test_a_malformed_embedding_is_a_missing_one_not_a_crash():
    assert parse_embedding("not json") is None
    assert parse_embedding(None) is None
    assert parse_embedding({"nope": 1}) is None


def test_the_threshold_is_a_named_constant():
    """Calibrated on real data; a magic number in the loop would be re-tuned by
    whoever next looked at an output they disliked."""
    assert 0.7 < DEFAULT_THRESHOLD < 0.95
