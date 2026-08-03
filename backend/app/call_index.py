"""Cheap, queryable metadata for every call in a connected transcript source.

THE PROBLEM. `app.call_digest` answers any call question by fetching every call
in the window from Fireflies *with full sentences*, assembling a corpus, and
handing it to the model. Measured on Chaostrack (22 calls, 2026-08-01):

    Fireflies metadata only     1.13 s      7.7 KB    ~2k tokens
    Fireflies with sentences    3.19 s   1461.7 KB  ~374k tokens
    LLM synthesis             168.40 s              ~38k in / 8k out, $0.23

~98% of the wall clock is the model reading transcripts — the API call is about
a second. And because that path is expensive it sits behind a keyword gate, so a
phrasing the regex misses ("give me the 5 latest transcripts") falls through to
the KG and gets answered from distilled summaries instead of the calls.

THE INDEX. This module keeps the 7.7 KB half in Postgres and never the
transcripts:

    "which calls last week?"        -> a DB read. No fetch, no model, no cost.
    "summarize the Genworth call"   -> resolve one id, fetch ONE transcript.
    "summarize last week"           -> unchanged; it genuinely needs them all.

Transcripts are deliberately not stored: they are the customer's raw
conversation content, they are large, and the source is the system of record. We
keep a pointer (`external_id`) and re-fetch on demand.

Provider-agnostic by construction — `provider` is a column and Fireflies is
merely the first populator, so Gong/Otter/Zoom can fill the same index later.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROVIDER_FIREFLIES = "fireflies"

# Fireflies rejects limit > 50 outright ("limit must not be greater than 50"),
# so a sync pages with `skip` rather than asking for everything at once. Mirrors
# _PAGE_SIZE in app/kg_ingest/pullers/fireflies.py.
_PAGE_SIZE = 50
# Sync ceiling ACROSS all pages. A busy quarter is ~150 calls; this bounds a
# first sync over a long history without needing a durable cursor.
_SYNC_LIMIT = 500

# Domains that never indicate the customer's account when deriving `account`
# from participant emails.
_GENERIC_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com",
})

# Metadata-only query. Note the absence of `sentences` — that single omission is
# the 1.4 MB / 374k-token difference measured above.
#
# `$fromDate` is what makes an INCREMENTAL top-up cheap: a freshness refresh
# asks only for calls since the last successful sync, so it is one page and one
# HTTP call however deep the company's history is. The variable is nullable and
# simply omitted from `variables` for a full sync, which GraphQL treats as null
# — i.e. exactly the unbounded query it was before.
_INDEX_QUERY = """
query Transcripts($limit: Int, $skip: Int, $fromDate: DateTime) {
  transcripts(limit: $limit, skip: $skip, fromDate: $fromDate) {
    id
    title
    date
    duration
    participants
    summary { overview }
  }
}
"""


@dataclass(frozen=True)
class IndexedCall:
    """One row of the index, as the answer paths consume it."""

    external_id: str
    title: str
    call_date: Optional[str]
    duration_min: Optional[float]
    participants: list[str]
    account: Optional[str]
    summary: str

    def render(self) -> str:
        """One compact line for a listing answer or a model prompt."""
        when = (self.call_date or "")[:10]
        who = f" · {self.account}" if self.account else ""
        mins = f" · {self.duration_min:.0f}m" if self.duration_min else ""
        return f"{when}{who}{mins} — {self.title or '(untitled)'}"


# A domain on at least this share of all calls is the vendor's own, not a
# customer's. No real customer attends most of your calls; you attend all of
# them.
_UBIQUITY_THRESHOLD = 0.5


def _own_domains(company_id: str, calls: Optional[list[dict]] = None) -> set[str]:
    """Email domains that belong to US, not to a customer.

    Two sources, because either alone is unreliable:

    1. The company's own members. Authoritative when present — but it is empty
       for any workspace whose membership rows were not populated, and then
       every call gets labelled with the vendor's own domain as the "account".
       That is exactly what happened on the first Chaostrack sync: 485 calls
       came back labelled `Chaostrack`.

    2. UBIQUITY across the corpus. The domain attending most of the calls is
       definitionally the host, not a customer — no customer is on 90% of your
       calls, and you are on all of them. Self-calibrating, needs no membership
       data, and corrects (1) when it is missing or partial.
    """
    domains: set[str] = set()
    try:
        from app.db.drip import list_members_with_email

        for member in list_members_with_email(company_id) or []:
            email = (member.get("email") or "").strip().lower()
            if "@" in email:
                domains.add(email.rsplit("@", 1)[1])
    except Exception:  # noqa: BLE001 — best effort; ubiquity below covers it
        logger.debug("call-index: could not read member domains for %s", company_id)

    if calls:
        counts: dict[str, int] = {}
        for call in calls:
            seen = {
                str(p).strip().lower().rsplit("@", 1)[1]
                for p in (call.get("participants") or [])
                if "@" in str(p)
            }
            for domain in seen:
                counts[domain] = counts.get(domain, 0) + 1
        threshold = max(2, int(len(calls) * _UBIQUITY_THRESHOLD))
        ubiquitous = {d for d, n in counts.items() if n >= threshold}
        if ubiquitous:
            logger.info(
                "call-index: treating %s as own domain(s) by ubiquity for %s",
                sorted(ubiquitous), company_id,
            )
        domains |= ubiquitous
    return domains


def derive_account(participants: list[str], own_domains: set[str]) -> Optional[str]:
    """Best-effort external account for a call, from participant email domains.

    Returns None rather than guessing when every participant is internal or on a
    generic consumer domain — an internal standup genuinely has no account, and
    a wrong label is worse than a blank one when a model reads this as evidence.
    """
    candidates: list[str] = []
    for raw in participants or []:
        value = (raw or "").strip().lower()
        if "@" not in value:
            continue
        domain = value.rsplit("@", 1)[1]
        if domain in own_domains or domain in _GENERIC_EMAIL_DOMAINS:
            continue
        candidates.append(domain)
    if not candidates:
        return None
    # Most common external domain wins — a 6-person call with one vendor rep
    # should still be labelled by the customer.
    best = max(set(candidates), key=candidates.count)
    return best.rsplit(".", 1)[0].replace("-", " ").title()


def _normalize_date(raw: Any) -> Optional[str]:
    """Fireflies returns epoch millis or an ISO string depending on age."""
    if raw in (None, ""):
        return None
    try:
        if isinstance(raw, (int, float)):
            return datetime.fromtimestamp(raw / 1000, tz=timezone.utc).isoformat()
        text = str(raw)
        if text.isdigit():
            return datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc).isoformat()
        return text
    except (ValueError, OverflowError, OSError):
        return None


def sync_company(
    company_id: str, *, limit: int = _SYNC_LIMIT, since: Optional[datetime] = None
) -> Optional[int]:
    """Refresh the index for one company from its connected source.

    Returns the number of calls written, or **None** when there is no connected
    source to sync from. That distinction is not decoration: returning 0 for
    both would let `ensure_fresh` read "no source" as "synced, found nothing",
    stamp the index usable, and answer *"No calls. Your transcript source is
    connected and I checked it"* to a company that has no transcript source at
    all — the same confidently-wrong shape this whole layer exists to prevent,
    smuggled in through a return value.

    Idempotent: rows upsert on (company_id, provider, external_id), so
    re-running refreshes rather than duplicating. Safe to call on a schedule or
    lazily before a read.

    ``since`` makes this an INCREMENTAL top-up — only calls after that instant
    are fetched. `ensure_fresh` passes it with a deliberate overlap so a
    read-path refresh costs one page and one HTTP call regardless of how much
    history the company has. Upsert semantics make the overlap free.

    Every outcome is STAMPED on `call_index_sync` — success and failure alike.
    That record is the whole point: without it a zero-row read cannot tell
    "this company has no calls" from "we never synced", and a reader that
    cannot tell those apart answers the first when the truth is the second.
    A failure is stamped and re-raised so the caller decides what to do; the
    fire-and-forget kickoffs swallow it, `ensure_fresh` degrades on it.
    """
    from app.call_digest import _load_api_key
    from app.db.client import require_client
    from app.kg_ingest.pullers.fireflies import _post

    api_key = _load_api_key(company_id)
    if not api_key:
        logger.info("call-index: no fireflies source for %s", company_id)
        # Deliberately NOT stamped: "no source connected" is not a sync outcome,
        # and writing a row here would make a company that never connected
        # Fireflies look like one whose sync found nothing.
        return None

    try:
        return _sync_from_source(
            company_id, api_key, limit=limit, since=since, post=_post,
            client=require_client(),
        )
    except Exception as exc:  # noqa: BLE001 — stamp, then let the caller decide
        _record_sync_failure(company_id, exc)
        raise


def _sync_from_source(
    company_id: str, api_key: str, *, limit: int, since: Optional[datetime],
    post, client,
) -> int:
    """The sync body, split out so `sync_company` owns the error stamping and
    this stays a straight-line read-transform-write."""
    raw: list[dict] = []
    skip = 0
    while len(raw) < limit:
        variables: dict = {"limit": min(_PAGE_SIZE, limit - len(raw)), "skip": skip}
        if since is not None:
            variables["fromDate"] = since.astimezone(timezone.utc).isoformat()
        page = post(api_key, _INDEX_QUERY, variables)
        raw.extend(page)
        if len(page) < _PAGE_SIZE:
            break            # last page
        skip += len(page)

    # Pass the fetched corpus so ubiquity can identify our own domain even
    # when membership rows are absent.
    own = _own_domains(company_id, raw)
    rows = []
    for call in raw:
        participants = call.get("participants") or []
        duration = call.get("duration")
        rows.append({
            "company_id": company_id,
            "provider": PROVIDER_FIREFLIES,
            "external_id": str(call.get("id")),
            "title": call.get("title") or "",
            "call_date": _normalize_date(call.get("date")),
            "duration_min": float(duration) if duration not in (None, "") else None,
            "participants": participants,
            "account": derive_account(participants, own),
            "summary": ((call.get("summary") or {}).get("overview") or "")[:4000],
            "synced_at": datetime.now(timezone.utc).isoformat(),
        })
    for i in range(0, len(rows), 200):
        client.table("call_index").upsert(
            rows[i:i + 200], on_conflict="company_id,provider,external_id"
        ).execute()
    # Stamped even when `rows` is EMPTY, and that is the entire point. An
    # incremental top-up that finds nothing new is a success; a full sync that
    # finds nothing means this company genuinely has no calls. Returning early
    # without stamping — the obvious-looking `if not rows: return 0` — is what
    # would leave a synced-and-empty company indistinguishable from a
    # never-synced one, which is the confidently-wrong answer this whole
    # freshness layer exists to prevent.
    _record_sync_success(company_id, len(rows), full=since is None)
    logger.info(
        "call-index: synced %s calls for %s (%s)",
        len(rows), company_id, "incremental" if since else "full",
    )
    return len(rows)


# ── sync state + freshness ───────────────────────────────────────────────────
#
# The index is only trustworthy if the reader can tell three states apart, and
# `call_index` alone cannot tell any of them from any other:
#
#   (a) this company has no calls            -> zero rows
#   (b) we have never synced this company    -> zero rows      <- same as (a)
#   (c) we synced 6h ago, a call happened    -> rows, newest one missing
#       since
#
# (b) read as (a) is the SILENT one: every interception returns None, the
# question degrades to the pre-index path, and nothing anywhere reports a
# problem. (c) is worse than silent — `answer_listing` states a COUNT, so a
# stale index produces a WRONG answer the user has no reason to doubt.
#
# `ensure_fresh` is therefore a PRECONDITION, not an optimisation: no answer
# path may speak until it has said what it knows.

# How long an index may go unrefreshed before a read triggers a top-up. The
# refresh is one metadata page (~1s measured) against a 168s path, so a short
# TTL is cheap; 15 min is short enough that "did we talk to them this morning"
# is right, long enough that a chatty session doesn't re-sync per turn.
_FRESH_TTL_S = 900
# Hard ceiling on an INLINE refresh inside a chat turn. On timeout we answer
# from what we hold, disclosed as of its real age, rather than hanging the turn
# behind a Fireflies outage. The daemon thread is left running — if it does
# finish, the next turn gets the benefit.
_INLINE_SYNC_TIMEOUT_S = 8.0
# How far BEFORE the last success an incremental top-up re-reads. Fireflies can
# surface a call after its nominal date (late processing, edited timestamps),
# so a strictly-after cursor would skip it forever. Upsert makes the overlap
# free.
_INCREMENTAL_OVERLAP = timedelta(days=1)


@dataclass(frozen=True)
class Freshness:
    """What the index can honestly claim about itself, right now."""

    #: Is a transcript source connected at all? False → the interceptions must
    #: not fire; there is nothing to index and no failure to report.
    connected: bool
    #: Last time a sync completed WITHOUT error. None → never successfully
    #: synced, so an empty index proves nothing.
    as_of: Optional[datetime] = None
    #: True when we could not bring the index up to date on this pass, so it
    #: may be missing recent calls. Answers must disclose their age.
    stale: bool = False
    #: Why the refresh failed, when it did. Surfaced, never swallowed.
    error: Optional[str] = None

    @property
    def usable(self) -> bool:
        """May an answer path read the index at all?

        Requires a connected source AND at least one successful sync. Without
        the second condition an empty read means "we don't know", not "there
        are none" — and answering "no calls" to "we don't know" is exactly the
        wrong answer this type exists to prevent.
        """
        return self.connected and self.as_of is not None

    def as_of_note(self, *, now: Optional[datetime] = None) -> str:
        """A disclosure clause for an answer built on data we could not refresh.

        Empty string when the index is current — no need to caveat a fresh
        answer, and a caveat on every answer trains the reader to ignore it.
        """
        if not self.stale or self.as_of is None:
            return ""
        now = now or datetime.now(timezone.utc)
        minutes = max(1, int((now - self.as_of).total_seconds() // 60))
        age = f"{minutes} min" if minutes < 60 else f"{minutes // 60}h"
        reason = f" ({self.error})" if self.error else ""
        return (
            f"\n\n_Note: this is the call list as of {age} ago — I could not "
            f"reach the transcript source just now{reason}, so a very recent "
            f"call may be missing._"
        )


def _sync_state(company_id: str) -> Optional[dict]:
    """The company's sync row, or None if it has never synced. Best-effort: a
    read failure is treated as "unknown", which routes to a refresh attempt
    rather than to a confident answer."""
    from app.db.client import require_client

    try:
        rows = (
            require_client().table("call_index_sync").select("*")
            .eq("company_id", company_id).eq("provider", PROVIDER_FIREFLIES)
            .limit(1).execute().data
        )
        return (rows or [None])[0]
    except Exception:  # noqa: BLE001
        logger.warning("call-index: sync-state read failed for %s", company_id,
                       exc_info=True)
        return None


def _write_sync_state(company_id: str, patch: dict) -> None:
    """Upsert the sync row. Never raises — a bookkeeping failure must not fail
    the sync that succeeded, nor mask the error of one that didn't."""
    from app.db.client import require_client

    row = {
        "company_id": company_id,
        "provider": PROVIDER_FIREFLIES,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **patch,
    }
    try:
        require_client().table("call_index_sync").upsert(
            row, on_conflict="company_id,provider"
        ).execute()
    except Exception:  # noqa: BLE001
        logger.warning("call-index: could not stamp sync state for %s",
                       company_id, exc_info=True)


