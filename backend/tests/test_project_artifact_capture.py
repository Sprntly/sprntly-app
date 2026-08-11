"""`project_artifact_capture.py` — the item-14 substrate's capture helper:
persist a user-picked chat output as a `reports` row, on demand.

Sibling of `test_report_capture.py`, but a DIFFERENT failure contract: this
capture is user-initiated (not best-effort), so nothing here is swallowed —
`save_chat_output_as_report` lets a DB error propagate and returns whatever
`save_report` returns (including `None`) rather than catching and hiding it.
"""
from __future__ import annotations

from app import project_artifact_capture as pac


def _patch_save(monkeypatch, result=42):
    """Capture the save_report kwargs instead of hitting the DB (same
    pattern as test_report_capture.py's `_patch_save`)."""
    import app.db as db

    seen: dict = {}

    def fake_save(company_id, **kw):
        seen.update({"company_id": company_id, **kw})
        return result

    monkeypatch.setattr(db, "save_report", fake_save)
    return seen


# ─── save_chat_output_as_report ───────────────────────────────────────────


def test_save_chat_output_persists_report(monkeypatch):
    seen = _patch_save(monkeypatch, result=42)

    report_id = pac.save_chat_output_as_report(
        content="## Prioritization\n\n- Ship A first\n- Then B",
        company_id="c1",
        workspace_id="w1",
        conversation_id=9,
    )

    assert report_id == 42
    assert seen["company_id"] == "c1"
    assert seen["workspace_id"] == "w1"
    assert seen["conversation_id"] == 9
    assert seen["skill"] == "saved-chat"
    assert seen.get("ask_id") is None
    assert seen["html"].startswith("<!doctype html")
    # Escaped, not a live tag.
    assert "<script>" not in seen["html"]
    assert "Prioritization" in seen["html"]


def test_save_chat_output_returns_none_when_save_yields_no_row(monkeypatch):
    _patch_save(monkeypatch, result=None)
    assert pac.save_chat_output_as_report(content="body", company_id="c1") is None


def test_save_chat_output_propagates_a_raised_db_error(monkeypatch):
    import app.db as db

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "save_report", boom)

    try:
        pac.save_chat_output_as_report(content="body", company_id="c1")
        raise AssertionError("expected the DB error to propagate")
    except RuntimeError as exc:
        assert str(exc) == "db down"


# ─── _derive_title ─────────────────────────────────────────────────────────


def test_derive_title_explicit_then_firstline_then_fallback():
    assert pac._derive_title("whatever", "  My explicit title  ") == "My explicit title"
    assert pac._derive_title("# Heading one\nbody", None) == "Heading one"
    assert pac._derive_title("\n\n   \n# \nSecond real line", None) == "Second real line"
    assert pac._derive_title("", None) == "Saved from chat"
    assert pac._derive_title("   \n\n  ", None) == "Saved from chat"

    long_title = "x" * 250
    assert pac._derive_title("ignored", long_title) == long_title[:200]
    assert len(pac._derive_title("ignored", long_title)) == 200

    long_first_line = "#" + ("y" * 250)
    assert len(pac._derive_title(long_first_line, None)) == 200


# ─── _wrap_as_report_html ──────────────────────────────────────────────────


def test_wrapped_html_is_self_contained_and_escaped():
    from app.report_capture import looks_like_html_report

    html_doc = pac._wrap_as_report_html("A title", "before <script>x</script> after")

    assert html_doc.startswith("<!doctype html")
    assert looks_like_html_report(html_doc) is True
    assert "<script>x</script>" not in html_doc
    assert "&lt;script&gt;x&lt;/script&gt;" in html_doc
    assert "A title" in html_doc
