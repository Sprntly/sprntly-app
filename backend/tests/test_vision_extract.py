"""A file with no text layer is read by looking at it.

Reported as five files attached to one chat message: two were screen-capture
PDFs, `pypdf` found no text in either — correctly, their pages are images — and
the route answered "could not extract any text from the file". Accurate about
the bytes, useless to someone who attached a screenshot because the picture IS
the content.

What these pin is mostly the SHAPE OF THE FALLBACK rather than the reading
itself, because the reading is a model call:

  * it fires only where extraction produced nothing — a fallback that also ran
    on ordinary files would be an invisible per-upload bill;
  * it never raises, whatever the API does, because it sits behind a failure
    and turning a 422 into a 500 is strictly worse than not trying;
  * an archive gets ONE budget, so a 500-image zip cannot become 500 calls.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

from app import vision_extract
from app.vision_extract import (
    MAX_VISION_BYTES,
    Extracted,
    extract_text,
    read_with_vision,
    vision_block,
)


# ── which files are even candidates ─────────────────────────────────────────

@pytest.mark.parametrize(
    "filename,expected_type,expected_media",
    [
        ("scan.pdf", "document", "application/pdf"),
        ("SHOUTING.PDF", "document", "application/pdf"),
        ("shot.png", "image", "image/png"),
        ("photo.jpg", "image", "image/jpeg"),
        ("photo.jpeg", "image", "image/jpeg"),
        ("anim.gif", "image", "image/gif"),
        ("modern.webp", "image", "image/webp"),
    ],
)
def test_a_picture_becomes_the_block_its_type_needs(filename, expected_type, expected_media):
    """A PDF is a `document`; an image is an `image`. The API takes them as
    different block types even though both end up rendered as pictures."""
    block = vision_block(filename, b"bytes")
    assert block is not None
    assert block["type"] == expected_type
    assert block["source"] == {
        "type": "base64",
        "media_type": expected_media,
        "data": base64.standard_b64encode(b"bytes").decode("ascii"),
    }


@pytest.mark.parametrize("filename", ["deck.ppt", "audio.m4a", "sheet.xlsx", "noext", ""])
def test_a_format_the_model_cannot_look_at_is_not_a_candidate(filename):
    """The fallback reads pictures. It does not guess at every binary — an
    .xlsx has a real parser and a legacy .ppt has nothing to look at."""
    assert vision_block(filename, b"bytes") is None


def test_an_oversized_file_is_not_sent():
    """The API's ceilings are on the ENCODED request and base64 inflates by
    4/3, so the guard is on raw bytes with room left for the prompt. A file
    over it gets the honest refusal rather than an API rejection first."""
    assert vision_block("huge.pdf", b"x" * (MAX_VISION_BYTES + 1)) is None
    assert vision_block("fits.pdf", b"x" * MAX_VISION_BYTES) is not None


def test_an_empty_file_is_not_sent():
    assert vision_block("empty.pdf", b"") is None


# ── the read never becomes a 500 ────────────────────────────────────────────

def test_read_with_vision_swallows_anything_the_call_throws(monkeypatch):
    """It sits behind an extraction that already failed. Every way it can go
    wrong — no API key, a timeout, an encrypted PDF the API rejects — has the
    same correct outcome: the caller's original refusal stands."""
    def _boom(**kwargs):
        raise RuntimeError("overloaded_error")

    monkeypatch.setattr("app.graph.gateway.llm_call", _boom)
    assert read_with_vision(filename="scan.pdf", data=b"pdf", enterprise_id="c1") == ""


def test_read_with_vision_returns_the_transcription(monkeypatch):
    seen: dict = {}

    def _fake(**kwargs):
        seen.update(kwargs)
        return type("R", (), {"output": "  # Invoice\n\n$412.00  "})()

    monkeypatch.setattr("app.graph.gateway.llm_call", _fake)
    out = read_with_vision(filename="scan.pdf", data=b"pdf", enterprise_id="c1")

    assert out == "# Invoice\n\n$412.00"
    # Attributed to the company, so the call is metered against the tenant that
    # uploaded the file rather than landing unattributed.
    assert seen["enterprise_id"] == "c1"
    assert seen["input_blocks"][0]["type"] == "document"
    # Faithfulness over fluency: the output is evidence another model reads.
    assert seen["temperature"] == 0