def _record_sync_success(company_id: str, written: int, *, full: bool) -> None:
    """Stamp a completed sync and CLEAR any prior error.

    `call_count` is only written on a FULL sync: an incremental top-up that
    writes 0 rows means "nothing new", not "this company has no calls", and
    letting it overwrite the count would turn a healthy refresh into evidence
    of an empty account.
    """
    now = datetime.now(timezone.utc).isoformat()
    patch = {"last_sync_at": now, "last_success_at": now, "last_error": None}
    if full:
        patch["call_count"] = written
    _write_sync_state(company_id, patch)


def _record_sync_failure(company_id: str, exc: BaseException) -> None:
    """Stamp a failed sync. `last_success_at` is deliberately NOT touched, so a
    source that has been failing for a day reads as a day stale rather than as
    freshly synced — a sync that stamps freshness on failure is worse than one
    that never stamps at all."""
    _write_sync_state(
        company_id,
        {"last_sync_at": datetime.now(timezone.utc).isoformat(),
         "last_error": str(exc)[:500]},
    )


def _parse_ts(raw: Any) -> Optional[datetime]:
    """PostgREST timestamptz → aware datetime; None on anything unparseable."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _has_source(company_id: str) -> bool:
    """Does this company have a usable transcript source right now?

    Read live rather than inferred from the presence of index rows: a company
    that disconnected Fireflies still HAS rows, and answering from them while
    `connected_sources_line` tells the model Fireflies is not connected puts
    two contradictory claims in one answer.
    """
    try:
        from app.call_digest import _load_api_key

        return bool(_load_api_key(company_id))
    except Exception:  # noqa: BLE001 — a lookup failure is not a disconnect
        logger.warning("call-index: source lookup failed for %s", company_id,
                       exc_info=True)
        return True


# Per-company refresh locks, so several call questions arriving together
# produce one sync rather than one each. Same shape as auto_sync's
# _corpus_seed_locks.
_refresh_locks: dict[str, threading.Lock] = {}
_refresh_locks_guard = threading.Lock()


def _refresh_lock(company_id: str) -> threading.Lock:
    with _refresh_locks_guard:
        lock = _refresh_locks.get(company_id)
        if lock is None:
            lock = threading.Lock()
            _refresh_locks[company_id] = lock
        return lock


def ensure_fresh(
    company_id: str,
    *,
    ttl_s: float = _FRESH_TTL_S,
    timeout_s: float = _INLINE_SYNC_TIMEOUT_S,
    now: Optional[datetime] = None,
) -> Freshness:
    """Bring the index up to date if it needs it, and report what it can claim.

    Every answer path calls this BEFORE reading. The hot path is one indexed
    row read: a company synced within the TTL returns immediately without
    touching the connection table or the network.
    """
    now = now or datetime.now(timezone.utc)
    state = _sync_state(company_id)
    last_success = _parse_ts((state or {}).get("last_success_at"))

    # Fresh enough — the common case, and the only one that costs a single read.
    if last_success is not None and (now - last_success).total_seconds() < ttl_s:
        return Freshness(connected=True, as_of=last_success)

    if not _has_source(company_id):
        return Freshness(connected=False)

    # Incremental when we have a prior success to anchor on, full otherwise.
    since = (last_success - _INCREMENTAL_OVERLAP) if last_success else None
    outcome: dict = {}

    def _refresh() -> None:
        try:
            # Serialized per company: a burst of call questions in one session
            # would otherwise fire a sync each, all fetching the same page. The
            # queued caller re-reads state below and usually finds it already
            # fresh.
            with _refresh_lock(company_id):
                written = sync_company(company_id, since=since)
            if written is None:
                # No connected source — NOT a successful empty sync. See
                # sync_company's return contract.
                outcome["no_source"] = True
            else:
                outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 — already stamped by sync_company
            outcome["error"] = str(exc)[:200]

    worker = threading.Thread(
        target=_refresh, name="call-index-refresh", daemon=True
    )
    worker.start()
    worker.join(timeout_s)

    if outcome.get("no_source"):
        # The source vanished between `_has_source` and the sync (disconnected
        # mid-question, or `_has_source` failed open on a lookup error). Report
        # it as disconnected rather than as an empty index.
        return Freshness(connected=False)
    if outcome.get("ok"):
        return Freshness(connected=True, as_of=datetime.now(timezone.utc))

    # Timed out or failed. Report what we actually hold — including the case
    # where we hold nothing, which `usable` turns into "do not answer from the
    # index" rather than into "this company has no calls".
    error = outcome.get("error") or (
        "the transcript source did not respond in time" if worker.is_alive() else None
    )
    logger.info("call-index: refresh incomplete for %s (%s)", company_id, error)
    return Freshness(
        connected=True, as_of=last_success, stale=True, error=error,
    )


def clear_company(company_id: str) -> None:
    """Drop this company's index and its sync state.

    Called on DISCONNECT. Leaving the rows behind is not harmless: chat would
    keep answering from indexed calls while `prompts.connected_sources_line`
    correctly reports that no transcript source is connected — two
    contradictory claims inside one answer, and the user has no way to tell
    which half is wrong. Best-effort; a failure here is logged, and
    `ensure_fresh` independently refuses to answer once the source is gone.
    """
    from app.db.client import require_client

    client = require_client()
    for table in ("call_index", "call_index_sync"):
        try:
            client.table(table).delete().eq("company_id", company_id).execute()
        except Exception:  # noqa: BLE001
            logger.warning("call-index: could not clear %s for %s", table,
                           company_id, exc_info=True)


def list_calls(
    company_id: str,
    *,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 50,
) -> list[IndexedCall]:
    """Calls for a company, newest first, optionally within a window.

    A pure DB read — the whole point. No source API call, no model.
    """
    from app.db.client import require_client

    query = (
        require_client().table("call_index").select("*")
        .eq("company_id", company_id)
        .order("call_date", desc=True)
        .limit(limit)
    )
    if since is not None:
        query = query.gte("call_date", since.isoformat())
    if until is not None:
        query = query.lte("call_date", until.isoformat())
    return [
        IndexedCall(
            external_id=row["external_id"],
            title=row.get("title") or "",
            call_date=row.get("call_date"),
            duration_min=row.get("duration_min"),
            participants=row.get("participants") or [],
            account=row.get("account"),
            summary=row.get("summary") or "",
        )
        for row in (query.execute().data or [])
    ]


def has_index(company_id: str) -> bool:
    """True when this company has any indexed calls — the gate a caller checks
    before preferring the index over the full-corpus path."""
    from app.db.client import require_client

    rows = (
        require_client().table("call_index").select("id")
        .eq("company_id", company_id).limit(1).execute().data
    )
    return bool(rows)


# ── listing intent ───────────────────────────────────────────────────────────
#
# Questions the index answers OUTRIGHT — "what calls were there", "the 5 latest
# transcripts", "who did we talk to last week". These want the LIST, not a
# synthesis, so paying 168 s and $0.23 to have a model re-derive a list we
# already hold in a table would be pure waste.
#
# Deliberately narrower than `call_digest.is_call_digest`: a summarize/recap
# verb means the caller wants the analysis, and that still takes the full path.
_LISTING_VERB = (
    r"(?:list|show|give\s+me|get|fetch|pull\s+up|what|which|how\s+many|who)"
)
_CALL_NOUN = (
    r"(?:calls?|meetings?|transcripts?|recordings?|conversations?|"
    r"stand-?ups?|qbrs?|check-?ins?|syncs?|interviews?)"
)
# "summarize the last 5 calls" is a synthesis ask despite naming a count.
_SYNTHESIS_VERB = re.compile(
    r"\b(?:summari[sz]e|summary|recap|digest|themes?|takeaways?|insights?|"
    r"what\s+did\s+we\s+(?:hear|learn)|analy[sz]e|sentiment)\b",
    re.I,
)
_LISTING_RULE = re.compile(
    rf"\b{_LISTING_VERB}\b.{{0,40}}\b{_CALL_NOUN}\b"
    # "who did we talk to this week", "who have we spoken with" — a listing ask
    # with no call-noun at all. The participants are in the index, so this is
    # answerable for free; without this branch it reaches the full digest.
    r"|\bwho\s+(?:did|have|has|do)\s+we\s+(?:talk|spok|speak|meet|met)\w*\b",
    re.I,
)

# An explicit count, in either order: "the 5 latest" and "the latest 5" are the
# same request. Matching only one order silently returned the whole window.
_COUNT_RULE = re.compile(
    r"\b(?:latest|last|recent|top|first)\s+(\d{1,2})\b"
    r"|\b(\d{1,2})\s+(?:latest|last|recent|most\s+recent)\b",
    re.I,
)


def is_listing_request(question: str) -> bool:
    """True when the question wants the LIST of calls, not an analysis of them.

    This is the fix for "give me the 5 latest transcripts for customer
    conversations", which matched no existing rule and so fell through to the
    KG — where it correctly but unhelpfully reported that raw transcripts were
    not available, while the index holds exactly what was asked for.
    """
    text = question or ""
    if _SYNTHESIS_VERB.search(text):
        return False
    return bool(_LISTING_RULE.search(text))


def answer_listing(
    company_id: str, question: str, window=None, fresh: Optional[Freshness] = None,
) -> Optional[dict]:
    """Answer a listing question straight from the index, or None to fall
    through. Standard Ask payload shape.

    `fresh` is the caller's `ensure_fresh` result. It is REQUIRED in substance
    even though it is optional in signature: without it this function cannot
    tell an empty index from an unsynced one, and would answer "no calls" to a
    question it has no data to answer at all. Callers that omit it get one
    computed here rather than a silently unguarded read.
    """
    fresh = fresh if fresh is not None else ensure_fresh(company_id)
    if not fresh.usable:
        return None

    since = getattr(window, "since", None)
    until = getattr(window, "until", None)
    calls = list_calls(company_id, since=since, until=until)
    if not calls:
        # A successful sync with no matching calls is a FACT, and saying so
        # beats falling through to a path that will spend 168s rediscovering
        # it — or worse, answer from the KG's distilled summaries and imply
        # coverage we don't have. Only reachable when `fresh.usable`, i.e. a
        # sync really did complete.
        where = "in that window" if since else "recorded at all"
        return {
            "answer": (
                f"No calls {where}. Your transcript source is connected and I "
                f"checked it — there simply aren't any." + fresh.as_of_note()
            ),
            "key_points": [], "citations": [], "confidence": 1.0,
            "unanswered": "", "_skill": None, "_skill_source": "call-index",
        }

    # Honour an explicit small count ("the 5 latest", "the latest 5") so the
    # answer matches the question rather than dumping the whole window.
    match = _COUNT_RULE.search(question or "")
    if match:
        wanted = int(match.group(1) or match.group(2))
        if wanted > 0:
            calls = calls[:wanted]

    lines = "\n".join(f"- {call.render()}" for call in calls)
    scope = " in that window" if since else ""
    # A bare count is a completeness claim. Only make it when the index is
    # actually current — otherwise state the list and disclose its age, the
    # same rule `render_transcript` applies to an elided transcript.
    answer = (
        f"**{len(calls)} call{'s' if len(calls) != 1 else ''}**{scope}, newest first:\n\n"
        f"{lines}\n\n"
        "Ask me to summarize any one of these, or all of them, and I'll pull the "
        "full transcript."
        + fresh.as_of_note()
    )
    return {
        "answer": answer,
        "key_points": [],
        "citations": [],
        "confidence": 1.0,
        "unanswered": "",
        "_skill": None,
        "_skill_source": "call-index",
    }


# ── single-call resolution and fetch ─────────────────────────────────────────
#
# The expensive path fetches EVERY call in a window. But "summarize the Mayer
# Brown call" names ONE, and the index already holds its external_id — so we can
# fetch that single transcript instead of 22. Measured contrast (Chaostrack):
# the full path is ~168s / $0.23; one call is a fraction of that.
#
# The failure this replaces is worse than slow. Before the index, that question
# fell to the KG and answered "you'd need to connect the recording or transcript
# directly (e.g. via Fireflies)" — while Fireflies was connected and working. A
# wrong answer that blames the user's setup is the thing to fix first.

# Fireflies exposes a single transcript by id. Only this one carries sentences.
_TRANSCRIPT_QUERY = """
query Transcript($id: String!) {
  transcript(id: $id) {
    id
    title
    date
    duration
    participants
    summary { overview action_items keywords }
    sentences { speaker_name text }
  }
}
"""

# Words that describe the ASK rather than the call, stripped before matching so
# "summarize the mayer brown call" is matched on "mayer brown".
#
# The second block is the polite/verb-particle wrapping a request carries. It
# was missing, and "can" is why: three characters, survives the strip, and sits
# inside "candidate" — see _MIN_SUBSTRING_TERM.
_ASK_WORDS = frozenset({
    "summarize", "summarise", "summary", "recap", "tell", "me", "about", "the",
    "a", "an", "of", "for", "on", "in", "with", "call", "calls", "meeting",
    "meetings", "transcript", "transcripts", "conversation", "conversations",
    "what", "was", "were", "did", "we", "discuss", "discussed", "happened",
    "give", "show", "get", "please", "and", "from", "our", "their", "this",
    "that", "it", "recording", "session", "sync", "notes", "detail", "details",
    # Request wrapping and the particles of the verbs in _SINGLE_SUMMARY_VERB
    # ("walk me through", "dig into"), which otherwise survive as fake names.
    "can", "could", "would", "will", "you", "your", "let", "lets", "want",
    "need", "walk", "through", "dig", "into", "pull", "up", "over", "run",
    # Call-type nouns. _SELECTION_STOPWORDS' comment already describes these as
    # dropped here ("sync", "demo", "meeting") — "demo" and "check" simply never
    # were. Among hundreds of calls a call-type noun names nothing; narrowing
    # BETWEEN candidates still keeps them, which is that stopword set's job.
    "demo", "demos", "check", "checkin", "standup", "huddle", "chat",
})

# Words that describe a call GENERICALLY — recency, who was on it in the
# abstract, when it happened — and so can never be the name of one.
#
# This is the same stripping mechanism as _ASK_WORDS, split out only because the
# reason differs: an ask-word describes the request, a generic word describes the
# call but identifies no particular one. Both must go before we ask "did this
# question name anything?", because a question whose only surviving words are
# "recent" and "customer" has named nothing at all — it is a digest ask, and
# every one of these words is a qualifier the digest and listing paths already
# understand.
_GENERIC_CALL_WORDS = frozenset({
    # recency / quantity
    "recent", "recently", "latest", "last", "past", "previous", "prior",
    "few", "couple", "several", "some", "any", "all", "every", "each",
    "more", "most", "other", "another", "next", "upcoming", "new",
    # who, in the abstract
    "customer", "customers", "client", "clients", "user", "users",
    "prospect", "prospects", "buyer", "buyers", "people", "folks",
    "everyone", "anyone", "someone", "team", "teams", "them", "they",
    # kind of call, in the abstract
    "sales", "discovery", "support", "success", "onboarding", "internal",
    "external", "weekly", "daily", "monthly",
    # when
    "today", "yesterday", "day", "days", "week", "weeks", "month", "months",
    "quarter", "quarters", "year", "years", "morning", "afternoon",
    "evening", "night", "ago",
})

# A verb that means the caller wants THIS call's content, not a list.
_SINGLE_SUMMARY_VERB = re.compile(
    r"\b(?:summari[sz]e|summary|recap|tell\s+me\s+about|what\s+(?:was|did|happened)|"
    r"details?\s+(?:of|on|about)|dig\s+into|walk\s+me\s+through)\b",
    re.I,
)


def _norm(text: str) -> str:
    """Lowercase alphanumerics only — so 'Mayer Brown', 'mayerbrown' and
    'Mayer-Brown' all compare equal. The index stores a squashed account
    ('Mayerbrown') while the title carries the spaced form."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _query_terms(question: str) -> list[str]:
    """The words that plausibly NAME a call — ask-words and generic call words
    removed.

    An empty result is the signal that the question named nothing: "can you
    summarize our recent customer calls" leaves nothing behind, which is exactly
    right, because it names no call.
    """
    words = re.findall(r"[A-Za-z0-9]+", question or "")
    return [
        w for w in words
        if len(w) > 2
        and w.lower() not in _ASK_WORDS
        and w.lower() not in _GENERIC_CALL_WORDS
    ]


