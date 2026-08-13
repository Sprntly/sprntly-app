"""Tests for the custom-artifact HTML sanitizer.

The stakes here are different from the evidence-brief stripper's: that document
is drawn in a sandboxed iframe with no `allow-scripts`, so the iframe is the
real defence and the stripper is depth. A custom artifact is rendered INLINE in
a contenteditable editor, so this module IS the defence — which is why these
tests are written as attacks that must fail, not as "the happy path survives".

Each vector below is a distinct way to get script to run or a fetch to leave
the page, not three spellings of `<script>`.
"""
from __future__ import annotations

import re

import pytest

from app.custom_artifact_html import html_to_text, sanitize_artifact_html


# ─── What the toolbar produces must survive unchanged ────────────────────────

@pytest.mark.parametrize(
    "html",
    [
        "<p>plain</p>",
        "<p><strong>bold</strong> and <em>italic</em> and <u>under</u></p>",
        "<h1>Title</h1><h2>Sub</h2><h3>Deeper</h3>",
        "<ul><li>one</li><li>two</li></ul>",
        "<ol start=\"3\"><li>three</li></ol>",
        "<blockquote><p>quoted</p></blockquote>",
        "<pre><code>x = 1</code></pre>",
        "<table><thead><tr><th>h</th></tr></thead><tbody><tr><td>c</td></tr></tbody></table>",
        "<p><s>struck</s> <sub>sub</sub> <sup>sup</sup></p>",
    ],
)
def test_toolbar_output_round_trips(html):
    """Everything the editor can emit comes back byte-identical.

    This is the property that makes the sanitizer invisible in normal use: a
    user's document must never change shape because it was saved.
    """
    assert sanitize_artifact_html(html) == html


def test_font_and_colour_spans_survive():
    """The 'change the font' requirement, at the sanitizer layer."""
    out = sanitize_artifact_html(
        '<p><span style="font-family: Georgia; font-size: 18px; color: #b00">x</span></p>'
    )
    assert "font-family: Georgia" in out
    assert "font-size: 18px" in out
    assert "color: #b00" in out


def test_safe_links_survive_and_get_rel_when_targeting_blank():
    out = sanitize_artifact_html('<p><a href="https://x.test" target="_blank">go</a></p>')
    assert 'href="https://x.test"' in out
    # A new-tab link must not hand the opener a handle back into the app.
    assert 'rel="noopener noreferrer"' in out


# ─── Attacks ─────────────────────────────────────────────────────────────────

def test_script_tag_is_dropped_with_its_body():
    out = sanitize_artifact_html("<p>before</p><script>alert(1)</script><p>after</p>")
    assert "alert" not in out and "<script" not in out
    # The surrounding document is untouched — a sanitizer that ate the page
    # would be "safe" and useless.
    assert "before" in out and "after" in out


def test_event_handler_attributes_are_dropped():
    """The classic bypass: an allowed tag carrying an executable attribute."""
    out = sanitize_artifact_html('<p onclick="steal()">text</p>')
    assert "onclick" not in out and "steal" not in out
    assert "text" in out


def test_javascript_url_loses_the_link_not_the_words():
    out = sanitize_artifact_html('<p><a href="javascript:alert(1)">click me</a></p>')
    assert "javascript:" not in out
    assert "click me" in out


def test_data_url_link_is_refused():
    """`data:text/html` is a same-origin document under the user's session."""
    out = sanitize_artifact_html('<p><a href="data:text/html,<script>alert(1)</script>">x</a></p>')
    assert "data:text/html" not in out


@pytest.mark.parametrize(
    "html",
    [
        '<iframe src="https://evil.test"></iframe>',
        '<object data="evil.swf"></object>',
        '<embed src="evil.swf">',
        '<svg><use href="https://evil.test#x"/></svg>',
        "<style>body{display:none}</style>",
        '<form action="https://evil.test"><input name="a"></form>',
        "<template><p>x</p></template>",
        '<math><mtext></mtext></math>',
        '<link rel="stylesheet" href="https://evil.test/x.css">',
        '<meta http-equiv="refresh" content="0;url=https://evil.test">',
    ],
)
def test_executing_and_fetching_tags_are_dropped_entirely(html):
    """Not just `<script>`.

    Each of these either runs code, restyles the whole app out from under the
    user, or reaches the network — and a blocklist that names only `<script>`
    lets every one of them through. The assertion is on the TAG NAME being gone
    rather than on a rendered result, so it holds whatever the parser does with
    malformed input.
    """
    out = sanitize_artifact_html(html)
    tag = html[1:].split(">")[0].split(" ")[0]
    assert f"<{tag}" not in out.lower()


