"""Tests for PRD reuse across brief regenerations (app.prd_runner.try_reuse_prd).

Brief regenerations mint a new brief_id, orphaning every PRD keyed to the old
one even when an insight is unchanged. Reuse clones the previous ready PRD
(rendered content — user edits included) into a fresh row for the new brief.
These tests prove:

  * sameness = normalized title AND tag (conservative, no fuzzy matching),
  * the generate route returns a ready clone instantly for an unchanged
    insight, with the source PRD's content,
  * `force=true` skips reuse and regenerates,
  * a changed/missing insight falls through to normal generation,
  * the warm path reuses before spending LLM tokens,
  * PRD_REUSE_ENABLED=false disables the whole path.
"""
from __future__ import annotations

import asyncio

from app import prd_runner
from app.prd_runner import _matching_insight_index, try_reuse_prd


def _ins(title: str, tag: str = "something_broken") -> dict:
    return {"title": title, "tag": tag}


# ── matching ────────────────────────────────────────────────────────────────

def test_matching_is_title_and_tag_normalized():
    prev = [_ins("Checkout drop-off"), _ins("Onboarding friction", tag="something_new")]
    # Case/whitespace-insensitive title match, same tag.
    assert _matching_insight_index(prev, _ins("  checkout   DROP-OFF ")) == 0
    # Same title but different tag → no match.
    assert _matching_insight_index(prev, _ins("Checkout drop-off", tag="something_new")) is None
    # Different title → no match.
    assert _matching_insight_index(prev, _ins("Brand-new finding")) is None
    # Empty title never matches anything.
    assert _matching_insight_index(prev, {"title": "", "tag": "something_broken"}) is None


# ── try_reuse_prd against the fake DB ───────────────────────────────────────

def _seed_brief(db_mod, dataset: str, insights: list[dict]) -> int:
    return db_mod.save_brief(
        dataset=dataset,
        week_label="Week of stub",
        payload={"summary_headline": "s", "insights": insights, "_schema_version": 1},
        schema_version=1,
    )


def _seed_ready_prd(db_mod, brief_id: int, insight_index: int, md: str) -> int:
    prd_id = db_mod.start_prd(
        brief_id=brief_id, insight_index=insight_index, title="t",
        template_version=1, variant="v2",
    )
    db_mod.complete_prd(prd_id, title="Reusable PRD", md=md)
    return prd_id


def test_reuse_clones_ready_prd_from_previous_brief(isolated_settings):
    db_mod = isolated_settings["db"]
    old_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])
    src_id = _seed_ready_prd(db_mod, old_brief, 0, "# The PRD body")
    new_brief = _seed_brief(db_mod, "acme", [_ins("checkout DROP-OFF")])

    brief = db_mod.get_brief_by_id(new_brief)
    new_id = try_reuse_prd(brief, 0)

    assert new_id is not None and new_id != src_id
    clone = db_mod.get_prd(new_id)
    assert clone["status"] == "ready"
    assert clone["payload_md"] == "# The PRD body"
    assert clone["brief_id"] == new_brief
    assert clone["variant"] == "v2"
    # Source row untouched.
    assert db_mod.get_prd(src_id)["brief_id"] == old_brief


def test_reuse_returns_none_when_insight_changed(isolated_settings):
    db_mod = isolated_settings["db"]
    old_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])
    _seed_ready_prd(db_mod, old_brief, 0, "# old")
    new_brief = _seed_brief(db_mod, "acme", [_ins("A different finding")])

    assert try_reuse_prd(db_mod.get_brief_by_id(new_brief), 0) is None


def test_reuse_returns_none_when_src_not_ready(isolated_settings):
    db_mod = isolated_settings["db"]
    old_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])
    db_mod.start_prd(  # generating, never completed
        brief_id=old_brief, insight_index=0, title="t",
        template_version=1, variant="v2",
    )
    new_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])

    assert try_reuse_prd(db_mod.get_brief_by_id(new_brief), 0) is None


def test_reuse_disabled_by_config(isolated_settings, monkeypatch):
    monkeypatch.setattr(prd_runner.settings, "prd_reuse_enabled", False)
    db_mod = isolated_settings["db"]
    old_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])
    _seed_ready_prd(db_mod, old_brief, 0, "# old")
    new_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])

    assert try_reuse_prd(db_mod.get_brief_by_id(new_brief), 0) is None


def test_reuse_does_not_cross_datasets(isolated_settings):
    db_mod = isolated_settings["db"]
    other = _seed_brief(db_mod, "other-co", [_ins("Checkout drop-off")])
    _seed_ready_prd(db_mod, other, 0, "# other tenant's PRD")
    new_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])

    assert try_reuse_prd(db_mod.get_brief_by_id(new_brief), 0) is None


# ── generate route integration ──────────────────────────────────────────────

def test_generate_route_reuses_unchanged_insight(tenant_client, isolated_settings):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    old_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])
    src_id = _seed_ready_prd(db_mod, old_brief, 0, "# Reused body")
    new_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])

    resp = t.client.post(
        "/v1/prd/generate", json={"brief_id": new_brief, "insight_index": 0}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"           # instant — no generation kicked
    assert body["prd_id"] != src_id            # a clone, not the source row
    assert db_mod.get_prd(body["prd_id"])["payload_md"] == "# Reused body"


def test_generate_route_force_skips_reuse(tenant_client, isolated_settings, fake_llm):
    t = tenant_client.make(slug="acme")
    db_mod = isolated_settings["db"]
    old_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])
    _seed_ready_prd(db_mod, old_brief, 0, "# Reused body")
    new_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])

    resp = t.client.post(
        "/v1/prd/generate",
        json={"brief_id": new_brief, "insight_index": 0, "force": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "generating"  # fresh generation, no clone


# ── warm path ───────────────────────────────────────────────────────────────

def test_warm_reuses_before_generating(isolated_settings, monkeypatch):
    monkeypatch.setattr(prd_runner.settings, "prd_warm_count", 1)
    db_mod = isolated_settings["db"]
    old_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])
    _seed_ready_prd(db_mod, old_brief, 0, "# Warm-reused body")
    new_brief = _seed_brief(db_mod, "acme", [_ins("Checkout drop-off")])

    generated = []

    async def _generate(prd_id, brief_id, insight_index, background=False):
        generated.append(prd_id)

    monkeypatch.setattr(prd_runner, "generate_prd", _generate)

    brief = db_mod.get_brief_by_id(new_brief)
    asyncio.run(prd_runner.warm_prds_for_brief(brief))

    assert generated == []  # reuse satisfied the warm — zero LLM spend
    reused = db_mod.find_existing_prd(new_brief, 0, variant="v2")
    assert reused is not None
    assert reused["status"] == "ready"
    assert reused["payload_md"] == "# Warm-reused body"