# A date the user typed, which names a call as surely as an account does. Same
# form `select_from_candidates` already accepts when narrowing a disambiguation.
_DATE_REFERENCE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

# Minimum length before a term is trusted as a MID-WORD match. A whole-word hit
# is trusted at any length — "BBVA", "IBM" and "SSO" are real accounts — so this
# floor only applies to substrings.
#
# It exists because "can" is three characters and sits inside "candidate". With
# unrestricted substring matching, "can you summarize our recent customer calls"
# scored an internal SE-candidate interview as a named match, resolved to that
# one call, and summarized it — a plural, general question answered from a
# single wrong transcript. Same class as the failures _LISTING_RULE and
# _NOT_CALLS exist for: routed from the vocabulary, answered from the wrong
# source.
_MIN_SUBSTRING_TERM = 4


def _call_words(call: IndexedCall) -> set[str]:
    """A call's account and title as individual normalized words, for whole-word
    matching. 'Mayer Brown + ChaosTrack Briefing' with account 'Mayerbrown'
    yields {mayerbrown, mayer, brown, chaostrack, briefing} — so either spelling
    of the account matches on a whole word rather than on a lucky substring."""
    return {
        _norm(w)
        for w in re.findall(r"[A-Za-z0-9]+", f"{call.account or ''} {call.title or ''}")
    } - {""}


