"""A NUL in an attached document must never 500 a send.

THE INCIDENT. A chat message with a document attached inlines the extracted text
into the question. One of those documents carried `U+0000`, `db.asks
.start_ask_job` inserted it, and Postgres refused the write:

    APIError 22P05: 'unsupported Unicode escape sequence'
                    '\\u0000 cannot be converted to text'

That is a 500 on `POST /v1/ask` — not a dropped attachment, the whole message.
`routes/conversations.py::add_turn` died on the same byte.

WHY IT SURVIVED EVERY EXISTING CHECK. `U+0000` is VALID UTF-8, so a UTF-16LE
file saved without a BOM decodes without error and every character-level
validation passes. Postgres `text` and `json` simply cannot store it. And the
suite's SQLite fake stores NULs happily, so no test downstream of the boundary
can reproduce the failure — which is exactly why these tests assert at the
BOUNDARY (the extractor and the request models) rather than against a DB.

The rule this file pins: strip, never refuse. The character is meaningless in a
document, and rejecting the message would turn a stray byte into the very
user-visible failure being fixed. `artifact_templates.store` takes the opposite
line for an uploaded FORMAT — a file the user picked wrong and can re-save — and
that asymmetry is deliberate.
"""
from __future__ import annotations

from app.ingest import strip_nul
from app.routes.ask import AskIn
from app.routes.conversations import TurnAttachment, TurnIn

NASTY = "Quarterly plan\x00 with an embedded NUL\x00"
CLEAN = "Quarterly plan with an embedded NUL"


def test_strip_nul_removes_every_occurrence_and_nothing_else():
    assert strip_nul(NASTY) == CLEAN
    # Not a general sanitiser: newlines, tabs and unicode are untouched, because
    # extracted markdown is meant to keep its shape.
    keep = "# Heading\n\n- bullet\twith tab — em dash ✓"
    assert strip_nul(keep) == keep


def test_strip_nul_tolerates_empty_and_falsy_input():
    assert strip_nul("") == ""


def test_a_question_carrying_a_nul_is_cleaned_not_rejected():
    """The whole point: the send still goes through."""
    body = AskIn(question=NASTY, dataset="acme")

    assert "\x00" not in body.question
    assert body.question == CLEAN


def test_a_turns_content_is_cleaned():
    body = TurnIn(role="user", content=NASTY)

    assert "\x00" not in body.content


def test_an_attachments_extracted_text_is_cleaned():
    """The likeliest entry point of all — this field IS the extracted document
    text, and it lands in a JSON column that refuses U+0000 just as `text` does."""
    att = TurnAttachment(name="plan.docx", content=NASTY)

    assert "\x00" not in att.content


def test_the_question_length_floor_still_applies_after_stripping():
    """Stripping happens inside validation, so a question that is only NULs is
    not smuggled past `min_length` as an empty string."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AskIn(question="\x00\x00", dataset="acme")
