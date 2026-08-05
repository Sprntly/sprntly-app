"""The reader's insight-type selection must decide the brief's TOP insight.

Onboarding step 09 and Settings -> Comms & Brief both write
companies.notification_settings.brief_insight_types. Until 2026-08-04 that
selection only reached the compose PROMPT (a ranking nudge the model was free
to ignore) and the BROWSER (brief-v2-adapter filtered `_pool` client-side), so
every other consumer of the canonical brief -- the weekly email, the Slack
post, brief nudges, PRD warming, the KG ledger, MCP -- led with whatever the
model ranked first. Measured on the live briefs, a preferred finding existed
but sat below rank 1 in most of them.

These cover the deterministic half: the composed pool is stable-partitioned by
the selection before `insights = pool[:MAX_INSIGHTS]`, so `insights[0]` is a
preferred finding whenever the pool holds one.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.graph.gateway import LLMResult


def _llm_result(output, model="claude-sonnet-4-6"):
    return LLMResult(
        output=output, model=model, prompt_version="test",
        input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
        cache_creation_input_tokens=0, cost_usd=0.001, latency_ms=5,
        stop_reason="end_turn",
    )


@pytest.fixture
def facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _seed_multi_source_theme(facade, ent, label):
    """A multi-source theme so the #846 evidence gate passes."""
    from app.graph.types import Entity, Relationship, Signal
    theme = Entity(enterprise_id=ent, type="theme", canonical_label=label)
    facade.create_entity(ent, theme)
    now = datetime.now(timezone.utc)
    for st, kind, props, age in [
        ("revenue", "deal_blocker", {"revenue_at_risk_usd": 1400000}, 1),
        ("customer_voice", "feature_request", {}, 2),
    ]:
        sig = Signal(enterprise_id=ent, source_type=st, kind=kind,
                     content=f"{label} {kind}", properties=props,
                     valid_at=now - timedelta(days=age))
        facade.write_signal(ent, sig)
        facade.write_relationship(ent, Relationship(
            enterprise_id=ent, type="REQUESTS", source_kind="signal",
            source_id=sig.id, target_kind="entity", target_id=theme.id))
    return theme


def _insight(i, *, insight_types, is_headline=False):
    return {
        "theme_id": f"t{i}",
        "tag": "something_broken",
        "insight_types": insight_types,
        "title": f"Finding {i}",
        "subtitle": f"Subtitle {i}.",
        "recommendation": f"Do thing {i}.",
        "metrics": [{"label": "ARR at risk", "value": "$1.4M"}],
        "chart_hints": [],
        "convergence": [{"source": "revenue", "signal": "s", "strength": "Strong"}],
        # Descending, so "highest confidence" and "model rank 0" agree — any
        # reordering below is the PREFERENCE acting, not a confidence tiebreak.
        "confidence": 0.9 - i * 0.05,
        "is_headline": is_headline,
        "prototypeable": True,
        "reasoning": f"Reason {i}.",
    }


def _set_prefs(isolated_settings, types, *, ent="ent-A", note=None):
    """Write the workspace selection exactly where onboarding/Settings write it."""
    db = isolated_settings["supabase"]
    if not db.table("companies").select("id").eq("id", ent).execute().data:
        db.table("companies").insert(
            {"id": ent, "slug": "acme", "display_name": "Acme"}).execute()
    settings = {"brief_insight_types": types}
    if note is not None:
        settings["brief_insight_note"] = note
    db.table("companies").update(
        {"notification_settings": settings}).eq("id", ent).execute()


def _run_with_insights(facade, insights, *, ent="ent-A", slug="acme"):
    from app.synthesis import agent as synth
    _seed_multi_source_theme(facade, ent, "SSO")
    ranked = {"summary_headline": "H", "insights": insights}
    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)):
        return synth.run_synthesis(facade, ent, dataset_slug=slug)


