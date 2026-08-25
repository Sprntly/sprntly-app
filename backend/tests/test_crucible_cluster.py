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
    UNGROUPABLE_PREFIX,
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
    out, _ = assign_clusters(claims, {"a": vec(1.0, 0.0), "b": vec(0.99, 0.14)})
    assert out[0].subject_cluster_id == out[1].subject_cluster_id


def test_unrelated_claims_do_not():
    claims = [claim("a", "checkout fails"), claim("b", "onboarding is slow")]
    out, _ = assign_clusters(claims, {"a": vec(1.0, 0.0), "b": vec(0.0, 1.0)})
    assert out[0].subject_cluster_id != out[1].subject_cluster_id


def test_the_group_is_named_in_the_corpus_own_words():
    """Not "c490". The whole point is that a reader recognises the theme."""
    claims = [claim("a", "checkout fails at the payment step"),
              claim("b", "checkout failing at payment")]
    out, _ = assign_clusters(claims, {"a": vec(1.0, 0.0), "b": vec(0.99, 0.14)})
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
    first = [c.subject_cluster_id for c in assign_clusters(claims, emb)[0]]
    second = [c.subject_cluster_id for c in assign_clusters(claims, emb)[0]]
    assert first == second


def test_a_claim_with_no_embedding_is_marked_ungroupable_not_left_unset():
    """A missing vector says nothing about whether the claim matters, so it is
    never dropped and never forced into someone else's group — but it must be
    MARKED. Left unset it falls through to the pipeline's fallback chain, whose
    next rung is the claim's kind, and it silently corroborates a taxonomy
    bucket. Only rows that HAD a vector were being marked, so a NULL
    `embedding` column arrived at that bug through the other door."""
    claims = [claim("a", "one"), claim("b", "two")]
    out, stats = assign_clusters(claims, {"a": vec(1.0, 0.0)})
    assert len(out) == 2
    assert out[1].subject_cluster_id.startswith(UNGROUPABLE_PREFIX)
    assert stats["degenerate"] == 1


def test_a_zero_vector_does_not_poison_the_run():
    """Normalising it would divide by zero and produce NaNs, which compare
    false against everything and turn matching into a coin flip."""
    claims = [claim("a", "one"), claim("b", "two")]
    out, stats = assign_clusters(claims, {"a": vec(0.0, 0.0), "b": vec(1.0, 0.0)})
    assert len(out) == 2
    # Excluded, not normalised to something arbitrary — and COUNTED, so the
    # caller can say so rather than reporting a business with no patterns.
    assert stats["degenerate"] == 1
    # Marked UNGROUPABLE, not left unset: an unset id falls through to
    # `_cluster`'s next rung, which is the claim's kind.
    assert out[0].subject_cluster_id.startswith(UNGROUPABLE_PREFIX)


def test_no_embeddings_at_all_marks_everything_rather_than_crashing():
    claims = [claim("a", "one"), claim("b", "two")]
    out, stats = assign_clusters(claims, {})
    assert all(c.subject_cluster_id.startswith(UNGROUPABLE_PREFIX) for c in out)
    assert stats["degenerate"] == 2


def test_a_subject_that_merely_looks_like_the_marker_is_not_mistaken_for_it():
    """"Ungroupable: legacy import" is a string a real signal can contain. The
    marker leads with NUL so it cannot be spelled by accident."""
    from app.crucible.cluster import UNGROUPABLE_PREFIX as P

    assert P.startswith("\x00")
    assert not "Ungroupable: legacy import".lower().startswith(P)


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
    out = parse_embedding("[1.0, 2.0]")
    assert list(out) == [1.0, 2.0]


def test_an_embedding_is_packed_as_float32_not_boxed_floats():
    """A list of 1536 Python floats is roughly eight times the memory of the
    same numbers packed. On a 10,000-signal tenant that is the difference
    between fitting in RAM and not."""
    import numpy as np

    out = parse_embedding("[1.0, 2.0]")
    assert isinstance(out, np.ndarray)
    assert out.dtype == np.float32


def test_a_malformed_embedding_is_a_missing_one_not_a_crash():
    assert parse_embedding("not json") is None
    assert parse_embedding(None) is None
    assert parse_embedding({"nope": 1}) is None


def test_the_threshold_is_a_named_constant():
    """Calibrated on real data; a magic number in the loop would be re-tuned by
    whoever next looked at an output they disliked."""
    assert 0.7 < DEFAULT_THRESHOLD < 0.95


# ─── The all-zero-vector failure, which looked exactly like a quiet success ──

def test_a_corpus_of_zero_vectors_is_reported_not_silently_ungrouped():
    """`embed_texts` returns ALL-ZERO vectors when no OpenAI key is
    configured. A zero vector has cosine 0.0 against everything, so it never
    reaches the threshold: every claim became its own cluster and the run
    reported "only 1 supporting claim — an anecdote" for the entire corpus
    while saying `ready`. A business with no patterns in it, indistinguishable
    from a missing API key."""
    claims = [claim(str(i), f"claim {i}") for i in range(6)]
    out, stats = assign_clusters(claims, {str(i): vec(0.0, 0.0) for i in range(6)})
    assert stats["degenerate"] == 6
    assert stats["embedded"] == 0
    assert all(c.subject_cluster_id.startswith(UNGROUPABLE_PREFIX) for c in out)


