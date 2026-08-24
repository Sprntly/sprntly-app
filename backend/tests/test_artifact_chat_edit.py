"""Editing the report or document open beside the chat.

Until now a report could be generated, read, shared and downloaded — and not
changed. Asked to "convert the RICE section into a table", chat wrote the table
into the conversation and told the user to paste it in themselves, because there
was no writer for either artifact.

Three things are under test, and they are the three that can hurt:

  * THE TARGET IS THE CALLER'S, NOT THE MODEL'S — a foreign id reads as absent
    (404, never 403), so a caller probing ids learns nothing.
  * AN EDIT THAT ISN'T ONE WRITES NOTHING — a question about the document must
    not bump a version or touch a stored row.
  * SHAPE SURVIVES — markdown is stored verbatim, HTML is sanitized, because
    both viewers decide how to render by sniffing the stored body.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import artifact_chat_edit as ace


class _Company:
    def __init__(self, company_id="c1", user_id="u1"):
        self.company_id = company_id
        self.user_id = user_id


def _edit(document="# Edited\n\nnew body", sections=("Summary",), summary="did it"):
    """A stand-in for the editor call — the LLM is never reached in this suite."""
    return {
        "document": document,
        "sections_changed": list(sections),
        "summary": summary,
    }


# ── the editor call itself ───────────────────────────────────────────────────


def test_apply_edit_returns_the_document_and_what_changed(monkeypatch):
    seen: dict = {}

    class _Result:
        output = {
            "document": "# Report\n\n| Feature | RICE |\n|---|---|\n",
            "sections_changed": ["RICE prioritisation"],
            "summary": "Turned the RICE list into a table.",
        }

    def _fake_call(**kw):
        seen.update(kw)
        return _Result()

    monkeypatch.setattr(ace, "llm_call", _fake_call)
    out = ace.apply_edit(
        "# Report\n\nRICE: reach 5, impact 3\n",
        "convert the RICE section into a table",
        enterprise_id="c1",
        label="report",
    )

    assert out["sections_changed"] == ["RICE prioritisation"]
    assert out["document"].startswith("# Report")
    # The instruction and the document both reach the model; the TARGET does not
    # — there is no id in this prompt for anything to get wrong.
    assert "convert the RICE section into a table" in seen["input"]
    assert "RICE: reach 5" in seen["input"]
    assert "report" in seen["input"].lower()
    # Long output: a report comes back whole and a non-streamed call of that
    # size hits the provider read timeout.
    assert seen["long_output"] is True


def test_apply_edit_refuses_an_empty_document(monkeypatch):
    class _Result:
        output = {"document": "   ", "sections_changed": ["x"], "summary": ""}

    monkeypatch.setattr(ace, "llm_call", lambda **kw: _Result())
    with pytest.raises(RuntimeError):
        ace.apply_edit("body", "shorten it", enterprise_id="c1")


def test_apply_edit_survives_a_non_dict_output(monkeypatch):
    """`LLMResult.output` is the contract; anything else is a failed edit, not a
    document written from a string repr."""
    class _Result:
        output = "not json"

    monkeypatch.setattr(ace, "llm_call", lambda **kw: _Result())
    with pytest.raises(RuntimeError):
        ace.apply_edit("body", "shorten it", enterprise_id="c1")


# ── reports ──────────────────────────────────────────────────────────────────


def test_editing_a_report_writes_the_new_body(isolated_settings, monkeypatch):
    from app import db

    rid = db.save_report(
        "c1", skill="voice-of-customer-report", title="VoC",
        html="<h1>VoC</h1><p>RICE: reach 5</p>",
    )
    monkeypatch.setattr(
        ace, "apply_edit",
        lambda *a, **k: _edit(document="<h1>VoC</h1><table><tr><td>RICE</td></tr></table>"),
    )

    out = ace.edit_report_scoped(rid, "make it a table", _Company())

    assert out["sections_changed"] == ["Summary"]
    assert "<table>" in out["report"]["html"]
    # And it is durable, not just returned.
    assert "<table>" in db.get_report(rid, "c1")["html"]


def test_a_legacy_markdown_report_is_edited_as_html(isolated_settings, monkeypatch):
    """The rows written before reports became rich documents hold markdown. They
    convert on the way IN, so the editor is handed the same shape it is handed
    for every other report — and the row is rewritten as HTML, which is the one
    moment that one-way conversion is something the user asked for."""
    from app import db

    rid = db.save_report(
        "c1", skill="voice-of-customer-report", title="V",
        html="# V\n\nThe **core** workflow works.\n",
    )
    seen: dict = {}

    def _capture(document, instruction, **kw):
        seen["document"] = document
        return _edit(document="<h1>V</h1><p>The <strong>core</strong> workflow works well.</p>")

    monkeypatch.setattr(ace, "apply_edit", _capture)
    ace.edit_report_scoped(rid, "say it works well", _Company())

    assert seen["document"].startswith("<h1>V</h1>"), "converted before the editor sees it"
    assert db.get_report(rid, "c1")["html"].startswith("<h1>V</h1>")


def test_an_edit_is_stored_as_sanitised_html(isolated_settings, monkeypatch):
    """An edit is model output like any other, and this body is rendered in the
    app, in the PDF and behind a public link. An editor that answered in markdown
    despite being handed HTML is converted rather than stored as source text."""
    from app import db

    rid = db.save_report("c1", skill="voice-of-customer-report", title="V", html="<p>V</p>")
    monkeypatch.setattr(
        ace, "apply_edit",
        lambda *a, **k: _edit(document="# V\n\n**bold** and <script>alert(1)</script>\n"),
    )

    ace.edit_report_scoped(rid, "tweak it", _Company())
    stored = db.get_report(rid, "c1")["html"]
    assert stored.startswith("<h1>V</h1>")
    assert "<strong>bold</strong>" in stored
    assert "<script>" not in stored


def test_a_question_about_a_report_writes_nothing(isolated_settings, monkeypatch):
    from app import db

    rid = db.save_report(
        "c1", skill="voice-of-customer-report", title="VoC", html="# VoC\n\nbody",
    )
    monkeypatch.setattr(
        ace, "apply_edit",
        lambda *a, **k: _edit(document="# TOTALLY DIFFERENT", sections=()),
    )

    out = ace.edit_report_scoped(rid, "what does this say about pricing?", _Company())

    assert out["sections_changed"] == []
    assert db.get_report(rid, "c1")["html"] == "# VoC\n\nbody", (
        "the editor judged this was not an edit — nothing may be written"
    )


def test_another_companys_report_reads_as_missing(isolated_settings, monkeypatch):
    from app import db

    rid = db.save_report("c1", skill="voice-of-customer-report", title="V", html="# V")
    monkeypatch.setattr(ace, "apply_edit", lambda *a, **k: _edit())

    with pytest.raises(HTTPException) as exc:
        ace.edit_report_scoped(rid, "shorten it", _Company(company_id="c2"))
    assert exc.value.status_code == 404, "never 403 — that would confirm it exists"
    assert db.get_report(rid, "c1")["html"] == "# V"


def test_an_empty_report_is_not_editable(isolated_settings, monkeypatch):
    from app import db

    rid = db.save_report("c1", skill="voice-of-customer-report", title="V", html="")
    monkeypatch.setattr(ace, "apply_edit", lambda *a, **k: _edit())

    with pytest.raises(HTTPException) as exc:
        ace.edit_report_scoped(rid, "shorten it", _Company())
    assert exc.value.status_code == 409


def test_a_failed_edit_leaves_the_report_untouched(isolated_settings, monkeypatch):
    from app import db

    rid = db.save_report("c1", skill="voice-of-customer-report", title="V", html="# V")

    def _boom(*a, **k):
        raise RuntimeError("the edit returned an empty document")

    monkeypatch.setattr(ace, "apply_edit", _boom)
    with pytest.raises(HTTPException) as exc:
        ace.edit_report_scoped(rid, "shorten it", _Company())
    assert exc.value.status_code == 502
    assert db.get_report(rid, "c1")["html"] == "# V", (
        "a report someone waited minutes for must not be replaced by nothing"
    )


# ── documents ────────────────────────────────────────────────────────────────


def _seed_document(company_id="c1", body="<h1>Update</h1><p>body</p>"):
    from app.db.custom_artifacts import create_artifact

    return create_artifact(
        company_id, title="Leadership update", kind="update", body_html=body,
    )


def test_editing_a_document_writes_and_bumps_the_version(isolated_settings, monkeypatch):
    from app.db.custom_artifacts import get_artifact

    row = _seed_document()
    monkeypatch.setattr(
        ace, "apply_edit",
        lambda *a, **k: _edit(document="<h1>Update</h1><p>shorter</p>"),
    )

    out = ace.edit_document_scoped(row["id"], "make it shorter", _Company())

    assert "shorter" in out["artifact"]["body_html"]
    stored = get_artifact("c1", row["id"])
    assert "shorter" in stored["body_html"]
    assert int(stored["version"]) > int(row["version"])


def test_a_document_edit_is_sanitized_on_the_way_in(isolated_settings, monkeypatch):
    """An edit is model output like any other, and this body renders in the app."""
    from app.db.custom_artifacts import get_artifact

    row = _seed_document()
    monkeypatch.setattr(
        ace, "apply_edit",
        lambda *a, **k: _edit(document="<h1>Hi</h1><script>alert(1)</script>"),
    )

    ace.edit_document_scoped(row["id"], "add a heading", _Company())
    assert "<script>" not in (get_artifact("c1", row["id"])["body_html"] or "")


def test_a_question_about_a_document_writes_nothing(isolated_settings, monkeypatch):
    from app.db.custom_artifacts import get_artifact

    row = _seed_document()
    monkeypatch.setattr(
        ace, "apply_edit", lambda *a, **k: _edit(document="<p>other</p>", sections=()),
    )

    out = ace.edit_document_scoped(row["id"], "what does this say?", _Company())

    assert out["sections_changed"] == []
    stored = get_artifact("c1", row["id"])
    assert stored["body_html"] == row["body_html"]
    assert int(stored["version"]) == int(row["version"]), (
        "a no-op save would move the version a colleague's editor is holding"
    )


def test_another_companys_document_reads_as_missing(isolated_settings, monkeypatch):
    row = _seed_document()
    monkeypatch.setattr(ace, "apply_edit", lambda *a, **k: _edit())

    with pytest.raises(HTTPException) as exc:
        ace.edit_document_scoped(row["id"], "shorten it", _Company(company_id="c2"))
    assert exc.value.status_code == 404
