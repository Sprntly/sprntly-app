"""The FORMAT a document is written in, when the user asked for a specific one.

A company can upload several formats and activate one; the active one governs
every new document automatically. What was missing is "this one, this time" —
and its absence was not a gap so much as a lie: asking for a PRD "using the
Template 1 template" produced a PRD in the ACTIVE format, with nothing anywhere
saying a different format had been used. The planner picked the right format,
and every layer below it dropped the answer on the floor.

Three things are tested here, and the middle one is the one that matters:

  1. An override reaches the skeleton — `resolve_prd_template` /
     `resolve_ticket_layout` write into the format that was ASKED for, not the
     one that happens to be active.
  2. An override that CANNOT be honoured is refused at the route, where the user
     can still do something about it — never substituted silently.
  3. An already-written PRD reports the format that wrote it, forever, even
     after a different format becomes active.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app import prd_runner
from app.artifact_templates.store import (
    TemplateNotFound,
    TemplateNotReady,
    TemplateWrongType,
    require_usable_template,
)
from app.prd_context import build_prd_context
from app.stories.layout import resolve_ticket_layout

COMPANY = "co-acme-7f3d"
OTHER = "co-globex-11b2"


def _row(template_id, artifact_type="prd", name="Acme PRD v2", *,
         is_active=False, compile_status="ready", compiled="<h1>{{title}}</h1>"):
    return {
        "id": template_id,
        "company_id": COMPANY,
        "artifact_type": artifact_type,
        "name": name,
        "is_active": is_active,
        "compile_status": compile_status,
        "compiled": compiled,
        "compile_notes": [],
    }


@pytest.fixture
def library(monkeypatch):
    """A company with an ACTIVE format and a second, inactive one.

    Both halves of every assertion below turn on that pair: without an active
    format there is nothing for an override to beat."""
    rows = {
        "active": _row("tpl-active", name="Template 2", is_active=True,
                       compiled="<h1>ACTIVE SKELETON</h1>"),
        "other": _row("tpl-other", name="Template 1",
                      compiled="<h1>REQUESTED SKELETON</h1>"),
    }
    by_id = {r["id"]: r for r in rows.values()}

    def _active(cid, kind):
        return rows["active"] if (cid == COMPANY and kind == "prd") else None

    def _by_id(cid, tid):
        return by_id.get(tid) if cid == COMPANY else None

    # THREE binding sites, not one, and they are genuinely different objects:
    # `prd_runner` imports `get_active_template` at module level (so patching
    # the source module would not reach it), the override leg imports
    # `get_template_by_id` inside the function (so the source module IS what it
    # reads), and `artifact_templates.store` goes through the `app.db` facade.
    # Patching only the source module made every one of these tests pass for the
    # wrong reason — against a real client that was not configured.
    monkeypatch.setattr(prd_runner, "get_active_template", _active)
    monkeypatch.setattr("app.db.artifact_templates.get_active_template", _active)
    monkeypatch.setattr("app.db.artifact_templates.get_template_by_id", _by_id)
    monkeypatch.setattr("app.db.get_template_by_id", _by_id)
    monkeypatch.setattr(prd_runner, "_load_part_a_template", lambda: "<h1>BUILT-IN</h1>")
    return rows


# ── 1. the override reaches the skeleton ─────────────────────────────────────

def test_no_override_uses_the_active_format(library):
    """Unchanged behaviour, asserted first: the override is an addition, and the
    path every existing caller takes must be untouched by it."""
    template, template_id = prd_runner.resolve_prd_template(COMPANY)

    assert template == "<h1>ACTIVE SKELETON</h1>"
    assert template_id == "tpl-active"


def test_a_requested_format_beats_the_active_one(library):
    template, template_id = prd_runner.resolve_prd_template(COMPANY, "tpl-other")

    assert template == "<h1>REQUESTED SKELETON</h1>"
    assert template_id == "tpl-other"


def test_the_stamp_records_the_format_that_actually_served(library):
    """`artifact_template_id` on the row is the resolver's ANSWER, never the
    caller's request — so a request that could not be honoured is recorded as
    what was really used, and "what format is this PRD in" stays truthful."""
    _t, served = prd_runner.resolve_prd_template(COMPANY, "tpl-does-not-exist")

    assert served == "tpl-active"


def test_a_foreign_format_id_cannot_be_used(library):
    """The read is company-filtered, so another tenant's id is a miss — and the
    fallback is this company's own active format, never the foreign skeleton."""
    template, template_id = prd_runner.resolve_prd_template(OTHER, "tpl-other")

    # OTHER has no active format either, so it lands on the built-in.
    assert template == "<h1>BUILT-IN</h1>"
    assert template_id is None


def test_a_ticket_format_cannot_write_a_prd(library, monkeypatch):
    """Wrong-kind falls back rather than honouring: a PRD written into a ticket
    skeleton is a document in the wrong shape."""
    _wrong = lambda cid, tid: _row(tid, artifact_type="tickets", compiled="[]")
    monkeypatch.setattr("app.db.artifact_templates.get_template_by_id", _wrong)
    monkeypatch.setattr("app.db.get_template_by_id", _wrong)

    template, template_id = prd_runner.resolve_prd_template(COMPANY, "tpl-tickets")

    assert template == "<h1>ACTIVE SKELETON</h1>"
    assert template_id == "tpl-active"