def test_style_attribute_keeps_only_allowlisted_properties():
    out = sanitize_artifact_html(
        '<p style="color: red; position: fixed; z-index: 9999; top: 0">x</p>'
    )
    assert "color: red" in out
    # Positioning is how you float text over the app's own chrome.
    assert "position" not in out and "z-index" not in out


def test_style_values_cannot_fetch():
    out = sanitize_artifact_html(
        '<p style="background-color: url(https://evil.test/pixel.png)">x</p>'
    )
    assert "evil.test" not in out


def test_unknown_wrappers_are_unwrapped_not_deleted():
    """A paste from Google Docs arrives wrapped in tags we do not model.

    Dropping their content would silently eat the paste — the failure a user
    would report as "it deleted my document".
    """
    out = sanitize_artifact_html(
        '<section><div class="c"><font face="Arial"><p>kept</p></font></div></section>'
    )
    assert "kept" in out
    assert "<section" not in out and "<font" not in out


def test_html_comments_are_removed():
    out = sanitize_artifact_html("<p>a</p><!--[if IE]><script>x()</script><![endif]-->")
    assert "<!--" not in out and "x()" not in out


@pytest.mark.parametrize("value", ["", None, "   "])
def test_empty_input_is_total(value):
    assert sanitize_artifact_html(value) == ""


def test_malformed_html_does_not_raise():
    """Never raises: callers store the result without a try/except."""
    assert isinstance(sanitize_artifact_html("<p><b>unclosed <i>x</p></div>"), str)


def test_sanitizer_is_idempotent():
    """Re-saving a stored document must not keep changing it."""
    once = sanitize_artifact_html('<p style="color: red">a</p><script>x</script>')
    assert sanitize_artifact_html(once) == once


# ─── Text extraction ─────────────────────────────────────────────────────────

def test_html_to_text_separates_blocks():
    """A heading and the paragraph under it must not run together."""
    text = html_to_text("<h1>Q3 Update</h1><p>Revenue grew</p>")
    assert "Q3 UpdateRevenue" not in text
    assert "Q3 Update" in text and "Revenue grew" in text


def test_html_to_text_drops_non_content():
    assert "alert" not in html_to_text("<p>a</p><script>alert(1)</script>")


# ─── Regressions from review of #1153 ───────────────────────────────────────
#
# The vectors above are all TAG-shaped, and that shared shape is what hid the
# bypass below: every one of them exercised the tag loop, and none reached the
# node types bs4 stores as PreformattedString and re-emits raw.


@pytest.mark.parametrize(
    "payload",
    [
        # CDATA. `html.parser` ends the section at `]]>`, but HTML5 has no
        # CDATA in HTML content — a browser enters the BOGUS COMMENT state and
        # ends it at the first `>`, so everything after that `>` is live markup.
        "<p>hi</p><![CDATA[x><img src=x onerror=alert(1)>]]>",
        "<![CDATA[x><script>alert(1)</script>]]>",
        # Processing instruction — same bogus-comment treatment in a browser.
        '<?php echo "<img src=x onerror=alert(1)>" ?>',
        "<?xml version='1.0'?><p>a</p>",
    ],
)
def test_preformatted_nodes_cannot_smuggle_markup(payload):
    """THE BYPASS: these are not tags, so the tag loop never saw them, and
    `str(soup)` re-emits them RAW AND UNESCAPED.

    Asserting on the raw markers rather than on a rendered result, because the
    whole point is that our parser and the browser disagree about where these
    nodes end — so the only safe outcome is that they are not in the output at
    all.
    """
    out = sanitize_artifact_html(payload)
    assert "<![CDATA[" not in out
    assert "<?" not in out
    assert "onerror" not in out
    assert "<script" not in out
    assert "alert(1)" not in out


