"""An attached file stays readable for the whole conversation.

THE BUG. An attachment's text is inlined into the question as an
`[Attached files]` block on the turn it is sent, and that turn answers
perfectly. On the NEXT question the same message comes back as conversation
history, where `prompt_history.clamp_turn_text` caps every turn at
MAX_TURN_CHARS (4,000) — so a 17-page report keeps its first page and
everything after it is gone.

Observed exactly: a zip holding a security report and an invoice answered
"2 files" with the invoice's amount and parties on turn one, then on turn two
said "the second file's contents were not loaded and cannot be read from what
was passed in this conversation". Nothing had failed to load; history had been
clamped at the boundary between the two documents.

THE CLAMP IS NOT THE THING TO CHANGE. Its own docstring records the incident it
exists for — an HTML report carrying base64 charts replayed into history 400'd
every later turn in the thread, non-retryably.

So attachments stop depending on history replay. Their text is ALREADY
persisted per turn (`conversation_turns.attachments[].content`, capped at 60k
by `TurnAttachment`); it was simply never read back. This grounds every ask in
the thread on it directly, the way documents and reports already work.
"""
from __future__ import annotations

from app.thread_context import (
    ATTACHMENTS_TOTAL_CHARS,
    THREAD_ATTACHMENTS_HEADER,
    build_thread_attachment_context,
)


def _turn(client, conversation_id: int, attachments: list[dict], content: str = "q"):
    client.table("conversation_turns").insert({
        "conversation_id": conversation_id,
        "role": "user",
        "content": content,
        "attachments": attachments,
    }).execute()


def test_a_files_text_is_returned_for_later_turns(tenant_client):
    """The whole point: turn two can still read what turn one attached."""
    t = tenant_client.make(slug="acme", user_id="user-a")
    from app.db.client import require_client

    c = require_client()
    conv = c.table("conversations").insert({
        "company_id": t.company_id, "user_id": "user-a", "title": "t",
    }).execute().data[0]

    _turn(c, conv["id"], [
        {"name": "report.pdf", "content": "Finding 3: the login form leaks."},
        {"name": "Invoice 3.pdf", "content": "Total: $1,620.00, due 5 September."},
    ])

    block = build_thread_attachment_context(conv["id"])

    assert THREAD_ATTACHMENTS_HEADER in block
    # BOTH files — the one history would have kept and the one it cut.
    assert "Finding 3: the login form leaks." in block
    assert "Total: $1,620.00" in block
    # Each under its own name, so the model can attribute a passage to a file.
    assert "### report.pdf" in block
    assert "### Invoice 3.pdf" in block


def test_files_from_several_turns_all_survive(tenant_client):
    """An attachment sent at turn one and another at turn five are both part of
    what the thread is about."""
    t = tenant_client.make(slug="acme", user_id="user-a")
    from app.db.client import require_client

    c = require_client()
    conv = c.table("conversations").insert({
        "company_id": t.company_id, "user_id": "user-a", "title": "t",
    }).execute().data[0]

    _turn(c, conv["id"], [{"name": "first.md", "content": "The earliest fact."}])
    _turn(c, conv["id"], [{"name": "second.md", "content": "A later fact."}])

    block = build_thread_attachment_context(conv["id"])

    assert "The earliest fact." in block
    assert "A later fact." in block


def test_a_name_only_attachment_contributes_nothing(tenant_client):
    """The "generate a PRD from this file" command persists a name-only chip —
    the file BECAME the PRD and never had in-chat text. There is nothing to
    ground on, and a heading with no body is noise in a prompt."""
    t = tenant_client.make(slug="acme", user_id="user-a")
    from app.db.client import require_client

    c = require_client()
    conv = c.table("conversations").insert({
        "company_id": t.company_id, "user_id": "user-a", "title": "t",
    }).execute().data[0]

    _turn(c, conv["id"], [{"name": "spec.docx", "content": ""}])

    assert build_thread_attachment_context(conv["id"]) == ""


def test_the_same_file_attached_twice_appears_once(tenant_client):
    """One document to the reader; two copies is only a bigger prompt."""
    t = tenant_client.make(slug="acme", user_id="user-a")
    from app.db.client import require_client

    c = require_client()
    conv = c.table("conversations").insert({
        "company_id": t.company_id, "user_id": "user-a", "title": "t",
    }).execute().data[0]

    _turn(c, conv["id"], [{"name": "brief.md", "content": "Ship on Friday."}])
    _turn(c, conv["id"], [{"name": "brief.md", "content": "Ship on Friday."}])

    block = build_thread_attachment_context(conv["id"])

    assert block.count("### brief.md") == 1


def test_the_total_is_bounded(tenant_client):
    """A sixteen-file send is supported; sixteen unbounded documents is not a
    prompt. The cap is generous because this is material the person
    deliberately handed over."""
    t = tenant_client.make(slug="acme", user_id="user-a")
    from app.db.client import require_client

    c = require_client()
    conv = c.table("conversations").insert({
        "company_id": t.company_id, "user_id": "user-a", "title": "t",
    }).execute().data[0]

    _turn(c, conv["id"], [
        {"name": f"f{i}.md", "content": "x" * 19_000} for i in range(10)
    ])

    block = build_thread_attachment_context(conv["id"])

    assert len(block) <= ATTACHMENTS_TOTAL_CHARS + len(THREAD_ATTACHMENTS_HEADER) + 200


def test_no_conversation_grounds_nothing(tenant_client):
    """A Slack ask or a warm has no thread. Not an error — just nothing to add."""
    assert build_thread_attachment_context(None) == ""
    assert build_thread_attachment_context(0) == ""


def test_an_unreadable_thread_does_not_break_the_answer(tenant_client, monkeypatch):
    """GROUNDING MUST NEVER BREAK AN ANSWER. A failed read costs the reader the
    attachment block, not their question."""
    from app.db import client as db_client

    def boom():
        raise RuntimeError("supabase down")

    monkeypatch.setattr(db_client, "require_client", boom)

    assert build_thread_attachment_context(1234) == ""
