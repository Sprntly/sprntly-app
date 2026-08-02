"""A follow-up to a generated REPORT must not be hijacked by a source lookup.

Reported live against a Voice of Customer report: the user asked "Explain more
on your recommendations point 1" and was answered "I can read your tickets live
… but no tracker is connected yet. Connect Jira or ClickUp."

Two things conspired. A report answer is persisted verbatim as its assistant
conversation turn, so the next ask replays the whole document as history; and
the document quotes its sources' record ids ("TKT-48766" from Zendesk,
"CALL-9875" from Talkdesk), which are shaped exactly like Jira issue keys. The
tracker router read those as evidence that the thread was an active tracker
thread, and "more" is in its follow-up vocabulary — so the message routed to a
live tracker read. The connector router had the identical flaw one branch down,
reading the report's own Sources line (and the request that produced it, "what
are customers saying on slack?") as the source the thread was reading.

Pure routing tests — no LLM, no DB.
"""
from __future__ import annotations

from app.skill_router import is_connector_lookup, is_jira_lookup

# A trimmed VoC report, keeping only what matters here: it is an HTML document,
# it quotes issue-key-shaped record ids, and it names its sources.
REPORT_HTML = """<!doctype html>
<html><head><style>body{font-family:serif}</style></head><body>
<h1>Voice of Customer Report</h1>
<p>21 July 2026 - 26 July 2026 - 6 days. User asked "what are customers saying
on slack?" - no Slack data was present in the upload.</p>
<p>TKT-48766 is a deduped duplicate of TKT-48821 (same requester, same ticket
merged by Zendesk) - counted once. CALL-9875 (partial, caller abandoned) and
CALL-9854 captured but flagged. CALL-9898 caller unresolved.</p>
<p><b>Sources:</b> Support tickets (Zendesk, 16 records); Call-center logs
(Talkdesk, 11 records); Sales/CS field notes (Salesforce CRM, 7 records).</p>
<h2>Recommendations</h2>
<ol><li>Ship the mobile latency fix before the Northwind renewal.</li></ol>
</body></html>"""

# The thread as `routes/ask.py:_load_history` returns it for the next ask.
REPORT_THREAD = [
    {"role": "user", "content": "/voice-of-customer-report what are customers saying on slack?"},
    {"role": "assistant", "content": REPORT_HTML},
]


def test_report_followup_does_not_route_to_the_tracker():
    """The exact reported message, plus every other natural way to ask for more
    of a report. Each one matches the tracker's follow-up vocabulary
    ("more"/"details"/"summarise"/"expand"/"who"), so before the fix the report's
    own TKT-/CALL- ids were all it took to send them to a live tracker read."""
    for q in [
        "Explain more on your recommendations point 1",
        "can you expand on the first recommendation?",
        "give me more details on that",
        "summarise the key findings for me",
        "who said the mobile app was unusable?",
        "what's the status of the Northwind account in this report?",
    ]:
        assert not is_jira_lookup(q, REPORT_THREAD), q


def test_report_followup_does_not_route_to_a_connector_lookup():
    """Same messages, the branch directly below. The report lists Zendesk /
    Talkdesk / Salesforce as provenance and the originating request named Slack
    — none of which means the user is asking us to go read those sources."""
    for q in [
        "Explain more on your recommendations point 1",
        "can you expand on the first recommendation?",
        "give me more details on that",
        "who said the mobile app was unusable?",
    ]:
        assert is_connector_lookup(q, REPORT_THREAD) is None, q


def test_report_document_is_not_tracker_evidence_by_itself():
    """The document-shaped exclusion, isolated from the "latest answer is a
    report" stand-down: a report two turns back must not make a later,
    genuinely ambiguous follow-up a tracker read either."""
    thread = [
        {"role": "user", "content": "run a voice of customer report"},
        {"role": "assistant", "content": REPORT_HTML},
        {"role": "user", "content": "thanks"},
        {"role": "assistant", "content": "Happy to help."},
    ]
    assert not is_jira_lookup("give me more details", thread)
    assert is_connector_lookup("give me more details", thread) is None


def test_a_real_tracker_followup_still_routes_after_a_report():
    """The fix must not cost a genuine tracker read. A message that names the
    tracker on its OWN words is decided statelessly, before any of this."""
    for q in [
        "get me my open tickets",
        "what's the status of KAN-1038?",
        "show me the bugs in jira",
    ]:
        assert is_jira_lookup(q, REPORT_THREAD), q


def test_a_real_connector_read_still_routes_after_a_report():
    """Naming the source in the message itself is likewise decided before the
    sticky branch — asking to go read Slack after a report still does."""
    assert is_connector_lookup("check slack for what was said about pricing", REPORT_THREAD) == {"slack"}


def test_a_genuine_tracker_thread_stays_sticky():
    """The stand-down keys on the LATEST answer only. A tracker thread that
    started with a report earlier still carries its own anaphoric follow-ups."""
    thread = [
        {"role": "user", "content": "run a voice of customer report"},
        {"role": "assistant", "content": REPORT_HTML},
        {"role": "user", "content": "now get me the open tickets"},
        {"role": "assistant", "content": "KAN-1033 is In Progress. Want the full details?"},
    ]
    assert is_jira_lookup("give me more details on that one", thread)


def test_a_genuine_connector_thread_stays_sticky():
    """Mirror of the above on the connector side."""
    thread = [
        {"role": "user", "content": "run a voice of customer report"},
        {"role": "assistant", "content": REPORT_HTML},
        {"role": "user", "content": "what did slack say about the pricing change?"},
        {"role": "assistant", "content": "In #pricing, three people raised the annual tier."},
    ]
    assert is_connector_lookup("who said that?", thread) == {"slack"}