def resolve_calls(company_id: str, question: str, *, limit: int = 200) -> list[IndexedCall]:
    """Indexed calls this question plausibly names, best first.

    Matches the question's naming words against each call's account and title,
    preferring WHOLE-WORD hits and admitting a substring only when the term is
    long enough to be distinctive. Returns [] when nothing matches, so the caller
    falls through rather than summarizing an arbitrary call.
    """
    terms = _query_terms(question)
    date_match = _DATE_REFERENCE.search(question or "")
    on_date = date_match.group(1) if date_match else None
    if not terms and not on_date:
        return []
    joined = _norm("".join(terms))
    scored: list[tuple[int, IndexedCall]] = []
    for call in list_calls(company_id, limit=limit):
        haystack = _norm(f"{call.account or ''}{call.title or ''}")
        words = _call_words(call)
        score = 0
        # A date the user named is a reference to that day's call(s).
        if on_date and (call.call_date or "").startswith(on_date):
            score += 5
        if haystack:
            # Whole-phrase hit ("mayerbrown") is the strongest signal.
            if len(joined) >= _MIN_SUBSTRING_TERM and joined in haystack:
                score += 10
            for term in terms:
                token = _norm(term)
                if not token:
                    continue
                if token in words:
                    score += 3          # whole word — trusted at any length
                elif len(token) >= _MIN_SUBSTRING_TERM and token in haystack:
                    score += 1          # mid-word, and only if distinctive
        if score:
            scored.append((score, call))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [call for _, call in scored]


