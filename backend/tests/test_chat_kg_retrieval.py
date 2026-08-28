"""Tests for the Ask × Knowledge Graph bridge (#18).

Two layers:

1. `app.graph.retrieval.retrieve_context` — pure, tenant-scoped retrieval.
   The fake Supabase has no pgvector, so `find_candidates` returns [] there;
   we mock embeddings + find_candidates and assert the bundle is composed from
   the REAL seeded `active_signals` / `edges_to` / `load_session_context`
   reads. What's genuinely exercised: edge-walking, dedup, ranking, the token
   budget cap, session-context folding, and tenant isolation. The pgvector
   kNN ordering itself is mocked (integration-tested against real Supabase).

2. `app.ask_runner.compose_ask_answer` + POST /v1/ask — the wiring: combined
   corpus+KG prompt, the KG context section, corpus-only fallback, and the
   decision-log row carrying kg_refs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ─────────────────────────── seeding helpers ───────────────────────────


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade

    return GraphFacade()


def _seed_theme_with_signals(facade, ent, label, specs):
    """specs: list of (source_type, kind, content, props, age_days).
    Wires each signal to the theme via a REQUESTS edge (signal → theme)."""
    from app.graph.types import Entity, Relationship, Signal

    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    now = datetime.now(timezone.utc)
    sigs = []
    for st, kind, content, props, age in specs:
        sig = Signal(
            enterprise_id=ent,
            source_type=st,
            kind=kind,
            content=content,
            properties=props,
            valid_at=now - timedelta(days=age),
        )
        facade.write_signal(ent, sig)
        facade.write_relationship(
            ent,
            Relationship(
                enterprise_id=ent,
                type="REQUESTS",
                source_kind="signal",
                source_id=sig.id,
                target_kind="entity",
                target_id=theme.id,
            ),
        )
        sigs.append(sig)
    return theme, sigs


def _patch_candidates(theme_scores):
    """Patch GraphFacade.find_candidates to return (Entity, score) tuples —
    stands in for the pgvector kNN the fake backend can't run. `theme_scores`
    is a list of (theme_entity, score)."""
    from app.graph.facade import GraphFacade

    return patch.object(
        GraphFacade, "find_candidates", lambda self, ent, typ, vec, k=10: list(theme_scores)
    )


def _patch_embed():
    """Patch the embeddings call retrieval imports lazily.

    Full `EMBEDDING_DIM` length (not a short stand-in): `retrieve_context` now
    has a defence-in-depth check that drops any vector of the wrong length
    before it reaches `find_candidates` (mirrors `document_catalog.py`), so a
    fixture vector shorter than that would be silently treated as no
    embedding and every theme-matching test below would stop exercising the
    kNN branch it's meant to."""
    from app.graph.embeddings import EMBEDDING_DIM

    return patch(
        "app.graph.embeddings.embed_texts",
        side_effect=lambda texts, **k: [[0.1] * EMBEDDING_DIM for _ in texts],
    )


# ─────────────────────────── retrieval: composition ───────────────────────────


def test_retrieve_context_returns_ranked_signals_and_themes(facade):
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Pipeline health",
        [
            ("revenue", "deal_blocker", "Acme $1.4M stuck on SSO", {}, 1),
            ("customer_voice", "feature_request", "Buyers want SSO", {}, 2),
        ],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.92)]):
        bundle = retrieve_context(facade, "ent-A", "How is my pipeline?")

    assert bundle["empty"] is False
    assert [t["label"] for t in bundle["themes"]] == ["Pipeline health"]
    assert {s["content"] for s in bundle["signals"]} == {
        "Acme $1.4M stuck on SSO",
        "Buyers want SSO",
    }
    # Every signal carries content + source_type + provenance for grounding.
    for s in bundle["signals"]:
        assert set(s) >= {"signal_id", "content", "source_type", "provenance", "rank"}
    # Ranked descending.
    ranks = [s["rank"] for s in bundle["signals"]]
    assert ranks == sorted(ranks, reverse=True)


def test_retrieve_context_theme_match_boosts_above_recent(facade):
    """A signal wired to a matched theme outranks an equally-fresh recent
    signal with no theme boost."""
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Onboarding",
        [("customer_voice", "feature_request", "matched-theme signal", {}, 0)],
    )
    # An unrelated, equally fresh signal with no edge to any matched theme.
    from app.graph.types import Signal

    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="customer_voice",
            kind="feature_request",
            content="loose recent signal",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    contents = [s["content"] for s in bundle["signals"]]
    assert contents[0] == "matched-theme signal"
    assert "loose recent signal" in contents


