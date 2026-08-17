"""Share an artifact the user already has into their Slack — the pure half.

The chat can now be told "share this PRD on my slack channel and ask the team
for feedback". That sentence carries three separate decisions, and this module
owns the two that are pure functions of data the route hands it:

  * WHICH DOCUMENT (`resolve_share_target`) — the same title-matching the chat's
    open path uses (`artifact_open.rank_artifacts`), widened to the kinds that
    can be shared. Reused rather than re-implemented so "the checkout PRD"
    resolves to the same document whether you ask to open it or to share it;
    two matchers would eventually disagree, and the one that posts to a team
    channel is the worse place to find out.
  * WHICH CHANNEL (`match_channel`) — the name the user typed against the
    channels the bot can actually see. Exact match wins outright; anything else
    is reported as a CHOICE rather than resolved, because a near-miss here puts
    a document in front of the wrong audience.

and the message itself (`compose_share`), which is composed HERE rather than
accepted from the client on purpose: preview and send both call it with the
same inputs, so what the user approved is what Slack receives, and the client
can never hand our bot token an arbitrary body to post.

Everything here is I/O-free and independently testable — the route does the
reads, the tenant scoping and the posting. Nothing in this module can send a
message; `preview` and `send` in routes/share.py are the only callers, and only
the second of those posts.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.artifact_open import MAX_CANDIDATES, rank_artifacts

logger = logging.getLogger(__name__)

#: Artifact kinds that can be shared, in `db.artifacts.list_artifacts_for_company`
#: vocabulary. Deliberately NOT `artifact_open.OPENABLE_TYPES`: that set is
#: "what the chat panel can display", which is a different question from "what
#: can be handed to a colleague". Evidence and prototypes are absent for the
#: opposite reasons — evidence is a working note rather than something a team
#: reads cold, and a prototype's home is its own canvas route, so a link into
#: the library would land the reader somewhere the thing they were sent is not.
SHAREABLE_TYPES: tuple[str, ...] = ("prd", "ticket_set", "report", "custom_artifact")

#: What the planner may CALL a kind → what the library calls it. The planner
#: speaks the user's words ("tickets"), the listing speaks its own schema
#: ("ticket_set"), and this is the one place the two are reconciled. A kind
#: outside this map is reported as unshareable rather than coerced — the same
#: rule `resolve_open_artifact` applies to a type it has no panel for, and for
#: the same reason: substituting a PRD for the prototype someone asked to share
#: is worse than saying it cannot be shared.
_TYPE_ALIASES: dict[str, str] = {
    "prd": "prd",
    "prds": "prd",
    "spec": "prd",
    "tickets": "ticket_set",
    "ticket": "ticket_set",
    "ticket_set": "ticket_set",
    "stories": "ticket_set",
    "report": "report",
    "reports": "report",
    "brief": "report",
    "custom_artifact": "custom_artifact",
    "document": "custom_artifact",
    "doc": "custom_artifact",
}

#: How the kind reads in the Slack message. The reader is a colleague who may
#: not use Sprntly, so this is the plain-English noun, never the schema key.
_KIND_LABELS: dict[str, str] = {
    "prd": "PRD",
    "ticket_set": "Tickets",
    "report": "Report",
    "custom_artifact": "Document",
}

#: The summary line's ceiling. A Slack section block caps at 3000 characters
#: and the note, title and link share that budget — but the real constraint is
#: editorial: this is a teaser that makes someone click, not the document.
SUMMARY_CHARS = 400

#: Ceiling on the note as it reaches Slack. The planner already clamps its own
#: output (`ask_planner._SHARE_NOTE_CHARS`); this bounds the value that arrives
#: on the SEND request, which the user may have edited in the preview and which
#: therefore never passed through the planner at all.
NOTE_CHARS = 2000

#: Markdown furniture to strip when reducing a document body to a teaser.
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_FENCE_RE = re.compile(r"^\s*```.*$", re.M)
_MD_DIRECTIVE_RE = re.compile(r"^\s*:::.*$", re.M)
_MD_EMPHASIS_RE = re.compile(r"[*_`>]+")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# ELEMENTS WHOSE CONTENT IS NOT PROSE, removed whole — opening tag, body and
# closing tag together.
#
# Stripping tags alone is NOT enough, and this is the bug it caused: a real PRD
# is stored as a full HTML document with a `<style>` block, so removing `<` and
# `>` left the STYLESHEET behind as the first "prose" in the document. The
# teaser posted to Slack read
#
#     Build the Public External-Brief Route @import url('https://fonts.google…
#     :root{--green:#1A6B47;--ink:#1F241F;--sub:#5B615B; …
#
# in front of a whole team. Reported from a live share, 2026-08-16.
_NON_PROSE_ELEMENTS_RE = re.compile(
    r"<\s*(style|script|head|svg|noscript|template)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.I | re.S,
)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# An unclosed `<style>` (a truncated document) would otherwise leak the whole
# tail; drop from the opening tag to the end rather than trusting the close.
_UNCLOSED_NON_PROSE_RE = re.compile(
    r"<\s*(style|script)\b[^>]*>.*", re.I | re.S,
)
# Residual CSS that reached the text some other way — a bare at-rule, or a
# `selector { … }` declaration block in a markdown document. Defensive: the
# element strip above is what actually fixes the reported case, and this
# catches the same shape arriving without its tags.
_CSS_AT_RULE_RE = re.compile(r"@[a-z-]+\b[^;{]*(?:;|\{[^}]*\})", re.I)
_CSS_BLOCK_RE = re.compile(r"[^{}<>]{0,120}\{[^{}]*(?::[^{}]*;|--[^{}]*)[^{}]*\}")


def canonical_type(named: Optional[str]) -> Optional[str]:
    """The library's name for the kind the user named, or None when the kind
    cannot be shared. None is also the answer for "they named no kind at all",
    which the route reads as "search every shareable kind"."""
    if not named:
        return None
    return _TYPE_ALIASES.get(named.strip().lower())


def kind_label(canonical: Optional[str]) -> str:
    """The plain-English noun for a canonical kind, for the Slack copy."""
    return _KIND_LABELS.get(canonical or "", "Document")


# ── the link ─────────────────────────────────────────────────────────────────


def share_link(base_url: str, *, artifact_type: str, artifact_id: Any,
               open_ids: Optional[dict] = None) -> str:
    """A URL a colleague can click to land ON the shared artifact.

    PRDs reuse the app's EXISTING canonical deep link (`/brief?prd={id}`, the
    same one the "your PRD is ready" ping has always sent) rather than
    inventing a second address for the same document — `useArtifactUrlSync`
    already consumes it from any `(app)` page, including the legacy bare-integer
    form this produces.

    The other three have no per-artifact route today, so they go to the library
    with a `focus` key naming the row. That key is exactly the
    `${type}-${id}` shape `ArtifactsScreen` already uses for
    `activeArtifactKey`, so the screen opens the row through the very same
    per-kind logic a click on it would run.
    """
    base = (base_url or "").rstrip("/")
    if artifact_type == "prd":
        prd_id = (open_ids or {}).get("prd_id") or artifact_id
        return f"{base}/brief?prd={prd_id}"
    return f"{base}/artifacts?focus={artifact_type}-{artifact_id}"


# ── which document ───────────────────────────────────────────────────────────


def _candidate(item: dict, base_url: str) -> dict:
    """A library row reduced to what a share needs: what it is, what it's
    called, and where it lives. Carries its own link so an ambiguous result can
    be disambiguated in chat without a second resolution pass."""
    art_type = item.get("type") or ""
    return {
        "type": art_type,
        "id": item.get("id"),
        "title": (item.get("title") or "").strip() or "Untitled",
        "kind_label": kind_label(art_type),
        "url": share_link(
            base_url,
            artifact_type=art_type,
            artifact_id=item.get("id"),
            open_ids=item.get("open") or {},
        ),
    }


def resolve_share_target(
    items: list[dict],
    *,
    artifact_type: Optional[str],
    artifact_query: Optional[str],
    base_url: str,
) -> dict:
    """Pick the artifact to share out of the caller's own library.

    `items` MUST already be tenant-scoped — this function does no auth, exactly
    like `artifact_open.resolve_open_artifact`, and simply ranks what the
    scoping produced.

    Returns {status, artifact, candidates} where status is one of:
      * "resolved"        — one clear match, in `artifact`
      * "ambiguous"       — several equally good, in `candidates`
      * "not_found"       — nothing matched the phrase
      * "unsupported_type"— they named a kind that cannot be shared

    A NULL `artifact_query` is not an error here — it is the common case
    ("share this PRD"), and it means the caller's own context decides. The route
    resolves that before calling this; reaching here with no query and no
    context is `not_found`, which the chat turns into "which one?".
    """
    out: dict = {"status": "not_found", "artifact": None, "candidates": []}

    canonical = canonical_type(artifact_type)
    if artifact_type and canonical is None:
        # They named a kind we cannot share (a prototype, evidence). Reported as
        # itself so the chat can say WHICH kind and why, never silently retried
        # as a PRD.
        out["status"] = "unsupported_type"
        out["named_type"] = (artifact_type or "").strip().lower()
        return out

    query = (artifact_query or "").strip()
    if not query:
        return out

    # Search the named kind, or every shareable kind when they named none.
    # `rank_artifacts` filters by one type per call, so an unnamed kind is a
    # sweep across all four — pooled and re-sorted so the best title wins
    # regardless of which kind it came from, rather than PRDs always beating a
    # better-matching report purely by being checked first.
    kinds = (canonical,) if canonical else SHAREABLE_TYPES
    pooled: list[tuple[float, dict]] = []
    for kind in kinds:
        pooled.extend(rank_artifacts(items, query, kind))
    if not pooled:
        return out
    pooled.sort(key=lambda pair: (pair[0], pair[1].get("created_at") or ""), reverse=True)

    best = pooled[0][0]
    # Same tie band as the open path — scores are small rationals, so an exact
    # equality test is what "equally good" means here.
    tied = [item for score, item in pooled if score >= best - 1e-9]
    if len(tied) == 1:
        out["status"] = "resolved"
        out["artifact"] = _candidate(tied[0], base_url)
        out["candidates"] = [out["artifact"]]
        return out
    out["status"] = "ambiguous"
    out["candidates"] = [_candidate(i, base_url) for i in tied[:MAX_CANDIDATES]]
    return out


# ── which channel ────────────────────────────────────────────────────────────


def _channel_row(ch: dict) -> dict:
    """A `slack_oauth.list_channels` row reduced to what the picker and the
    preview need. `is_member` rides along because it decides whether the post
    will need a self-join — and whether a PRIVATE channel can be posted to at
    all (the bot cannot self-join one; see `post_message`'s not_in_channel
    handling)."""
    return {
        "id": ch.get("id") or "",
        "name": ch.get("name") or "",
        "is_private": bool(ch.get("is_private")),
        "is_member": bool(ch.get("is_member")),
    }


def match_channel(channels: list[dict], query: Optional[str]) -> dict:
    """Resolve the channel the user named against the ones the bot can see.

    Returns {status, channel, candidates} where status is:
      * "resolved"  — an EXACT name match (case-insensitive)
      * "ambiguous" — several channels contain the phrase
      * "not_found" — the name matched nothing
      * "needs_channel" — they named none, so every channel is a candidate

    DELIBERATELY STRICT, and this is the difference between this matcher and
    the artifact one above. A document resolved slightly wrong is a document
    the user can decline in the preview; a CHANNEL resolved slightly wrong is
    the audience itself, and "#product" vs "#product-leads" is one substring
    apart. So only an exact name resolves outright — a partial match is offered
    as a choice, never taken.
    """
    rows = [_channel_row(c) for c in (channels or []) if c.get("id")]
    q = (query or "").strip().lstrip("#").strip().lower()
    if not q:
        return {"status": "needs_channel", "channel": None, "candidates": rows}

    exact = [r for r in rows if r["name"].lower() == q]
    if len(exact) == 1:
        return {"status": "resolved", "channel": exact[0], "candidates": exact}
    if len(exact) > 1:
        # Slack itself forbids duplicate channel names, so this is defensive
        # rather than expected — but "impossible" data is exactly what should
        # ask rather than pick.
        return {"status": "ambiguous", "channel": None, "candidates": exact}

    partial = [r for r in rows if q in r["name"].lower()]
    if partial:
        return {
            "status": "ambiguous",
            "channel": None,
            "candidates": partial[:MAX_CANDIDATES],
        }
    return {"status": "not_found", "channel": None, "candidates": rows}


def channel_warning(channel: Optional[dict]) -> Optional[str]:
    """What the user must be told BEFORE they confirm, or None.

    Membership is checked at preview time rather than discovered at send time
    on purpose: `post_message` raises a perfectly good "invite the bot" error,
    but it raises it AFTER the user has pressed Send on a message they believe
    is going out. A private channel the bot isn't in cannot be self-joined, so
    that one is a blocker; a public one is merely a heads-up, because the
    auto-join recovers it.
    """
    if not channel or channel.get("is_member"):
        return None
    if channel.get("is_private"):
        return (
            f"Sprntly isn't in #{channel.get('name')}, and it can't add itself "
            "to a private channel. Invite the Sprntly bot there first "
            f"(/invite @Sprntly in #{channel.get('name')}), then try again."
        )
    return (
        f"Sprntly isn't in #{channel.get('name')} yet — it will join the "
        "channel in order to post this."
    )


def channel_is_blocked(channel: Optional[dict]) -> bool:
    """True when posting CANNOT succeed: a private channel the bot is not in.
    The preview refuses to offer a Send it knows would fail."""
    return bool(
        channel and not channel.get("is_member") and channel.get("is_private")
    )


# ── the message ──────────────────────────────────────────────────────────────


def summarize(body: Optional[str], *, limit: int = SUMMARY_CHARS) -> str:
    """A document body reduced to a teaser paragraph.

    Deterministic — no model call. Non-prose ELEMENTS go first, whole
    (`<style>`, `<script>`, `<head>` and friends — see `_NON_PROSE_ELEMENTS_RE`
    for the stylesheet that shipped to a customer's Slack), then comments,
    then tags, then markdown furniture. What is left is the first prose that
    survives, truncated on a word boundary. A document with no prose (a pure
    table, an empty draft) yields "", and the composer simply omits the line
    rather than posting an empty quote block.

    ORDER IS LOAD-BEARING: the element strip must run BEFORE the tag strip, or
    `<style>` becomes plain text and its contents become the teaser.
    """
    if not body:
        return ""
    text = _HTML_COMMENT_RE.sub(" ", body)
    text = _NON_PROSE_ELEMENTS_RE.sub(" ", text)
    text = _UNCLOSED_NON_PROSE_RE.sub(" ", text)
    text = _HTML_TAG_RE.sub(" ", text)
    text = _CSS_AT_RULE_RE.sub(" ", text)
    text = _CSS_BLOCK_RE.sub(" ", text)
    text = _MD_FENCE_RE.sub(" ", text)
    text = _MD_DIRECTIVE_RE.sub(" ", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_EMPHASIS_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Truncate on a word boundary when there is one late enough to be worth it;
    # a mid-word cut reads as corruption rather than as an excerpt.
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + "…"


def compose_share(
    *,
    note: Optional[str],
    artifact: dict,
    summary: str,
    sharer_name: Optional[str] = None,
) -> tuple[str, list[dict]]:
    """(plain-text fallback, Block Kit blocks) for one share.

    Called by BOTH preview and send with the same arguments, which is what
    makes the preview honest: the user approves this exact output. The plain
    text is not decoration — Slack requires it for notifications and
    accessibility, and it is what a reader sees in a push notification.

    Shape: the sharer's own words first (they are the reason the message
    exists), then the document as a titled link with a teaser, then a quiet
    attribution line. The note is posted verbatim; only the parts Sprntly
    itself asserts are composed here.
    """
    title = (artifact.get("title") or "Untitled").strip()
    label = artifact.get("kind_label") or kind_label(artifact.get("type"))
    url = artifact.get("url") or ""
    clean_note = " ".join((note or "").split())[:NOTE_CHARS]

    blocks: list[dict] = []
    if clean_note:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": clean_note},
        })
    # `<url|title>` is Slack's link syntax — the title is the clickable text, so
    # the message reads as a document rather than as a pasted URL.
    doc_line = f"*{label}:* <{url}|{_escape(title)}>" if url else f"*{label}:* {_escape(title)}"
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": doc_line}})
    if summary:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": _escape(summary)}],
        })
    attribution = (
        f"Shared from Sprntly by {sharer_name}" if sharer_name
        else "Shared from Sprntly"
    )
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": attribution}],
    })

    text_parts = [p for p in (clean_note, f"{label}: {title}", url) if p]
    return "\n".join(text_parts), blocks


def _escape(text: str) -> str:
    """Slack's three mrkdwn control characters, escaped.

    Applies to values SPRNTLY composes into the message (a document title, a
    generated summary) — not to the user's own note, which is posted as they
    wrote it. A title containing `<` would otherwise open a link span and eat
    the rest of the line.
    """
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