def test_doctype_and_declarations_are_dropped():
    out = sanitize_artifact_html("<!DOCTYPE html><p>a</p>")
    assert "DOCTYPE" not in out
    assert "a" in out


def test_a_named_target_still_gets_noopener():
    """Browsers imply `noopener` for `_blank` and NOTHING else, so a named
    target opens a page holding a live `window.opener` that can navigate the
    Sprntly tab to a phishing page."""
    out = sanitize_artifact_html('<p><a href="https://evil.test" target="x">go</a></p>')
    assert 'rel="noopener noreferrer"' in out
    # A named window has no use in a document; it is only a way to keep a handle.
    assert 'target="x"' not in out


# ─── The other half of the editor contract ──────────────────────────────────
#
# `web/app/(app)/artifacts/doc/editorSchema.ts` documents this pairing from the
# TypeScript side; this is the Python end. The payloads below are REAL output
# captured from the TipTap editor (via its own serializer, in jsdom), not
# hand-written HTML — which matters, because the failure being guarded against
# is the editor producing something the sanitizer silently strips, and
# hand-written samples would only ever encode what I already believed.


@pytest.mark.parametrize(
    "editor_output",
    [
        # Marks the toolbar's four buttons produce.
        "<p><strong>bold</strong> <em>italic</em> <u>under</u> <s>struck</s></p>",
        # Headings, capped at 4 by the editor for exactly this reason.
        "<h1>One</h1><h2>Two</h2><h3>Three</h3><h4>Four</h4>",
        # Lists, quote, code block.
        "<ul><li><p>a</p></li></ul>",
        "<ol><li><p>a</p></li></ol>",
        "<blockquote><p>quoted</p></blockquote>",
        "<pre><code>x = 1</code></pre>",
        # A font choice, serialized the way the DOM normalizes it.
        '<p><span style="font-family: Georgia, \'Times New Roman\', serif">g</span></p>',
        '<p><span style="font-size: 19px">big</span></p>',
        # Colour: the editor stores what the DOM gives back, which is rgb()
        # rather than the hex the picker offered. Both must survive, because
        # which one is stored is the browser's choice, not ours.
        '<p><span style="color: rgb(180, 35, 24)">red</span></p>',
        '<p><span style="background-color: rgb(254, 243, 199)">hl</span></p>',
        '<p><span style="color: #B42318">red</span></p>',
        # A link, with the rel/target the editor attaches.
        '<p><a target="_blank" rel="noopener noreferrer" href="https://sprntly.ai">s</a></p>',
    ],
)
def test_editor_output_survives_the_sanitizer(editor_output):
    """Whatever the toolbar can make, the storage layer must keep.

    Not "does not crash" — the assertion is that the FORMATTING SURVIVES. An
    unknown tag here is unwrapped and an unknown style property dropped, both
    silently, so a mismatch shows up as the user's bold or font quietly
    disappearing the moment they save. That is the exact failure this pairing
    exists to prevent, and it is invisible to every test on either side alone.
    """
    out = sanitize_artifact_html(editor_output)

    # Every tag the editor emitted is still there.
    for tag in re.findall(r"<(\w+)", editor_output):
        assert f"<{tag}" in out, f"{tag} was stripped from {editor_output!r}"

    # Every style PROPERTY the editor emitted is still there (the value's
    # notation may be normalized; the declaration must not vanish).
    for prop in re.findall(r"([\w-]+)\s*:", re.sub(r"<[^>]*?href[^>]*?>", "", editor_output)):
        if prop in {"font-family", "font-size", "color", "background-color"}:
            assert prop in out, f"{prop} was dropped from {editor_output!r}"


def test_the_editors_colour_notation_is_not_mistaken_for_a_fetch():
    """`rgb(...)` contains a parenthesis, and the value guard rejects `url(`.

    A guard written as "reject any function call" would take the editor's own
    colour output with it — every coloured word in every document silently
    losing its colour on save.
    """
    out = sanitize_artifact_html('<p><span style="color: rgb(14, 110, 73)">g</span></p>')
    assert "rgb(14, 110, 73)" in out