def test_a_blank_transcription_is_not_content(monkeypatch):
    """The prompt tells the model to output nothing when there is nothing to
    read. Whitespace back must not become a document."""
    monkeypatch.setattr(
        "app.graph.gateway.llm_call",
        lambda **kw: type("R", (), {"output": "   \n  "})(),
    )
    assert read_with_vision(filename="blank.png", data=b"png", enterprise_id="c1") == ""


def test_a_transcription_cannot_become_the_largest_thing_in_the_prompt(monkeypatch):
    monkeypatch.setattr(
        "app.graph.gateway.llm_call",
        lambda **kw: type("R", (), {"output": "x" * 200_000})(),
    )
    out = read_with_vision(filename="long.pdf", data=b"pdf", enterprise_id="c1")
    assert len(out) == vision_extract.MAX_VISION_CHARS


# ── extract_text: text first, model second ──────────────────────────────────

def _run(**kwargs) -> Extracted:
    return asyncio.run(extract_text(enterprise_id="c1", **kwargs))


def test_text_that_extracts_never_reaches_the_model(monkeypatch):
    """The whole cost argument for this feature rests on this line."""
    called: list[str] = []
    monkeypatch.setattr(
        vision_extract, "read_with_vision",
        lambda **kw: called.append(kw["filename"]) or "",
    )

    result = _run(filename="notes.md", data=b"# Real heading\n\ntext")

    assert "Real heading" in result.text
    assert result.used_vision is False
    assert called == []


def test_the_unparsed_stub_is_a_failed_read_not_content(monkeypatch):
    """`convert` returns a NON-EMPTY placeholder for a type it cannot parse.
    A plain empty-check waves that through as a document whose body is the
    sentence "its content is not included in analysis yet"."""
    monkeypatch.setattr(
        vision_extract, "read_with_vision",
        lambda **kw: "# transcribed",
    )
    result = _run(filename="shot.png", data=b"\x89PNG\r\n\x1a\n\x00\x00noise")

    assert result == Extracted("# transcribed", True)


def test_a_corrupt_container_falls_through_instead_of_raising(monkeypatch):
    """`convert` RAISES on a truncated PDF rather than returning its stub. That
    is the same "we got nothing" to the person who uploaded it, so it takes the
    same fallback — not a 500."""
    monkeypatch.setattr(
        vision_extract, "read_with_vision",
        lambda **kw: "# read anyway",
    )
    result = _run(filename="truncated.pdf", data=b"%PDF-1.4 broken")

    assert result == Extracted("# read anyway", True)


def test_a_spent_budget_keeps_the_old_behaviour(monkeypatch):
    """`allow_vision=False` is a caller out of archive budget. It must not read
    the file — and must not pretend it did."""
    monkeypatch.setattr(
        vision_extract, "read_with_vision",
        lambda **kw: pytest.fail("vision ran with the budget spent"),
    )
    assert _run(
        filename="shot.png", data=b"\x89PNG\r\n\x1a\n\x00\x00noise", allow_vision=False,
    ) == Extracted("", False)


def test_a_non_candidate_is_not_charged(monkeypatch):
    """A legacy .ppt cannot be looked at, so no call is made and no budget is
    spent — an archive of them must not exhaust the allowance for the one
    scan at the end of it."""
    monkeypatch.setattr(
        vision_extract, "read_with_vision",
        lambda **kw: pytest.fail("vision ran on a format it cannot read"),
    )
    assert _run(filename="deck.ppt", data=b"\xd0\xcf\x11\xe0binary") == Extracted("", False)


def test_a_failed_read_still_counts_as_a_call(monkeypatch):
    """The budget bounds calls MADE, not calls that worked. Charging only
    successes would let an archive of unreadable scans make unlimited ones."""
    monkeypatch.setattr(vision_extract, "read_with_vision", lambda **kw: "")
    assert _run(
        filename="scan.pdf", data=b"\x89PNG\r\n\x1a\n\x00\x00noise",
    ) == Extracted("", True)


# ── the request shape the blocks ride in ────────────────────────────────────
#
# `_build_base_kwargs` is on the path of EVERY model call in the app, so what
# matters as much as the new branch is that the old one is untouched.

def _kwargs(**over):
    from app.llm import _build_base_kwargs

    base = dict(
        model="claude-haiku-4-5", max_tokens=100, system="sys", user="ask",
        user_cacheable_prefix=None,
    )
    base.update(over)
    return _build_base_kwargs(**base)


