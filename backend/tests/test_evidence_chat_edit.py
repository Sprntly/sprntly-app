"""Editing the evidence page open beside the chat.

Reported (2026-09-03): "improve the evidence with an analytical chart of the
evidence" drew the chart into the chat thread. It had nowhere else to go —
evidence was the one artifact in that panel with no edit path at all. A PRD had
one, a report had one, a team document had one, and the document the request
was actually about did not.

What is under test is what can hurt:

  * THE TARGET IS THE CALLER'S — a foreign id reads as absent (404, never 403),
    so probing ids teaches nothing.
  * THE PAGE SURVIVES THE EDIT — an evidence brief is a whole HTML document
    with its own stylesheet and hand-authored SVG charts, and the prose
    sanitiser used for reports would strip it to headings. That is the failure
    this suite exists to catch, because it would look like a successful edit.
  * AN EDIT THAT ISN'T ONE WRITES NOTHING — a question about the page, or a
    chart whose numbers are not in it, leaves the row alone.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

import app.artifact_chat_edit as ace

# A miniature of the real thing: a document, a stylesheet, a class the viewer
# keys on, and a chart drawn as inline SVG.
PAGE = (
    '<meta charset="utf-8">\n<style>.wrap{max-width:60rem}</style>\n'
    '<div class="wrap"><h1>Export failures</h1>'
    '<section class="finding"><h2>Enterprise accounts</h2>'
    '<p>23% of uploads fail on iPhone 15 Pro.</p>'
    '<svg class="chart" viewBox="0 0 100 40"><rect width="23" height="8"/></svg>'
    '<p class="caption">iPhone 15 Pro fails at 23% — every other device under 2%.</p>'
    "</section></div>"
)


class _Company:
    def __init__(self, company_id="c1", user_id="u1", workspace_id=None):
        self.company_id = company_id
        self.user_id = user_id
        self.workspace_id = workspace_id


def _row(evidence_id=7, body=PAGE, title="Export failures"):
    return {
        "id": evidence_id, "brief_id": 1, "insight_index": 0,
        "title": title, "payload_md": body, "status": "ready", "variant": "v3",
    }


def _edited(document, sections=("Enterprise accounts",), summary="Added a chart."):
    return {
        "document": document,
        "sections_changed": list(sections),
        "summary": summary,
    }


@pytest.fixture()
def owned(monkeypatch):
    """The ownership chain resolves, and records what it was asked for."""
    seen: dict = {}

    def _require(evidence_id, company_id, workspace_id=None):
        seen.update(id=evidence_id, company=company_id, workspace=workspace_id)
        return _row(evidence_id)

    monkeypatch.setattr(
        "app.deps.ownership.require_owned_evidence", _require
    )
    return seen


@pytest.fixture()
def saved(monkeypatch):
    """What reached the database."""
    writes: list = []
    monkeypatch.setattr(
        "app.db.complete_evidence",
        lambda eid, title, md: writes.append((eid, title, md)),
    )
    return writes


def test_a_chart_lands_in_the_page_not_in_the_chat(owned, saved, monkeypatch):
    """THE REPORTED CASE. The instruction asks for a chart in the evidence; a
    chart in the evidence is what gets stored."""
    added = PAGE.replace(
        "</section>",
        '<svg class="chart" viewBox="0 0 100 40"><rect width="9" height="8"/></svg>'
        '<p class="caption">Failures cluster in the top decile of upload size.</p>'
        "</section>",
    )
    monkeypatch.setattr(ace, "apply_edit", lambda *a, **k: _edited(added))

    out = ace.edit_evidence_scoped(7, "add an analytical chart", _Company())

    assert out["sections_changed"] == ["Enterprise accounts"]
    assert len(saved) == 1
    _eid, _title, stored = saved[0]
    assert stored.count("<svg") == 2
    assert "top decile" in stored


def test_the_page_survives_the_round_trip(owned, saved, monkeypatch):
    """The failure that would look like success: an evidence page put through
    the prose sanitiser comes back as headings and paragraphs, with the
    stylesheet, the layout container and every chart gone."""
    monkeypatch.setattr(ace, "apply_edit", lambda *a, **k: _edited(PAGE))

    ace.edit_evidence_scoped(7, "tighten the summary", _Company())

    stored = saved[0][2]
    assert "<style" in stored
    assert 'class="wrap"' in stored
    assert "<svg" in stored
    assert 'class="caption"' in stored


def test_the_editor_is_given_the_evidence_shape(owned, saved, monkeypatch):
    """A prose FORMAT rule tells the model to return a dozen tags, which is an
    instruction to destroy this document. The shape is what stops that, so it
    is asserted at the seam rather than trusted."""
    seen: dict = {}

    def _apply(document, instruction, *, enterprise_id, label="document", shape="prose"):
        seen.update(label=label, shape=shape, document=document)
        return _edited(PAGE)

    monkeypatch.setattr(ace, "apply_edit", _apply)
    ace.edit_evidence_scoped(7, "add a chart", _Company())

    assert seen["shape"] == "evidence"
    assert seen["label"] == "evidence page"
    assert seen["document"] == PAGE


def test_a_script_never_survives_an_edit(owned, saved, monkeypatch):
    """The page renders with scripts disabled, and it is model output like any
    other. `normalize_evidence_html` is the same gate the generator uses."""
    monkeypatch.setattr(
        ace, "apply_edit",
        lambda *a, **k: _edited(PAGE + "<script>fetch('/steal')</script>"),
    )
    ace.edit_evidence_scoped(7, "add a chart", _Company())
    assert "<script" not in saved[0][2]


def test_a_question_writes_nothing(owned, saved, monkeypatch):
    """"What does this evidence say about pricing?" is not an edit. Neither is
    a chart whose numbers are not in the page — the editor is told to change
    nothing and say so, and that verdict must reach the row untouched."""
    monkeypatch.setattr(
        ace, "apply_edit",
        lambda *a, **k: _edited(PAGE, sections=(), summary="No numbers for that chart."),
    )
    out = ace.edit_evidence_scoped(7, "chart the revenue impact", _Company())

    assert out["sections_changed"] == []
    assert out["summary"] == "No numbers for that chart."
    assert saved == []


def test_a_foreign_page_is_absent_not_forbidden(monkeypatch, saved):
    """404, never 403 — a foreign tenant must not be able to tell "exists but
    not yours" from "does not exist"."""
    def _deny(evidence_id, company_id, workspace_id=None):
        raise HTTPException(404, "Evidence not found")

    monkeypatch.setattr("app.deps.ownership.require_owned_evidence", _deny)
    with pytest.raises(HTTPException) as exc:
        ace.edit_evidence_scoped(7, "add a chart", _Company())
    assert exc.value.status_code == 404
    assert saved == []