# A pool where the model's own rank 0 is `top_problems` and each of the other
# five types sits strictly BELOW it — so for any single-type preference other
# than top_problems, a correct implementation must promote a lower-ranked
# finding to the lead.
def _mixed_pool():
    return [
        _insight(0, insight_types=["top_problems"], is_headline=True),
        _insight(1, insight_types=["build_priorities"]),
        _insight(2, insight_types=["user_feedback"]),
        _insight(3, insight_types=["competitor_moves"]),
        _insight(4, insight_types=["reliability_signals"]),
        _insight(5, insight_types=["wins"]),
    ]


# ---------- the ordering primitive ----------

def test_order_pool_is_a_stable_partition():
    """Matching findings lead in their existing order; the rest keep theirs.
    Mirrors the frontend's orderPoolForTypes so the two cannot disagree."""
    from app.insight_types import order_pool_for_types

    pool = [
        {"title": "a", "insight_types": ["wins"]},
        {"title": "b", "insight_types": ["top_problems"]},
        {"title": "c", "insight_types": ["wins", "user_feedback"]},
        {"title": "d", "insight_types": ["top_problems"]},
    ]
    ordered, matched = order_pool_for_types(pool, ["wins"])
    assert [i["title"] for i in ordered] == ["a", "c", "b", "d"]
    assert matched == 2


def test_order_pool_is_identity_without_a_selection():
    """No selection => the model's own ranking stands, untouched."""
    from app.insight_types import order_pool_for_types

    pool = _mixed_pool()
    ordered, matched = order_pool_for_types(pool, [])
    assert ordered == pool
    assert matched == 0


def test_order_pool_ignores_unknown_slugs_and_missing_types():
    """A junk selection degrades to 'no preference' rather than filtering to
    nothing, and a finding with no insight_types is never treated as a match."""
    from app.insight_types import order_pool_for_types

    pool = [{"title": "a"}, {"title": "b", "insight_types": ["wins"]}]
    ordered, matched = order_pool_for_types(pool, ["not_a_type"])
    assert ordered == pool and matched == 0
    ordered, matched = order_pool_for_types(pool, ["wins"])
    assert [i["title"] for i in ordered] == ["b", "a"]
    assert matched == 1


def test_order_pool_never_drops_a_finding():
    """Preferences REORDER, they never exclude (SKILL.md step 4b) — the pool
    that comes out is a permutation of the pool that went in."""
    from app.insight_types import order_pool_for_types

    pool = _mixed_pool()
    ordered, _ = order_pool_for_types(pool, ["wins"])
    assert len(ordered) == len(pool)
    assert sorted(i["title"] for i in ordered) == sorted(i["title"] for i in pool)


# ---------- each preference value steers the composed brief ----------

@pytest.mark.parametrize("slug,expected_title", [
    ("top_problems", "Finding 0"),
    ("build_priorities", "Finding 1"),
    ("user_feedback", "Finding 2"),
    ("competitor_moves", "Finding 3"),
    ("reliability_signals", "Finding 4"),
    ("wins", "Finding 5"),
])
def test_each_preference_decides_the_top_insight(
        facade, isolated_settings, slug, expected_title):
    """Every one of the six selectable types promotes its own finding to the
    lead of the CANONICAL brief — the array the email and Slack render from."""
    _set_prefs(isolated_settings, [slug])
    brief = _run_with_insights(facade, _mixed_pool())

    assert brief["insights"][0]["title"] == expected_title
    assert slug in brief["insights"][0]["insight_types"]
    # And it is what got PERSISTED, not just returned.
    rows = isolated_settings["supabase"].table("briefs").select("*") \
        .eq("dataset", "acme").execute().data
    assert rows[0]["payload"]["insights"][0]["title"] == expected_title


def test_multi_type_preference_keeps_pool_rank_among_matches(
        facade, isolated_settings):
    """With several types picked, the matches lead in the model's own
    best-first order — the preference chooses the SET, our ranking orders it."""
    _set_prefs(isolated_settings, ["wins", "user_feedback"])
    brief = _run_with_insights(facade, _mixed_pool())

    # Finding 2 (user_feedback) outranks Finding 5 (wins) in the model's pool.
    assert [i["title"] for i in brief["insights"][:2]] == ["Finding 2", "Finding 5"]


