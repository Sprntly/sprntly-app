"""Server-side canonical-CSS injection at the PRD / Evidence finalizers.

Phase 2: the model emits an EMPTY `<style>`; the finalizer splices the skill's
canonical stylesheet (`assets/*.css`) into the stored document so `payload_md`
stays a self-contained HTML doc. These tests pin that behaviour on all three
generation paths and guard the skill-asset contract.
"""
from __future__ import annotations

import types

from app.skills.loader import get_skill


# ── skill-asset contract ──────────────────────────────────────────────────────

def test_prd_skill_ships_canonical_css_asset_and_empty_template_style():
    spec = get_skill("prd-author")
    assert "prd.css" in spec.assets, "prd-author must ship assets/prd.css"
    css = spec.assets["prd.css"]
    assert ":root{--green:#1A6B47" in css      # the canonical token block
    assert ".page{position:relative" in css     # a core component rule
    # The template the MODEL sees must NOT carry the CSS anymore — only an empty
    # marker — or the whole point (fewer output tokens) is lost.
    tmpl = spec.templates["prd-template.html"]
    assert "<style>" in tmpl
    assert "@import" not in tmpl and ":root{" not in tmpl and ".page{" not in tmpl


def test_evidence_skill_ships_canonical_css_asset():
    spec = get_skill("evidence-brief")
    assert "evidence.css" in spec.assets, "evidence-brief must ship assets/evidence.css"
    css = spec.assets["evidence.css"]
    assert "--problem:#dd4b32" in css
    assert ".wrap{max-width:820px" in css


# ── PRD finalizer ─────────────────────────────────────────────────────────────

def _fake_result(output: str):
    return types.SimpleNamespace(
        output=output, model="claude-sonnet-4-6",
        prompt_version="prd-author-v4+prd-author@deadbeef",
    )


def test_finalize_part_a_injects_canonical_css(monkeypatch):
    from app import prd_runner

    stored = {}
    monkeypatch.setattr(
        prd_runner, "complete_prd",
        lambda prd_id, title, md: stored.update(prd_id=prd_id, title=title, md=md),
    )
    # The model output the empty-marker template shape (no CSS rules of its own).
    model_html = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<style>/* injected server-side */</style></head>"
        "<body><div class='frame'><div class='page'><h1>Real PRD</h1></div></div></body></html>"
    )
    ctx = {"title": "Real PRD", "trail": None, "company_id": None}
    prd_runner._finalize_part_a(1, 2, 0, ctx, _fake_result(model_html))

    md = stored["md"]
    # Canonical stylesheet is now in the stored doc…
    assert ":root{--green:#1A6B47" in md
    assert "@media print" in md
    # …the empty marker is gone, exactly one <style>, and the body survived.
    assert "/* injected server-side */" not in md
    assert md.count("<style>") == 1
    assert "<h1>Real PRD</h1>" in md
    assert md.lstrip().startswith("<!DOCTYPE html>")


def test_finalize_part_a_overwrites_css_the_model_leaked(monkeypatch):
    """Defense-in-depth: if the model ignores the instruction and emits CSS, the
    canonical block still wins (idempotent, single source of truth)."""
    from app import prd_runner

    stored = {}
    monkeypatch.setattr(
        prd_runner, "complete_prd",
        lambda prd_id, title, md: stored.update(md=md),
    )
    model_html = (
        "<!DOCTYPE html><head><style>.page{padding:1px}</style></head>"
        "<body><div class='page'>x</div></body>"
    )
    prd_runner._finalize_part_a(
        1, 2, 0, {"title": "T", "trail": None, "company_id": None},
        _fake_result(model_html),
    )
    assert ".page{padding:1px}" not in stored["md"]
    assert ".page{position:relative" in stored["md"]


# ── Evidence finalizer — corpus fallback path ────────────────────────────────
# (The KG path's identical injection line is covered end-to-end by
#  tests/test_evidence_kg.py::test_payload_md_shape_matches_ui_contract.)

def test_evidence_corpus_runner_injects_canonical_css(monkeypatch):
    from app import evidence_runner

    model_html = (
        '<meta charset="utf-8"><style></style>'
        '<div class="wrap"><h1>Brief</h1></div>'
    )
    monkeypatch.setattr(
        evidence_runner, "llm_call",
        lambda **kw: types.SimpleNamespace(output=model_html, model="m",
                                           prompt_version="v"),
    )
    monkeypatch.setattr(evidence_runner, "get_brief_by_id",
                        lambda bid: {"dataset": "acme",
                                     "insights": [{"title": "SSO gap"}]})
    monkeypatch.setattr(evidence_runner, "load_corpus",
                        lambda ds: types.SimpleNamespace(joined=lambda: "corpus text"))
    monkeypatch.setattr(evidence_runner, "resolve_company",
                        lambda ds: ("ent-A", "acme"))
    stored = {}
    monkeypatch.setattr(
        evidence_runner, "complete_evidence",
        lambda evidence_id, title, md: stored.update(md=md),
    )
    evidence_runner._run_sync(evidence_id=5, brief_id=2, insight_index=0)

    md = stored["md"]
    assert "--problem:#dd4b32" in md
    assert md.startswith("<meta")
    assert '<div class="wrap">' in md
    assert md.count("<style>") == 1
