"""Sanitizer for custom-artifact bodies — an allowlist, applied on every write.

A custom artifact holds rich text a HUMAN typed (or an LLM generated) and the
app renders it INLINE, in a contenteditable surface. That rules out the
defence every other stored-HTML surface here uses: evidence briefs and reports
are drawn in a sandboxed iframe with no `allow-scripts`, so their generators
only have to strip `<script>` (app/evidence_html.py) and the iframe catches the
rest. An editor cannot live in that iframe, so this document's safety has to
come from the CONTENT being clean rather than from the frame around it.

Hence an ALLOWLIST, not a blocklist. Everything not named here is removed:
every unknown tag, every unknown attribute, every `on*` handler, every
`javascript:` URL, every `style` property outside the short list the toolbar
can actually produce. A blocklist of "dangerous tags" is the thing that keeps
being wrong — `<script>` is the famous one, but `<iframe srcdoc>`,
`<object data>`, `<svg><use href>`, `<math>` and a bare `<style>` block all
execute or exfiltrate too, and the next HTML spec revision adds more. An
allowlist is wrong in the safe direction: a tag we forgot renders as its text.

WHAT SURVIVES is exactly what the editor's toolbar emits — headings, bold,
italic, underline, strike, lists, quotes, code, tables, links, and inline
font/colour spans. That correspondence is deliberate and is the reason a round
trip is lossless in practice: the editor cannot create a node this rejects.

STRUCTURE IS NOT UNWRAPPED, IT IS DROPPED WITH ITS CHILDREN for the small set
of tags whose CONTENT is executable or hostile (`script`, `style`, `iframe`,
`object`, `embed`, `template`, `noscript`, `svg`, `math`). Everything else that
is merely unknown (`<section>`, `<font>`, a stray `<div>`) is UNWRAPPED — its
text is kept and the tag disappears — because a user pasting from Google Docs
or Notion arrives with a pile of unknown wrappers around perfectly good prose,
and dropping their content would silently eat the paste.

The sanitizer is applied in `db.custom_artifacts`' callers rather than deep in
the db module for one reason: generation writes through `finish_artifact` and
the HTTP save writes through `update_artifact`, and both call this first, so
the rule is visible at each entry point instead of hidden under one.
"""
from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, NavigableString

# From `bs4.element`, not the package root: `PreformattedString` is not
# re-exported by `bs4/__init__.py` (checked against the pinned beautifulsoup4),
# so the top-level import raises ImportError at startup.
# Its subclasses are exactly the node types this module must drop:
# CData, ProcessingInstruction, Comment, Declaration, Doctype.
from bs4.element import PreformattedString

logger = logging.getLogger(__name__)

# Tags kept, with their attributes. An empty set means "tag yes, attributes no".
_ALLOWED: dict[str, set[str]] = {
    "p": {"style"},
    "br": set(),
    "hr": set(),
    "h1": {"style"}, "h2": {"style"}, "h3": {"style"}, "h4": {"style"},
    "strong": set(), "b": set(),
    "em": set(), "i": set(),
    "u": set(), "s": set(), "strike": set(),
    "sub": set(), "sup": set(),
    "blockquote": set(),
    "ul": set(), "ol": {"start"}, "li": set(),
    "code": set(), "pre": set(),
    "a": {"href", "title", "target", "rel"},
    "span": {"style"},
    "mark": {"style", "data-color"},
    "table": set(), "thead": set(), "tbody": set(), "tr": set(),
    "th": {"colspan", "rowspan", "style"},
    "td": {"colspan", "rowspan", "style"},
}

# Tags removed WITH their subtree — content that is executable, styling that
# escapes the document, or embedding that reaches the network. See the module
# docstring for why these are not merely unwrapped.
_DROP_WITH_CONTENT = frozenset({
    "script", "style", "iframe", "object", "embed", "template", "noscript",
    "svg", "math", "form", "input", "button", "select", "textarea", "link",
    "meta", "base", "audio", "video", "source", "canvas", "applet", "frame",
    "frameset", "portal",
})

# CSS properties the toolbar can produce. Everything else in a `style`
# attribute is dropped — notably `position`, `z-index` and anything that could
# lift text out of the document and over the app's own chrome (a clickjacking
# shape), and `background-image`, which fetches.
_ALLOWED_CSS = frozenset({
    "font-family", "font-size", "font-weight", "font-style",
    "color", "background-color", "text-align", "text-decoration",
    # Indentation, which the editor stores as a margin on the block (see
    # web/app/(app)/artifacts/doc/editorIndent.ts). Added when the toolbar
    # gained indent/outdent for documents: without it the indent applied on
    # screen and was stripped on the next save, which is the silent-loss
    # failure this allowlist and the editor's schema are paired to prevent.
    #
    # Safe on the terms the exclusions above are drawn: a margin cannot fetch
    # (no `url()` — and `_CSS_VALUE_BANNED` refuses one anyway), cannot lift
    # text out of the document the way `position` can, and cannot overlay the
    # app's own chrome. It moves a block sideways inside its own column.
    "margin-left",
})