def test_preference_reorders_the_persisted_pool_too(facade, isolated_settings):
    """`_pool` is saved in preference order, so the browser's own partition of
    it is the identity — the web hero and the emailed lead cannot diverge."""
    _set_prefs(isolated_settings, ["wins"])
    brief = _run_with_insights(facade, _mixed_pool())

    assert brief["_pool"][0]["title"] == "Finding 5"
    assert len(brief["_pool"]) == 6  # nothing dropped
    assert brief["insights"][0]["title"] == brief["_pool"][0]["title"]


def test_headline_flag_follows_the_new_lead(facade, isolated_settings):
    """The model marked Finding 0 as is_headline. Once the preference promotes
    another finding, the flag moves with it — workspace-brief's headline pick
    and prd_runner's ordering both read that flag and would otherwise point at
    a demoted card."""
    _set_prefs(isolated_settings, ["reliability_signals"])
    brief = _run_with_insights(facade, _mixed_pool())

    assert brief["insights"][0]["title"] == "Finding 4"
    assert brief["insights"][0]["is_headline"] is True
    assert [i["title"] for i in brief["_pool"] if i.get("is_headline")] == ["Finding 4"]


def test_preference_audit_is_recorded_on_the_brief(facade, isolated_settings):
    """The brief carries what the selection was and how much of the pool it
    matched, so "why is this my top insight" is answerable after the fact."""
    _set_prefs(isolated_settings, ["wins", "user_feedback"])
    brief = _run_with_insights(facade, _mixed_pool())

    assert brief["_insight_prefs"] == {
        "selected": ["wins", "user_feedback"], "matched": 2}


# ---------- an updated preference changes the NEXT brief ----------

def test_changing_the_selection_changes_the_next_brief(facade, isolated_settings):
    """The Comms & Brief / Settings edit is authoritative at the next
    generation — the whole point of the feature."""
    from app.synthesis import agent as synth

    _set_prefs(isolated_settings, ["wins"])
    first = _run_with_insights(facade, _mixed_pool())
    assert first["insights"][0]["title"] == "Finding 5"

    # The PM reopens Settings -> Comms & Brief and switches to reliability.
    _set_prefs(isolated_settings, ["reliability_signals"])
    ranked = {"summary_headline": "H", "insights": _mixed_pool()}
    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)):
        second = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    assert second["insights"][0]["title"] == "Finding 4"
    assert second["_insight_prefs"]["selected"] == ["reliability_signals"]


def test_clearing_the_selection_restores_model_rank(facade, isolated_settings):
    """Emptying the chips means "surface everything" — back to the model's own
    ranking, not a stuck previous preference."""
    from app.synthesis import agent as synth

    _set_prefs(isolated_settings, ["wins"])
    assert _run_with_insights(facade, _mixed_pool())["insights"][0]["title"] == "Finding 5"

    _set_prefs(isolated_settings, [])
    ranked = {"summary_headline": "H", "insights": _mixed_pool()}
    with patch.object(synth, "llm_call", return_value=_llm_result(ranked)):
        second = synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    assert second["insights"][0]["title"] == "Finding 0"
    assert second["_insight_prefs"] == {"selected": [], "matched": 0}


# ---------- sane fallbacks ----------

def test_no_preference_set_leaves_the_brief_untouched(facade, isolated_settings):
    """A company that never picked anything (no companies row / empty blob)
    gets the model's ranking, and nothing raises."""
    brief = _run_with_insights(facade, _mixed_pool())
    assert [i["title"] for i in brief["insights"]] == [
        "Finding 0", "Finding 1", "Finding 2"]
    assert brief["_insight_prefs"] == {"selected": [], "matched": 0}