def test_tickets_honour_a_requested_layout(monkeypatch):
    layout_json = json.dumps([
        {"label": "Why now", "source": "why_now"},
        {"label": "User story", "source": "user_story"},
    ])
    monkeypatch.setattr(
        "app.db.artifact_templates.get_active_template",
        lambda cid, kind: _row("tpl-active-tickets", artifact_type="tickets",
                               is_active=True, compiled=json.dumps([
                                   {"label": "Active label", "source": "why_now"},
                               ])),
    )
    monkeypatch.setattr(
        "app.db.artifact_templates.get_template_by_id",
        lambda cid, tid: _row(tid, artifact_type="tickets", compiled=layout_json),
    )

    layout, template_id = resolve_ticket_layout(COMPANY, "tpl-requested")

    assert template_id == "tpl-requested"
    assert [e["label"] for e in layout] == ["Why now", "User story"]


# ── 2. an unusable override is refused where the user can act on it ──────────

def test_an_unknown_format_is_refused_not_substituted(library):
    with pytest.raises(TemplateNotFound):
        require_usable_template(COMPANY, "tpl-nope", "prd")


def test_a_foreign_format_is_indistinguishable_from_a_missing_one(library):
    """404, never 403 — a foreign tenant must not be able to tell "exists but
    not yours" from "doesn't exist"."""
    with pytest.raises(TemplateNotFound):
        require_usable_template(OTHER, "tpl-other", "prd")


def test_a_format_for_another_kind_of_document_is_refused(library, monkeypatch):
    _wrong = lambda cid, tid: _row(tid, artifact_type="tickets", name="Acme tickets")
    monkeypatch.setattr("app.db.artifact_templates.get_template_by_id", _wrong)
    monkeypatch.setattr("app.db.get_template_by_id", _wrong)

    with pytest.raises(TemplateWrongType) as exc:
        require_usable_template(COMPANY, "tpl-tickets", "prd")
    # The message names both sides in words a person uses, never `impl_spec`.
    assert "ticket format" in str(exc.value)
    assert "PRD" in str(exc.value)


def test_a_format_that_never_compiled_is_refused(library, monkeypatch):
    _draft = lambda cid, tid: _row(tid, compile_status="pending", compiled="")
    monkeypatch.setattr("app.db.artifact_templates.get_template_by_id", _draft)
    monkeypatch.setattr("app.db.get_template_by_id", _draft)

    with pytest.raises(TemplateNotReady):
        require_usable_template(COMPANY, "tpl-draft", "prd")


def test_an_active_format_is_usable_even_mid_recheck(library, monkeypatch):
    """The gate is `compiled != ""`, not `compile_status == "ready"` — a format
    being re-checked still has its last good skeleton, and refusing it would
    refuse the format the company is demonstrably already generating with."""
    _mid = lambda cid, tid: _row(tid, is_active=True, compile_status="compiling")
    monkeypatch.setattr("app.db.artifact_templates.get_template_by_id", _mid)
    monkeypatch.setattr("app.db.get_template_by_id", _mid)

    assert require_usable_template(COMPANY, "tpl-active", "prd")["id"] == "tpl-active"


# ── 3. a written PRD reports the format that wrote it, forever ───────────────

def _seed_prd(db, *, slug, prd_id, artifact_template_id=None):
    brief = (
        db.table("briefs")
        .insert({"dataset": slug, "week_label": "W",
                 "payload": {"insights": []}, "is_current": True})
        .execute().data[0]
    )
    row = {
        "id": prd_id, "brief_id": brief["id"], "insight_index": 0,
        "title": "Export flow revamp", "status": "ready",
        "payload_md": "# The PRD body",
    }
    if artifact_template_id is not None:
        row["artifact_template_id"] = artifact_template_id
    db.table("prds").insert(row).execute()
    return brief


def test_the_prd_context_names_the_format_that_wrote_it(
    tenant_client, isolated_settings, monkeypatch
):
    """The defect this replaces: asked "what template did this PRD use?", the
    assistant compared the document against the company's CONFLUENCE page
    templates and reported a "custom structure" — a confident answer about
    entirely the wrong kind of template."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_prd(db, slug="acme", prd_id=4242, artifact_template_id="tpl-other")
    monkeypatch.setattr(
        "app.db.artifact_templates.get_template_by_id",
        lambda cid, tid: _row(tid, name="Template 1"),
    )

    block = build_prd_context(t.company_id, 4242)

    assert "The format this PRD was written in" in block
    assert "Template 1" in block


def test_a_pinned_format_is_not_corrected_to_whatever_is_active_now(
    tenant_client, isolated_settings, monkeypatch
):
    """THE REQUIREMENT: activating a different format later must not change what
    an existing PRD reports. The block says so in words, because a model holding
    both this and the workspace's format library would otherwise helpfully
    "correct" the stamp to the currently-active format."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_prd(db, slug="acme", prd_id=4243, artifact_template_id="tpl-other")
    monkeypatch.setattr(
        "app.db.artifact_templates.get_template_by_id",
        lambda cid, tid: _row(tid, name="Template 1", is_active=False),
    )

    block = build_prd_context(t.company_id, 4243)

    assert "NOT the company's currently active PRD format" in block
    assert "does not need correcting" in block


def test_a_prd_with_no_stamp_says_it_used_the_built_in(
    tenant_client, isolated_settings
):
    """NULL has always meant "Sprntly's own format" on that column. Stated
    rather than omitted — "no custom format" is the answer to the question, and
    a missing section sends the model looking for one."""
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_prd(db, slug="acme", prd_id=4244)

    block = build_prd_context(t.company_id, 4244)

    assert "built-in PRD format" in block


def test_a_deleted_format_is_reported_as_deleted_not_as_absent(
    tenant_client, isolated_settings, monkeypatch
):
    t = tenant_client.make(slug="acme")
    db = isolated_settings["supabase"]
    _seed_prd(db, slug="acme", prd_id=4245, artifact_template_id="tpl-gone")
    monkeypatch.setattr(
        "app.db.artifact_templates.get_template_by_id", lambda cid, tid: None
    )

    block = build_prd_context(t.company_id, 4245)

    assert "since been deleted" in block