def test_the_workspace_travels_with_the_ownership_check(owned, saved, monkeypatch):
    """Evidence is workspace-scoped through its brief, and the route passes the
    caller's workspace. Dropping it here would widen the check to the whole
    company."""
    monkeypatch.setattr(ace, "apply_edit", lambda *a, **k: _edited(PAGE))
    ace.edit_evidence_scoped(
        7, "tighten it", _Company(workspace_id="ws-2"), workspace_id="ws-2",
    )
    assert owned["workspace"] == "ws-2"
    assert owned["company"] == "c1"


def test_an_empty_page_is_a_conflict_not_an_edit(monkeypatch, saved):
    """A page still generating, or one that failed. Editing it would write the
    model's idea of a brief over a row the reader is watching fill in."""
    monkeypatch.setattr(
        "app.deps.ownership.require_owned_evidence",
        lambda eid, cid, ws=None: _row(eid, body=""),
    )
    with pytest.raises(HTTPException) as exc:
        ace.edit_evidence_scoped(7, "add a chart", _Company())
    assert exc.value.status_code == 409
    assert saved == []


def test_an_unusable_edit_is_refused_rather_than_stored(owned, saved, monkeypatch):
    """The editor returned something that is not a document. Storing it would
    render a blank panel where a finished brief used to be."""
    monkeypatch.setattr(ace, "apply_edit", lambda *a, **k: _edited("   "))
    with pytest.raises(HTTPException) as exc:
        ace.edit_evidence_scoped(7, "add a chart", _Company())
    assert exc.value.status_code == 502
    assert saved == []


def test_an_editor_failure_leaves_the_page_alone(owned, saved, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("the edit returned an empty document")

    monkeypatch.setattr(ace, "apply_edit", boom)
    with pytest.raises(HTTPException) as exc:
        ace.edit_evidence_scoped(7, "add a chart", _Company())
    assert exc.value.status_code == 502
    assert saved == []


# ── the prompts ──────────────────────────────────────────────────────────────


def test_the_two_shapes_say_opposite_things_about_tags():
    """One prompt, two FORMAT rules. The prose one names an allow-list; the
    evidence one says keep the whole page. Sharing the prose rule is what would
    make an evidence edit destructive."""
    assert "stripped on save" in ace._EDIT_SYSTEM
    assert "stripped on save" not in ace._EDIT_SYSTEM_EVIDENCE
    assert "SELF-CONTAINED HTML PAGE" in ace._EDIT_SYSTEM_EVIDENCE
    assert "inline SVG" in ace._EDIT_SYSTEM_EVIDENCE
    # Everything else is shared, so the invent-nothing rule holds for both.
    for system in (ace._EDIT_SYSTEM, ace._EDIT_SYSTEM_EVIDENCE):
        assert "INVENT NOTHING" in system


def test_the_chart_rule_refuses_to_invent_a_data_point():
    """A chart is the most believable thing on the page, so the one failure
    that must not happen is a plausible number drawn from nowhere."""
    system = ace._EDIT_SYSTEM_EVIDENCE
    assert "Plot only numbers already in this page" in system
    assert "change NOTHING" in system


def test_the_prompt_version_moved_with_the_shape():
    assert ace.EDIT_PROMPT_VERSION == "artifact-chat-edit-v2"


def test_the_planner_can_name_an_evidence_page_as_the_target():
    """`edit_artifact`'s precondition is a line naming what is open. Evidence
    was never rendered into that line, so the action was unreachable for it —
    which is why the chart went into the chat."""
    from app.ask_planner import _open_artifact_block

    line = _open_artifact_block(
        None, None,
        {"kind": "evidence", "id": 42, "title": "Export failures"},
    )
    assert "evidence #42" in line
    assert "Export failures" in line


def test_the_planner_menu_says_a_chart_belongs_in_the_document():
    from app.ask_planner import _PLANNER_SYSTEM

    lowered = " ".join(_PLANNER_SYSTEM.lower().split())
    assert "a chart is an edit to the document, not a reply" in lowered
    assert "add a chart to the evidence" in lowered
    # …and which document wins when a PRD is open beside it.
    assert "when a prd and an evidence page are both open" in lowered
