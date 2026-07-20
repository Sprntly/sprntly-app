"""Tests for app.evidence_kg — KG-grounded Evidence Page generation.

The evidence doc is the PROVENANCE TRAIL behind a brief insight: the SUPPORTS
signals backing its hypothesis + the theme's convergence signals, each with
source attribution (source_type + provenance). These tests exercise the trail
assembly over a real GraphFacade on the in-memory fake Supabase (mirroring
test_synthesis_agent), mock the gateway llm_call for the doc, and check the
fallback + route dispatch.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.graph.gateway import LLMResult


def _llm_result(output, model="claude-sonnet-4-6"):
    return LLMResult(
        output=output, model=model, prompt_version="evidence-kg-v1",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _seed_template(data_dir):
    """The KG evidence path loads the shared evidence template; tests need it
    present under TEMPLATE_DIR (== data_dir here)."""
    (data_dir / "sprntly_evidence_template.md").write_text(
        "# Evidence template\n:::hero\n{}\n:::\n─────\n"
    )


def _seed_company(supabase, slug="acme", company_id="ent-A"):
    supabase.table("companies").insert({
        "id": company_id, "slug": slug, "display_name": slug.title(),
    }).execute()


def _seed_brief(db_mod, dataset="acme", insights=None):
    if insights is None:
        insights = [{"title": "SSO gap blocks $1.4M in deals",
                     "theme_id": "THEME_ID", "confidence": 0.85}]
    payload = {"summary_headline": "stub", "insights": insights,
               "_schema_version": 1}
    return db_mod.save_brief(dataset=dataset, week_label="Week of stub",
                             payload=payload, schema_version=1)


def _seed_theme_hypothesis(facade, ent="ent-A"):
    """Reproduce what run_synthesis persists: a theme with converging signals,
    a hypothesis Entity (properties.theme_id), ADDRESSES theme, SUPPORTS from
    each backing signal. Returns (theme, hypothesis, signals)."""
    from app.graph.types import Entity, Relationship, Signal

    theme = Entity(enterprise_id=ent, type="theme", canonical_label="SSO")
    facade.create_entity(ent, theme)

    now = datetime.now(timezone.utc)
    specs = [
        ("revenue", "deal_blocker",
         {"revenue_at_risk_usd": 1400000}, "hubspot", 0.9, 2.0,
         "Acme $1.4M deal blocked on missing SSO"),
        ("customer_voice", "feature_request",
         {}, "fireflies", 0.8, 1.0,
         "Customers repeatedly ask for SSO on calls"),
        ("project_mgmt", "bug",
         {}, "clickup", 0.7, 1.0,
         "12 open tickets reference SSO login failures"),
    ]
    signals = []
    for st, kind, props, connector, conf, wt, content in specs:
        sig = Signal(enterprise_id=ent, source_type=st, kind=kind,
                     content=content, properties=props, confidence=conf,
                     weight=wt, provenance={"connector": connector},
                     valid_at=now)
        facade.write_signal(ent, sig)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="REQUESTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
        signals.append(sig)

    hyp = Entity(enterprise_id=ent, type="hypothesis",
                 canonical_label="SSO gap blocks $1.4M in deals",
                 properties={"theme_id": theme.id, "tag": "something_broken",
                             "confidence": 0.85})
    facade.create_entity(ent, hyp)
    facade.write_relationship(ent, Relationship(
        enterprise_id=ent, type="ADDRESSES", source_kind="entity",
        source_id=hyp.id, target_kind="entity", target_id=theme.id))
    # SUPPORTS edges from the two strongest signals (revenue + customer_voice).
    for sig in signals[:2]:
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="SUPPORTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=hyp.id))
    return theme, hyp, signals


# ---------- hypothesis resolution ----------

def test_find_hypothesis_by_theme_id(facade):
    from app.evidence_kg import _find_hypothesis
    theme, hyp, _ = _seed_theme_hypothesis(facade)
    found = _find_hypothesis(facade, "ent-A", theme.id, None)
    assert found is not None and found.id == hyp.id


def test_find_hypothesis_falls_back_to_title(facade):
    from app.evidence_kg import _find_hypothesis
    _, hyp, _ = _seed_theme_hypothesis(facade)
    # No theme_id on the insight → match on the hypothesis canonical_label.
    found = _find_hypothesis(facade, "ent-A", None,
                             "SSO gap blocks $1.4M in deals")
    assert found is not None and found.id == hyp.id


def test_find_hypothesis_none_when_no_match(facade):
    from app.evidence_kg import _find_hypothesis
    _seed_theme_hypothesis(facade)
    assert _find_hypothesis(facade, "ent-A", "nope", "no such title") is None


# ---------- shared resolver: Evidence and PRD ground on the SAME hypothesis ----------
#
# evidence_kg._find_hypothesis and graph.retrieval used to be SEPARATE resolvers
# that diverged on the no-theme_id path (evidence title-fell-back, PRD bailed).
# They are now ONE shared resolver — these pin that single source of truth and the
# aligned no-theme_id behavior so Evidence and PRD can never drift apart.


def test_evidence_resolver_delegates_to_shared_retrieval_resolver():
    # The evidence resolver IS the retrieval one — same function object reached
    # via the thin wrapper, so there is exactly one resolution implementation.
    import app.evidence_kg as ek
    from app.graph.retrieval import resolve_insight_hypothesis

    # The wrapper imports the shared resolver function-locally; assert the symbol
    # it would call is the canonical one (no duplicate body in evidence_kg).
    import inspect
    src = inspect.getsource(ek._find_hypothesis)
    assert "resolve_insight_hypothesis" in src
    assert callable(resolve_insight_hypothesis)


def test_evidence_and_prd_resolve_same_hypothesis_by_theme(facade):
    # Given one insight (with theme_id), Evidence and PRD resolve the IDENTICAL
    # hypothesis Entity.
    from app.evidence_kg import _find_hypothesis
    from app.graph.retrieval import resolve_insight_hypothesis

    theme, hyp, _ = _seed_theme_hypothesis(facade)
    title = "SSO gap blocks $1.4M in deals"
    ev = _find_hypothesis(facade, "ent-A", theme.id, title)
    prd = resolve_insight_hypothesis(facade, "ent-A", theme.id, title)
    assert ev is not None and prd is not None
    assert ev.id == prd.id == hyp.id


def test_evidence_and_prd_agree_on_no_theme_id_title_fallback(facade):
    # The previously-divergent path: insight carries NO theme_id but its title
    # matches a hypothesis label. BOTH resolvers must now title-fall-back to the
    # SAME hypothesis (the safer aligned behavior), never one resolving and the
    # other bailing.
    from app.evidence_kg import _find_hypothesis
    from app.graph.retrieval import resolve_insight_hypothesis

    _, hyp, _ = _seed_theme_hypothesis(facade)
    title = "SSO gap blocks $1.4M in deals"
    ev = _find_hypothesis(facade, "ent-A", None, title)
    prd = resolve_insight_hypothesis(facade, "ent-A", None, title)
    assert ev is not None and prd is not None
    assert ev.id == prd.id == hyp.id


def test_shared_resolver_no_theme_id_no_title_match_is_none_for_both(facade):
    # No theme_id AND no title match → both bail (empty trail), never a blind
    # corpus guess. Aligned across Evidence and PRD.
    from app.evidence_kg import _find_hypothesis
    from app.graph.retrieval import resolve_insight_hypothesis

    _seed_theme_hypothesis(facade)
    assert _find_hypothesis(facade, "ent-A", None, "unrelated title") is None
    assert resolve_insight_hypothesis(facade, "ent-A", None, "unrelated title") is None


# ---------- evidence trail assembly ----------

def test_trail_unions_supports_and_convergence_signals(facade):
    from app.evidence_kg import gather_evidence_trail
    theme, hyp, signals = _seed_theme_hypothesis(facade)
    trail = gather_evidence_trail(facade, "ent-A", theme_id=theme.id,
                                  hypothesis=hyp)
    # All three theme-converging signals appear (2 also SUPPORTS the hypothesis,
    # deduped to one entry each).
    ids = {t["signal_id"] for t in trail}
    assert ids == {s.id for s in signals}
    assert len(trail) == 3


def test_trail_carries_source_attribution(facade):
    from app.evidence_kg import gather_evidence_trail
    theme, hyp, _ = _seed_theme_hypothesis(facade)
    trail = gather_evidence_trail(facade, "ent-A", theme_id=theme.id,
                                  hypothesis=hyp)
    revenue = next(t for t in trail if t["source_type"] == "revenue")
    assert revenue["provenance"] == {"connector": "hubspot"}
    assert revenue["confidence"] == 0.9
    assert revenue["weight"] == 2.0
    assert "1.4M" in revenue["content"]
    # Every item carries the four attribution fields the doc must cite.
    for t in trail:
        assert {"source_type", "provenance", "confidence", "weight"} <= set(t)


def test_trail_sorted_strongest_first(facade):
    from app.evidence_kg import gather_evidence_trail
    theme, hyp, _ = _seed_theme_hypothesis(facade)
    trail = gather_evidence_trail(facade, "ent-A", theme_id=theme.id,
                                  hypothesis=hyp)
    weights = [t["weight"] for t in trail]
    assert weights == sorted(weights, reverse=True)
    assert trail[0]["source_type"] == "revenue"  # weight 2.0 leads


def test_trail_skips_superseded_signals(facade):
    from app.evidence_kg import gather_evidence_trail
    theme, hyp, signals = _seed_theme_hypothesis(facade)
    # Supersede the customer_voice signal → it must drop out of the trail.
    facade.supersede_signal("ent-A", signals[1].id, signals[0].id)
    trail = gather_evidence_trail(facade, "ent-A", theme_id=theme.id,
                                  hypothesis=hyp)
    assert signals[1].id not in {t["signal_id"] for t in trail}
    assert len(trail) == 2


def test_trail_empty_when_no_signals(facade):
    from app.evidence_kg import gather_evidence_trail
    from app.graph.types import Entity
    ent = "ent-A"
    theme = Entity(enterprise_id=ent, type="theme", canonical_label="bare")
    facade.create_entity(ent, theme)
    trail = gather_evidence_trail(facade, ent, theme_id=theme.id,
                                  hypothesis=None)
    assert trail == []


def test_trail_uses_one_batched_signal_fetch(facade, monkeypatch):
    """N+1 kill: gather_evidence_trail must batch via get_signals, not call
    the per-id get_signal once per edge."""
    from app.evidence_kg import gather_evidence_trail
    theme, hyp, _ = _seed_theme_hypothesis(facade)

    counts = {"get_signal": 0, "get_signals": 0}
    orig_signals = facade.get_signals

    def _wrapped_get_signal(*a, **k):
        counts["get_signal"] += 1
        raise AssertionError("get_signal should not be called per-edge anymore")

    def _wrapped_get_signals(*a, **k):
        counts["get_signals"] += 1
        return orig_signals(*a, **k)

    monkeypatch.setattr(facade, "get_signal", _wrapped_get_signal)
    monkeypatch.setattr(facade, "get_signals", _wrapped_get_signals)
    trail = gather_evidence_trail(facade, "ent-A", theme_id=theme.id, hypothesis=hyp)
    assert {t["signal_id"] for t in trail}  # still produced a trail
    assert counts["get_signal"] == 0
    assert counts["get_signals"] == 1


# ---------- build_evidence_kg (doc + grounding + decision log) ----------

def test_build_feeds_signals_to_llm_and_logs_refs(facade, isolated_settings,
                                                   monkeypatch):
    from app import evidence_kg
    _seed_template(isolated_settings["data_dir"])
    theme, hyp, signals = _seed_theme_hypothesis(facade)

    captured = {}

    def fake_llm(**kw):
        captured.update(kw)
        return _llm_result("# Evidence\nGrounded in HubSpot + Fireflies.")

    monkeypatch.setattr(evidence_kg, "llm_call", fake_llm)
    insight = {"title": "SSO gap blocks $1.4M in deals",
               "theme_id": theme.id, "confidence": 0.85}
    md, meta = evidence_kg.build_evidence_kg(facade, "ent-A", insight)

    # The signals' content + source attribution reach the llm_call input.
    prompt = captured["input"]
    assert "Acme $1.4M deal blocked on missing SSO" in prompt
    assert "hubspot" in prompt and "fireflies" in prompt
    assert "revenue" in prompt and "customer_voice" in prompt
    # Agent + prompt_version are attributed.
    assert captured["agent"] == "evidence"
    assert captured["prompt_version"] == evidence_kg.EVIDENCE_KG_PROMPT_VERSION
    # System prompt enforces the never-invent rule.
    assert "Never invent" in captured["system"]

    # The model's body is preserved; the canonical stylesheet is injected around
    # it (Phase 2 — model emits empty <style>, server splices assets/evidence.css).
    assert "# Evidence" in md
    assert "--problem:#dd4b32" in md
    # kg_refs = signal ids + hypothesis id + theme id.
    assert set(meta["kg_refs"]) == {s.id for s in signals} | {hyp.id, theme.id}


def test_build_binds_evidence_brief_skill(facade, isolated_settings, monkeypatch):
    """Evidence generation runs through the vendored `evidence-brief` skill:
    the gateway llm_call is invoked with skill="evidence-brief" so its SKILL.md
    is prepended as BOTH the METHOD (converge ≥2 signals → wedge →
    best-chart-per-finding → honesty pass) AND the HTML rendering contract that
    governs the output (a self-contained visual brief)."""
    from app import evidence_kg

    _seed_template(isolated_settings["data_dir"])
    theme, _hyp, _signals = _seed_theme_hypothesis(facade)

    captured = {}

    def fake_llm(**kw):
        captured.update(kw)
        return _llm_result(":::hero\n{}\n:::\n")

    monkeypatch.setattr(evidence_kg, "llm_call", fake_llm)
    insight = {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}
    evidence_kg.build_evidence_kg(facade, "ent-A", insight)

    assert captured["skill"] == "evidence-brief"
    # The skill is real + installed + non-routable (bound by name, not chat).
    from app.skills.catalog import NON_ROUTABLE
    from app.skills.loader import get_skill

    assert "evidence-brief" in NON_ROUTABLE
    assert get_skill("evidence-brief").method.strip()  # SKILL.md present


def test_evidence_emits_html_contract_not_blocks(facade, isolated_settings,
                                                 monkeypatch):
    """The runner asks for the self-contained HTML brief, NOT the retired
    `:::block` markdown: the system prompt steers HTML, the user prompt carries
    no `:::`/template scaffolding, and evidence-brief is a long-output skill so
    the (large) HTML payload streams instead of truncating."""
    from app import evidence_kg
    from app.graph.gateway import _LONG_OUTPUT_SKILLS

    theme, _hyp, _sigs = _seed_theme_hypothesis(facade)
    captured = {}
    monkeypatch.setattr(
        evidence_kg, "llm_call",
        lambda **kw: captured.update(kw) or _llm_result(
            '<div class="wrap"></div>'),
    )
    insight = {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}
    evidence_kg.build_evidence_kg(facade, "ent-A", insight)

    # System prompt steers the self-contained HTML brief; the retired :::block
    # template scaffolding is gone (the prompt may still *forbid* :::blocks).
    system = captured["system"]
    assert "HTML" in system and "self-contained" in system
    assert ":::hero" not in system and ":::cuts-index" not in system
    # User prompt carries the trail but no leftover :::block template scaffolding.
    user = captured["input"]
    assert ":::hero" not in user
    assert "{template}" not in user and "EVIDENCE PAGE TEMPLATE" not in user
    # Long-output so the big HTML doc streams on the long read timeout.
    assert "evidence-brief" in _LONG_OUTPUT_SKILLS


def test_build_decision_log_carries_kg_refs(facade, isolated_settings,
                                            monkeypatch):
    from app import evidence_kg
    _seed_template(isolated_settings["data_dir"])
    theme, hyp, signals = _seed_theme_hypothesis(facade)
    monkeypatch.setattr(evidence_kg, "llm_call",
                        lambda **kw: _llm_result("# doc"))
    insight = {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}
    evidence_kg.build_evidence_kg(facade, "ent-A", insight)

    logs = isolated_settings["supabase"].table("agent_decision_log") \
        .select("*").eq("enterprise_id", "ent-A").execute().data
    gen = [r for r in logs if r["decision_type"] == "generate_evidence"]
    assert len(gen) == 1
    row = gen[0]
    assert row["agent"] == "evidence"
    assert set(row["kg_refs"]) == {s.id for s in signals} | {hyp.id, theme.id}
    assert row["factors"]["signal_count"] == 3
    assert set(row["factors"]["source_types"]) == {
        "revenue", "customer_voice", "project_mgmt"}


def test_build_raises_no_backing_when_empty(facade, isolated_settings):
    from app.evidence_kg import build_evidence_kg, NoKGBackingError
    _seed_template(isolated_settings["data_dir"])
    insight = {"title": "orphan insight", "theme_id": "missing-theme"}
    with pytest.raises(NoKGBackingError):
        build_evidence_kg(facade, "ent-A", insight)


# ---------- _run_sync_kg: completion + fallback ----------

def test_run_sync_kg_completes_with_doc(isolated_settings, monkeypatch):
    from app import evidence_kg
    _seed_template(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], slug="acme",
                  company_id="ent-A")
    facade = __import__("app.graph", fromlist=["GraphFacade"]).GraphFacade()
    theme, _hyp, _sigs = _seed_theme_hypothesis(facade)
    brief_id = _seed_brief(db_mod, dataset="acme", insights=[
        {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}])
    evidence_id = db_mod.start_evidence(brief_id=brief_id, insight_index=0,
                                        title="t", template_version=2,
                                        variant="v2")
    monkeypatch.setattr(evidence_kg, "llm_call",
                        lambda **kw: _llm_result("# KG evidence body"))

    evidence_kg._run_sync_kg(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"
    # Body preserved; canonical stylesheet injected around it (Phase 2).
    assert "# KG evidence body" in row["payload_md"]
    assert "--problem:#dd4b32" in row["payload_md"]
    assert row["title"] == "SSO gap blocks $1.4M in deals"


def test_run_sync_kg_falls_back_to_legacy_when_no_backing(
    isolated_settings, monkeypatch
):
    """Empty KG (no hypothesis/theme signals) → legacy CORPUS path runs and
    completes the row, so evidence never hard-fails. The corpus fallback now
    emits the SAME self-contained HTML brief (binding `evidence-brief`), just
    grounded on the corpus instead of the KG trail."""
    from app import evidence_kg, evidence_runner
    _seed_template(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], slug="acme",
                  company_id="ent-A")
    # corpus for the legacy fallback to read
    ds = isolated_settings["data_dir"] / "acme"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    brief_id = _seed_brief(db_mod, dataset="acme", insights=[
        {"title": "no KG backing", "theme_id": "missing"}])
    evidence_id = db_mod.start_evidence(brief_id=brief_id, insight_index=0,
                                        title="t", template_version=4,
                                        variant="v3")

    kg_calls, legacy_calls = [], []
    monkeypatch.setattr(evidence_kg, "llm_call",
                        lambda **kw: kg_calls.append(kw))
    monkeypatch.setattr(
        evidence_runner, "llm_call",
        lambda **kw: legacy_calls.append(kw) or _llm_result(
            '<div class="wrap"><h1>Legacy corpus brief</h1></div>'),
    )

    evidence_kg._run_sync_kg(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"
    # legacy path produced the HTML brief from the corpus, with the canonical
    # stylesheet injected server-side (Phase 2 — same contract as the KG path).
    assert '<div class="wrap"><h1>Legacy corpus brief</h1></div>' in row["payload_md"]
    assert "--problem:#dd4b32" in row["payload_md"]
    assert kg_calls == []                        # KG llm_call never fired
    assert len(legacy_calls) == 1                # legacy corpus call did
    assert legacy_calls[0]["skill"] == "evidence-brief"  # same skill binding


def test_generate_evidence_kg_streams_over_channel(isolated_settings, monkeypatch):
    """The brief's HTML deltas are published to the evidence:<id> channel as
    they stream, then a terminal 'done' — the SSE route relays these to the
    client (mirrors the prd:<id> stream)."""
    from app import evidence_kg
    from app.graph import token_stream

    _seed_template(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], slug="acme",
                  company_id="ent-A")
    facade = __import__("app.graph", fromlist=["GraphFacade"]).GraphFacade()
    theme, _hyp, _sigs = _seed_theme_hypothesis(facade)
    brief_id = _seed_brief(db_mod, dataset="acme", insights=[
        {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}])
    evidence_id = db_mod.start_evidence(brief_id=brief_id, insight_index=0,
                                        title="t", template_version=2,
                                        variant="v2")

    async def _flow():
        chan = f"evidence:{evidence_id}"
        collected: list[dict] = []

        async def _sub():
            async for f in token_stream.subscribe(chan):
                collected.append(f)

        sub_task = asyncio.ensure_future(_sub())
        await asyncio.sleep(0)  # let the subscriber register its queue

        def _call(**kwargs):
            od = kwargs.get("on_delta")
            if od:
                od("<h1>Streaming")
                od(" evidence</h1>")
            return _llm_result("<h1>Streaming evidence</h1>")

        monkeypatch.setattr(evidence_kg, "llm_call", _call)
        await evidence_kg.generate_evidence_kg(evidence_id, brief_id, 0)
        await asyncio.sleep(0)  # flush the scheduled publishes + terminal close
        await sub_task
        return collected

    frames = asyncio.run(_flow())
    deltas = [f["text"] for f in frames if f["kind"] == "delta"]
    assert "".join(deltas) == "<h1>Streaming evidence</h1>"
    assert frames[-1]["kind"] == "done", "terminal frame closes the stream"
    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "ready"  # poll fallback still authoritative


def test_generate_evidence_kg_streams_corpus_fallback_over_channel(
    isolated_settings, monkeypatch
):
    """No KG backing → the legacy corpus path runs, and IT streams over the
    same evidence:<id> channel (the sink is threaded through the fallback)."""
    from app import evidence_kg, evidence_runner
    from app.graph import token_stream

    _seed_template(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], slug="acme",
                  company_id="ent-A")
    ds = isolated_settings["data_dir"] / "acme"
    ds.mkdir(exist_ok=True)
    (ds / "a.md").write_text("legacy corpus body")
    brief_id = _seed_brief(db_mod, dataset="acme", insights=[
        {"title": "no KG backing", "theme_id": "missing"}])
    evidence_id = db_mod.start_evidence(brief_id=brief_id, insight_index=0,
                                        title="t", template_version=4,
                                        variant="v3")

    async def _flow():
        chan = f"evidence:{evidence_id}"
        collected: list[dict] = []

        async def _sub():
            async for f in token_stream.subscribe(chan):
                collected.append(f)

        sub_task = asyncio.ensure_future(_sub())
        await asyncio.sleep(0)

        def _legacy_call(**kwargs):
            od = kwargs.get("on_delta")
            if od:
                od("<h1>Corpus brief</h1>")
            return _llm_result("<h1>Corpus brief</h1>")

        monkeypatch.setattr(evidence_runner, "llm_call", _legacy_call)
        await evidence_kg.generate_evidence_kg(evidence_id, brief_id, 0)
        await asyncio.sleep(0)
        await sub_task
        return collected

    frames = asyncio.run(_flow())
    deltas = [f["text"] for f in frames if f["kind"] == "delta"]
    assert "".join(deltas) == "<h1>Corpus brief</h1>"
    assert frames[-1]["kind"] == "done"
    assert db_mod.get_evidence(evidence_id)["status"] == "ready"


def test_generate_evidence_kg_streams_error_frame_on_failure(
    isolated_settings, monkeypatch
):
    """A failed generation closes the channel with a terminal 'error' frame so
    a connected client stops waiting (the poll shows the failed row)."""
    from app import evidence_kg
    from app.graph import token_stream

    _seed_template(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], slug="acme",
                  company_id="ent-A")
    facade = __import__("app.graph", fromlist=["GraphFacade"]).GraphFacade()
    theme, _hyp, _sigs = _seed_theme_hypothesis(facade)
    brief_id = _seed_brief(db_mod, dataset="acme", insights=[
        {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}])
    evidence_id = db_mod.start_evidence(brief_id=brief_id, insight_index=0,
                                        title="t", template_version=2,
                                        variant="v2")

    async def _flow():
        chan = f"evidence:{evidence_id}"
        collected: list[dict] = []

        async def _sub():
            async for f in token_stream.subscribe(chan):
                collected.append(f)

        sub_task = asyncio.ensure_future(_sub())
        await asyncio.sleep(0)

        def _boom(**_kw):
            raise ValueError("gateway exploded")

        monkeypatch.setattr(evidence_kg, "llm_call", _boom)
        await evidence_kg.generate_evidence_kg(evidence_id, brief_id, 0)
        await asyncio.sleep(0)
        await sub_task
        return collected

    frames = asyncio.run(_flow())
    assert frames[-1]["kind"] == "error"
    assert db_mod.get_evidence(evidence_id)["status"] == "failed"


def test_generate_evidence_kg_records_failure(isolated_settings, monkeypatch):
    from app import evidence_kg
    _seed_template(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], slug="acme",
                  company_id="ent-A")
    facade = __import__("app.graph", fromlist=["GraphFacade"]).GraphFacade()
    theme, _hyp, _sigs = _seed_theme_hypothesis(facade)
    brief_id = _seed_brief(db_mod, dataset="acme", insights=[
        {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}])
    evidence_id = db_mod.start_evidence(brief_id=brief_id, insight_index=0,
                                        title="t", template_version=2,
                                        variant="v2")

    def _boom(**_kw):
        raise ValueError("gateway exploded")

    monkeypatch.setattr(evidence_kg, "llm_call", _boom)
    asyncio.run(evidence_kg.generate_evidence_kg(evidence_id, brief_id, 0))

    row = db_mod.get_evidence(evidence_id)
    assert row["status"] == "failed"
    assert "ValueError" in (row["error"] or "")


# ---------- route dispatch ----------

def test_route_dispatches_to_kg(tenant_client, isolated_settings, monkeypatch):
    """POST /generate always schedules the KG runner (synthesis is the only
    engine; generate_evidence_kg itself falls back to the corpus path when the
    KG has no backing for the insight)."""
    from app.routes import evidence as evidence_route
    # Seed a company whose slug == the brief's dataset so require_owned_brief
    # resolves the brief to the caller's company.
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, dataset="acme")

    scheduled = {}
    async def fake_kg(evidence_id, b_id, idx):
        scheduled["runner"] = "kg"
    monkeypatch.setattr(evidence_route, "generate_evidence_kg", fake_kg)

    resp = t.client.post("/v1/evidence/generate",
                         json={"brief_id": brief_id, "insight_index": 0})
    assert resp.status_code == 200
    assert resp.json()["status"] in ("generating", "ready")
    # Let the scheduled task run so we can observe the KG runner was chosen.
    import time
    for _ in range(20):
        if scheduled:
            break
        time.sleep(0.01)
    assert scheduled.get("runner") == "kg"


def test_route_surfaces_failed_run_instead_of_regenerating(
    tenant_client, isolated_settings, monkeypatch
):
    """A FAILED prior generation must NOT silently re-run on the next open:
    the route returns the failed row (status + error) so the client shows an
    explicit retry. Only force=true starts a fresh generation. Regression —
    failed rows were invisible to the dedup, so every reopen of a failing
    insight kicked off a brand-new LLM run."""
    from app.prompts import EVIDENCE_TEMPLATE_VERSION, EVIDENCE_VARIANT
    from app.routes import evidence as evidence_route

    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    brief_id = _seed_brief(db_mod, dataset="acme")
    failed_id = db_mod.start_evidence(
        brief_id=brief_id, insight_index=0, title="t",
        template_version=EVIDENCE_TEMPLATE_VERSION, variant=EVIDENCE_VARIANT,
    )
    db_mod.fail_evidence(failed_id, "gateway exploded")

    scheduled = []
    async def fake_kg(evidence_id, b_id, idx):
        scheduled.append(evidence_id)
    monkeypatch.setattr(evidence_route, "generate_evidence_kg", fake_kg)

    # Plain open: the failure is surfaced, nothing is scheduled.
    resp = t.client.post("/v1/evidence/generate",
                         json={"brief_id": brief_id, "insight_index": 0})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["evidence_id"] == failed_id
    assert "gateway exploded" in (body.get("error") or "")
    import time
    time.sleep(0.05)
    assert scheduled == []

    # Explicit retry: force=true starts a fresh run on a NEW row.
    resp = t.client.post(
        "/v1/evidence/generate",
        json={"brief_id": brief_id, "insight_index": 0, "force": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "generating"
    assert resp.json()["evidence_id"] != failed_id
    for _ in range(20):
        if scheduled:
            break
        time.sleep(0.01)
    assert scheduled and scheduled[0] != failed_id


def test_payload_md_shape_matches_ui_contract(isolated_settings, monkeypatch):
    """The KG path writes the row shape the UI reads: a ready row whose
    payload_md is the self-contained HTML brief (variant v3), which the
    EvidenceScreen renders in a sandboxed iframe."""
    from app import evidence_kg
    _seed_template(isolated_settings["data_dir"])
    db_mod = isolated_settings["db"]
    _seed_company(isolated_settings["supabase"], slug="acme",
                  company_id="ent-A")
    facade = __import__("app.graph", fromlist=["GraphFacade"]).GraphFacade()
    theme, _hyp, _sigs = _seed_theme_hypothesis(facade)
    brief_id = _seed_brief(db_mod, dataset="acme", insights=[
        {"title": "SSO gap blocks $1.4M in deals", "theme_id": theme.id}])
    evidence_id = db_mod.start_evidence(brief_id=brief_id, insight_index=0,
                                        title="t", template_version=4,
                                        variant="v3")
    html = (
        '<meta charset="utf-8"><style>.wrap{max-width:820px}</style>'
        '<div class="wrap"><h1>Beginners Plateau</h1>'
        '<svg viewBox="0 0 720 250"></svg></div>'
    )
    # The model sometimes wraps its HTML in a ```html code fence; the runner must
    # strip it so the stored payload is raw HTML (else the UI iframe shows
    # literal backticks / fails the `^<` sniff and renders blank).
    fenced = f"```html\n{html}\n```"
    monkeypatch.setattr(evidence_kg, "llm_call", lambda **kw: _llm_result(fenced))
    evidence_kg._run_sync_kg(evidence_id, brief_id, 0)

    row = db_mod.get_evidence(evidence_id)
    # Same contract fields the EvidenceScreen renders.
    assert set(row) >= {"id", "title", "payload_md", "status", "variant"}
    assert row["status"] == "ready"
    assert row["variant"] == "v3"               # HTML-brief storage variant
    # Stored as raw HTML — the wrapping code fence is stripped.
    assert "```" not in row["payload_md"]
    assert row["payload_md"].startswith("<meta")
    # Self-contained HTML, not the retired :::block markdown.
    assert '<div class="wrap"' in row["payload_md"]
    assert "<svg" in row["payload_md"]
    assert ":::" not in row["payload_md"]
    # Phase 2: the model emits an EMPTY <style>; the finalizer injects the
    # canonical evidence stylesheet so the stored doc is self-contained.
    assert "--problem:#dd4b32" in row["payload_md"]
    assert row["payload_md"].count("<style>") == 1
