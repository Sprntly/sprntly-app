"""Cross-connector sweep — ONE bounded, parallel gather across every connected
source, for a question that names none of them.

The named-source path (registry.answer_for_hints) already reads the tools a
question points at. This module serves the OTHER half of chat, and it is the
larger half: "where did the checkout redesign land?", "what do we know about
churn at Acme?". Those name no source, so nothing above the generic path claims
them, and the generic path answers from the legacy corpus plus the KG snapshot —
never from anything live. A company with Jira, Slack and Confluence all
connected got an answer that had read none of them.

WHY A SWEEP AND NOT A TOOL LOOP. The named path hands the model tools and lets
it decide what to call, iterating up to six times. That is right when the
question names one source and wrong here: the union of every connector's tools
is a large tool list, the model works through it SERIALLY, and the wall clock is
the product. Chat listing went 168s → 4s in the last batch precisely by refusing
to live-fetch what an index could answer; re-introducing a six-iteration
multi-source loop on the default path would undo that.

So this is a GATHER, not an agent: one deterministic keyword probe per source,
all of them in flight at once, one hard wall-clock budget across the whole fan-
out, and the assembled digest handed to the answer call that was going to happen
anyway. The cost is one extra bounded round of I/O — no extra LLM call.

Latency contract, in order of how much it saves:

- Nothing is swept unless the question yields real search terms. A greeting, a
  "make it shorter", a bare pronoun follow-up produce none, so they pay nothing.
- Two of the nine sources never touch the network: recorded calls come from
  `call_index` (the same index that made listing fast) and GitHub comes from the
  already-synced `github_pull_requests` rows. There is no live Fireflies leg for
  exactly this reason — the index is strictly faster and strictly fresher-bounded.
- Live legs run in parallel with ONE budget over all of them (`BUDGET_S`), not a
  budget each. A source still running when the budget expires is abandoned, not
  waited on, and is reported as unread.
- A dead, unauthorized or slow connector can only ever cost the budget. Its leg
  fails inside its own thread, is recorded as unread, and every other source
  still lands.

HONESTY. A partial sweep that reads as complete is worse than no sweep: "nothing
in Slack about it" must never mean "Slack timed out". Every source that was NOT
read is named in the rendered block with its reason, and the system addendum
(prompts.ASK_SYSTEM_LIVE_SWEEP_ADDENDUM) requires the model to say so.

TENANCY. Sessions are opened from `enterprise_id` alone, exactly as the named
path opens them — the model contributes search TERMS and nothing else. It cannot
name a tenant, an installation, a token or a repo here, because it is not in the
loop at all: the sweep runs before the model sees anything.

TWO SOURCES ARE DELIBERATELY NOT SWEPT:

- `google_drive` — its adapter builds the googleapiclient service with no
  deadline (a known open bug), so an abandoned Drive read holds a worker thread
  open indefinitely rather than merely missing the budget. It also needs no leg:
  `ask_runner.document_grounding` already puts connected Drive files in the
  prompt, and picking WHICH document to read is a separate surface.

  TRACED, not assumed, when the sweep grew to cover every connector: Drive
  files reach the answer through `document_grounding`'s Stage T — the catalog's
  fused lexical+semantic rank over every non-locally-indexed provider — and
  their BODIES resolve through `document_bodies.resolve_drive_body`, from the
  markdown `google_drive_sync` already wrote. Both stages cover Drive without
  the question naming it, so there is no gap between sweep and grounding for a
  Drive file to fall through. The two honest limits, neither of them a gap in
  this module: Drive is answered from SYNCED markdown, so a file added since
  the last sync is not there (the sweep's live legs would have seen it), and a
  Drive file with no catalog row is not ranked. Both belong to the sync, not to
  the fan-out. A `google_drive` test in test_cross_connector_sweep.py pins the
  no-leg decision so nobody adds a deadline-less one back.
- `fireflies` live — superseded by the `call_index` leg above.

MEETINGS ARE COVERED BY TWO DIFFERENT MECHANISMS, ON PURPOSE. Zoom and
Fireflies ride the `call_index` leg (a DB read — the index holds both). Google
Meet cannot: `call_index.CALL_PROVIDERS` is fireflies+zoom, Meet is in neither,
and a Meet conference record carries no title for an index to hold anyway. So
Meet is a LIVE leg that reads transcripts, and it is the only leg that carries
its own deadline — see connector_lookup/google_meet.py.
"""
from __future__ import annotations

import concurrent.futures as _futures
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

#: Wall-clock budget for the WHOLE fan-out, shared by every live leg. Not a
#: per-source timeout: three sources answering in 2s each cost 2s, not 6s, and
#: one source hanging costs the budget rather than its own 15s HTTP bound plus
#: everyone else's.
#:
#: 8s, chosen against measurement rather than taste. Five healthy sources probed
#: in parallel land in ~1.1s (the slowest one, not the sum), and the framework's
#: own per-HTTP-call bound is 15s — so this budget is generous for every healthy
#: source and strictly TIGHTER than the failure mode it exists to cap. What it
#: buys is the worst case: a dead connector adds 8s to one answer, once, and is
#: then reported as unread rather than waited on again.
BUDGET_S = 8.0

