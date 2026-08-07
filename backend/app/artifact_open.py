"""Resolve an OPEN request from chat to a concrete, openable artifact.

"open the PRD for compliance reporting" is already understood upstream — the
envelope resolver (app.chat_intent's `open_artifact` action) recognises the
request and never confuses it for a generation. What was missing is the
ACTION: turning the phrase the user named a document with into the ID of a
document the chat's right-hand panel can actually render, plus an honest
verdict when the phrase names nothing, or names more than one thing.

The search index is the SAME artifact index the "Artifacts" nav item renders —
`db.artifacts.list_document_artifacts`, which `list_artifacts_for_company`
itself calls for its PRD/evidence half. One tenant-scoped read, already
collapsed to the newest generation per logical PRD, so an open can never land
on a superseded regeneration of the document the user asked for. Only that half
is read: prototypes, reports and ticket sets have no view in the chat's
right-hand panel, so querying them from inside the send path would be round
trips spent on rows that can never be the answer.

Matching is DELIBERATELY deterministic (token coverage over the title), not a
second model call. This runs inside the send path, the envelope decision has
already spent one call, and — more importantly — an open that silently picks
the wrong document is worse than one that asks. The three outcomes ARE the
contract, and the client is required to honour all three:

    resolved          exactly one best match       → open it
    ambiguous         several equally good matches → ask, with real chips
    not_found         nothing cleared the bar      → say so, open NOTHING
    unsupported_type  a kind this panel can't show → say where it DOES live

`unsupported_type` exists because the alternative is worse than useless: a user
who asks for "the dark mode prototype" and is handed the dark mode PRD has been
given the wrong document with no indication that a substitution happened. Naming
what they asked for and pointing at the Artifacts tab is the only honest answer,
and it follows the same principle as the ambiguous case — an open that silently
picks the wrong document is worse than one that asks.

`not_found` deliberately does not degrade into "generate one instead". Opening
and generating are different verbs on the user's side and different pipelines
on ours; the whole point of this path is that asking for an existing document
never spawns a new one (see app.chat_intent's OPEN-vs-GENERATE rule, which is
the single place that distinction is made).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Artifact kinds this resolver can open. Both have an EXISTING right-panel view
# in the chat (ContentPanel's PRD tab and Evidence tab), reached through the
# same `openPrdTab` entry point every other in-app artifact open already uses.
# Prototypes and reports are deliberately absent — a prototype opens on its own
# `/prototype` route, not the chat panel, and wiring one here would promise a
# panel that does not exist.
OPENABLE_TYPES = ("prd", "evidence")

# Statuses that cannot be shown. A failed or invalidated row is not an artifact
# the user can open, and offering it as a candidate turns a good match into a
# dead click.
_UNOPENABLE_STATUSES = frozenset({"failed", "invalidated"})

# A title must account for MORE THAN this share of the user's own words —
# strictly more, so half-coverage does not qualify. One incidental word in
# common ("Reporting Dashboard" for "compliance reporting") is a coincidence,
# not a match, and offering it as a candidate makes the disambiguation question
# worse rather than better. The practical effect: a two-word request needs both
# words, a three-word request needs two.
_COVERAGE_FLOOR = 0.5

# Scores within this of the best are treated as EQUALLY good, i.e. ambiguous.
# Float comparison only — the scorer's outputs are small rationals.
_TIE = 1e-9

# How many candidates a disambiguation question may carry. Past a handful the
# question stops being answerable and the user is better served re-phrasing.
MAX_CANDIDATES = 5

_WORD_RE = re.compile(r"[a-z0-9]+")

# Words that carry no discriminating power in an artifact title OR in the
# phrase someone names one with. The document nouns ("prd", "doc", "spec") are
# in here for a specific reason: both the request and a good share of the
# titles contain them, so keeping them would score EVERY PRD as a partial match
# for EVERY open request — the exact failure that makes a disambiguation list
# useless.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "the", "for", "of", "on", "in", "to", "our", "my",
        "this", "that", "these", "those", "it", "its", "with", "about",
        "please", "up", "me", "at", "by", "from", "is", "are",
        "prd", "prds", "doc", "docs", "document", "documents", "spec", "specs",
        "evidence", "open", "show", "pull", "view", "find",
    }
)


def _stem(word: str) -> str:
    """Crude singularisation so "exports" matches "export".

    Deliberately minimal (trailing -s only, never on a short word or a double
    s). Anything cleverer needs a stemmer, and a stemmer's false merges cost
    more here than the plurals it would catch: this score decides whether we
    ask the user a question or silently open a document.
    """
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _tokens(text: Optional[str]) -> set[str]:
    """Content words of `text`, lowercased, stemmed, stopwords removed."""
    if not text:
        return set()
    return {
        _stem(w) for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS
    }


def _normalized(text: Optional[str]) -> str:
    """`text` reduced to space-joined content words, for substring checks."""
    if not text:
        return ""
    return " ".join(w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS)


def score_title(query: str, title: Optional[str]) -> float:
    """0..1.25 — how well `title` answers the phrase the user named.

    The base is COVERAGE of the user's words, not of the title's: a request for
    "compliance reporting" is fully answered by "Automated Compliance Reporting
    for Enterprise Admins" even though most of that title went unmentioned, and
    scoring the other direction would rank a terse title above the right one.

    A contiguous-phrase bonus breaks the common tie: with "export scheduling",
    both "Scheduled Export Limits" and "Export Scheduling" cover every word,
    but only one of them is what the user said.
    """
    q_tokens = _tokens(query)
    t_tokens = _tokens(title)
    if not q_tokens or not t_tokens:
        return 0.0
    coverage = len(q_tokens & t_tokens) / len(q_tokens)
    if coverage <= 0:
        return 0.0
    q_norm = _normalized(query)
    bonus = 0.25 if q_norm and q_norm in _normalized(title) else 0.0
    return coverage + bonus


def _candidate(item: dict) -> dict:
    """An artifact list row reduced to what the CLIENT needs to open it.

    Only the ids the existing open paths take: `prd_id` for the PRD panel,
    `brief_id`/`insight_index` for the Evidence panel (which is scoped by the
    insight, not by an evidence row id — see ChatScreen's `kind: "evidence"`
    source). Everything else on the row is listing chrome.

    `brief_anchored` travels with them because the pair alone is ambiguous: a
    chat or uploaded PRD carries `insight_index = 0` as a storage sentinel, and
    a client that fed that pair to the panel's Evidence tab would load the
    brief's FIRST finding under an unrelated document. False means "these
    coordinates identify the row, not a finding — don't resolve them".
    """
    open_ids = item.get("open") or {}
    return {
        "type": item.get("type"),
        "id": item.get("id"),
        "title": item.get("title") or "Untitled",
        "status": item.get("status") or "",
        "prd_id": open_ids.get("prd_id"),
        "brief_id": open_ids.get("brief_id"),
        "insight_index": open_ids.get("insight_index"),
        "brief_anchored": bool(item.get("brief_anchored")),
        "week_label": (item.get("source") or {}).get("week_label"),
    }


def rank_artifacts(
    items: list[dict], query: str, artifact_type: str = "prd"
) -> list[tuple[float, dict]]:
    """(score, row) for every openable artifact of `artifact_type` that clears
    the bar, best first; ties broken by recency (newest first).

    Pure — no I/O — so the whole matching contract is unit-testable without a
    database or a model.
    """
    scored: list[tuple[float, str, dict]] = []
    for item in items:
        if item.get("type") != artifact_type:
            continue
        if (item.get("status") or "") in _UNOPENABLE_STATUSES:
            continue
        score = score_title(query, item.get("title"))
        if score <= _COVERAGE_FLOOR:
            continue
        scored.append((score, item.get("created_at") or "", item))
    # Newest-first within a score band: a regeneration family is already
    # collapsed upstream, so a tie here is two genuinely different documents
    # and recency is the only non-arbitrary order to show them in.
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [(score, item) for score, _created, item in scored]


def resolve_open_artifact(
    *,
    artifact_type: str,
    query: str,
    dataset: str,
) -> dict:
    """Resolve an open request to {status, artifact_type, query, artifact,
    candidates}.

    `dataset` IS the tenant scope and must ALREADY be gated by the caller (the
    route resolves it from the authenticated workspace); this function does no
    auth of its own and simply reads the documents that scoping produces.

    Never raises: a lookup failure degrades to `not_found`, which the client
    renders as "I couldn't find that" — the same thing the user sees when the
    phrase genuinely matches nothing, and strictly better than failing a send.
    """
    kind = artifact_type or "prd"
    out: dict = {
        "status": "not_found",
        "artifact_type": kind,
        "query": query,
        "artifact": None,
        "candidates": [],
    }
    # A kind we have no panel for is reported AS ITSELF. Coercing it to "prd"
    # would open the PRD for whatever they named and call it done — the silent
    # wrong-document failure this module exists to avoid.
    if kind not in OPENABLE_TYPES:
        out["status"] = "unsupported_type"
        return out
    if not (query or "").strip() or not dataset:
        return out

    try:
        from app.db.artifacts import list_document_artifacts

        # `openable_only` drops failed/invalidated rows BEFORE the regeneration
        # family collapses to its newest row. Without it, one deploy restart —
        # which flips every in-flight PRD to `invalidated` (db/prds.py's
        # invalidate_orphan_generating_prds) — makes the whole family
        # unreachable from chat, because the newest row is the dead one and the
        # ready generation behind it never surfaces. That restart is a
        # documented recurring event, not a hypothetical, and the resulting
        # "I couldn't find it" points at an Artifacts tab where it IS listed.
        items = list_document_artifacts(dataset=dataset, openable_only=True)
    except Exception:  # noqa: BLE001 — an open must never break the send
        logger.exception("artifact open lookup failed; reporting not_found")
        return out

    ranked = rank_artifacts(items, query, kind)
    if not ranked:
        return out

    best = ranked[0][0]
    tied = [item for score, item in ranked if score >= best - _TIE]
    if len(tied) == 1:
        out["status"] = "resolved"
        out["artifact"] = _candidate(tied[0])
        out["candidates"] = [out["artifact"]]
        return out

    out["status"] = "ambiguous"
    out["candidates"] = [_candidate(i) for i in tied[:MAX_CANDIDATES]]
    return out
