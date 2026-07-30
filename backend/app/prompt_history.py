"""Clamp a conversation turn before it is folded into a prompt.

Chat answers are persisted verbatim as conversation turns (web chatPersistence)
and replayed into EVERY later prompt in that thread — the haiku router call and
the answer call both fold history in (`qa_agent._render_history`). Nothing on
that path caps a turn's size: `routes/ask.py:_load_history` returns whole rows.

So one fat answer poisons the rest of the conversation. An HTML report answer is
the realistic case: a report carrying base64 charts is ~1 MB of `data:` URI, which
is hundreds of thousands of tokens once replayed — a non-retryable 400 on every
subsequent ask in that thread, on a path that has no fallback. The DS Claude
report is the newest producer of such answers, but the VoC and public-feedback
HTML reports have the same shape, so the clamp lives here and is applied at the
FOLD, protecting every producer at once (including rows already stored).

`clamp_turn_text` is deliberately lossy and deliberately cheap:
  1. `data:` URIs (the megabyte) are replaced with a short placeholder.
  2. A turn that IS an HTML document is reduced to its visible text, so a
     follow-up sees the report's narrative instead of its stylesheet.
  3. Whatever is left is truncated to a character cap.

None of this touches what the user sees — the stored answer and the rendered
report are unchanged. It only bounds what we re-send to the model.
"""
from __future__ import annotations

import re

# ~4k chars ≈ 1k tokens per turn. Six turns of history then costs ~6k tokens,
# which is noise next to the corpus/KG blocks the same prompts already carry.
MAX_TURN_CHARS = 4000

# No `\s` in the payload class. With it, the match ran past the end of the
# base64 and ate the prose that followed: "…;base64,AAAA\n\nExport users retain
# 2.3x longer" collapsed to "[embedded image omitted].3x longer", deleting the
# narrative this clamp exists to preserve (whitespace, then more [A-Za-z0-9], is
# indistinguishable from a wrapped payload to a greedy class). The cost is that a
# base64 payload split across lines only has its first line replaced — rare in a
# `data:` attribute, and the residue is bounded by the char cap below.
_DATA_URI_RE = re.compile(r"data:[\w.+-]+/[\w.+-]+;base64,[A-Za-z0-9+/=]*")
# Same sniff the frontend uses to decide "this answer is a document, not markdown"
# (web/app/lib/htmlBrief.ts looksLikeHtmlBrief).
_HTML_START_RE = re.compile(r"^\s*<(?:!doctype|meta|html|div|style)\b", re.I)
_DROP_BLOCK_RE = re.compile(r"<(style|script|head)\b[\s\S]*?</\1>", re.I)
_BLOCK_END_RE = re.compile(r"</(?:p|div|h[1-6]|li|tr|section|figure)>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")

_IMAGE_PLACEHOLDER = "[embedded image omitted]"
_TRUNCATION_MARK = "… [earlier turn truncated]"


def strip_data_uris(text: str) -> str:
    """Replace every `data:<type>;base64,…` payload with a short placeholder."""
    return _DATA_URI_RE.sub(_IMAGE_PLACEHOLDER, text)


def looks_like_html(text: str) -> bool:
    return bool(_HTML_START_RE.match(text or ""))


def html_to_text(html: str) -> str:
    """Visible text from an HTML report — best effort, never raises."""
    out = _DROP_BLOCK_RE.sub(" ", html)
    out = _BLOCK_END_RE.sub("\n", out)
    out = out.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    out = _TAG_RE.sub(" ", out)
    for entity, char in (
        ("&nbsp;", " "), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"), ("&amp;", "&"),
    ):
        out = out.replace(entity, char)
    out = _WS_RE.sub(" ", out)
    out = _NEWLINES_RE.sub("\n\n", out)
    return "\n".join(line.strip() for line in out.splitlines() if line.strip()).strip()


def clamp_turn_text(text: object, *, max_chars: int = MAX_TURN_CHARS) -> str:
    """One conversation turn, safe to fold into a prompt.

    Strips base64 payloads, reduces an HTML document to its narrative, and caps
    the result. Returns "" for anything that isn't a non-empty string.
    """
    if not isinstance(text, str) or not text:
        return ""
    out = strip_data_uris(text)
    if looks_like_html(out):
        out = html_to_text(out)
    out = out.strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + _TRUNCATION_MARK
    return out