def is_single_call_request(question: str, history=None) -> bool:
    """True when the message concerns ONE (or a few) named calls, not a window.

    Two shapes qualify:

      * a named ask — "summarize the Mayer Brown call"
      * a REPLY to our own "which one?" — "both", "the first one", a date. Such
        a reply carries no call name and no summary verb, so it matches nothing
        on its own words; only the pending disambiguation in `history` makes it
        meaningful. Without this the disambiguation was a dead end and "both"
        fell through to the KG.

    A summary verb ALONE is not enough, and that was the bug: "can you summarize
    our recent customer calls" is a plural, general ask naming no call, and this
    claimed it, resolved it to exactly one call, and summarized an internal
    hiring interview as though it were the customer call asked about. A general
    ask belongs to the listing or digest path, which answer over the whole
    window instead of picking one member of it.
    """
    text = question or ""
    if _prior_disambiguation(history):
        return True
    if not _SINGLE_SUMMARY_VERB.search(text):
        return False
    # A window word means they want the digest, not one call.
    if re.search(r"\b(?:last|this|past)\s+(?:week|month|quarter)\b|\ball\b", text, re.I):
        return False
    # Something must NAME a call: an account or a distinctive title term (what
    # survives _query_terms), or a date. "our recent customer calls" survives
    # none of it — every word is a generic qualifier — and so stands down.
    return bool(_query_terms(text)) or bool(_DATE_REFERENCE.search(text))