#: Chars kept from one source. Deliberately well under the named path's 10k:
#: seven of these ride the same prompt as the corpus, the KG bundle and the
#: uploaded-document block, and the sweep is context AROUND the answer, not the
#: answer.
PER_SOURCE_CHARS = 2_500

#: Ceiling on the assembled block. Sources are rendered in priority order and
#: the overflow is dropped and NAMED, never silently trimmed.
TOTAL_CHARS = 12_000

#: Most sources probed in one sweep. Every current leg fits under it; the cap
#: exists so adding connectors cannot quietly widen the fan-out.
#:
#: Ten, raised from eight when Asana and Google Meet gained live-read adapters:
#: 2 local legs (calls, github — DB reads, no network) + 7 live providers = 9,
#: with one slot of headroom. The arithmetic is asserted by a test rather than
#: kept in this comment, because the comment is what rots. It matters because
#: the cap counts BOTH kinds of leg, so at eight a company
#: with everything connected would have had two live legs truncated in
#: `LIVE_PROVIDERS` order — silently, since a provider dropped before the
#: fan-out never becomes a SourceResult and so is never named in the unread
#: list. Raising it is a deliberate widening and cheap in the dimension that
#: matters: the legs share ONE wall-clock budget, so two more of them cost two
#: more threads, not two more seconds. What still bounds the PROMPT is
#: TOTAL_CHARS, which drops whole low-priority sources and NAMES them.
MAX_SOURCES = 10

#: Terms carried into the probes. Past ~8 the queries stop narrowing and start
#: matching nothing at all (an AND-ish search over a whole sentence).
MAX_TERMS = 8

#: Terms a message must yield before ANY source is probed.
#:
#: Two, not one, and the second one is doing real work. A single surviving
#: content word is as often an instruction about the answer ("make it punchier")
#: or a bare acknowledgement as it is a topic, and the probe is far cheaper to
#: skip than to run and discard. The cost is honest and worth stating: a
#: one-noun question ("anything on Acme?") does not sweep. It still answers from
#: corpus + KG exactly as it did before, and naming a source or asking a fuller
#: question both reach the live read — so the failure mode is "no better than
#: yesterday", never "worse".
MIN_TERMS = 2

#: Live-readable providers this sweep probes, in render priority order. Each is
#: gated on an actual connection before anything is opened.
#:
#: Order is render priority and therefore also DROP priority when TOTAL_CHARS
#: runs out, so it is not alphabetical and not arbitrary. `google_meet` sits
#: last of the live legs deliberately: it is the only one whose probe must read
#: transcripts to match at all (a Meet conference carries no title — see
#: connector_lookup/google_meet.py), so it is both the slowest to answer and
#: the one whose full content is most cheaply re-reachable by naming it.
LIVE_PROVIDERS: tuple[str, ...] = (
    "jira", "clickup", "slack", "confluence", "hubspot", "asana", "google_meet",
)


# ── query terms ──────────────────────────────────────────────────────────────

#: Words that carry no topic. A sweep keyed on "what", "our" or "tell" searches
#: every source for nothing and pays full price for it. Kept deliberately
#: separate from call_index's `_ASK_WORDS`: that set is tuned for words that
#: NAME A CALL (it drops "meeting", "transcript", "customer"), which are exactly
#: the words a cross-source topic question is often about.
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "were", "been", "being", "have", "has",
    "had", "did", "does", "doing", "will", "would", "shall", "should", "can",
    "could", "may", "might", "must", "our", "ours", "your", "yours", "their",
    "them", "they", "this", "that", "these", "those", "what", "whats", "which",
    "who", "whom", "whose", "when", "where", "why", "how", "any", "all", "some",
    "there", "here", "with", "without", "from", "into", "about", "over",
    "under", "than", "then", "them", "you", "yourself", "ive", "weve", "were",
    "tell", "show", "give", "find", "get", "say", "said", "know", "think",
    "need", "want", "please", "help", "let", "make", "made", "look", "see",
    "going", "got", "just", "also", "more", "most", "much", "many", "very",
    "really", "actually", "currently", "now", "today", "yesterday", "week",
    "month", "year", "recent", "recently", "latest", "new", "old", "status",
    "update", "updates", "summary", "summarize", "summarise", "anything",
    "everything", "something", "nothing", "one", "two", "sprntly",
    # Words about the ANSWER rather than about the company. Without these,
    # "make it shorter and punchier" clears MIN_TERMS on two words that name
    # nothing, and every connector gets searched for "shorter punchier". These
    # turns are common in a working thread, so they would have been a large
    # share of all sweeps and a pure latency cost.
    "shorter", "longer", "punchier", "tighter", "simpler", "clearer", "formal",
    "casual", "concise", "expand", "shorten", "lengthen", "rewrite", "redo",
    "rephrase", "reword", "revise", "edit", "tweak", "again", "instead",
    "bullet", "bullets", "table", "paragraph", "sentence", "tone", "draft",
    "thanks", "thank", "thx", "cheers", "yes", "yeah", "yep", "nope", "okay",
    "sure", "perfect", "great", "nice", "sorry", "hello", "hey", "hi",
})


