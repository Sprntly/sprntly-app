"""Tests for app.report_context — the "REPORT IN THIS CHAT" grounding block.

Reported live: a Voice of Customer report was generated in chat, and the
follow-up "Explain more on your recommendations point 1" could not be answered.
Routing was half of it (tests/test_report_followup_routing.py); this is the
other half — even once the message reached the answer path, the report was not
in the prompt. The only route back to it was the conversation history, and
`clamp_turn_text` caps a folded turn at 4k characters, which for a report is
spent long before the recommendations at the bottom of the document.

build_report_context loads the captured report itself instead. Contract under
test: latest report only, visible text (not markup), tenant-scoped, and
best-effort — anything missing collapses to '' rather than raising.
"""
from __future__ import annotations

from app.report_context import _REPORT_CAP, build_report_context

# A report whose recommendations sit AFTER more than a history-clamp's worth of
# front matter — the shape that made the reported bug possible.
FILLER = "<p>Theme detail paragraph that fills out the front of the report.</p>" * 90

REPORT_HTML = (
    "<!DOCTYPE html><html><head><title>Voice of Customer Report</title>"
    "<style>body{font-family:Tiempos,serif}h1{font-size:32px}</style></head>"
    "<body><h1>Voice of Customer Report</h1>"
    "<p>21 July 2026 - 26 July 2026.</p>"
    f"{FILLER}"
    "<h2>Recommendations</h2>"
    "<ol><li>Ship the mobile latency fix before the Northwind renewal.</li>"
    "<li>Publish a status page for the call-centre outages.</li></ol>"
    "</body></html>"
)


def _seed_report(
    *,
    company_id: str,
    conversation_id: int,
    html: str = REPORT_HTML,
    title: str = "Voice of Customer Report",
    skill: str = "voice-of-customer-report",
    question: str = "what are customers saying on slack?",
) -> int:
    from app.db.client import require_client

    resp = require_client().table("reports").insert({
        "company_id": company_id,
        "skill": skill,
        "title": title,
        "html": html,
        "question": question,
        "conversation_id": conversation_id,
    }).execute()
    return resp.data[0]["id"]


def test_block_carries_the_whole_report_including_its_recommendations(
    isolated_settings, monkeypatch
):
    """The point of the module: the END of the document survives. The history
    fold cannot do this — 4k characters in, this report is still in its filler."""
    from tests._company_helpers import company_client

    ctx = company_client(monkeypatch)
    _seed_report(company_id=ctx.company_id, conversation_id=77)

    block = build_report_context(ctx.company_id, 77)

    assert "REPORT IN THIS CHAT" in block
    assert "Voice of Customer Report" in block
    assert "Ship the mobile latency fix before the Northwind renewal." in block
    assert "Publish a status page for the call-centre outages." in block
    # Provenance the model can name back to the user.
    assert "voice-of-customer-report" in block
    assert "what are customers saying on slack?" in block


def test_markup_and_stylesheets_are_stripped(isolated_settings, monkeypatch):
    """A report is mostly presentation. The block carries its findings, not its
    CSS — otherwise the cap is spent on layout."""
    from tests._company_helpers import company_client

    ctx = company_client(monkeypatch)
    _seed_report(company_id=ctx.company_id, conversation_id=78)

    block = build_report_context(ctx.company_id, 78)

    assert "font-family" not in block
    assert "<h1>" not in block
    assert "<style>" not in block


def test_latest_report_wins(isolated_settings, monkeypatch):
    """A thread can generate several. A follow-up means the one just produced."""
    from tests._company_helpers import company_client

    ctx = company_client(monkeypatch)
    _seed_report(
        company_id=ctx.company_id, conversation_id=79,
        html="<html><body><h1>Old</h1><p>Stale finding.</p></body></html>",
        title="Old report",
    )
    _seed_report(
        company_id=ctx.company_id, conversation_id=79,
        html="<html><body><h1>New</h1><p>Fresh finding.</p></body></html>",
        title="New report",
    )

    block = build_report_context(ctx.company_id, 79)

    assert "Fresh finding." in block
    assert "Stale finding." not in block


def test_another_tenants_conversation_reads_as_empty(isolated_settings, monkeypatch):
    """The builder feeds tenant data into an LLM prompt, so it carries its own
    company filter — a guessed conversation id must not surface someone else's
    report."""
    from tests._company_helpers import company_client

    ctx = company_client(monkeypatch)
    _seed_report(company_id=ctx.company_id, conversation_id=80)

    assert build_report_context("some-other-company", 80) == ""


def test_no_report_no_block(isolated_settings, monkeypatch):
    """Every ordinary ask takes this path — a thread that has generated nothing
    pays no prompt at all."""
    from tests._company_helpers import company_client

    ctx = company_client(monkeypatch)

    assert build_report_context(ctx.company_id, 81) == ""


def test_missing_ids_short_circuit():
    """No conversation (or no tenant) → '' without touching the DB. Guards the
    non-chat callers of the ask pipeline, which have no conversation at all."""
    assert build_report_context(None, 5) == ""
    assert build_report_context("acme", None) == ""


def test_a_read_failure_degrades_to_no_grounding(isolated_settings, monkeypatch):
    """Best-effort by contract: grounding must never be able to fail an answer."""
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.latest_report_for_conversation", boom)

    assert build_report_context("acme", 82) == ""


def test_an_enormous_report_is_capped(isolated_settings, monkeypatch):
    """A report carrying a huge embedded table must not crowd out the corpus and
    KG grounding the same answer still needs."""
    from tests._company_helpers import company_client

    ctx = company_client(monkeypatch)
    _seed_report(
        company_id=ctx.company_id, conversation_id=83,
        html="<html><body>" + ("<p>row of report content</p>" * 20_000) + "</body></html>",
    )

    block = build_report_context(ctx.company_id, 83)

    assert "[… report truncated]" in block
    assert len(block) < _REPORT_CAP + 1_000