_BLOCK = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": "x"}}


def test_without_blocks_the_request_is_the_string_form_it_always_was():
    """Every existing caller passes no blocks. `content` must stay a plain
    string for them — a list would be a new request shape for the whole app."""
    assert _kwargs()["messages"] == [{"role": "user", "content": "ask"}]
    assert _kwargs(user_blocks=None)["messages"] == [{"role": "user", "content": "ask"}]
    assert _kwargs(user_blocks=[])["messages"] == [{"role": "user", "content": "ask"}]


def test_a_block_goes_before_the_text_that_asks_about_it():
    """Anthropic's own guidance: place the document ahead of the question."""
    content = _kwargs(user_blocks=[_BLOCK])["messages"][0]["content"]
    assert content == [_BLOCK, {"type": "text", "text": "ask"}]


def test_a_block_precedes_the_cacheable_prefix_too():
    """The prefixed branch already builds a list. The file still leads it, and
    the prefix keeps its cache_control — the media block gets none, because no
    caller sends the same file twice inside a TTL."""
    content = _kwargs(user_blocks=[_BLOCK], user_cacheable_prefix="METHOD")["messages"][0]["content"]

    assert content[0] == _BLOCK
    assert "cache_control" not in content[0]
    assert content[1]["text"] == "METHOD"
    assert content[1]["cache_control"]
    assert content[2] == {"type": "text", "text": "ask"}


def test_the_gateway_forwards_them():
    """`input_blocks` is the gateway's name for it; `user_blocks` is app.llm's.
    A rename on one side that never reached the other would silently drop the
    file and answer about nothing."""
    import inspect

    from app.graph.gateway import llm_call
    from app.llm import call_md

    assert "input_blocks" in inspect.signature(llm_call).parameters
    assert "user_blocks" in inspect.signature(call_md).parameters


# ── a provider outage is not a broken file ──────────────────────────────────
#
# Reported after the Anthropic account ran out of credit: four screen-capture
# PDFs came back as "we could not read anything in that file — it may be
# password-protected, empty, or a format we cannot open". Nothing was wrong
# with the files, and the obvious next move is to go and inspect them.

def _credit_error():
    import anthropic
    import httpx

    body = {"type": "error", "error": {
        "type": "invalid_request_error",
        "message": "Your credit balance is too low to access the Anthropic API.",
    }}
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, json=body, request=request)
    # The real SDK error stringifies the whole body, which is where the
    # "credit balance is too low" marker `llm_errors` keys on actually lives.
    return anthropic.BadRequestError(
        "Error code: 400 - " + repr(body), response=response, body=body,
    )


def test_an_unreachable_model_raises_instead_of_blaming_the_file(monkeypatch):
    from app.vision_extract import ProviderUnavailable

    monkeypatch.setattr(
        "app.graph.gateway.llm_call", lambda **kw: (_ for _ in ()).throw(_credit_error()),
    )

    with pytest.raises(ProviderUnavailable) as caught:
        read_with_vision(filename="scan.pdf", data=b"pdf", enterprise_id="c1")

    # The sentence names an admin action and says nothing was lost — it comes
    # from `llm_errors`, so every surface explains a provider limit the same way.
    assert "provider" in caught.value.message.lower()
    assert caught.value.code == "provider_limit"


def test_extract_text_propagates_it(monkeypatch):
    """Flattening it to "" here would put the misleading 422 straight back."""
    from app.vision_extract import ProviderUnavailable

    def _down(**kwargs):
        raise ProviderUnavailable("provider is out of credits", "provider_limit")

    monkeypatch.setattr(vision_extract, "read_with_vision", _down)

    with pytest.raises(ProviderUnavailable):
        _run(filename="scan.pdf", data=b"\x89PNG\r\n\x1a\n\x00\x00noise")


def test_an_ordinary_failure_still_just_returns_nothing(monkeypatch):
    """Only PROVIDER failures raise. A malformed response, a bug in the call —
    anything `llm_errors` does not recognize — keeps the old outcome, because a
    fallback behind a failed extraction must not turn a 422 into a 500."""
    monkeypatch.setattr(
        "app.graph.gateway.llm_call",
        lambda **kw: (_ for _ in ()).throw(ValueError("something local broke")),
    )
    assert read_with_vision(filename="scan.pdf", data=b"pdf", enterprise_id="c1") == ""