def sweep_terms(question: str, *, limit: int = MAX_TERMS) -> list[str]:
    """The topic words worth searching every source for.

    A result shorter than `MIN_TERMS` is the load-bearing case: it means the
    message named no topic worth a fan-out ("make it shorter", "thanks", "who
    said that?"), and `sweep` then probes nothing. That is the cheapest and most
    important latency gate on this path — most turns in a working thread are
    follow-ups and instructions, and none of them should cost a round of I/O.
    """
    seen: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9][A-Za-z0-9'&._-]*", question or ""):
        word = raw.strip("'._-")
        if len(word) < 3:
            continue
        if word.lower() in _STOPWORDS:
            continue
        if word.lower() in {w.lower() for w in seen}:
            continue
        seen.append(word)
        if len(seen) >= limit:
            break
    return seen


# ── results ──────────────────────────────────────────────────────────────────

#: A leg's outcome. `ok` is the only one whose text reaches the prompt as data;
#: every other value is reported to the model as a source it did NOT read.
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_DROPPED = "dropped"
#: The connection row exists but no session could be opened — an expired grant,
#: a revoked token. Kept apart from EMPTY on purpose: "connected but returned
#: nothing for these terms" and "we could not open it at all" license completely
#: different sentences in the answer, and collapsing them would have the model
#: report an unopened source as a searched one.
STATUS_UNAVAILABLE = "unavailable"
#: Skipped because opening it would have rotated the tenant's OAuth token.
#: A healthy connector we declined to touch — not a broken one.
STATUS_REFRESH_DUE = "refresh_due"


class _SessionUnavailable(Exception):
    """A leg's connector could not be opened. Carried as an exception so it
    crosses the worker-thread boundary distinctly from an empty result."""


class _RefreshWouldBeRequired(Exception):
    """Opening this session would rotate and persist the tenant's OAuth token,
    so the sweep declines. Distinct from _SessionUnavailable because the user-
    facing reason differs: the connector is healthy, we simply refuse to be the
    caller that mutates its credential."""


@dataclass
class SourceResult:
    """One source's contribution to one sweep."""

    key: str
    display_name: str
    status: str = STATUS_EMPTY
    text: str = ""
    detail: str = ""
    elapsed_ms: int = 0
    #: The structured rows behind `text`, when the adapter could build them
    #: without a new HTTP call (`base.RecordsCapable`). `None` — NOT `[]` — is
    #: "this leg cannot produce records for this call"; sweep_persist reads
    #: that as "fall back to hashing `text`" (see its module docstring). `[]`
    #: is reserved for "the adapter tried and there was genuinely nothing" and
    #: is not produced by any adapter today.
    records: "list[RawRecord] | None" = None

    @property
    def usable(self) -> bool:
        return self.status == STATUS_OK and bool(self.text.strip())

    def unread_reason(self) -> str:
        if self.status == STATUS_TIMEOUT:
            return "did not answer within the time budget"
        if self.status == STATUS_ERROR:
            return self.detail or "could not be read just now"
        if self.status == STATUS_REFRESH_DUE:
            return (
                "was NOT searched — its access token is due for renewal, and "
                "this cross-source sweep deliberately never renews one. Ask "
                "about this source directly and it will be read live"
            )
        if self.status == STATUS_UNAVAILABLE:
            return (
                "is connected but could not be opened — it was NOT searched, and "
                "its access may need refreshing"
            )
        if self.status == STATUS_DROPPED:
            return "read, but dropped from this prompt for length"
        return self.detail or "returned nothing for these terms"


@dataclass
class SweepResult:
    """Everything one sweep learned, plus everything it failed to learn."""

    terms: list[str] = field(default_factory=list)
    sources: list[SourceResult] = field(default_factory=list)
    elapsed_ms: int = 0
    budget_exceeded: bool = False

    @property
    def read(self) -> list[SourceResult]:
        return [s for s in self.sources if s.usable]

    @property
    def unread(self) -> list[SourceResult]:
        return [s for s in self.sources if not s.usable]

    def render(self) -> str:
        """The prompt block, or "" when no source produced anything usable.

        Returning "" when nothing was read is deliberate: a block that says only
        "I checked five sources and found nothing" invites the model to assert
        the absence, and absence-of-evidence from a keyword probe is not
        evidence-of-absence. The unread list is only worth stating ALONGSIDE
        something that WAS read.
        """
        usable = self.read
        if not usable:
            return ""
        head = (
            "LIVE CROSS-SOURCE SWEEP — read from the connected sources just "
            "now, searched for: " + ", ".join(self.terms)
        )
        parts = [head]
        for source in usable:
            parts.append(f"\n### {source.display_name}\n{source.text.strip()}")
        missed = self.unread
        if missed:
            parts.append(
                "\n### Sources NOT covered by this sweep\n"
                + "\n".join(
                    f"- {s.display_name}: {s.unread_reason()}" for s in missed
                )
            )
        return "\n".join(parts)

    def covered_providers(self) -> set[str]:
        """PROVIDER keys this sweep actually read something for.

        Not the display names: those carry qualifiers ("HubSpot (deals)") and a
        local leg answers under what it reads ("calls") rather than under the
        provider that feeds it ("fireflies"). A caller deciding whether a source
        still belongs in its "did not check this" list must match on the stable
        key, or it will apologise for a source it is holding results from.
        """
        keys = {s.key for s in self.read}
        covered = {k for k in keys if k in _LIVE_LEGS}
        for provider, leg in _LOCAL_LEG_FOR_PROVIDER.items():
            if leg in keys:
                covered.add(provider)
        return covered

    def outcome_summary(self) -> str:
        """`jira=ok slack=timeout calls=empty` — statuses only, never the terms
        and never the content.

        This is the whole observability story for the sweep, and it needs to be,
        because the two failure modes look identical from outside: a sweep that
        found nothing and a sweep that never ran (flag off, terms below the
        floor, every source timing out) both produce an answer with no live
        context and no error. The ask decision log records only THAT a sweep
        contributed (`live_sweep`); which sources answered lives here.
        """
        return " ".join(
            f"{s.key}={s.status}" for s in self.sources
        ) or "no-sources"


# ── legs ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _AdapterLeg:
    """A live leg: one named tool on one connector adapter, one call, no loop."""

    provider: str
    tool: str
    build_input: object  # Callable[[list[str]], dict]

    def run(
        self, enterprise_id: str, terms: list[str]
    ) -> "tuple[str, list[RawRecord] | None]":
        """`(text, records)` for one tool call. `dispatch() -> str` is untouched
        and still the only thing the named-source path calls; this leg prefers
        the adapter's OPTIONAL `dispatch_records` (base.RecordsCapable) when it
        implements one, so the fetch happens ONCE rather than once for text and
        again for records. An adapter without it — or one that returns `None`
        for this particular call — falls straight back to `dispatch`, exactly
        the pre-existing behaviour, with `records` staying `None`."""
        from app.connector_lookup import registry

        adapter = registry.provider_for(self.provider)
        if adapter is None:
            raise _SessionUnavailable(self.provider)
        # Checked BEFORE open_session, because open_session is where the write
        # happens. See _credential_is_refresh_free.
        if self.provider in _REFRESHES_ON_OPEN and not _credential_is_refresh_free(
            enterprise_id, self.provider
        ):
            raise _RefreshWouldBeRequired(self.provider)
        session = adapter.open_session(enterprise_id)
        if session is None:
            raise _SessionUnavailable(self.provider)
        inp = self.build_input(terms)
        records_fn = getattr(adapter, "dispatch_records", None)
        if records_fn is not None:
            combined = records_fn(session, self.tool, inp)
            if combined is not None:
                text, records = combined
                return str(text or ""), records
        return str(adapter.dispatch(session, self.tool, inp) or ""), None


#: Every live leg searches by keyword and nothing else. A sweep asks one
#: question of each source — "what do you have on these words" — because
#: anything richer is a per-source decision, and per-source decisions are what
#: the named path and its tool loop are for.
_LIVE_LEGS: dict[str, _AdapterLeg] = {
    "jira": _AdapterLeg("jira", "jira_search", lambda t: {"text": " ".join(t)}),
    "clickup": _AdapterLeg(
        "clickup", "clickup_search_tasks", lambda t: {"text": " ".join(t)}
    ),
    # `fallback_to_recent` is what makes this leg contribute anything at all to
    # a source-agnostic question, and it is not optional polish.
    #
    # Slack matches literal message text, AND-ish across the query. This leg
    # joins up to MAX_TERMS topic words the user never aimed at Slack, so the
    # common outcome is zero real hits plus the occasional coincidence —
    # observed on staging 2026-08-07 as ONE stray hit from a channel nobody had
    # selected, while the channels holding the actual discussion returned
    # nothing. The existing `slack._GENERIC_QUERY_TERMS` mitigation cannot help
    # here: it is gated on sort=newest AND is single-word-only by design, and
    # this query is always multi-word.
    #
    # With the flag, a literal miss falls through to the recency window and is
    # LABELLED as unrelated background rather than rendered as topic evidence.
    # Slack is the richest source a topic question has, so a leg that reliably
    # returns noise is worse than one that says what it is looking at.
    "slack": _AdapterLeg(
        "slack", "slack_search_messages",
        lambda t: {
            "query": " ".join(t), "sort": "relevance",
            "fallback_to_recent": True,
        },
    ),
    "confluence": _AdapterLeg(
        "confluence", "confluence_search", lambda t: {"text": " ".join(t)}
    ),
    # HubSpot needs an object type and each type is its own HTTP call, so the
    # sweep asks for DEALS only and the rendered heading says so. Contacts,
    # companies and tickets stay reachable by naming HubSpot, which routes to
    # the named path and its full toolset.
    "hubspot": _AdapterLeg(
        "hubspot", "hubspot_search",
        lambda t: {"object_type": "deals", "query": " ".join(t)},
    ),
    "asana": _AdapterLeg(
        "asana", "asana_search_tasks", lambda t: {"text": " ".join(t)}
    ),
    # Meet's probe reads TRANSCRIPTS — there is no metadata to match, because a
    # Meet conference record has no title. That makes it the one leg whose cost
    # is not a single search call, so the adapter carries its own deadline
    # (google_meet.SCAN_BUDGET_S, asserted below BUDGET_S) and stops itself
    # whether or not this sweep is still waiting. Without that, an abandoned
    # leg would keep a worker thread walking a customer's calls for minutes —
    # exactly the reason `google_drive` has no leg at all.
    "google_meet": _AdapterLeg(
        "google_meet", "google_meet_search_transcripts",
        lambda t: {"keywords": " ".join(t)},
    ),
}

_LIVE_DISPLAY_SUFFIX = {"hubspot": " (deals)"}


def _leg_calls(enterprise_id: str, terms: list[str]) -> str:
    """Recorded calls, from the INDEX — never the Fireflies API.

    The index is a plain DB read and holds every synced call (Fireflies and
    Zoom); the live API is the path that used to take 168s to list them. A
    keyword miss still reports the size of the index, so the model can say "13
    recorded calls, none about this" instead of "no calls".
    """
    from app import call_index

    matches = call_index.resolve_calls(enterprise_id, " ".join(terms))
    if not matches:
        total = call_index.count_calls(enterprise_id)
        if not total:
            return ""
        return (
            f"{total} recorded calls are indexed for this workspace, but none of "
            "their titles or accounts match these terms. That says nothing about "
            "what was SAID on them — the index holds titles, dates and accounts, "
            "not transcripts."
        )
    rows = [
        f"- {c.call_date or 'undated'} · {c.title or 'untitled'}"
        + (f" · {c.account}" if c.account else "")
        + (f" · {c.summary}" if c.summary else "")
        for c in matches[:10]
    ]
    head = f"{len(matches)} indexed call(s) match these terms"
    if len(matches) > 10:
        head += " (showing the 10 most relevant)"
    return head + ":\n" + "\n".join(rows)


def _leg_github(enterprise_id: str, terms: list[str]) -> str:
    """Open pull requests, from the SYNCED rows — no GitHub API call.

    The live GitHub adapter needs a `repo` in owner/name form and this workspace
    has no repo-enumeration tool, so a keyword sweep cannot reach it. The synced
    `github_pull_requests` table needs neither, and open PRs are the part of a
    repo a product question is usually about.
    """
    from app import db

    rows = db.list_open_pull_requests(enterprise_id) or []
    if not rows:
        return ""
    lowered = [t.lower() for t in terms]
    hits = [
        r for r in rows
        if any(t in f"{r.get('title') or ''} {r.get('repo_full_name') or ''}".lower()
               for t in lowered)
    ]
    if not hits:
        return (
            f"{len(rows)} open pull request(s) are synced for this workspace, none "
            "whose title mentions these terms. Only PR titles are matched here, "
            "not diffs or comments."
        )
    rows_out = [
        f"- {r.get('repo_full_name') or 'repo'}#{r.get('pr_number')} · "
        f"{r.get('title') or 'untitled'}"
        + (" · draft" if r.get("is_draft") else "")
        for r in hits[:10]
    ]
    head = f"{len(hits)} open pull request(s) match these terms"
    if len(hits) > 10:
        head += " (showing 10)"
    return head + ":\n" + "\n".join(rows_out)


@dataclass(frozen=True)
class _LocalLeg:
    """A leg served from our OWN storage — an index or a synced table. Runs on
    the calling thread before the fan-out, because it is a fast DB read and
    because it then costs nothing when every live source times out."""

    key: str
    display_name: str
    connected: object  # Callable[[str], bool]
    run: object        # Callable[[str, list[str]], str]


def _has_calls(enterprise_id: str) -> bool:
    from app import call_index

    return bool(call_index.has_index(enterprise_id))


def _has_github(enterprise_id: str) -> bool:
    from app import db

    return bool(db.list_github_installations(enterprise_id))


def _local_legs() -> tuple[_LocalLeg, ...]:
    """Built per sweep rather than held as a module constant, so the functions
    resolve from module globals at call time. A constant tuple would capture the
    originals, and a test (or a future decorator) patching the module attribute
    would silently not take effect."""
    return (
        _LocalLeg("calls", "Recorded calls", _has_calls, _leg_calls),
        _LocalLeg("github", "GitHub (open pull requests)", _has_github, _leg_github),
    )


#: Which local leg answers for a PROVIDER the caller named. The named path speaks
#: in connection-row keys ("fireflies"), the local legs in what they read
#: ("calls" — the index, which holds Fireflies AND Zoom). Without this map a
#: caller that scopes the sweep to `{"fireflies"}` would silently sweep nothing,
#: because no live leg has that key.
_LOCAL_LEG_FOR_PROVIDER: dict[str, str] = {
    "fireflies": "calls",
    "zoom": "calls",
    "github": "github",
}


# ── the sweep ────────────────────────────────────────────────────────────────


#: provider -> the `settings` attribute that must be true for its sweep leg to
#: run. A provider absent from this map is unconditionally enabled.
#:
#: Rollout gates for the legs added in #1113, DEFAULT OFF. See the flags'
#: own comment in config.py for why a read-only-looking leg needs one: a sweep
#: leg's content is written into the tenant's knowledge graph by
#: `sweep_persist`, and staging's writes land on the PROD Supabase — so an
#: unflagged merge is an irreversible prod-data action taken as a side effect
#: of deploying a branch.
_ROLLOUT_FLAGS: dict[str, str] = {
    "asana": "chat_sweep_asana",
    "google_meet": "chat_sweep_google_meet",
}


def provider_enabled(provider: str) -> bool:
    """Is this provider's sweep leg turned on?

    Consulted at the CHOKE POINT (`_live_candidates`, which every sweep entry
    point goes through) rather than at each call site, for the reason the last
    kill-switch review recorded: the sweep had two entry points and the flag
    was checked at one, so half the feature kept running with the switch off.
    A gate a caller can forget is not a gate.

    Fails CLOSED — an unreadable settings object means the leg does not run.
    The cost of being wrong that way is one unswept source; the cost of being
    wrong the other way is writing a prod tenant's graph from code nobody has
    watched yet.
    """
    flag = _ROLLOUT_FLAGS.get(provider)
    if flag is None:
        return True
    try:
        from app.config import settings

        return bool(getattr(settings, flag, False))
    except Exception:  # noqa: BLE001 — cannot prove it is on ⇒ it is off
        logger.warning(
            "cross-connector sweep: could not read the %s rollout flag — "
            "leaving the leg off", flag, exc_info=True,
        )
        return False


def _live_candidates(enterprise_id: str) -> list[str]:
    """Connected providers this sweep has a leg for, in render priority order.

    One DB read (registry.connected_providers) rather than an open_session per
    adapter: a company with two connectors must not pay five credential reads to
    discover it has two.
    """
    from app.connector_lookup import registry

    connected = set(registry.connected_providers(enterprise_id))
    return [
        p for p in LIVE_PROVIDERS
        if p in connected and p in _LIVE_LEGS and provider_enabled(p)
    ]


#: Providers whose `open_session` is a WRITE path: it notices a near-expiry
#: access token, POSTs the refresh token and PERSISTS what comes back.
#:
#: That is the whole reason this guard exists. Those refresh tokens ROTATE (for
#: Atlassian and HubSpot), so two callers reading one stale row present the SAME
#: token; providers keep a reuse grace window precisely because clients race, so
#: both can succeed and return different new tokens, and whichever write lands
#: LAST wins the row. If that is the earlier-issued payload, the stored
#: credential is one the provider has already retired — the tenant's connector
#: is dead until a human reconnects, surfacing only as a later 401.
#:
#: `asana` and `google_meet` are here for the same MECHANISM even though their
#: blast radius is smaller. Neither rotates its refresh token, so a lost race
#: strands nothing; but both `stories.push._asana_creds` and
#: `connectors.google_meet.sync_context` still call `db.update_connection_tokens`
#: on open. The rule this set encodes is "a background sweep never mutates auth
#: state", not "a background sweep never bricks a connector" — a write is a
#: write, and the day one of these providers starts rotating, nothing here has
#: to be remembered.
_REFRESHES_ON_OPEN: frozenset[str] = frozenset(
    {"jira", "confluence", "hubspot", "asana", "google_meet"}
)

#: Skew, in seconds, before expiry at which we treat a credential as too close
#: to refresh to touch. Must be >= the LARGEST skew any guarded provider uses,
#: or we would open a session inside their refresh window and trigger the very
#: write this avoids: jira 300 (jira_fetch._TOKEN_REFRESH_SKEW_S), confluence
#: 120, hubspot 120, asana 120 (stories.push._asana_creds), google_meet 120
#: (connectors.google_meet.sync_context). 300 is therefore the floor, not a
#: preference, and a test asserts it dominates jira's — so a future provider
#: with a wider window fails loudly instead of silently reopening the write
#: path.
CREDENTIAL_SKEW_S = 300


def _credential_is_refresh_free(enterprise_id: str, provider: str) -> bool:
    """True when opening this session will NOT rotate the stored token.

    THE SWEEP MUST NEVER BE THE THING THAT MUTATES AUTH STATE. It is
    opportunistic breadth on an ordinary chat turn, running several sources in
    parallel with no coordination — the worst possible caller to hand a rotating
    credential to. Refreshing stays the job of the paths that are
    user-intent-driven or scheduled: the named connector-lookup path and
    `auto_sync`.

    So the sweep reads the row itself and declines when a refresh is due, rather
    than letting `open_session` decide. The result is that a stale-token source
    is SKIPPED and reported honestly, which costs one unread source on one turn.
    The alternative cost is a bricked connector. That trade is not close.

    Fails CLOSED — anything unreadable, unparseable or unexpected returns False,
    because the safe answer to "I cannot tell whether opening this will write"
    is not to open it. An unguarded provider (Slack, ClickUp: no refresh on
    open) never reaches here.
    """
    import json
    import time as _time

    from app import db
    from app.connectors.tokens import decrypt_token_json

    try:
        row = db.get_connection(enterprise_id, provider)
        if not row:
            return False
        token = json.loads(decrypt_token_json(row["token_json_encrypted"]))
        obtained_at = token.get("obtained_at")
        expires_in = token.get("expires_in")
        if not isinstance(obtained_at, (int, float)) or not isinstance(
            expires_in, (int, float)
        ):
            # Freshness cannot be proven, so every guarded provider would
            # refresh. Decline.
            return False
        return _time.time() < obtained_at + expires_in - CREDENTIAL_SKEW_S
    except Exception:  # noqa: BLE001 — unreadable credential ⇒ do not touch it
        logger.warning(
            "cross-connector sweep: could not check %s credential freshness for "
            "%s — skipping rather than risking a token rotation",
            provider, enterprise_id, exc_info=True,
        )
        return False


def can_sweep(provider: str) -> bool:
    """True when this provider has a probe the sweep can run for it.

    The named-lookup path uses this to decide WHICH sources deserve a scarce
    tool slot: a provider the sweep cannot reach (Google Drive, live Fireflies)
    becomes unreachable entirely if it loses its slot, while a sweepable one is
    still covered. Coverage should never depend on alphabetical luck.

    A live provider must be in BOTH `_LIVE_LEGS` (there is a probe) and
    `LIVE_PROVIDERS` (the sweep will actually run it) — `_live_candidates`
    intersects the two, so a provider in only one of them would answer True
    here and then never be swept, which is exactly the "reported as covered,
    silently not read" failure the whole unread list exists to prevent.
    """
    if provider in _LOCAL_LEG_FOR_PROVIDER:
        return True
    # `provider_enabled` belongs here too. A rolled-out-OFF provider that
    # answered True would be ranked DOWN the tool-slot order on the assumption
    # the sweep covers it — and then not swept, so the user's named source
    # would be reachable by neither path.
    return (
        provider in _LIVE_LEGS
        and provider in LIVE_PROVIDERS
        and provider_enabled(provider)
    )


def _display(provider: str) -> str:
    from app.connector_lookup import registry

    return registry.display_name(provider) + _LIVE_DISPLAY_SUFFIX.get(provider, "")


def _run_local(
    enterprise_id: str, terms: list[str], deadline: float,
    only: set[str] | None = None,
) -> list[SourceResult]:
    out: list[SourceResult] = []
    for leg in _local_legs():
        if only is not None and leg.key not in only:
            continue
        started = time.monotonic()
        result = SourceResult(key=leg.key, display_name=leg.display_name)
        # A failed CONNECTEDNESS probe is not a failed read, and the difference
        # is user-visible. "GitHub could not be read" told to a company that has
        # no GitHub is a worse answer than saying nothing — so a probe that
        # raises drops the leg silently, while a probe that says yes and then
        # fails on the read below IS reported.
        try:
            if not leg.connected(enterprise_id):
                continue
        except Exception:  # noqa: BLE001
            logger.warning(
                "cross-connector sweep: %s connectivity probe failed for %s",
                leg.key, enterprise_id, exc_info=True,
            )
            continue
        try:
            if time.monotonic() > deadline:
                result.status = STATUS_TIMEOUT
            else:
                text = leg.run(enterprise_id, terms)
                if text.strip():
                    result.status, result.text = STATUS_OK, text
        except Exception as exc:  # noqa: BLE001 — a leg degrades, never breaks chat
            logger.warning(
                "cross-connector sweep: local leg %s failed for %s",
                leg.key, enterprise_id, exc_info=True,
            )
            result.status = STATUS_ERROR
            result.detail = f"could not be read ({type(exc).__name__})"
        result.elapsed_ms = int((time.monotonic() - started) * 1000)
        out.append(result)
    return out


def _run_live(
    enterprise_id: str, terms: list[str], providers: list[str], deadline: float
) -> tuple[list[SourceResult], bool]:
    """Fan out one probe per provider and collect whatever lands in time.

    Sessions open INSIDE the worker, not before it: opening five sessions
    serially on the calling thread would put a slow token refresh outside the
    budget it is supposed to be bounded by.

    A future still running when the budget expires is abandoned — `shutdown` is
    called with `wait=False`, so a hung upstream costs one leaked worker thread
    that dies when its own HTTP timeout fires, never a held chat answer.
    """
    results: list[SourceResult] = []
    if not providers:
        return results, False
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return [
            SourceResult(key=p, display_name=_display(p), status=STATUS_TIMEOUT)
            for p in providers
        ], True

    started = time.monotonic()
    executor = _futures.ThreadPoolExecutor(
        max_workers=len(providers), thread_name_prefix="sweep"
    )
    try:
        pending = {
            executor.submit(_LIVE_LEGS[p].run, enterprise_id, terms): p
            for p in providers
        }
        done, not_done = _futures.wait(pending, timeout=remaining)
        for future in done:
            provider = pending[future]
            result = SourceResult(key=provider, display_name=_display(provider))
            try:
                text, records = future.result()
            except _RefreshWouldBeRequired:
                logger.info(
                    "cross-connector sweep: skipped %s for %s — refresh due, and "
                    "the sweep does not rotate tokens",
                    provider, enterprise_id,
                )
                result.status = STATUS_REFRESH_DUE
            except _SessionUnavailable:
                logger.info(
                    "cross-connector sweep: %s connected but not openable for %s",
                    provider, enterprise_id,
                )
                result.status = STATUS_UNAVAILABLE
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "cross-connector sweep: %s leg failed for %s",
                    provider, enterprise_id, exc_info=True,
                )
                result.status = STATUS_ERROR
                result.detail = f"could not be read ({type(exc).__name__})"
            else:
                if text and text.strip():
                    result.status, result.text = STATUS_OK, text
                    result.records = records
                else:
                    result.detail = "is connected but returned nothing for these terms"
            result.elapsed_ms = int((time.monotonic() - started) * 1000)
            results.append(result)
        for future in not_done:
            provider = pending[future]
            results.append(SourceResult(
                key=provider, display_name=_display(provider),
                status=STATUS_TIMEOUT,
                elapsed_ms=int((time.monotonic() - started) * 1000),
            ))
    finally:
        # cancel_futures cancels only the ones that never started; the running
        # ones are deliberately not waited on.
        executor.shutdown(wait=False, cancel_futures=True)
    order = {p: i for i, p in enumerate(providers)}
    results.sort(key=lambda r: order.get(r.key, len(order)))
    return results, bool(not_done)


