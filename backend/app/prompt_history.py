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


# ── Folding a WHOLE conversation into a prompt ───────────────────────────────
#
# `clamp_turn_text` bounds ONE turn. This is the other half: how a thread that
# does not fit its byte budget is reduced.
#
# Every fold site used to do the same two things — take `history[-N:]` and spend
# the byte budget newest-first — which drops the OLDEST turns SILENTLY, with no
# marker. That is exactly backwards for the messages that need history most: a
# deictic message ("draft it up", "break that into tickets") names its referent
# nowhere, and the FEATURE it refers to was named at turn 2, which is the first
# thing a newest-first budget discards. The model then sees a thread whose topic
# is missing and cannot tell that anything is missing.
#
# So: consider ALL turns, and when the budget is exceeded COMPACT rather than
# truncate — keep the HEAD and the TAIL, elide the middle, and leave an explicit
# in-band marker saying how many turns were dropped. This is the same shape
# `app/call_index.py::render_transcript` already uses for over-long transcripts,
# deliberately so: one elision idiom in the codebase, and the model gets the same
# "this is partial, and here is where" signal in both places.
#
# HEAD SHARE 0.3 (render_transcript uses 0.4). Both halves earn their keep, but
# not equally: the head only has to carry the TOPIC, which a thread names in its
# first turn or two and cheaply — one user turn saying "CSV export of the weekly
# report" is a couple of hundred characters. The tail has to carry every
# requirement, correction and constraint the thread accumulated, plus the
# assistant offer a bare "yes" adopts, and that is what a deictic message
# actually resolves against. A call transcript has no such asymmetry (its
# opening is agenda + attendees, proportionally worth more), hence the different
# split. At the 24k budget both callers use, 0.3 still buys the head ~7.2k chars
# — at least four turns at either per-turn clamp, comfortably more than the
# one or two the topic lives in.
HISTORY_HEAD_SHARE = 0.3

# Default total byte budget for a rendered history block. ~24k chars ≈ 6k tokens,
# which is what qa_agent's 6-turn window already cost at its worst (6 × 4000) —
# so uncapping the TURN count while capping BYTES leaves the worst case where it
# was and only changes WHICH turns survive it.
MAX_HISTORY_CHARS = 24_000

_ELISION = (
    "[... {dropped} earlier turns from the middle of this conversation were "
    "omitted to fit the context budget — the opening and the most recent turns "
    "are complete, the middle is NOT. Say so if asked about anything that would "
    "have fallen in the omitted stretch ...]"
)


def history_rows(
    history: object, *, turn_chars: int = MAX_TURN_CHARS
) -> list[str]:
    """`Role: content` lines, oldest→newest, one per non-empty turn.

    Empty turns are skipped rather than rendered as a bare "User: " — an empty
    turn carries nothing for the model and a blank role line is pure noise in a
    prompt. A missing/None role falls back to "user" rather than raising.
    """
    if not isinstance(history, list):
        return []
    rows: list[str] = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "user").capitalize()
        content = clamp_turn_text(turn.get("content"), max_chars=turn_chars)
        if not content:
            continue
        rows.append(f"{role}: {content}")
    return rows


def compact_rows(
    rows: list[str],
    *,
    char_budget: int = MAX_HISTORY_CHARS,
    head_share: float = HISTORY_HEAD_SHARE,
) -> list[str]:
    """`rows` if they fit, else head + elision marker + tail.

    Head and tail cannot overlap: reaching this branch means the rows total more
    than `char_budget`, while the head is bounded by `head_share` of it and the
    tail by the remainder — so if they covered every row their combined bytes
    would be ≤ the budget, a contradiction. `dropped` is therefore always ≥ 1
    (asserted by the guard below rather than assumed).

    The newest row is force-kept even if it alone blows the tail budget. With
    the per-turn clamps both callers use that is unreachable, but a history
    block whose LAST turn silently vanished would be the worst possible failure
    of this function, so the invariant is enforced here rather than inferred
    from a constant somewhere else.
    """
    total = sum(len(row) + 1 for row in rows)
    if total <= char_budget:
        return rows

    head_budget = int(char_budget * head_share)
    tail_budget = char_budget - head_budget

    head: list[str] = []
    used = 0
    for row in rows:
        if used + len(row) > head_budget:
            break
        head.append(row)
        used += len(row) + 1

    tail: list[str] = []
    used = 0
    for row in reversed(rows):
        if used + len(row) > tail_budget:
            break
        tail.insert(0, row)
        used += len(row) + 1
    if not tail:
        tail = [rows[-1]]
        head = head[: len(rows) - 1]

    dropped = len(rows) - len(head) - len(tail)
    if dropped <= 0:
        return rows
    return head + [_ELISION.format(dropped=dropped)] + tail


def render_history_block(
    history: object,
    *,
    turn_chars: int = MAX_TURN_CHARS,
    char_budget: int = MAX_HISTORY_CHARS,
    head_share: float = HISTORY_HEAD_SHARE,
    header: str = "Conversation so far:",
) -> str:
    """The whole conversation as prompt text, compacted (never silently cut).

    Byte-identical to the old last-N/newest-first renderers for any thread that
    fits the budget — which is the overwhelmingly common case, so the change is
    invisible to short threads and only reshapes long ones.
    """
    rows = history_rows(history, turn_chars=turn_chars)
    if not rows:
        return ""
    body = compact_rows(rows, char_budget=char_budget, head_share=head_share)
    return f"{header}\n" + "\n".join(body) + "\n\n"
