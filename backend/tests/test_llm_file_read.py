"""Model-reading for files no parser can handle (app/llm_file_read.py).

Images ride `image` content blocks; PDFs ride `document` blocks (the API reads
scanned pages natively, so no OCR engine or rasterizer is a dependency).
Everything fails open — a read that errors or comes back empty leaves the
existing placeholder in place, so the file is retried later rather than
silently marked done.
"""
from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from app import llm_file_read
from app.ingest import convert


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64


# ─────────────────────── attachment_for ───────────────────────


@pytest.mark.parametrize("name,media", [
    ("shot.png", "image/png"),
    ("photo.JPG", "image/jpeg"),
    ("photo.jpeg", "image/jpeg"),
    ("anim.gif", "image/gif"),
    ("pic.webp", "image/webp"),
])
def test_images_become_image_blocks(name, media):
    block = llm_file_read.attachment_for(name, PNG)
    assert block["type"] == "image"
    assert block["source"]["media_type"] == media
    assert base64.b64decode(block["source"]["data"]) == PNG


def test_pdf_becomes_a_document_block():
    """A scanned PDF is read natively by the API — no rasterizing here."""
    block = llm_file_read.attachment_for("scan.pdf", PDF)
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


@pytest.mark.parametrize("name", ["legacy.doc", "old.xls", "deck.ppt", "sheet.ods"])
def test_legacy_binaries_are_not_model_readable(name):
    """An LLM cannot read a binary .doc — handing it the bytes yields nothing.
    These need real parsers, so they stay on the placeholder path."""
    assert llm_file_read.attachment_for(name, b"\xd0\xcf\x11\xe0data") is None
    assert not llm_file_read.is_readable_by_model(name, b"\xd0\xcf\x11\xe0")


def test_empty_and_oversized_files_are_refused():
    assert llm_file_read.attachment_for("shot.png", b"") is None
    too_big = b"\x00" * (llm_file_read.MAX_READ_BYTES + 1)
    assert llm_file_read.attachment_for("shot.png", too_big) is None


# ─────────────────────── needs_model_read ───────────────────────


def test_stub_backed_image_needs_a_read():
    stub = convert("shot.png", PNG)  # a real converter stub
    assert llm_file_read.needs_model_read("shot.png", PNG, stub)


def test_scanned_pdf_with_no_text_layer_needs_a_read():
    """pypdf returns '' for an image-only PDF — the signature of a scan."""
    assert llm_file_read.needs_model_read("scan.pdf", PDF, "")
    assert llm_file_read.needs_model_read("scan.pdf", PDF, "   \n  ")


def test_pdf_with_a_real_text_layer_is_left_alone():
    text = "## Page 1\n\n" + ("The customer asked for SSO. " * 20)
    assert not llm_file_read.needs_model_read("report.pdf", PDF, text)


def test_readable_file_is_not_re_read():
    assert not llm_file_read.needs_model_read(
        "notes.txt", b"hello", "customers want SSO",
    )


def test_unreadable_format_never_needs_a_read():
    """No point queueing a model read for something the model can't read."""
    stub = convert("legacy.doc", b"\xd0\xcf\x11\xe0binary")
    assert not llm_file_read.needs_model_read("legacy.doc", b"\xd0\xcf\x11\xe0", stub)


# ─────────────────────── read_file ───────────────────────


class _Result:
    def __init__(self, output):
        self.output = output


def test_read_file_sends_the_attachment_through_the_gateway():
    """Reads go through the gateway, not a raw client call — that's what binds
    the customer's own key and records cost/telemetry."""
    with patch("app.graph.gateway.llm_call",
               return_value=_Result("# Invoice\n\nTotal: $42")) as mock_call:
        text = llm_file_read.read_file("co-1", "invoice.png", PNG)

    assert text == "# Invoice\n\nTotal: $42"
    kwargs = mock_call.call_args.kwargs
    assert kwargs["enterprise_id"] == "co-1"
    assert kwargs["attachments"][0]["type"] == "image"
    # The system prompt must forbid invention — a transcription that guesses
    # is worse than no transcription.
    assert "NEVER GUESS" in kwargs["system"]


def test_read_file_returns_none_for_the_empty_sentinel():
    """A blank scan must not be stored as if it were content."""
    with patch("app.graph.gateway.llm_call", return_value=_Result("NO_CONTENT")):
        assert llm_file_read.read_file("co-1", "blank.png", PNG) is None


def test_read_file_returns_none_on_empty_output():
    with patch("app.graph.gateway.llm_call", return_value=_Result("   ")):
        assert llm_file_read.read_file("co-1", "blank.png", PNG) is None


def test_read_file_fails_open_when_the_model_call_raises():
    """A model failure must never break ingest — the caller keeps the
    placeholder and the file is retried later."""
    with patch("app.graph.gateway.llm_call", side_effect=RuntimeError("overloaded")):
        assert llm_file_read.read_file("co-1", "shot.png", PNG) is None


def test_read_file_skips_formats_it_cannot_read_without_calling_the_model():
    with patch("app.graph.gateway.llm_call") as mock_call:
        assert llm_file_read.read_file("co-1", "legacy.doc", b"\xd0\xcf\x11\xe0") is None
    mock_call.assert_not_called()


# ───────────── gateway plumbing: attachments reach messages.create ─────────────


def test_attachments_lead_the_user_turn_when_there_is_no_cached_prefix():
    """The API expects media BEFORE the text that refers to it."""
    from app.llm import _build_base_kwargs

    block = llm_file_read.attachment_for("shot.png", PNG)
    kwargs = _build_base_kwargs(
        model="m", max_tokens=100, system="sys", user="transcribe it",
        user_cacheable_prefix=None, attachments=[block],
    )
    content = kwargs["messages"][0]["content"]
    assert content[0]["type"] == "image"
    assert content[-1] == {"type": "text", "text": "transcribe it"}


def test_attachments_sit_after_a_cacheable_prefix():
    """A per-file attachment must not lead the turn when a cacheable prefix
    exists — it would bust the prefix cache on every call."""
    from app.llm import _build_base_kwargs

    block = llm_file_read.attachment_for("scan.pdf", PDF)
    kwargs = _build_base_kwargs(
        model="m", max_tokens=100, system="sys", user="transcribe it",
        user_cacheable_prefix="STABLE METHOD", attachments=[block],
    )
    content = kwargs["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1]["type"] == "document"
    assert content[-1]["text"] == "transcribe it"


def test_no_attachments_keeps_the_plain_string_shape():
    """Every existing caller must be byte-identical."""
    from app.llm import _build_base_kwargs

    kwargs = _build_base_kwargs(
        model="m", max_tokens=100, system="sys", user="hello",
        user_cacheable_prefix=None,
    )
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