def _apply_caps(sources: list[SourceResult]) -> None:
    """Per-source truncation with an honest marker, then a total-length ceiling
    that DROPS whole low-priority sources rather than cutting them mid-item —
    a half-rendered list of Jira issues reads as a complete one."""
    from app.connector_lookup.base import cap_text

    budget = TOTAL_CHARS
    for source in sources:
        if not source.usable:
            continue
        source.text = cap_text(source.text, limit=PER_SOURCE_CHARS)
        if len(source.text) <= budget:
            budget -= len(source.text)
            continue
        source.status = STATUS_DROPPED
        source.text = ""
        source.records = None


def sweep(
    enterprise_id: str,
    question: str,
    *,
    budget_s: float = BUDGET_S,
    only: set[str] | None = None,
    min_terms: int = MIN_TERMS,
) -> SweepResult:
    """Gather cross-source context for one question. Never raises.

    Returns an empty result (no sources, no terms) whenever there is nothing
    worth paying for: no tenant, no topic words, or no connected source with a
    leg. The caller can treat an empty `render()` as "answer exactly as before".

    `only` scopes the sweep to specific PROVIDER keys instead of discovering
    every connected one. The named-lookup path uses it (registry.answer_for_hints)
    to cover the sources a question named that did not fit the tool-loop cap:
    there, breadth is already decided by what the user asked about, and sweeping
    beyond it would answer about sources nobody mentioned.

    `min_terms` lets that same caller lower the floor. The floor exists to stop
    the DEFAULT path fanning out on a topicless message; when the user has
    explicitly named three tools, one topic word is enough to be worth a probe,
    and refusing would silently drop a source the user asked for.
    """
    started = time.monotonic()
    result = SweepResult()
    if not enterprise_id:
        return result
    terms = sweep_terms(question)
    if len(terms) < min_terms:
        return result
    result.terms = terms
    deadline = started + max(budget_s, 0.0)

    local_only = None
    if only is not None:
        local_only = {
            leg for provider, leg in _LOCAL_LEG_FOR_PROVIDER.items() if provider in only
        }
    try:
        result.sources.extend(
            _run_local(enterprise_id, terms, deadline, only=local_only)
        )
    except Exception:  # noqa: BLE001
        logger.exception("cross-connector sweep: local legs failed for %s", enterprise_id)

    connected: set[str] = set()
    try:
        from app.connector_lookup import registry as _registry

        connected = set(_registry.connected_providers(enterprise_id))
        providers = [
            p for p in LIVE_PROVIDERS
            if p in connected and p in _LIVE_LEGS and provider_enabled(p)
        ]
        if only is not None:
            providers = [p for p in providers if p in only]
    except Exception:  # noqa: BLE001
        logger.exception(
            "cross-connector sweep: provider discovery failed for %s", enterprise_id
        )
        providers = []

    # A provider whose ONLY coverage is a local leg, that IS connected, and
    # whose leg produced nothing, must still be named.
    #
    # `_run_local` drops a leg whose connectedness probe says no, and that is
    # right for a company that simply has no GitHub — "GitHub could not be
    # read" would be a worse answer than silence. It is WRONG once we know the
    # provider is connected. Zoom is the case that exposed it: its only sweep
    # coverage is the `calls` index (`_LOCAL_LEG_FOR_PROVIDER`), gated on
    # `call_index.has_index`, so a Zoom-connected company before its first sync
    # got no Zoom coverage AND no mention of Zoom anywhere — the sweep quietly
    # answered as though Zoom did not exist. Silence about a source the user
    # has connected is the same false-completeness this module's unread list
    # exists to prevent.
    seen_keys = {s.key for s in result.sources}
    for provider, leg in _LOCAL_LEG_FOR_PROVIDER.items():
        if provider not in connected or leg in seen_keys:
            continue
        if only is not None and provider not in only:
            continue
        if provider in providers:
            continue  # it also has a live leg; that one will report for it
        result.sources.append(SourceResult(
            key=provider, display_name=_display(provider),
            detail=(
                "is connected, but nothing of it has been indexed yet — this "
                "sweep reads the synced index rather than the live API, so "
                "there is nothing to search until the first sync completes. "
                "That is NOT evidence there is nothing there"
            ),
        ))

    room = max(MAX_SOURCES - len(result.sources), 0)
    if len(providers) > room:
        providers = providers[:room]
    try:
        live, exceeded = _run_live(enterprise_id, terms, providers, deadline)
    except Exception:  # noqa: BLE001
        logger.exception("cross-connector sweep: fan-out failed for %s", enterprise_id)
        live, exceeded = [], False
    result.sources.extend(live)
    result.budget_exceeded = exceeded

    _apply_caps(result.sources)
    result.elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "cross-connector sweep: %s read=%d unread=%d in %dms [%s]",
        enterprise_id, len(result.read), len(result.unread), result.elapsed_ms,
        result.outcome_summary(),
    )
    return result


def context_block(enterprise_id: str, question: str) -> tuple[str, SweepResult]:
    """`(prompt block, result)` for the answer path. The block is "" when the
    sweep found nothing usable, which is the caller's signal to compose exactly
    the prompt it composed before this feature existed."""
    result = sweep(enterprise_id, question)
    return result.render(), result
