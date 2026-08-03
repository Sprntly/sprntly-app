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
    """Patch the embeddings call retrieval imports lazily."""
    return patch(
        "app.graph.embeddings.embed_texts",
        side_effect=lambda texts, **k: [[0.1] * 4 for _ in texts],
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

    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    assert len(rows) == 1
    row = rows[0]
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
    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    assert len(rows) == 1
    factors = rows[0]["factors"]
    if isinstance(factors, str):
        factors = json.loads(factors)
    assert factors["kg_used"] is False
    kg_refs = rows[0]["kg_refs"]
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
    rows = (
        isolated_settings["supabase"].table("agent_decision_log").select("*").execute().data
    )
    assert len(rows) == 1
    factors = rows[0]["factors"]
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