def test_retrieve_context_uses_one_batched_signal_fetch(facade, monkeypatch):
    """N+1 kill: the per-theme signal walk must batch via get_signals across
    ALL matched themes, never the per-id get_signal once per edge."""
    from app.graph.retrieval import retrieve_context

    theme_a, _ = _seed_theme_with_signals(
        facade, "ent-A", "Theme A",
        [("revenue", "deal_blocker", "a1", {}, 0),
         ("customer_voice", "feature_request", "a2", {}, 0)],
    )
    theme_b, _ = _seed_theme_with_signals(
        facade, "ent-A", "Theme B",
        [("project_mgmt", "bug", "b1", {}, 0)],
    )

    counts = {"get_signal": 0, "get_signals": 0}
    orig_signals = facade.get_signals

    def _no_get_signal(*a, **k):
        counts["get_signal"] += 1
        raise AssertionError("get_signal should not be called per-edge anymore")

    def _wrapped_get_signals(*a, **k):
        counts["get_signals"] += 1
        return orig_signals(*a, **k)

    monkeypatch.setattr(facade, "get_signal", _no_get_signal)
    monkeypatch.setattr(facade, "get_signals", _wrapped_get_signals)
    with _patch_embed(), _patch_candidates([(theme_a, 0.9), (theme_b, 0.8)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert bundle["empty"] is False
    assert counts["get_signal"] == 0
    # ONE batched theme-edge fetch covering both matched themes (the recent-
    # signals path uses active_signals, not get_signals).
    assert counts["get_signals"] == 1


def test_retrieve_context_dedupes_signal_across_paths(facade):
    """A signal reachable via BOTH a matched theme AND the recent-signals pull
    appears once."""
    from app.graph.retrieval import retrieve_context

    theme, sigs = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Churn",
        [("revenue", "deal_blocker", "the one signal", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.8)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    ids = [s["signal_id"] for s in bundle["signals"]]
    assert ids.count(sigs[0].id) == 1


def test_retrieve_context_skips_superseded_signals(facade):
    from app.graph.retrieval import retrieve_context

    theme, sigs = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Theme",
        [
            ("revenue", "deal_blocker", "stale claim", {}, 0),
            ("revenue", "deal_reopened", "fresh claim", {}, 0),
        ],
    )
    facade.supersede_signal("ent-A", sigs[0].id, sigs[1].id)
    with _patch_embed(), _patch_candidates([(theme, 0.8)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    contents = [s["content"] for s in bundle["signals"]]
    assert "stale claim" not in contents
    assert "fresh claim" in contents


def test_retrieve_context_folds_in_session_context(facade):
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Entity

    facade.create_entity(
        "ent-A",
        Entity(enterprise_id="ent-A", type="hypothesis", canonical_label="SSO unblocks enterprise"),
    )
    facade.create_entity(
        "ent-A",
        Entity(enterprise_id="ent-A", type="decision", canonical_label="Prioritize SSO this quarter"),
    )
    facade.create_entity(
        "ent-A",
        Entity(enterprise_id="ent-A", type="outcome", canonical_label="Churn down 4pts"),
    )
    with _patch_embed(), _patch_candidates([]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert [h["label"] for h in bundle["hypotheses"]] == ["SSO unblocks enterprise"]
    assert [d["label"] for d in bundle["decisions"]] == ["Prioritize SSO this quarter"]
    assert [o["label"] for o in bundle["outcomes"]] == ["Churn down 4pts"]
    assert bundle["empty"] is False


def test_retrieve_context_enriches_ledger_entities_with_decision_chain(facade):
    """A decision/outcome/hypothesis with a real decision-chain edge (the
    hypothesis->decision->...->outcome->hypothesis loop `decision_chain.py`
    writes) resolves into a `related` dict on the bundle entity, not just a
    bare label — this is what lets `render_context_section` make the causal
    chain explicit instead of three disconnected lists."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Entity, Relationship

    hyp = Entity(
        enterprise_id="ent-A", type="hypothesis",
        canonical_label="SSO unblocks enterprise",
    )
    facade.create_entity("ent-A", hyp)
    decision = Entity(
        enterprise_id="ent-A", type="decision",
        canonical_label="Prioritize SSO this quarter",
        properties={"hypothesis_id": hyp.id, "prd_id": "prd-1"},
    )
    facade.create_entity("ent-A", decision)
    facade.write_relationship("ent-A", Relationship(
        enterprise_id="ent-A", type="PROMOTED_TO",
        source_kind="entity", source_id=hyp.id,
        target_kind="entity", target_id=decision.id,
    ))
    outcome = Entity(
        enterprise_id="ent-A", type="outcome",
        canonical_label="Churn down 4pts",
        properties={"actual_impact": "4pt reduction"},
    )
    facade.create_entity("ent-A", outcome)
    facade.write_relationship("ent-A", Relationship(
        enterprise_id="ent-A", type="VALIDATES",
        source_kind="entity", source_id=outcome.id,
        target_kind="entity", target_id=hyp.id,
    ))

    with _patch_embed(), _patch_candidates([]):
        bundle = retrieve_context(facade, "ent-A", "q")

    [hyp_out] = bundle["hypotheses"]
    [dec_out] = bundle["decisions"]
    [out_out] = bundle["outcomes"]

    assert dec_out["properties"]["prd_id"] == "prd-1"
    assert dec_out["related"]["promoted_from_hypothesis"] == "SSO unblocks enterprise"
    assert out_out["properties"]["actual_impact"] == "4pt reduction"
    assert out_out["related"]["validates_hypothesis"] == "SSO unblocks enterprise"
    assert hyp_out["related"]["validated_by_outcome"] == "Churn down 4pts"


def test_retrieve_context_recent_signals_without_theme_match(facade):
    """No theme match (find_candidates → []) still surfaces recent non-stale
    signals — covers fresh connector data not yet wired to a theme."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="analytics",
            kind="metric_shift",
            content="DAU dropped 12%",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with _patch_embed(), _patch_candidates([]):
        bundle = retrieve_context(facade, "ent-A", "what changed?")

    assert [s["content"] for s in bundle["signals"]] == ["DAU dropped 12%"]
    assert bundle["themes"] == []


# ─────────────────────────── retrieval: Leg C (content / entity search) ──────
#
# Legs A (theme kNN, mocked via `_patch_candidates`) and B (recent-signals
# window) never reach `kg_signal.content`. These tests seed a signal Leg A/B
# genuinely CANNOT surface (no theme edge, shadowed out of the recent window)
# and assert Leg C — `content_leg=True` — reaches it anyway, mocking the two
# facade primitives (`search_signals_by_content` / `signal_candidates_by_
# embedding`) the same way `_patch_candidates` stands in for the real
# pgvector kNN the fake backend can't run.


def _patch_content_leg(keyword=(), embedding=()):
    """Patch the two Leg C facade primitives. `keyword`/`embedding` are lists
    of (Signal, score) tuples, mirroring each primitive's real return shape."""
    from app.graph.facade import GraphFacade

    return (
        patch.object(GraphFacade, "search_signals_by_content",
                    lambda self, ent, question, k=30: list(keyword)),
        patch.object(GraphFacade, "signal_candidates_by_embedding",
                    lambda self, ent, vec, k=30: list(embedding)),
    )


def _unreachable_signal(facade, ent, content, *, age_days=3, n_shadowing=10):
    """Write a signal Legs A+B genuinely cannot reach: no theme edge, and
    OLDER than `n_shadowing` newer unrelated signals — more than the default
    recent-signals window (8), so Leg B's "newest 8" never includes it."""
    from app.graph.types import Signal

    now = datetime.now(timezone.utc)
    sig = Signal(
        enterprise_id=ent, source_type="customer_voice", kind="feature_request",
        content=content, valid_at=now - timedelta(days=age_days),
    )
    facade.write_signal(ent, sig)
    for i in range(n_shadowing):
        facade.write_signal(ent, Signal(
            enterprise_id=ent, source_type="customer_voice", kind="feature_request",
            content=f"unrelated recent note {i}", valid_at=now,
        ))
    return sig


def test_content_leg_off_by_default_leaves_the_bundle_unchanged(facade):
    """`content_leg` defaults False — a caller that doesn't opt in gets the
    EXACT Leg A+B bundle, byte-identical to before Leg C existed, even when a
    content match would have been available."""
    from app.graph.retrieval import retrieve_context

    sig = _unreachable_signal(facade, "ent-A", "AIG flagged pricing concerns")
    kw_patch, embed_patch = _patch_content_leg(keyword=[(sig, 1.0)])
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(facade, "ent-A", "what's the latest on AIG")

    assert sig.id not in {s["signal_id"] for s in bundle["signals"]}
    assert "content_leg_ids" not in bundle


def test_content_leg_keyword_surfaces_a_signal_legs_a_b_miss(facade):
    """`content_leg=True` reaches a signal via the keyword sub-leg alone
    (embedding leg returns nothing) that no theme edge or recency window
    could — an entity-named question reaching content that matches no
    topic-theme."""
    from app.graph.retrieval import retrieve_context

    sig = _unreachable_signal(facade, "ent-A", "AIG flagged pricing concerns")
    kw_patch, embed_patch = _patch_content_leg(keyword=[(sig, 1.0)])
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(
            facade, "ent-A", "what's the latest on the AIG account",
            content_leg=True,
        )

    assert sig.id in {s["signal_id"] for s in bundle["signals"]}
    assert bundle["content_leg_ids"][sig.id] == "keyword"


def test_content_leg_semantic_surfaces_a_paraphrase(facade):
    """The semantic sub-leg alone (keyword returns nothing) also reaches an
    unreachable signal — the paraphrase case the keyword leg cannot cover."""
    from app.graph.retrieval import retrieve_context

    sig = _unreachable_signal(facade, "ent-A", "the insurer renewal is stalling on price")
    kw_patch, embed_patch = _patch_content_leg(embedding=[(sig, 0.87)])
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(
            facade, "ent-A", "what's the latest on AIG's renewal",
            content_leg=True,
        )

    assert sig.id in {s["signal_id"] for s in bundle["signals"]}
    assert bundle["content_leg_ids"][sig.id] == "embedding"


def test_content_leg_both_sub_legs_hit_attributes_keyword_plus_embedding(facade):
    """A signal both sub-legs surface is attributed to both, not just the
    last one admitted — `content_leg_ids` records the union."""
    from app.graph.retrieval import retrieve_context

    sig = _unreachable_signal(facade, "ent-A", "AIG renewal stalling on price")
    kw_patch, embed_patch = _patch_content_leg(
        keyword=[(sig, 1.0)], embedding=[(sig, 0.9)],
    )
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(
            facade, "ent-A", "what's the latest on AIG", content_leg=True,
        )

    assert bundle["content_leg_ids"][sig.id] == "keyword+embedding"


def test_content_leg_word_boundary_campaign_does_not_match_aig_query(facade):
    """The word-boundary contract the migration's tsvector/tsquery search
    enforces: a signal about a "campaign" must NOT be treated as an "AIG"
    content hit — asserted via `content_leg_ids` attribution rather than mere
    bundle membership, since Leg B's recent-signals window would ALSO surface
    a freshly-written signal regardless of Leg C, and that is not what this
    test is about. The ILIKE substring false-positive itself (an `%AIG%` scan
    also matching "campAIGn") is closed at the SQL layer — the RPC uses
    `websearch_to_tsquery`, not ILIKE; this asserts the retrieval-side half
    of the contract: `retrieve_context` only ever ATTRIBUTES what the facade
    primitive actually returned as a Leg C hit."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    now = datetime.now(timezone.utc)
    campaign_sig = Signal(
        enterprise_id="ent-A", source_type="customer_voice", kind="feature_request",
        content="the marketing campaign launched last week", valid_at=now,
    )
    facade.write_signal("ent-A", campaign_sig)
    # The facade primitive itself would never return this signal for an "AIG"
    # query against the real GIN/tsquery index — modeled here as an empty
    # keyword-leg result, exactly like the migration's word-boundary search.
    kw_patch, embed_patch = _patch_content_leg(keyword=[])
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(facade, "ent-A", "what's the latest on AIG",
                                  content_leg=True)

    assert campaign_sig.id not in (bundle.get("content_leg_ids") or {})


def test_content_leg_retired_signal_is_excluded_even_when_the_rpc_returns_it(facade):
    """Retirement (`signal_is_retired`) is enforced in Python post-hydration
    for Leg C exactly like Legs A/B — a superseded signal the mocked RPC
    still "returns" (modeling a server that hasn't caught up) must not reach
    the bundle."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    sig = Signal(
        enterprise_id="ent-A", source_type="customer_voice", kind="feature_request",
        content="AIG old fact", valid_at=datetime.now(timezone.utc),
        properties={"superseded_by": "some-newer-id"},
    )
    facade.write_signal("ent-A", sig)
    kw_patch, embed_patch = _patch_content_leg(keyword=[(sig, 1.0)])
    # recent_signals=0 disables Leg B entirely, so the ONLY path this signal
    # could reach the bundle through is Leg C's own admit check — isolating
    # exactly what this test is about (Leg C's OWN retirement check), not
    # Leg B's independent filtering of the same signal.
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(facade, "ent-A", "what's the latest on AIG",
                                  content_leg=True, recent_signals=0)

    assert sig.id not in {s["signal_id"] for s in bundle["signals"]}


def test_content_leg_stale_signal_is_excluded(facade):
    """A signal past its `stale_after` window is excluded — same as Legs A/B
    (mirrors `_admit_content`'s explicit stale_after check)."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    long_ago = datetime.now(timezone.utc) - timedelta(days=90)
    sig = Signal(
        enterprise_id="ent-A", source_type="verbal_claim", kind="feature_request",
        content="AIG very old claim", valid_at=long_ago,
    )
    facade.write_signal("ent-A", sig)
    assert sig.stale_after is not None and sig.stale_after < datetime.now(timezone.utc)
    kw_patch, embed_patch = _patch_content_leg(keyword=[(sig, 1.0)])
    # recent_signals=0 disables Leg B — see the retirement test above for why.
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(facade, "ent-A", "what's the latest on AIG",
                                  content_leg=True, recent_signals=0)

    assert sig.id not in {s["signal_id"] for s in bundle["signals"]}


def test_content_leg_boost_ranks_above_recent_but_a_theme_hit_can_still_lead(facade):
    """A content-leg hit (`_CONTENT_BOOST=0.3`) outranks a generic recent
    signal (boost 0.0) EVEN WHEN IT IS OLDER — the boost has to overcome the
    recency-factor gap, not just tie-break an equally-fresh pair (which a
    stable sort could get right by accident, boost or not). Matches the
    theme leg's "float above recency alone" contract, without asserting a
    specific ordering against a THEME hit, which is a tuning knob, not an
    invariant."""
    from app.graph.retrieval import retrieve_context

    # 3 days old — content_sig is UNREACHABLE via Leg B (shadowed out of the
    # recent-8 window by the 10 fresher unrelated signals `_unreachable_
    # signal` writes), so the only way it can appear at all is Leg C, at a
    # LOWER recency factor than the fresh recent signals it must still beat.
    content_sig = _unreachable_signal(facade, "ent-A", "AIG pricing concern")

    kw_patch, embed_patch = _patch_content_leg(keyword=[(content_sig, 1.0)])
    with _patch_embed(), _patch_candidates([]), kw_patch, embed_patch:
        bundle = retrieve_context(facade, "ent-A", "what's the latest on AIG",
                                  content_leg=True)

    contents = [s["content"] for s in bundle["signals"]]
    assert contents[0] == "AIG pricing concern"


def test_content_leg_cap_is_passed_through_as_each_sub_legs_k(facade):
    """`content_leg_cap` is the `k` each sub-leg is asked for — the actual
    cap enforcement is server-side (the RPC's `p_k` / the facade primitive's
    own `k` param), so what `retrieve_context` owns and this asserts is that
    the caller's `content_leg_cap` reaches both calls verbatim, not some
    other default."""
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context

    seen_k: dict[str, int] = {}

    def fake_keyword(self, ent, question, k=30):
        seen_k["keyword"] = k
        return []

    def fake_embedding(self, ent, vec, k=30):
        seen_k["embedding"] = k
        return []

    with _patch_embed(), _patch_candidates([]), \
         patch.object(GraphFacade, "search_signals_by_content", fake_keyword), \
         patch.object(GraphFacade, "signal_candidates_by_embedding", fake_embedding):
        retrieve_context(facade, "ent-A", "what's the latest on AIG",
                         content_leg=True, content_leg_cap=7)

    assert seen_k == {"keyword": 7, "embedding": 7}


def test_content_leg_failure_is_best_effort_and_degrades_to_leg_a_b(facade):
    """A Leg C read failure (both sub-legs) logs and degrades to the Leg A+B
    bundle — never breaks the answer, matching every other leg's contract."""
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    now = datetime.now(timezone.utc)
    recent_sig = Signal(
        enterprise_id="ent-A", source_type="customer_voice", kind="feature_request",
        content="still here", valid_at=now,
    )
    facade.write_signal("ent-A", recent_sig)

    def _boom(self, *a, **k):
        raise RuntimeError("rpc unavailable")

    with _patch_embed(), _patch_candidates([]), \
         patch.object(GraphFacade, "search_signals_by_content", _boom), \
         patch.object(GraphFacade, "signal_candidates_by_embedding", _boom):
        bundle = retrieve_context(facade, "ent-A", "what's the latest on AIG",
                                  content_leg=True)

    assert bundle["empty"] is False
    assert [s["content"] for s in bundle["signals"]] == ["still here"]


# ── ask_runner._retrieve_kg_bundle: content_leg wiring at the caller boundary ─
#
# `_retrieve_kg_bundle` is the ONE function backing every KG-grounded caller
# (the direct Ask answer path, a custom-skill grounding, and the two
# voice-of-customer paths). These assert the wiring decision itself — the
# answer path gets Leg C by default; the two VoC-scale callers opt out — by
# capturing what `retrieve_context` actually receives, not by exercising the
# real KG.


def test_retrieve_kg_bundle_defaults_content_leg_true(isolated_settings, monkeypatch):
    """The answer-path default: a caller that passes no `content_leg`
    override (the direct Ask path, `qa_agent._kg_grounding`, ask_runner's
    wave-2 kg gather) gets Leg C enabled."""
    from app import ask_runner

    seen = {}

    def fake_retrieve_context(facade, enterprise_id, question, **kwargs):
        seen.update(kwargs)
        return {"signals": [], "themes": [], "decisions": [], "hypotheses": [],
                "outcomes": [], "kg_refs": [], "token_estimate": 0,
                "signals_dropped": 0, "empty": True}

    with patch("app.graph.retrieval.retrieve_context", fake_retrieve_context):
        ask_runner._retrieve_kg_bundle("ent-A", "what's the latest on AIG")

    assert seen.get("content_leg") is True


def test_retrieve_kg_bundle_voc_scale_callers_opt_out_of_content_leg(isolated_settings, monkeypatch):
    """The two VoC-scale callers (`call_digest.build_kg_context`,
    `qa_agent`'s pinned voice-of-customer report) must NOT silently change
    what their calibrated, widened retrieval counts — this is the explicit
    `content_leg=False` opt-out both pass."""
    from app import ask_runner

    seen = {}

    def fake_retrieve_context(facade, enterprise_id, question, **kwargs):
        seen.update(kwargs)
        return {"signals": [], "themes": [], "decisions": [], "hypotheses": [],
                "outcomes": [], "kg_refs": [], "token_estimate": 0,
                "signals_dropped": 0, "empty": True}

    with patch("app.graph.retrieval.retrieve_context", fake_retrieve_context):
        ask_runner._retrieve_kg_bundle(
            "ent-A", "show me all the feedback",
            scale={"k": 40}, content_leg=False,
        )

    assert seen.get("content_leg") is False
    assert seen.get("k") == 40, "the scale kwargs must still reach retrieve_context"


def test_call_digest_kg_context_opts_out_of_content_leg(isolated_settings, monkeypatch):
    """`call_digest.build_kg_context` — one of the two VoC-scale callers —
    passes `content_leg=False` at its own call site, not relying on a caller
    upstream to remember it. `_retrieve_kg_bundle` is imported LAZILY inside
    `build_kg_context` (see that function's docstring on why), so the patch
    target is `app.ask_runner`'s own attribute — a fresh `from ... import`
    re-resolves it there every call."""
    from app import ask_runner, call_digest

    seen = {}

    def fake_bundle(enterprise_id, question, *, scale=None, content_leg=True, **kw):
        seen["content_leg"] = content_leg
        seen["scale"] = scale
        return None

    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", fake_bundle)
    call_digest.build_kg_context("ent-A", "show me all the feedback", live_calls=False)

    assert seen["content_leg"] is False
    assert seen["scale"] is not None


# ─────────────────────────── retrieval: budget + empty ───────────────────────────


def test_retrieve_context_token_budget_caps_signals(facade):
    from app.graph.retrieval import retrieve_context

    big = "x" * 4000  # ~1000 tokens each at 4 chars/token
    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Theme",
        [("revenue", "deal_blocker", f"{big}-{i}", {}, i) for i in range(10)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q", token_budget=2500)

    # 2500-token budget / ~1000 tokens per signal → at most ~3 signals.
    assert 1 <= len(bundle["signals"]) <= 3
    assert bundle["token_estimate"] <= 2500 + 1000  # last one may straddle the cap


def test_retrieve_context_reports_the_signals_the_budget_dropped(facade):
    """The cut has to be countable, not just taken.

    This loop used to `break` and leave no trace: the bundle reported
    `token_estimate` (what it spent) and nothing about what it discarded, so a
    caller could not tell a clipped bundle from a complete one — and neither
    could the user reading the answer built from it. `signals_dropped` is that
    missing number, and kept + dropped must account for the whole pool.
    """
    from app.graph.retrieval import retrieve_context

    big = "x" * 4000  # ~1000 tokens each at 4 chars/token
    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Theme",
        [("revenue", "deal_blocker", f"{big}-{i}", {}, i) for i in range(10)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(
            facade, "ent-A", "q", token_budget=2500, signals_per_theme=10,
        )

    kept = len(bundle["signals"])
    assert bundle["signals_dropped"] > 0, "a clipped bundle must say it clipped"
    assert kept + bundle["signals_dropped"] == 10


def test_retrieve_context_reports_no_drop_when_everything_fits(facade):
    """The count must be a shortfall signal, not a constant. If it were
    non-zero on a complete bundle every feedback answer would carry a
    truncation caveat it hadn't earned."""
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Theme",
        [("revenue", "deal_blocker", f"short-{i}", {}, i) for i in range(3)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q", token_budget=100_000)

    assert len(bundle["signals"]) == 3
    assert bundle["signals_dropped"] == 0


def test_retrieve_context_pool_widens_with_signals_per_theme(facade):
    """Budget and pool are separate knobs, and the pool is the one that used to
    bind first. Raising only the token budget would have changed nothing here:
    `_SIGNALS_PER_THEME` capped a theme's evidence at 6 long before any budget
    was consulted, so this is the half of the fix that a budget-only change
    would have silently missed."""
    from app.graph.retrieval import (
        _RECENT_SIGNALS, _SIGNALS_PER_THEME, retrieve_context,
    )

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Theme",
        [("revenue", "deal_blocker", f"s-{i}", {}, i) for i in range(20)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        narrow = retrieve_context(facade, "ent-A", "q", token_budget=100_000)
        wide = retrieve_context(
            facade, "ent-A", "q", token_budget=100_000, signals_per_theme=20,
        )

    # Two paths feed the pool and both are capped: the theme walk keeps
    # `_SIGNALS_PER_THEME` of the 20, and the recent-signals fold-in then adds
    # `_RECENT_SIGNALS` more that the walk had left behind. 14 of 20 reachable
    # signals, on a budget with room for every one of them — the pool, not the
    # budget, is what a feedback answer was losing evidence to.
    assert len(narrow["signals"]) == _SIGNALS_PER_THEME + _RECENT_SIGNALS
    # Widened, the theme walk alone covers all 20 and the recent fold-in has
    # nothing left to contribute (same signals, deduped by id).
    assert len(wide["signals"]) == 20


def test_voc_scale_outruns_the_char_budget_that_reports_truncation(facade):
    """The invariant the whole fix rests on.

    Two ceilings can cut a feedback answer: retrieval's token budget, which used
    to cut SILENTLY, and `call_digest._KG_CHAR_BUDGET`, which trims on a line
    boundary and sets a flag the coverage line reports. The fix is to make the
    honest one bind first — so the VoC token budget must be able to carry more
    than the char budget can hold. If someone later lowers `VOC_TOKEN_BUDGET` or
    raises `_KG_CHAR_BUDGET` past it, the silent cut comes back and this fails.
    """
    from app.call_digest import _KG_CHAR_BUDGET
    from app.graph.retrieval import _CHARS_PER_TOKEN, VOC_SCALE

    voc_capacity_chars = VOC_SCALE["token_budget"] * _CHARS_PER_TOKEN
    assert voc_capacity_chars > _KG_CHAR_BUDGET, (
        "retrieval must be able to overshoot the char budget, or the cut that "
        "reaches the user is the one that cannot announce itself"
    )


def test_voc_scale_raises_every_knob_together(facade):
    """`VOC_SCALE` is exported as one bundle precisely so it cannot be
    half-applied. Each entry must actually exceed the default it replaces —
    a preset that widened the budget but left the pool at its default would
    look like a fix and change nothing."""
    from app.graph.retrieval import (
        DEFAULT_TOKEN_BUDGET, VOC_SCALE, _DEFAULT_THEME_K, _RECENT_SIGNALS,
        _SIGNALS_PER_THEME,
    )

    assert VOC_SCALE["token_budget"] > DEFAULT_TOKEN_BUDGET
    assert VOC_SCALE["k"] > _DEFAULT_THEME_K
    assert VOC_SCALE["signals_per_theme"] > _SIGNALS_PER_THEME
    assert VOC_SCALE["recent_signals"] > _RECENT_SIGNALS


def test_retrieve_context_empty_kg_returns_empty_bundle(facade):
    from app.graph.retrieval import retrieve_context

    with _patch_embed(), _patch_candidates([]):
        bundle = retrieve_context(facade, "ent-empty", "anything?")

    assert bundle["empty"] is True
    assert bundle["signals"] == []
    assert bundle["themes"] == []
    assert bundle["kg_refs"] == []


def test_retrieve_context_resilient_when_embeddings_unavailable(facade):
    """If embed_texts raises (e.g. no OPENAI key), retrieval degrades to
    recent-signals-only instead of failing."""
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Signal

    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="analytics",
            kind="metric_shift",
            content="recent only",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with patch(
        "app.graph.embeddings.embed_texts",
        side_effect=RuntimeError("OPENAI_API_KEY not configured"),
    ):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert [s["content"] for s in bundle["signals"]] == ["recent only"]
    assert bundle["themes"] == []


def test_kg_refs_collects_signal_theme_and_entity_ids(facade):
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Entity

    theme, sigs = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Theme",
        [("revenue", "deal_blocker", "sig", {}, 0)],
    )
    dec = Entity(enterprise_id="ent-A", type="decision", canonical_label="ship it")
    facade.create_entity("ent-A", dec)
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert sigs[0].id in bundle["kg_refs"]
    assert theme.id in bundle["kg_refs"]
    assert dec.id in bundle["kg_refs"]


# ─────────────────────────── noise floor ───────────────────────────


def test_retrieve_context_drops_themes_below_the_noise_floor(facade):
    """`find_candidates` is a pure kNN — nearest, not relevant. A theme whose
    score is indistinguishable from noise must not reach the bundle."""
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Unrelated theme",
        [("revenue", "deal_blocker", "noise signal", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.03)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert bundle["themes"] == []


def test_render_context_section_omits_below_floor_themes(facade):
    from app.graph.retrieval import render_context_section, retrieve_context
    from app.graph.types import Signal

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Unrelated theme",
        [("revenue", "deal_blocker", "noise signal", {}, 0)],
    )
    # An independent recent signal keeps the bundle non-empty so this test
    # proves the theme block is specifically omitted, not that render()
    # short-circuits on an empty bundle.
    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="analytics",
            kind="metric_shift",
            content="unrelated recent signal",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with _patch_embed(), _patch_candidates([(theme, 0.03)]):
        bundle = retrieve_context(facade, "ent-A", "q")
    text = render_context_section(bundle)

    assert bundle["empty"] is False
    assert "relevance 0.03" not in text
    assert "## Relevant themes" not in text


def test_below_floor_theme_signals_do_not_reach_the_bundle(facade):
    """Signals reachable ONLY through a below-floor theme edge never enter
    the bundle.

    DO NOT "simplify" this to age=0 — every signal `_seed_theme_with_signals`
    writes is a real row in kg_signal, so at age=0 it is picked up
    independently by the recency path (step 4 in `retrieve_context`)
    regardless of whether its theme survives the floor. An age-0 version of
    this test would pass even with the floor completely disabled, because
    the assertion would be satisfied by dedup against the recency path, not
    by the floor dropping anything. Aging past the `revenue` source's
    30-day stale window (see `SOURCE_STALE_WINDOW_DAYS`) removes that
    escape hatch: `active_signals` excludes them, so the theme edge — now
    filtered — is the ONLY path in, and a red assertion here is actually
    caused by the floor working, not a fixture accident. See the sibling
    `test_recent_signal_survives_when_its_theme_is_filtered` for the age-0
    case, which is a genuinely different assertion (theme=None via
    recency), not a relaxed version of this one."""
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Unrelated theme",
        [
            ("revenue", "deal_blocker", "stale noise 1", {}, 40),
            ("revenue", "deal_blocker", "stale noise 2", {}, 40),
            ("revenue", "deal_blocker", "stale noise 3", {}, 40),
        ],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.03)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert bundle["signals"] == []


def test_retrieve_context_keeps_themes_above_the_noise_floor(facade):
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Pipeline health",
        [("revenue", "deal_blocker", "sig", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert bundle["themes"] == [
        {"entity_id": theme.id, "label": "Pipeline health", "score": 0.9}
    ]


def test_find_candidates_still_called_with_the_full_candidate_window(facade):
    """The noise floor changes admission, not the candidate window — the kNN
    primitive is still asked for k=_DEFAULT_THEME_K candidates."""
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import _DEFAULT_THEME_K, retrieve_context

    seen_k: list[int] = []

    def spy(self, ent, typ, vec, k=10):
        seen_k.append(k)
        return []

    with _patch_embed(), patch.object(GraphFacade, "find_candidates", spy):
        retrieve_context(facade, "ent-A", "q")

    assert seen_k == [_DEFAULT_THEME_K]


def test_theme_at_exactly_the_floor_is_kept(facade):
    """The comparison is `>=`: a score exactly at the floor is kept, not
    dropped."""
    from app.graph.retrieval import _MIN_THEME_SCORE, retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Borderline theme",
        [("revenue", "deal_blocker", "sig", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, _MIN_THEME_SCORE)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert [t["label"] for t in bundle["themes"]] == ["Borderline theme"]


def test_recent_signal_survives_when_its_theme_is_filtered(facade):
    """A signal wired to a below-floor theme but also independently recent
    still reaches the bundle — sourced from the recency path, not the
    (now-filtered) theme walk, so `theme` is None."""
    from app.graph.retrieval import retrieve_context

    theme, sigs = _seed_theme_with_signals(
        facade, "ent-A", "Unrelated theme",
        [("revenue", "deal_blocker", "dual-path signal", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.03)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert [s["content"] for s in bundle["signals"]] == ["dual-path signal"]
    assert bundle["signals"][0]["signal_id"] == sigs[0].id
    assert bundle["signals"][0]["theme"] is None


def test_all_themes_below_floor_and_nothing_else_yields_empty_bundle(facade):
    """All candidates filtered, no recent signals, no session context → the
    pre-#18 corpus-only fallback: `empty` is True and render is blank, not a
    crash and not an empty header."""
    from app.graph.retrieval import render_context_section, retrieve_context
    from app.graph.types import Entity

    theme = Entity(enterprise_id="ent-A", type="theme", canonical_label="Noise theme")
    facade.create_entity("ent-A", theme)
    with _patch_embed(), _patch_candidates([(theme, 0.03)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert bundle["empty"] is True
    assert render_context_section(bundle) == ""


def test_all_themes_below_floor_still_returns_recent_signals(facade):
    from app.graph.retrieval import retrieve_context
    from app.graph.types import Entity, Signal

    theme = Entity(enterprise_id="ent-A", type="theme", canonical_label="Noise theme")
    facade.create_entity("ent-A", theme)
    facade.write_signal(
        "ent-A",
        Signal(
            enterprise_id="ent-A",
            source_type="analytics",
            kind="metric_shift",
            content="unrelated recent signal",
            valid_at=datetime.now(timezone.utc),
        ),
    )
    with _patch_embed(), _patch_candidates([(theme, 0.03)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert bundle["empty"] is False
    assert [s["content"] for s in bundle["signals"]] == ["unrelated recent signal"]


def test_mixed_scores_keep_only_the_above_floor_theme(facade):
    from app.graph.retrieval import retrieve_context

    theme_a, _ = _seed_theme_with_signals(
        facade, "ent-A", "Relevant theme",
        [("revenue", "deal_blocker", "relevant signal", {}, 0)],
    )
    theme_b, _ = _seed_theme_with_signals(
        facade, "ent-A", "Noise theme",
        [("revenue", "deal_blocker", "noise signal", {}, 40)],
    )
    with _patch_embed(), _patch_candidates([(theme_a, 0.9), (theme_b, 0.04)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert [t["label"] for t in bundle["themes"]] == ["Relevant theme"]
    assert [s["content"] for s in bundle["signals"]] == ["relevant signal"]


def test_kg_refs_excludes_below_floor_theme_ids(facade):
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Noise theme",
        [("revenue", "deal_blocker", "noise signal", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.03)]):
        bundle = retrieve_context(facade, "ent-A", "q")

    assert theme.id not in bundle["kg_refs"]


def test_noise_floor_drop_logs_counts_only(facade, caplog):
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "Noise theme",
        [("revenue", "deal_blocker", "noise signal", {}, 0)],
    )
    with caplog.at_level(logging.INFO, logger="app.graph.retrieval"):
        with _patch_embed(), _patch_candidates([(theme, 0.03)]):
            retrieve_context(facade, "ent-A", "q")

    drops = [r for r in caplog.records if "noise floor dropped" in r.getMessage()]
    assert len(drops) == 1
    msg = drops[0].getMessage()
    assert "enterprise_id=ent-A" in msg
    assert "returned=1" in msg
    assert "kept=0" in msg
    assert "floor=0.15" in msg
    assert "top_score=0.03" in msg


def test_noise_floor_log_never_contains_theme_labels_or_signal_content(facade, caplog):
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "SENTINEL-THEME-LABEL-DO-NOT-LOG",
        [("revenue", "deal_blocker", "SENTINEL-SIGNAL-CONTENT-DO-NOT-LOG", {}, 0)],
    )
    with caplog.at_level(logging.INFO, logger="app.graph.retrieval"):
        with _patch_embed(), _patch_candidates([(theme, 0.03)]):
            retrieve_context(facade, "ent-A", "q")

    all_msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "SENTINEL-THEME-LABEL-DO-NOT-LOG" not in all_msgs
    assert "SENTINEL-SIGNAL-CONTENT-DO-NOT-LOG" not in all_msgs


def test_existing_theme_scores_in_this_suite_are_all_above_the_floor():
    """Guard: every `_patch_candidates` call site in this suite uses a score
    drawn from {0.8, 0.9, 0.92} (verified at ticket time). If a future floor
    raise pushes `_MIN_THEME_SCORE` above 0.8 it would silently invalidate
    those 23 call sites — fail loudly here instead of leaving them green for
    the wrong reason."""
    from app.graph.retrieval import _MIN_THEME_SCORE

    assert _MIN_THEME_SCORE <= 0.8


# ──────────────── the sentinel: "no embedding" vs "compute one" ────────────
#
# `question_embedding=None` means "compute it yourself" — it does NOT mean
# "there is no embedding". A caller that already knows its embedding is
# unusable (no key, or an all-zero vector) must say so explicitly via
# `skip_semantic=True`; passing `None` unconditionally re-triggers a self-embed
# that, with no key, returns the same unusable zero vector. Defence-in-depth:
# a zero or wrong-dimension vector reaching `qvec` by ANY route (a caller
# passing one directly, or the self-embed above) is dropped before it can
# reach `find_candidates` — mirrors `document_catalog.py`'s exact check.


def test_zero_vector_never_reaches_find_candidates(facade):
    """A zero vector passed directly as `question_embedding` must not reach
    `find_candidates` — the defence-in-depth check mirrors
    `document_catalog.py`'s `embedding is not None and (len(...) !=
    EMBEDDING_DIM or not any(...))`. RED before the fix: nothing rejected a
    zero vector once it arrived at `qvec`."""
    from app.graph.embeddings import EMBEDDING_DIM
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context

    calls: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, vec, k=10: calls.append(vec) or [],
    ):
        retrieve_context(
            facade, "ent-A", "q", question_embedding=[0.0] * EMBEDDING_DIM,
        )

    assert calls == [], f"find_candidates was called with a zero vector: {calls}"


def test_wrong_dimension_vector_is_treated_as_no_embedding(facade):
    """A vector of the wrong length is dropped, mirroring
    `document_catalog.py`'s length check — not just an all-zero vector."""
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context

    calls: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, vec, k=10: calls.append(vec) or [],
    ):
        retrieve_context(facade, "ent-A", "q", question_embedding=[0.5, 0.5, 0.5])

    assert calls == []


def test_empty_list_embedding_is_treated_as_no_embedding(facade, monkeypatch):
    """`[]` is not confused with "compute one" — it is a caller-supplied
    value (not `None`), so no self-embed fires, and it fails the dimension
    check, so no kNN call is made either."""
    from app.graph.embeddings import EMBEDDING_DIM
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context

    embed_calls: list = []
    monkeypatch.setattr(
        "app.graph.embeddings.embed_texts",
        lambda texts, **kw: embed_calls.append(texts)
        or [[0.1] * EMBEDDING_DIM for _ in texts],
    )
    knn_calls: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, vec, k=10: knn_calls.append(vec) or [],
    ):
        retrieve_context(facade, "ent-A", "q", question_embedding=[])

    assert knn_calls == []
    assert embed_calls == [], "an empty list must not trigger a self-embed"


def test_usable_key_knn_vector_is_byte_identical_to_prefix(facade):
    """With a real caller-supplied vector, theme kNN runs exactly as it does
    today — `find_candidates` receives the SAME vector, unmodified."""
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context

    vec = [0.01 * i for i in range(1536)]
    seen: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, v, k=10: seen.append(v) or [],
    ):
        retrieve_context(facade, "ent-A", "q", question_embedding=vec)

    assert seen == [vec]


def test_retrieve_context_without_embedding_still_self_embeds(facade, monkeypatch):
    """A non-Ask caller passing no `question_embedding` and no
    `skip_semantic` keeps the ORIGINAL self-contained behaviour: it embeds
    for itself and still runs kNN when a key is configured.
    `retrieve_context`'s five non-Ask callers depend on this default staying
    exactly as it was — the sentinel is opt-in, never the default."""
    from app.graph.embeddings import EMBEDDING_DIM
    from app.graph.facade import GraphFacade
    from app.graph.retrieval import retrieve_context

    embed_calls: list = []
    monkeypatch.setattr(
        "app.graph.embeddings.embed_texts",
        lambda texts, **kw: embed_calls.append(texts)
        or [[0.05] * EMBEDDING_DIM for _ in texts],
    )
    knn_calls: list = []
    with patch.object(
        GraphFacade, "find_candidates",
        lambda self, ent, typ, vec, k=10: knn_calls.append(vec) or [],
    ):
        retrieve_context(facade, "ent-A", "q")

    assert len(embed_calls) == 1
    assert len(knn_calls) == 1


def test_no_question_or_key_in_logs(facade, caplog):
    """No log line emitted anywhere in `retrieve_context` — across the
    self-embed branch, the defence-in-depth drop, and the noise-floor drop —
    contains the question text, a theme label, a signal body, or an API key
    value."""
    from app.graph.retrieval import retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade, "ent-A", "SENTINEL-THEME-LABEL-DO-NOT-LOG",
        [("revenue", "deal_blocker", "SENTINEL-SIGNAL-CONTENT-DO-NOT-LOG", {}, 0)],
    )
    question = "SENTINEL-QUESTION-TEXT-DO-NOT-LOG"
    fake_key = "sk-SENTINEL-KEY-VALUE-DO-NOT-LOG"
    with caplog.at_level(logging.INFO):
        # No key configured: exercises the self-embed → zero-vector →
        # defence-in-depth-drop path, which is the one most likely to log
        # something derived from the question or the (absent) key.
        retrieve_context(facade, "ent-A", question)
        # A caller-supplied zero vector: the other route to the same drop.
        retrieve_context(facade, "ent-A", question, question_embedding=[0.0] * 1536)

    all_msgs = " ".join(r.getMessage() for r in caplog.records)
    assert question not in all_msgs
    assert "SENTINEL-THEME-LABEL-DO-NOT-LOG" not in all_msgs
    assert "SENTINEL-SIGNAL-CONTENT-DO-NOT-LOG" not in all_msgs
    assert fake_key not in all_msgs


# ─────────────────────────── tenant isolation ───────────────────────────


def test_retrieve_context_tenant_isolation(facade):
    """ent-B's signals never leak into ent-A's bundle, even with an identical
    theme match shape."""
    from app.graph.retrieval import retrieve_context

    theme_a, _ = _seed_theme_with_signals(
        facade, "ent-A", "Shared", [("revenue", "deal_blocker", "A-only signal", {}, 0)]
    )
    _seed_theme_with_signals(
        facade, "ent-B", "Shared", [("revenue", "deal_blocker", "B-only signal", {}, 0)]
    )
    # Even if the kNN mock (wrongly) returned ent-A's theme, edges_to is tenant-
    # scoped, so a cross-tenant query can't read the other tenant's signals.
    with _patch_embed(), _patch_candidates([(theme_a, 0.9)]):
        bundle_a = retrieve_context(facade, "ent-A", "q")
    contents_a = [s["content"] for s in bundle_a["signals"]]
    assert "A-only signal" in contents_a
    assert "B-only signal" not in contents_a

    with _patch_embed(), _patch_candidates([]):
        bundle_b = retrieve_context(facade, "ent-B", "q")
    contents_b = [s["content"] for s in bundle_b["signals"]]
    assert "B-only signal" in contents_b
    assert "A-only signal" not in contents_b


# ─────────────────────────── render section ───────────────────────────


def test_render_context_section_includes_signals_and_provenance(facade):
    from app.graph.retrieval import render_context_section, retrieve_context

    theme, _ = _seed_theme_with_signals(
        facade,
        "ent-A",
        "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )
    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        bundle = retrieve_context(facade, "ent-A", "q")
    text = render_context_section(bundle)
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" in text
    assert "Acme blocked on SSO" in text
    assert "revenue" in text  # source_type surfaced for citation


def test_render_context_section_renders_ledger_properties_and_causal_chain():
    """Given a bundle with a populated decisions/outcomes entity (properties +
    a resolvable edge), the rendered text includes the properties AND the
    causal-chain text (e.g. "validates hypothesis: ..."), not just the bare
    label — the actual regression: a staging chat answer said an outcome
    hadn't been reached despite the outcome entity existing, because the old
    render only ever emitted `- {label}`."""
    from app.graph.retrieval import render_context_section

    bundle = {
        "empty": False,
        "signals": [],
        "themes": [],
        "hypotheses": [
            {
                "entity_id": "hyp-1",
                "label": "SSO unblocks enterprise",
                "properties": {},
                "related": {"validated_by_outcome": "Churn down 4pts"},
            },
        ],
        "decisions": [
            {
                "entity_id": "dec-1",
                "label": "Prioritize SSO this quarter",
                "properties": {"prd_id": "prd-1"},
                "related": {"promoted_from_hypothesis": "SSO unblocks enterprise"},
            },
        ],
        "outcomes": [
            {
                "entity_id": "out-1",
                "label": "Churn down 4pts",
                "properties": {"actual_impact": "4pt reduction"},
                "related": {"validates_hypothesis": "SSO unblocks enterprise"},
            },
        ],
        "kg_refs": [],
    }

    text = render_context_section(bundle)

    # Properties surfaced, not discarded.
    assert "prd_id=prd-1" in text
    assert "actual_impact=4pt reduction" in text
    # The causal chain is explicit text, not just parallel unconnected lists.
    assert "validates hypothesis: SSO unblocks enterprise" in text
    assert "promotes hypothesis: SSO unblocks enterprise" in text
    assert "validated by outcome: Churn down 4pts" in text
    # Bare labels alone are no longer the whole story for these entities.
    assert text.count("- Churn down 4pts") == 1  # only the outcome line, once


def test_render_context_section_ledger_entity_without_related_edge_still_renders():
    """An entity with no resolvable chain edge (e.g. a still-open hypothesis)
    degrades to label + properties only — no crash, no phantom chain text."""
    from app.graph.retrieval import render_context_section

    bundle = {
        "empty": False,
        "signals": [],
        "themes": [],
        "hypotheses": [
            {"entity_id": "hyp-2", "label": "Unvalidated idea", "properties": {}},
        ],
        "decisions": [],
        "outcomes": [],
        "kg_refs": [],
    }

    text = render_context_section(bundle)
    assert "Unvalidated idea" in text
    assert "not yet validated by a measured outcome" in text


def test_render_context_section_empty_bundle_is_blank():
    from app.graph.retrieval import render_context_section

    assert render_context_section({"empty": True}) == ""
    assert render_context_section({}) == ""


# ─────────────────────────── compose_ask_answer wiring ───────────────────────────


def _only_answer_row(isolated_settings) -> dict:
    """The ask's own `answer` row, asserting the whole world of rows it wrote.

    An ask now writes TWO rows, not one: this `answer` row, and a
    `document_selection` row written from inside document grounding — the
    function BOTH ask paths go through. The skill-routed path wrote none of
    this before, which is why topical selection returning the wrong document
    there left nothing in the record to find it by.

    Grouped by `decision_type` rather than counted in total, so this still
    closes the world: a second `answer` row, a missing one, a stray write of
    any other type, or a duplicated `document_selection` all still fail. It is
    deliberately not loosened to "at least one row".
    """
    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    by_type: dict[str, list[dict]] = {}
    for row in rows:
        by_type.setdefault(row["decision_type"], []).append(row)
    assert sorted(by_type) == ["answer", "document_selection"], by_type
    assert len(by_type["answer"]) == 1
    assert len(by_type["document_selection"]) == 1
    return by_type["answer"][0]


def test_compose_ask_answer_corpus_only_when_no_enterprise(
    isolated_settings, fake_llm
):
    """No tenant → corpus-only path, identical to pre-#18. No KG section in the
    prompt, no decision-log row."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    ask_runner.compose_ask_answer("asurion", "What changed?", enterprise_id=None)

    assert len(fake_llm["calls"]) == 1
    user = fake_llm["calls"][0]["user"]
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" not in user
    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    assert rows == []


def test_direct_path_kg_knn_query_derives_from_the_message(
    isolated_settings, fake_llm, facade
):
    """T4 (AC4) — the query text embedded to drive KG theme kNN is the bare
    current-turn message, not the folded thread. `compose_ask_answer`
    computes ONE embedding, shared by document grounding and KG retrieval
    (`test_the_question_is_embedded_once_and_shared_by_both_consumers` in
    `test_ask_document_retrieval.py`), so proving the embedded text is bare
    proves the kNN query is too."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    theme, _ = _seed_theme_with_signals(
        facade, "co-1", "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )
    history = [
        {"role": "user", "content": "what did users say about the onboarding flow?"},
        {"role": "assistant", "content": "Most complaints were about the email step."},
    ]
    embedded_texts: list = []

    def _embed(texts, **kw):
        embedded_texts.append(list(texts))
        from app.graph.embeddings import EMBEDDING_DIM

        return [[0.1] * EMBEDDING_DIM for _ in texts]

    with patch("app.graph.embeddings.embed_texts", side_effect=_embed), \
         _patch_candidates([(theme, 0.9)]):
        ask_runner.compose_ask_answer(
            "asurion", "how is pipeline doing now?", enterprise_id="co-1",
            history=history,
        )

    assert embedded_texts == [["how is pipeline doing now?"]]


def test_compose_ask_answer_injects_kg_section_and_logs_refs(
    isolated_settings, fake_llm, facade
):
    """With a tenant + seeded KG: the prompt gets a KG section AND a decision-log
    row lands with agent='ask', decision_type='answer', kg_refs populated."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "grounded", "key_points": ["k"], "citations": [], "confidence": 0.7,
        "unanswered": "",
    }
    theme, sigs = _seed_theme_with_signals(
        facade,
        "co-1",
        "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )

    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        ask_runner.compose_ask_answer("asurion", "How is pipeline?", enterprise_id="co-1")

    user = fake_llm["calls"][0]["user"]
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" in user
    assert "Acme blocked on SSO" in user

    row = _only_answer_row(isolated_settings)
    assert row["agent"] == "ask"
    assert row["decision_type"] == "answer"
    assert row["enterprise_id"] == "co-1"
    kg_refs = row["kg_refs"]
    if isinstance(kg_refs, str):
        kg_refs = json.loads(kg_refs)
    assert sigs[0].id in kg_refs
    assert theme.id in kg_refs


def test_compose_ask_answer_empty_kg_falls_back_to_corpus_only(
    isolated_settings, fake_llm
):
    """Tenant resolves but its KG is empty → corpus-only prompt; the decision
    log still records the ask with kg_used=False and empty kg_refs."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    with _patch_embed(), _patch_candidates([]):
        ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-empty")

    user = fake_llm["calls"][0]["user"]
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" not in user
    row = _only_answer_row(isolated_settings)
    factors = row["factors"]
    if isinstance(factors, str):
        factors = json.loads(factors)
    assert factors["kg_used"] is False
    kg_refs = row["kg_refs"]
    if isinstance(kg_refs, str):
        kg_refs = json.loads(kg_refs)
    assert kg_refs == []


def test_compose_ask_answer_prd_grounded_skips_kg_and_corpus(
    isolated_settings, fake_llm
):
    """A PRD-grounded ask (prd_context set) must make NO KG retrieval (the
    embeddings HTTP call + pgvector queries) and NO corpus load — the PRD block
    IS the grounding, riding the cacheable user prefix. The decision log still
    records the ask with prd_grounded=True / kg_used=False."""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    retrievals, corpus_loads = [], []
    with patch.object(
        ask_runner, "_retrieve_kg_bundle",
        side_effect=lambda eid, q: retrievals.append(q) or None,
    ), patch.object(
        ask_runner, "load_corpus",
        side_effect=lambda d: corpus_loads.append(d) or None,
    ):
        ask_runner.compose_ask_answer(
            "asurion", "What does this PRD say?", enterprise_id="co-1",
            prd_context="=== CURRENT PRD CONTEXT ===\nThe open PRD body.",
        )

    assert retrievals == []     # no embeddings/pgvector call
    assert corpus_loads == []   # no corpus load either
    call = fake_llm["calls"][0]
    # The PRD block rides the CACHEABLE prefix, not the uncached user turn.
    assert call["kwargs"]["user_cacheable_prefix"] == (
        "=== CURRENT PRD CONTEXT ===\nThe open PRD body."
    )
    assert "CURRENT PRD CONTEXT" not in call["user"]
    assert "What does this PRD say?" in call["user"]
    factors = _only_answer_row(isolated_settings)["factors"]
    if isinstance(factors, str):
        factors = json.loads(factors)
    assert factors["prd_grounded"] is True
    assert factors["kg_used"] is False


def test_compose_ask_answer_prd_prefix_stable_across_turns(
    isolated_settings, fake_llm
):
    """Turns 2+ of the same PRD conversation send a byte-identical cacheable
    prefix (same PRD content → prompt-cache read); only the question varies in
    the uncached user turn."""
    from app import ask_runner

    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }
    block = "=== CURRENT PRD CONTEXT ===\nSame PRD content."
    ask_runner.compose_ask_answer(
        "asurion", "first question", enterprise_id="co-1", prd_context=block
    )
    ask_runner.compose_ask_answer(
        "asurion", "second question", enterprise_id="co-1", prd_context=block
    )
    first, second = fake_llm["calls"]
    assert (
        first["kwargs"]["user_cacheable_prefix"]
        == second["kwargs"]["user_cacheable_prefix"]
        == block
    )
    assert first["user"] != second["user"]


def test_prd_branch_documents_trail_the_prd_block(isolated_settings, fake_llm):
    """On the PRD branch, `user_cacheable_prefix` orders `facts` ->
    `prd_context` -> `docs_block`: the ~26K-token PRD block (byte-stable
    across turns of one conversation) precedes the per-question document
    index, same treatment as the corpus branch. (AC2)"""
    from tests.test_ask_document_retrieval import _seed_file, _seed_source

    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    db = isolated_settings["supabase"]
    db.table("companies").insert(
        {"id": "co-prd-order", "slug": "slug-co-prd-order", "display_name": "Sprntly"}
    ).execute()
    db.table("products").insert(
        {"id": "prod-co-prd-order", "company_id": "co-prd-order", "name": "Sprntly",
         "website": "https://sprntly.ai", "is_primary": 1}
    ).execute()
    src = _seed_source(db, "src-prd-order", company_id="co-prd-order")
    _seed_file(db, "f-prd-order", src, company_id="co-prd-order",
               filename="Prd_Order_Report.docx",
               extracted_text="THE PRD-BRANCH DOCUMENT BODY")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5,
        "unanswered": "",
    }

    ask_runner.compose_ask_answer(
        "asurion", "About Prd_Order_Report", enterprise_id="co-prd-order",
        prd_context="=== CURRENT PRD CONTEXT ===\nTHE PRD BODY.",
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert prefix.index(WORKSPACE_CONFIG_HEADER) < prefix.index("THE PRD BODY")
    assert prefix.index("THE PRD BODY") < prefix.index("THE PRD-BRANCH DOCUMENT BODY")


# ────────────────────── compose_ask_answer × workspace configuration ────────


def _seed_company_with_config(
    db, company_id, *, website="https://sprntly.ai", display_name="Sprntly",
    product_name="Sprntly",
):
    """A companies row + its primary product row — the two reads
    `company_facts_block` composes into the answer-prompt configuration
    block."""
    db.table("companies").insert(
        {"id": company_id, "slug": f"slug-{company_id}", "display_name": display_name}
    ).execute()
    db.table("products").insert(
        {
            "id": f"prod-{company_id}",
            "company_id": company_id,
            "name": product_name,
            "website": website,
            "is_primary": 1,
        }
    ).execute()


def test_compose_ask_answer_prompt_carries_company_domain(
    isolated_settings, fake_llm
):
    """Regression: the incident tenant's own domain must ride the prompt so it
    can outrank a wrong domain elsewhere. Fails on unfixed code — the domain
    appears nowhere in the assembled prompt when no workspace configuration is
    wired in. (AC7, AC12)"""
    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    _seed_company_with_config(isolated_settings["supabase"], "co-1")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    with _patch_embed(), _patch_candidates([]):
        ask_runner.compose_ask_answer("asurion", "What's our domain?", enterprise_id="co-1")

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert WORKSPACE_CONFIG_HEADER in prefix
    assert "https://sprntly.ai" in prefix


def test_compose_ask_answer_puts_company_facts_in_cacheable_prefix_not_user(
    isolated_settings, fake_llm
):
    """The config block rides the CACHEABLE prefix, never the uncached `user`
    turn, and the precedence addendum lands in `system`. (AC7)"""
    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER
    from app.prompts import ASK_SYSTEM_COMPANY_FACTS_ADDENDUM

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    _seed_company_with_config(isolated_settings["supabase"], "co-1")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    with _patch_embed(), _patch_candidates([]):
        ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-1")

    call = fake_llm["calls"][0]
    assert WORKSPACE_CONFIG_HEADER in call["kwargs"]["user_cacheable_prefix"]
    assert WORKSPACE_CONFIG_HEADER not in call["user"]
    assert ASK_SYSTEM_COMPANY_FACTS_ADDENDUM in call["system"]


def test_compose_ask_answer_kg_branch_carries_company_facts(
    isolated_settings, fake_llm, facade
):
    """The config block rides alongside a KG-grounded answer too — the two
    conditions are independent (config can be present with no KG bundle and
    vice versa). (AC7)"""
    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    _seed_company_with_config(isolated_settings["supabase"], "co-1")
    fake_llm["payload"] = {
        "answer": "grounded", "key_points": [], "citations": [], "confidence": 0.7,
        "unanswered": "",
    }
    theme, _sigs = _seed_theme_with_signals(
        facade, "co-1", "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )

    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        ask_runner.compose_ask_answer("asurion", "How is pipeline?", enterprise_id="co-1")

    call = fake_llm["calls"][0]
    assert WORKSPACE_CONFIG_HEADER in call["kwargs"]["user_cacheable_prefix"]
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" in call["user"]


def test_compose_ask_answer_prd_branch_prefixes_company_facts(
    isolated_settings, fake_llm
):
    """PRD-grounded branch: the composed prefix is exactly
    `facts + "\\n\\n---\\n\\n" + prd_context`. (AC9)"""
    from app import ask_runner
    from app.ask_runner import company_facts_block

    _seed_company_with_config(isolated_settings["supabase"], "co-1")
    facts = company_facts_block("co-1")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }
    block = "=== CURRENT PRD CONTEXT ===\nThe open PRD body."

    ask_runner.compose_ask_answer(
        "asurion", "q?", enterprise_id="co-1", prd_context=block,
    )

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert prefix == f"{facts}\n\n---\n\n{block}"


def test_compose_ask_answer_prd_prefix_stable_across_turns_with_company_facts(
    isolated_settings, fake_llm
):
    """Two consecutive calls, same tenant + same PRD, produce byte-identical
    prefixes even with the per-tenant config block riding it. (AC9)"""
    from app import ask_runner

    _seed_company_with_config(isolated_settings["supabase"], "co-1")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }
    block = "=== CURRENT PRD CONTEXT ===\nSame PRD content."

    ask_runner.compose_ask_answer(
        "asurion", "first question", enterprise_id="co-1", prd_context=block
    )
    ask_runner.compose_ask_answer(
        "asurion", "second question", enterprise_id="co-1", prd_context=block
    )

    first, second = fake_llm["calls"]
    assert (
        first["kwargs"]["user_cacheable_prefix"]
        == second["kwargs"]["user_cacheable_prefix"]
    )


def test_compose_ask_answer_unchanged_when_no_workspace_config(
    isolated_settings, fake_llm
):
    """No product row at all (2 of the 21-tenant population) → prefix/system
    stay byte-identical to the pre-fix composition — never an error. (AC6)"""
    from app import ask_runner

    ds = isolated_settings["data_dir"] / "asurion"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    with _patch_embed(), _patch_candidates([]):
        ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-no-doc")

    call = fake_llm["calls"][0]
    assert call["kwargs"]["user_cacheable_prefix"] == (
        "Source material:\n\n<<< SOURCE: a >>>\nlegacy corpus body\n<<< END SOURCE >>>"
    )
    assert "WORKSPACE CONFIGURATION" not in call["system"]


def test_compose_ask_answer_prefix_none_when_no_corpus_and_no_facts(
    isolated_settings, fake_llm
):
    """Empty dataset + no product row → user_cacheable_prefix is None, exactly
    as before this ticket. (AC6)"""
    from app import ask_runner

    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    with _patch_embed(), _patch_candidates([]):
        ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-empty-no-doc")

    assert fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"] is None


def test_compose_ask_answer_guarded_website_is_omitted(
    isolated_settings, fake_llm
):
    """A preview-deploy website (`*.vercel.app`) is guarded out of the block —
    the company name still renders, the website line does not."""
    from app import ask_runner
    from app.ask_runner import WORKSPACE_CONFIG_HEADER

    _seed_company_with_config(
        isolated_settings["supabase"], "co-preview",
        website="https://my-app-git-main.vercel.app",
    )
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }

    with _patch_embed(), _patch_candidates([]):
        ask_runner.compose_ask_answer("asurion", "q?", enterprise_id="co-preview")

    prefix = fake_llm["calls"][0]["kwargs"]["user_cacheable_prefix"]
    assert WORKSPACE_CONFIG_HEADER in prefix
    assert "vercel.app" not in prefix


# ─────────────────────────── route: POST /v1/ask ───────────────────────────


def _seed_corpus(data_dir, dataset="asurion", body="some corpus body"):
    ds = data_dir / dataset
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text(body)


def _ask_and_wait(client, question, dataset="asurion", *, timeout=5.0):
    """POST /v1/ask (fire-and-forget) then poll GET /v1/ask/{id} until terminal,
    returning the citation-stripped status body once the worker has run."""
    import time as _time

    start = client.post("/v1/ask", json={"question": question, "dataset": dataset})
    assert start.status_code == 200, start.text
    ask_id = start.json()["ask_id"]
    deadline = _time.monotonic() + timeout
    body = None
    while _time.monotonic() < deadline:
        resp = client.get(f"/v1/ask/{ask_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "generating":
            return body
        _time.sleep(0.02)
    return body


def test_ask_route_uses_kg_context_when_signals_exist(
    tenant_client, isolated_settings, fake_llm
):
    # The Ask route now requires a company (require_company) AND the dataset slug
    # must resolve to that company (require_owned_dataset). Seed a company whose
    # slug == the dataset, then seed KG signals under that company's id so the
    # resolved tenant's graph carries them into the answer.
    t = tenant_client.make(slug="asurion")
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "grounded", "key_points": [], "citations": [], "confidence": 0.8,
        "unanswered": "",
    }
    from app.graph import GraphFacade

    facade = GraphFacade()
    theme, _ = _seed_theme_with_signals(
        facade,
        t.company_id,
        "Pipeline",
        [("revenue", "deal_blocker", "Acme blocked on SSO", {}, 0)],
    )

    with _patch_embed(), _patch_candidates([(theme, 0.9)]):
        body = _ask_and_wait(t.client, "How is my pipeline?")
    assert body["status"] == "ready"
    assert body["citations"] == []  # still stripped
    user = fake_llm["calls"][-1]["user"]
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" in user
    assert "Acme blocked on SSO" in user


def test_ask_route_corpus_only_for_legacy_session(
    tenant_client, isolated_settings, fake_llm
):
    """When the resolved company's KG is EMPTY (no signals seeded), the Ask route
    falls back to corpus-only — response shape unchanged. This is the pre-#18
    corpus-only behaviour, now reached via a resolved-but-empty tenant rather than
    an unresolved legacy session (the route requires a company after the
    tenant-isolation fix)."""
    t = tenant_client.make(slug="asurion")
    _seed_corpus(isolated_settings["data_dir"])
    fake_llm["payload"] = {
        "answer": "x", "key_points": [], "citations": [], "confidence": 0.5, "unanswered": "",
    }
    body = _ask_and_wait(t.client, "What is churn?")
    assert body["status"] == "ready"
    assert body["answer"] == "x"
    assert "LIVE CONTEXT FROM CONNECTED SOURCES" not in fake_llm["calls"][-1]["user"]
