"""Server-side canonical CSS injection for generated HTML artifacts.

Phase 2 latency work. The `prd-author` and `evidence-brief` skills previously
had the model emit the full ~90-line ``<style>`` block as OUTPUT tokens on every
generation — byte-stable boilerplate that inflates Part A / evidence latency for
zero informational gain. We now instruct the model to emit an EMPTY ``<style>``
marker and splice the canonical stylesheet in here, post-generation. That means:

  * the stored ``payload_md`` stays a fully self-contained HTML document, so the
    frontends (sandboxed iframes), design-agent patches, scoped edits and
    print/export all keep working unchanged — nothing downstream has to know the
    CSS was injected;
  * the model stops paying output tokens to re-emit a stable stylesheet;
  * every artifact renders from ONE canonical stylesheet. This fixes the
    evidence ``examples/`` CSS-contract bug, where the skill told the model to
    "copy the ``<style>`` verbatim from examples/" — a directory the prompt layer
    never actually injected — so the model reconstructed the CSS from memory on
    every run and the output drifted.

The canonical CSS lives in each skill's ``assets/`` dir (``prd-author/assets/
prd.css``, ``evidence-brief/assets/evidence.css``). The gateway deliberately
never injects ``assets/*`` into the prompt (they are downstream render inputs,
not prompt inputs — see ``app.graph.gateway``), which is exactly why they are the
right home for a stylesheet the *server*, not the model, applies.
"""
from __future__ import annotations

import re

# The FIRST inline <style>…</style> in the document — DOTALL so it spans lines,
# IGNORECASE for <STYLE>/<Style>. We replace this whole element (whatever the
# model put inside — the empty marker, or CSS it emitted anyway) so injection is
# authoritative and idempotent: re-running it on an already-injected document
# just swaps one canonical block for an identical one.
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_HEAD_CLOSE_RE = re.compile(r"</head\s*>", re.IGNORECASE)


def inject_canonical_css(html: str, css: str) -> str:
    """Return *html* with its stylesheet replaced by the canonical *css*.

    Behaviour, in priority order:

    1. If the document has a ``<style>`` element, its FIRST one is replaced with
       ``<style>{css}</style>`` — so the model's empty marker (or any CSS it
       emitted despite the instruction) is overwritten by the canonical block.
    2. Otherwise, if there is a ``</head>``, the block is inserted right before
       it.
    3. Otherwise (fragment documents — the evidence brief opens with
       ``<meta><style>…`` and no ``<head>``), the block is prepended.

    The replacement is done with a function (not a string) so CSS containing
    backslashes or ``\\g``/``\\1``-looking sequences can never be misread as a
    regex backreference.
    """
    block = f"<style>\n{css.strip()}\n</style>"
    if _STYLE_RE.search(html):
        return _STYLE_RE.sub(lambda _m: block, html, count=1)
    if _HEAD_CLOSE_RE.search(html):
        return _HEAD_CLOSE_RE.sub(lambda _m: block + _m.group(0), html, count=1)
    return block + html