def test_preference_matching_nothing_falls_back_to_model_rank(
        facade, isolated_settings):
    """A type with no findings this week must not blank or reshuffle the brief
    — preferences never exclude, so the strongest findings still lead."""
    _set_prefs(isolated_settings, ["wins"])
    pool = [
        _insight(0, insight_types=["top_problems"], is_headline=True),
        _insight(1, insight_types=["build_priorities"]),
        _insight(2, insight_types=["user_feedback"]),
    ]
    brief = _run_with_insights(facade, pool)

    assert [i["title"] for i in brief["insights"]] == [
        "Finding 0", "Finding 1", "Finding 2"]
    assert brief["_insight_prefs"] == {"selected": ["wins"], "matched": 0}
    # No match => the model's own headline pick is left alone.
    assert brief["insights"][0]["is_headline"] is True


def test_stored_junk_selection_degrades_to_no_preference(
        facade, isolated_settings):
    """A retired/hand-edited slug must not silently filter the brief."""
    _set_prefs(isolated_settings, ["drive_metric", "not_a_type"])
    brief = _run_with_insights(facade, _mixed_pool())
    assert brief["insights"][0]["title"] == "Finding 0"
    assert brief["_insight_prefs"] == {"selected": [], "matched": 0}


def test_unreadable_preferences_never_break_generation(facade, isolated_settings):
    """The selection read is best-effort: a DB failure must degrade to "no
    preference", never lose the brief."""
    from app.synthesis import reader_prefs

    with patch.object(reader_prefs, "_notification_settings",
                      side_effect=RuntimeError("supabase down")):
        brief = _run_with_insights(facade, _mixed_pool())

    assert brief["insights"][0]["title"] == "Finding 0"
    assert brief["_insight_prefs"] == {"selected": [], "matched": 0}


def test_selection_still_reaches_the_compose_prompt(facade, isolated_settings):
    """The deterministic reorder ADDS to the prompt nudge, it doesn't replace
    it — the model still gets to phrase the brief around the preference."""
    from app.synthesis import agent as synth

    _set_prefs(isolated_settings, ["reliability_signals"],
               note="Latency on enterprise accounts matters most")
    _seed_multi_source_theme(facade, "ent-A", "SSO")
    captured = {}

    def _capture(*args, **kwargs):
        captured["input"] = kwargs.get("input", "")
        return _llm_result({"summary_headline": "H", "insights": _mixed_pool()})

    with patch.object(synth, "llm_call", side_effect=_capture):
        synth.run_synthesis(facade, "ent-A", dataset_slug="acme")

    assert "READER PREFERENCES" in captured["input"]
    assert "Reliability & incident signals" in captured["input"]
    assert "Latency on enterprise accounts matters most" in captured["input"]


# ---------- the evidence gates are untouched ----------

def test_preference_does_not_bypass_the_evidence_gate(facade, isolated_settings):
    """#846/#923: a company with no evidence-provider signal gets no brief,
    preference or not. The reorder runs strictly AFTER composition, which only
    happens once the gate has passed — it must not create a path around it."""
    from app.synthesis import agent as synth
    from app.graph.types import Entity, Relationship, Signal

    _set_prefs(isolated_settings, ["wins"], ent="ent-B")
    # project_mgmt only — a NON_EVIDENCE_TYPES source (Jira-shaped company).
    theme = Entity(enterprise_id="ent-B", type="theme", canonical_label="Rollout")
    facade.create_entity("ent-B", theme)
    sig = Signal(enterprise_id="ent-B", source_type="project_mgmt",
                 kind="ticket", content="ticket",
                 valid_at=datetime.now(timezone.utc) - timedelta(days=1))
    facade.write_signal("ent-B", sig)
    facade.write_relationship("ent-B", Relationship(
        enterprise_id="ent-B", type="REQUESTS", source_kind="signal",
        source_id=sig.id, target_kind="entity", target_id=theme.id))

    with patch.object(synth, "llm_call") as call:
        brief = synth.run_synthesis(facade, "ent-B", dataset_slug="jira-only")

    # Nothing composed, so nothing to reorder — and no insights to lead with.
    assert call.call_count == 0
    assert brief.get("insights") == []