def test_a_non_finite_vector_is_treated_as_missing():
    """NaN compares false against everything, so it would match nothing and
    everything by turns depending on comparison order."""
    claims = [claim("a", "one"), claim("b", "two")]
    out, stats = assign_clusters(
        claims, {"a": [float("nan"), 1.0], "b": vec(1.0, 0.0)})
    assert stats["degenerate"] == 1
    # Marked UNGROUPABLE, not left unset: an unset id falls through to
    # `_cluster`'s next rung, which is the claim's kind.
    assert out[0].subject_cluster_id.startswith(UNGROUPABLE_PREFIX)


def test_ragged_vectors_decline_rather_than_guess():
    claims = [claim("a", "one"), claim("b", "two")]
    out, stats = assign_clusters(claims, {"a": [1.0, 0.0], "b": [1.0, 0.0, 0.0]})
    assert stats["clusters"] == 0
    # Declining still has to MARK them — an untouched claim falls through to
    # grouping by kind, which is the failure this whole path exists to avoid.
    assert all(c.subject_cluster_id.startswith(UNGROUPABLE_PREFIX) for c in out)


def test_an_empty_vector_is_missing_rather_than_ragged():
    """`parse_embedding("[]")` used to yield a zero-length array, making the
    whole matrix ragged — so one bad row disabled clustering for an entire
    tenant."""
    assert parse_embedding("[]") is None


# ─── The label is the medoid, not whoever sorted first ───────────────────────

def test_the_label_comes_from_the_most_central_member():
    """The leader is simply whichever member appeared first in the input.
    Naming a theme after an arbitrary member is how nine claims about billing
    end up titled with the one sentence about a calendar invite.

    Here the FIRST claim is the outlier: it is in the group, but the other
    three sit together, so the group should be named after them.
    """
    claims = [claim("odd", "a calendar invite was declined"),
              claim("b1", "billing retries fail on renewal"),
              claim("b2", "billing retries fail on renewal again"),
              claim("b3", "billing retries failing at renewal")]
    emb = {
        "odd": vec(1.0, 0.30),
        "b1": vec(1.0, 0.00),
        "b2": vec(1.0, 0.01),
        "b3": vec(1.0, 0.02),
    }
    out, stats = assign_clusters(claims, emb, threshold=0.90)
    assert stats["clusters"] == 1, "fixture must put all four in one group"
    assert "billing" in out[0].subject.lower(), out[0].subject


def test_clustering_a_realistic_corpus_finishes_quickly():
    """A real tenant produced 1,744 clusters from 2,777 signals, so the
    many-clusters case IS the normal case — and growing the leader block with
    `np.vstack` per cluster reallocates and copies it every time, which is
    quadratic in exactly that dimension.
    """
    import time

    import numpy as np

    rng = np.random.default_rng(0)          # seeded: this must not be flaky
    n, dim = 3000, 128
    claims = [claim(str(i), f"claim {i}") for i in range(n)]
    emb = {str(i): rng.normal(size=dim).tolist() for i in range(n)}

    started = time.monotonic()
    out, stats = assign_clusters(claims, emb, threshold=0.84)
    elapsed = time.monotonic() - started

    # Random high-dimensional vectors are near-orthogonal, so this is close to
    # the worst case: almost every claim becomes its own leader.
    assert stats["clusters"] > n * 0.9
    assert elapsed < 20, f"clustering {n} claims took {elapsed:.1f}s"

# ── The quoted example ───────────────────────────────────────────────────────

def test_an_example_keeps_a_whole_short_claim():
    """The label budget (90) cut real findings mid-clause on staging — "…a
    native, clean bidirectional NetSuite sync that eliminates the need for" —
    which reads as a source who trailed off rather than a sentence we clipped."""
    from app.crucible.cluster import example_for
    said = ("Rippling offers a native, clean bidirectional NetSuite sync that "
            "eliminates the need for manual CSV uploads")
    assert example_for(said) == said


def test_an_example_that_must_be_cut_says_it_was_cut():
    """Silently stopping mid-clause is the one option that misrepresents the
    source: the reader cannot see that anything was removed."""
    from app.crucible.cluster import example_for, _EXAMPLE_MAX
    said = "word " * 200
    out = example_for(said)
    assert len(out) <= _EXAMPLE_MAX + 1
    assert out.endswith("\u2026")


def test_an_example_prefers_the_source_own_sentence_end():
    """A quote that ends where the source ended a thought reads as a quote."""
    from app.crucible.cluster import example_for, _EXAMPLE_MAX
    first = "A" + "a" * (_EXAMPLE_MAX - 60) + " ends here."
    out = example_for(first + " And then a second sentence runs on well past the budget.")
    assert out.endswith("ends here.")
    assert "\u2026" not in out


def test_an_example_takes_the_same_causal_cut_as_a_label():
    """Identical I5 guarantee: a quote that explains WHY would be an
    unsupported causal claim wearing quotation marks."""
    from app.crucible.cluster import example_for
    out = example_for("Keystrokes drop in the mobile editor because the sync loop stalls")
    assert out == "Keystrokes drop in the mobile editor"


def test_an_example_of_nothing_is_empty_not_a_placeholder():
    """`label_for` answers "unlabelled", which is a fine label and a terrible
    quotation — it puts quotation marks around a word no source ever said."""
    from app.crucible.cluster import example_for
    assert example_for("") == ""
    assert example_for(" ,;: ") == ""