# A CSS value may not reach the network or invoke a scheme. `url(...)` covers
# background/borders, `expression(` is the legacy IE script vector, and the
# scheme check catches `javascript:`/`vbscript:` smuggled into a value.
_CSS_VALUE_BANNED = re.compile(r"url\s*\(|expression\s*\(|javascript:|vbscript:", re.I)

# Link schemes. Relative links are allowed (they stay inside the app); anything
# with a scheme must be one of these. `data:` is excluded deliberately — a
# `data:text/html` link is a same-origin document under the user's session.
_SAFE_URL = re.compile(r"^(?:https?:|mailto:|tel:|#|/|\.{0,2}/)", re.I)


def _clean_style(value: str) -> str:
    """Keep only allowlisted, network-free CSS declarations."""
    kept: list[str] = []
    for decl in (value or "").split(";"):
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop, val = prop.strip().lower(), val.strip()
        if prop not in _ALLOWED_CSS or not val:
            continue
        if _CSS_VALUE_BANNED.search(val):
            continue
        kept.append(f"{prop}: {val}")
    return "; ".join(kept)


def sanitize_artifact_html(html: str) -> str:
    """Return `html` reduced to the allowlist above.

    Total function: never raises on malformed input (BeautifulSoup's parser
    recovers), and returns "" for empty/None. A caller can therefore treat the
    result as safe to store without a try/except around every save.
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")

    # EVERY node bs4 stores as a PreformattedString goes: comments, CDATA
    # sections, processing instructions, declarations and doctypes.
    #
    # This is a sanitizer BYPASS, not tidiness, and it is the sharpest edge in
    # this module. `str(soup)` re-emits all of these RAW AND UNESCAPED, and the
    # parser's idea of where they end is not the browser's. Python's
    # `html.parser` ends a CDATA section at `]]>`, but HTML5 has no CDATA in
    # HTML content: a browser enters the *bogus comment* state and ends it at
    # the FIRST `>`. So this survived sanitization untouched —
    #
    #     <p>hi</p><![CDATA[x><img src=x onerror=alert(1)>]]>
    #
    # — and the browser reads `<![CDATA[x>` as a comment and the `<img>` after
    # it as a live element with a live `onerror`. `<?php … ?>` smuggles a tag
    # through the same way. Extracting the whole class rather than naming
    # `Comment` alone is the fix, and matches this module's stated posture:
    # allowlist what is understood, drop what is not.
    for node in soup.find_all(string=lambda s: isinstance(s, PreformattedString)):
        node.extract()

    for tag in soup.find_all(True):
        # `tag` may already have been detached by an ancestor's decompose().
        if tag.decomposed or tag.parent is None:
            continue
        name = (tag.name or "").lower()

        if name in _DROP_WITH_CONTENT:
            tag.decompose()
            continue

        if name not in _ALLOWED:
            # Unknown but harmless: keep the words, drop the wrapper.
            tag.unwrap()
            continue

        allowed_attrs = _ALLOWED[name]
        for attr in list(tag.attrs):
            key = attr.lower()
            if key not in allowed_attrs:
                del tag.attrs[attr]
                continue
            raw = tag.attrs[attr]
            # BeautifulSoup gives multi-valued attributes (rel, class) as lists.
            value = " ".join(raw) if isinstance(raw, list) else str(raw)
            if key == "style":
                cleaned = _clean_style(value)
                if cleaned:
                    tag.attrs[attr] = cleaned
                else:
                    del tag.attrs[attr]
            elif key == "href":
                if _SAFE_URL.match(value.strip()):
                    tag.attrs[attr] = value.strip()
                else:
                    # An unsafe scheme loses the LINK, not the text.
                    del tag.attrs[attr]
            else:
                tag.attrs[attr] = value

        # Any surviving link opens out of the app, so it must not hand the
        # opener a window handle back into it.
        #
        # `rel` is attached whenever a target is present AT ALL, not only for
        # `_blank`. Browsers imply `noopener` for `_blank` and for nothing else,
        # so `<a href="https://evil.test" target="x">` would otherwise open with
        # a working `window.opener` that can navigate the Sprntly tab to a
        # phishing page. Any target other than `_blank` is also rewritten to it:
        # a named window has no use in a document and is only a way to keep a
        # handle.
        if name == "a" and tag.attrs.get("href") and tag.attrs.get("target"):
            tag.attrs["target"] = "_blank"
            tag.attrs["rel"] = "noopener noreferrer"

    return str(soup)


def html_to_text(html: str) -> str:
    """Visible text of a document body, for summaries, search and LLM prompts.

    Block-level tags become newlines so a heading and the paragraph under it do
    not run together into one word — the failure that makes an extracted
    "title" read as `Q3 UpdateRevenue grew`.
    """
    if not html or not html.strip():
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(_DROP_WITH_CONTENT):
        tag.decompose()
    for br in soup.find_all(["br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr"]):
        br.insert_after(NavigableString("\n"))
    text = soup.get_text()
    # Collapse the runs of blank lines the insertions above create.
    return re.sub(r"\n{3,}", "\n\n", text).strip()