def fetch_transcript(company_id: str, external_id: str) -> Optional[dict]:
    """Fetch ONE transcript, with sentences, from the source."""
    from app.call_digest import _load_api_key
    from app.kg_ingest.pullers.fireflies import URL, _TIMEOUT

    import requests

    api_key = _load_api_key(company_id)
    if not api_key:
        return None
    response = requests.post(
        URL,
        json={"query": _TRANSCRIPT_QUERY, "variables": {"id": external_id}},
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        logger.warning("call-index: transcript fetch failed: %s", body["errors"][:1])
        return None
    return (body.get("data") or {}).get("transcript")


# Budget for ONE rendered transcript, in characters (~4 chars/token). The
# longest call in the Chaostrack corpus is 880 sentences / ~58k chars / ~14.5k
# tokens, so at 240k chars (~60k tokens) this never bites for a single call —
# which is the point. It exists only as a backstop against a pathological
# multi-hour recording.
#
# The previous cap was 600 SENTENCES, and it was wrong in the way that matters:
# it bit on 3 of 10 recent calls (609, 651, 880 sentences), it was SILENT, and
# it truncated the TAIL — the end of a call, which is exactly where next steps,
# pricing agreements and commitments land. The Mayer Brown summary was produced
# from a transcript missing its last 9 sentences, and nothing in the answer said
# so. A summary that quietly omits the close of a customer call is worse than a
# slow one.
_TRANSCRIPT_CHAR_BUDGET = 240_000
# When the budget IS exceeded, keep the opening (context, who is on the call)
# and the closing (decisions, next steps) and elide the middle — rather than
# lopping off the end.
_HEAD_SHARE = 0.4


def render_transcript(raw: dict, *, max_chars: int = _TRANSCRIPT_CHAR_BUDGET) -> str:
    """A transcript as prompt text, complete unless it is genuinely enormous.

    If the budget is exceeded, the middle is elided rather than the tail, and an
    explicit marker is left in the text so the MODEL knows the transcript is
    partial and can say so. Silent truncation is what turns a summary into a
    confident half-answer.
    """
    sentences = raw.get("sentences") or []
    rendered = [
        f"  {s.get('speaker_name') or '?'}: {s.get('text') or ''}"
        for s in sentences
    ]
    total = sum(len(line) + 1 for line in rendered)

    body: list[str]
    if total <= max_chars:
        body = rendered
        note = ""
    else:
        head_budget = int(max_chars * _HEAD_SHARE)
        tail_budget = max_chars - head_budget
        head: list[str] = []
        used = 0
        for line in rendered:
            if used + len(line) > head_budget:
                break
            head.append(line)
            used += len(line) + 1
        tail: list[str] = []
        used = 0
        for line in reversed(rendered):
            if used + len(line) > tail_budget:
                break
            tail.insert(0, line)
            used += len(line) + 1
        dropped = len(rendered) - len(head) - len(tail)
        body = head + [
            f"  [... {dropped} sentences from the middle of this call omitted "
            f"to fit the context budget — the opening and closing are complete, "
            f"the middle is NOT. Say so if asked about anything that would have "
            f"fallen in the omitted stretch ...]"
        ] + tail
        note = (
            f"\nNOTE: this transcript is PARTIAL — {dropped} of {len(rendered)} "
            f"sentences from the middle were omitted. Do not claim completeness."
        )

    lines = [f"CALL: {raw.get('title') or '(untitled)'}"]
    overview = ((raw.get("summary") or {}).get("overview") or "").strip()
    if overview:
        lines.append(f"SOURCE SUMMARY: {overview}")
    if note:
        lines.append(note)
    lines.append("TRANSCRIPT:")
    lines.extend(body)
    return "\n".join(lines)


_SINGLE_CALL_SYSTEM = (
    "You are summarizing ONE customer call for a product manager. You are given "
    "the call's metadata and its transcript.\n\n"
    "Lead with what a PM would act on: what the customer wants, what is "
    "blocking them, commitments made, and open questions. Quote the customer "
    "verbatim where a quote carries more than a paraphrase would.\n\n"
    "Ground every claim in the transcript. If something was not discussed, say "
    "so rather than inferring it — a confident invention about a real customer "
    "conversation is worse than an admission of absence."
)


# A stable marker in the disambiguation answer, so a later turn can recognise
# that the assistant asked "which one?" and consume the reply. Without this the
# disambiguation is a dead end: it poses a question it cannot read the answer
# to, and "both" falls through to the KG — which then reports the transcripts
# are unavailable, while they are one fetch away.
_DISAMBIGUATION_MARKER = "calls that could match. Which one?"

# How many transcripts one answer may fetch. "both" is common and cheap; "all"
# over a large match set is the full-corpus cost by another name.
_MAX_CALLS_PER_ANSWER = 3

_ALL_REPLY = re.compile(r"^\s*(?:both|all|all\s+of\s+them|every|each)\b", re.I)

# Only true function words are stripped when narrowing between candidates —
# unlike _ASK_WORDS, which also drops call-type nouns.
_SELECTION_STOPWORDS = frozenset({
    "the", "that", "this", "one", "ones", "please", "and", "for", "with",
    "about", "give", "show", "summarize", "summarise", "summary", "call",
    "calls",
})
# NB "one" is deliberately absent. In a reply it almost always means "item"
# ("that one", "which one", "the Genworth one") rather than "the first" — with
# it mapped to 0, "the Genworth one" silently selected the FIRST candidate
# instead of falling through to the narrowing branch. The digit "1" is
# unambiguous and stays.
_ORDINALS = {
    "first": 0, "1st": 0, "1": 0,
    "second": 1, "2nd": 1, "two": 1, "2": 1,
    "third": 2, "3rd": 2, "three": 2, "3": 2,
    "last": -1, "latest": 0, "newest": 0, "oldest": -1,
}


def _prior_disambiguation(history) -> Optional[str]:
    """The question that triggered a "which one?" reply, if the last assistant
    turn was one. Returns None otherwise."""
    turns = list(history or [])
    for i in range(len(turns) - 1, -1, -1):
        turn = turns[i]
        if (turn.get("role") or "user") != "assistant":
            continue
        if _DISAMBIGUATION_MARKER not in (turn.get("content") or ""):
            return None
        # The user turn immediately before it is the original ask.
        for j in range(i - 1, -1, -1):
            if (turns[j].get("role") or "user") == "user":
                return turns[j].get("content") or None
        return None
    return None


def select_from_candidates(reply: str, candidates: list[IndexedCall]) -> list[IndexedCall]:
    """Apply a user's reply to a disambiguation list.

    Handles "both"/"all", ordinals ("the first one", "2"), and any distinctive
    token from the rendered options (a date, an account, a title word).
    """
    text = (reply or "").strip()
    if _ALL_REPLY.match(text):
        return candidates[:_MAX_CALLS_PER_ANSWER]

    # A date the user copied from the options.
    date_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if date_match:
        picked = [c for c in candidates if (c.call_date or "").startswith(date_match.group(1))]
        if picked:
            return picked[:1]

    # An ordinal, but only when the message is short enough to BE a selection —
    # "the first one" selects; "what did the first customer say about pricing"
    # is a new question that happens to contain "first".
    if len(text.split()) <= 4:
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            if word in _ORDINALS:
                index = _ORDINALS[word]
                if -len(candidates) <= index < len(candidates):
                    return [candidates[index]]

    # Otherwise treat the reply as a narrowing term over the candidates only.
    #
    # NOTE this uses a LOOSER tokenizer than _query_terms. That one strips
    # call-type nouns ("sync", "demo", "meeting") because when identifying a
    # call among hundreds they are pure noise. Here we are narrowing between
    # 2-6 candidates whose titles differ by exactly those words — "the sync
    # one" vs "the briefing one" — so stripping them threw away the only
    # discriminating term and returned nothing.
    terms = [
        _norm(w) for w in re.findall(r"[A-Za-z0-9]+", text)
        if len(w) > 2 and w.lower() not in _SELECTION_STOPWORDS
    ]
    if terms:
        narrowed = [
            c for c in candidates
            if any(term and term in _norm(f"{c.account or ''}{c.title or ''}")
                   for term in terms)
        ]
        if narrowed:
            return narrowed[:_MAX_CALLS_PER_ANSWER]
    return []


def _summarize_calls(company_id: str, question: str, calls: list[IndexedCall]) -> Optional[dict]:
    """Fetch each named call's transcript and summarize. One model call over
    however many transcripts were selected — still far below the whole window."""
    from app.graph.gateway import llm_call

    blocks: list[str] = []
    used: list[IndexedCall] = []
    for call in calls[:_MAX_CALLS_PER_ANSWER]:
        raw = fetch_transcript(company_id, call.external_id)
        if raw and (raw.get("sentences") or []):
            blocks.append(render_transcript(raw))
            used.append(call)
    if not blocks:
        return None

    joined = "\n\n=====\n\n".join(blocks)
    plural = len(used) > 1
    ask = (
        "Summarize EACH call separately under its own heading, then add a short "
        "'Across both calls' section for threads that span them. Do not merge "
        "them into one narrative — they are distinct conversations.\n\n"
        if plural else ""
    )
    result = llm_call(
        enterprise_id=company_id,
        agent="call-index",
        purpose="single_call_summary" if not plural else "multi_call_summary",
        system=_SINGLE_CALL_SYSTEM,
        input=f"{ask}{joined}\n\nQuestion: {question}",
        prompt_version="call-index-single-v1",
        max_tokens=6000 if plural else 4000,
    )
    body = result.output if isinstance(result.output, str) else str(result.output)
    if plural:
        header = f"**{len(used)} calls** · " + ", ".join(
            f"{(c.account or c.title or '')[:24]} ({(c.call_date or '')[:10]})" for c in used
        )
    else:
        call = used[0]
        header = f"**{call.title or 'Call'}** · {(call.call_date or '')[:10]}"
        if call.account:
            header += f" · {call.account}"
    return {
        "answer": f"{header}\n\n{body}",
        "key_points": [], "citations": [], "confidence": 0.9,
        "unanswered": "", "_skill": None,
        "_skill_source": "call-index-single" if not plural else "call-index-multi",
    }


def answer_single_call(
    company_id: str, question: str, *, history=None,
    fresh: Optional[Freshness] = None,
) -> Optional[dict]:
    """Answer a question about one (or a few) named calls, or None to fall
    through.

    Resolves the reference against the index and fetches only those transcripts
    instead of every call in the window. Two entry paths:

      * a named ask — "summarize the Mayer Brown call"
      * a REPLY to a previous "which one?" — "both", "the first one", a date.
        Without this second path the disambiguation was a dead end.

    Falling through on an unsynced index is correct here — unlike a listing,
    "I couldn't find that call" and "I haven't looked" both end up at the same
    downstream path, and the caller's grounding tells the model what IS
    connected. What must not happen is resolving against a half-built index and
    confidently summarizing the wrong call, which the freshness gate prevents.
    """
    fresh = fresh if fresh is not None else ensure_fresh(company_id)
    if not fresh.usable:
        return None

    # A reply to our own disambiguation. Re-resolve the ORIGINAL question to
    # rebuild the same candidate list, then apply the selection.
    original = _prior_disambiguation(history)
    if original:
        candidates = resolve_calls(company_id, original)
        chosen = select_from_candidates(question, candidates)
        if chosen:
            return _summarize_calls(company_id, original, chosen)

    matches = resolve_calls(company_id, question)
    if not matches:
        return None

    # Ambiguous: several calls fit. Ask, from the index, at zero model cost —
    # cheaper AND more useful than picking one and being wrong.
    if 1 < len(matches) <= 6 and _norm(matches[0].account or "") not in _norm(question):
        lines = "\n".join(f"- {call.render()}" for call in matches)
        return {
            "answer": (
                f"I found {len(matches)} {_DISAMBIGUATION_MARKER}\n\n{lines}\n\n"
                "Reply with one of them, or \"both\" for all of them."
            ),
            "key_points": [], "citations": [], "confidence": 0.6,
            "unanswered": "", "_skill": None,
            "_skill_source": "call-index-disambiguate",
        }

    return _summarize_calls(company_id, question, matches[:1])


# ── index-driven routing ─────────────────────────────────────────────────────
#
# The interceptions above are keyword gates, and keyword gates keep losing. Two
# real failures, both asked on 2026-08-02:
#
#   "give me top 3 product requests from last week"
#   "give me top 3 product requests from last week's customer conversations"
#
# Neither carries a digest VERB (summarize/recap/themes), so is_call_digest was
# False even after "conversations" was added to its noun list. Both fell to the
# generic path, were answered from an uploaded SIMULATED csv covering Jan 1-10,
# and — after temporal grounding landed — confidently asserted "No connected
# source covers the period you asked about". That claim was false: the index
# held real calls from Jul 29-31.
#
# So stop guessing from words and ask the DATA. If the question names a real
# window, and this company actually has indexed calls inside it, and the
# question is about the sort of thing customers say on calls, then the calls are
# the right source — whatever verb the user happened to use.
# Gating on a POSITIVE vocabulary is the mistake that produced these failures in
# the first place, and repeating it one level up ("requests|feedback|themes|...")
# would fail the same way: the set of words that mean "what customers said" is
# unbounded, and every miss silently answers from the wrong source.
#
# Invert it. The set of things that clearly are NOT calls is small, stable, and
# already has dedicated paths — tickets, code, deploys, uploaded spreadsheets.
# Everything else, when the company HAS calls in the named window, is better
# answered from the calls than from a lossy derivative of them.
# NB every noun takes an optional plural. Without it "\btickets\b" never matched
# "ticket\b", and "what tickets did we close last week" routed to the calls —
# caught by the control cases below, invisible otherwise.
#
# And every RELEASE word needs its VERB forms. This list originally held only
# the nouns, which is how "did the prototype ship last week?" — a yes/no
# question about a ship date — was claimed by this routing and answered with a
# full voice-of-customer digest over the week's calls: ~188 seconds and a
# multi-section report for a question whose answer is one word. "ship" and
# "prototype" were absent entirely, and "releases?"/"deploys?" matched the noun
# but never "released"/"deployed". Same lesson as the plural bug one line up: a
# miss in the NEGATIVE vocabulary silently answers from the wrong source too.
#
# The asymmetry that settles what belongs here: a word wrongly INCLUDED only
# demotes a question from "prefer the calls" back to normal routing, which still
# answers it from the KG. A word wrongly OMITTED spends ~3 minutes building a
# document from a source the question was never about. The costs are not
# comparable, so release vocabulary is included even though customers do discuss
# launches and shipping on calls.
#
# "cut" is deliberately NOT here despite being release slang ("cut a release"):
# "cut costs", "price cut" and "cut the feature" are all ordinary customer talk,
# and this list is only defensible while every word in it is unambiguous.
_NOT_CALLS = re.compile(
    r"\b(?:tickets?|issues?|epics?|sprints?|backlogs?|jira|linear|clickup|asana|"
    r"commits?|pull\s*requests?|prs?|repos?|branch(?:es)?|"
    r"ship(?:s|ped|ping|ment|ments)?|launch(?:es|ed|ing)?|"
    r"deploy(?:s|ed|ing|ment|ments)?|releas(?:e|es|ed|ing)|"
    r"roll(?:s|ed|ing)?[-\s]*outs?|prototypes?|"
    r"code|csvs?|spreadsheets?|excel|dashboards?|analytics|funnels?|"
    r"retention\s+curves?)\b",
    re.I,
)


def windowed_call_question(
    company_id: str, question: str, fresh: Optional[Freshness] = None,
):
    """The Window to answer from calls, or None to leave routing alone.

    Returns a window when all four hold:
      1. the index is USABLE — a source is connected and has synced at least
         once, so an empty read means "none" and not "we haven't looked",
      2. the question NAMES a window (not the 7-day fallback),
      3. it does not clearly belong to another source (tickets, code, uploaded
         tabular data — each of which has its own interception), and
      4. the index actually HAS calls in that window.

    (4) is what makes this safe ahead of the generic path: it cannot hijack a
    question for a company with no calls, or for a period genuinely uncovered —
    it returns None and the existing behaviour stands. And because the calls are
    what the KG is DERIVED from, preferring them when they exist is preferring
    primary evidence over a lossy summary of it.

    (1) is what keeps (4) meaningful. Without it an unsynced index reads as "no
    calls in that window" and the question routes to the generic path — the
    exact silent failure this routing was written to fix, reintroduced one
    level up.
    """
    from app.call_digest import parse_window

    if _NOT_CALLS.search(question or ""):
        return None
    try:
        window = parse_window(question)
    except Exception:  # noqa: BLE001 — routing must never break the answer
        return None
    if not getattr(window, "explicit", False):
        return None
    # Deliberately AFTER the cheap regex/parse gates: `ensure_fresh` may reach
    # the network, and a question that was never going to route here shouldn't
    # pay for a sync.
    fresh = fresh if fresh is not None else ensure_fresh(company_id)
    if not fresh.usable:
        return None
    if not list_calls(company_id, since=window.since, until=window.until, limit=1):
        return None
    return window
