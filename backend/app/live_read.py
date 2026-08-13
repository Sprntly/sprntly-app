"""Read the sources the PLANNER named — live, in parallel, under one deadline.

The planner (`app/ask_planner.py`) decides which connected tools plausibly hold
the answer to a chat message. This module is the executor for that decision: it
takes a provider list and reads each one straight from its adapter, at request
time, from the provider's own API.

WHY THIS REPLACES THE KEYWORD SWEEP
-----------------------------------
`connector_lookup/sweep.py` reached the same connectors, but it decided FOR
ITSELF which to read: extract keywords from the question, require two of them,
then probe EVERY connected source. That is coverage by brute force. It reads
Confluence for a question about a Jira ticket, and it reads nothing at all for a
one-noun question ("anything on Acme?") because the two-term floor rejected it.

Here the decision belongs to the planner and nothing else. There is no keyword
extraction, no term floor, and no "probe everything" — this module reads exactly
the list it is handed. That list may be one source or every source; the fan-out
is parallel, so breadth costs the slowest source rather than the sum, and the
planner is free to name all of them when all of them are relevant.

WHAT IS ENFORCED HERE REGARDLESS OF THE PLAN
--------------------------------------------
Three properties are not the planner's to decide, because they are consequences
of reading N networked sources inside a request a human is waiting on:

  * ONE SHARED DEADLINE. Six sources read serially at the framework's 15s
    per-call bound is a 90-second chat answer. Fired together under a single
    `budget_s`, six healthy sources cost about as much as the slowest one. A
    source still running when the budget expires is ABANDONED, not waited on.
  * EVERY UNREAD SOURCE IS NAMED. This is the honesty rule and it is the one
    that matters most: if Slack times out and we simply omit it, the answer says
    "nothing in Slack about that", which is a lie the user cannot detect. A
    source that timed out, errored, or could not be opened is reported as unread
    WITH its reason, and the answer prompt is required to say so.
  * A CHARACTER BUDGET. Every source's text rides the same prompt as the corpus,
    the KG bundle and any uploaded documents. Without a ceiling one large
    Confluence hit evicts everything else. Overflow drops WHOLE sources — never
    a half-rendered list, which reads as a complete one.

TENANCY. Sessions are opened from `enterprise_id` alone, exactly as the named
path opens them. The planner contributes search TERMS and a provider list and
nothing else — it cannot name a tenant, an installation, a token or a repo,
because it is not in this loop at all. It ran before this module was called.

CONSTRAINTS ARE HONOURED WHERE THEY LAND, AND ONLY THERE. The planner emits
`since`/`until`/`top_n`. Auditing the adapters (2026-08-07) found that only
Slack's `slack_search_messages` accepts a window at all (`days`); `jira_search`,
`confluence_search`, `clickup_search_tasks` and `hubspot_search` take keywords
and filters but no date range. So a window is applied to Slack and recorded as
DROPPED for the rest, rather than being quietly discarded — the brief
(`docs/ASK_PLANNER.md` §6) claims "live connector reads carry constraints in the
tool args", and that is true of exactly one adapter. `top_n` is applied here
instead, as a result cap, because it costs nothing and every source can honour
it.
"""
from __future__ import annotations

import concurrent.futures as _futures
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:  # pragma: no cover — typing only
    from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

#: Wall-clock budget for the WHOLE fan-out, shared by every source. Not a
#: per-source timeout: three sources answering in 2s each cost 2s, not 6s, and
#: one source hanging costs the budget rather than its own 15s HTTP bound plus
#: everyone else's.
#:
#: 8s, inherited from the sweep's measured figure rather than re-guessed: five
#: healthy sources probed in parallel landed in ~1.1s (the slowest, not the sum)
#: and the framework's per-HTTP bound is 15s, so this is generous for every
#: healthy source and strictly tighter than the failure it exists to cap.
BUDGET_S = 8.0

#: Chars kept from one source before the honest-truncation marker.
PER_SOURCE_CHARS = 2_500

#: Ceiling on the whole assembled block. Sources render in the planner's own
#: order and overflow drops WHOLE sources, named.
TOTAL_CHARS = 12_000

#: Default result cap per source when the question carries no `top_n`.
DEFAULT_TOP_N = 10

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_UNAVAILABLE = "unavailable"
STATUS_DROPPED = "dropped"
STATUS_NOT_READABLE = "not_readable"

#: Statuses whose text is worth putting in the prompt.
_USABLE = {STATUS_OK}


class _SessionUnavailable(RuntimeError):
    """Connected on paper, but no session could be opened (revoked token, a
    workspace that lost its install). Distinct from an error: nothing failed,
    the credential simply is not usable, and the user is told that."""


@dataclass
class SourceRead:
    """One source's outcome. `text` is only meaningful when `status` is ok."""

    key: str
    display_name: str
    status: str = STATUS_EMPTY
    text: str = ""
    detail: str = ""
    elapsed_ms: int = 0
    records: "list[RawRecord] | None" = None
    #: Constraints the planner asked for that this adapter cannot express.
    dropped_constraints: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.status in _USABLE and bool(self.text.strip())

    def unread_reason(self) -> str:
        """How this source is described to the model when it was NOT read."""
        if self.detail:
            return self.detail
        return {
            STATUS_TIMEOUT: "did not respond in time",
            STATUS_ERROR: "could not be read",
            STATUS_UNAVAILABLE: "is connected but its credentials could not be used",
            STATUS_DROPPED: "was read but did not fit in the context budget",
            STATUS_EMPTY: "was read and had nothing matching",
            STATUS_NOT_READABLE: "cannot be read live by Sprntly",
        }.get(self.status, "was not read")


@dataclass
class LiveReadResult:
    sources: list[SourceRead] = field(default_factory=list)
    budget_exhausted: bool = False

    @property
    def read(self) -> list[SourceRead]:
        return [s for s in self.sources if s.usable]

    @property
    def unread(self) -> list[SourceRead]:
        return [s for s in self.sources if not s.usable]

    def outcome_summary(self) -> str:
        """`jira=ok slack=timeout confluence=empty` — statuses only, never the
        terms and never the content.

        This is the observability story for a live read, and it needs to exist
        because the two failure modes are indistinguishable from outside: a read
        that found nothing and a read that never happened both produce an answer
        with no live context and no error."""
        return " ".join(f"{s.key}={s.status}" for s in self.sources) or "no-sources"

    def render_block(self) -> str:
        """The context block handed to the answer call.

        Both halves are required. The read sources give the model something to
        answer FROM; the unread list gives it something it must say. A block
        with only the first half is what produces "nothing in Slack about it"
        when Slack was never actually read."""
        if not self.sources:
            return ""
        parts: list[str] = []
        for source in self.read:
            parts.append(f"### {source.display_name}\n{source.text.strip()}")
        unread = self.unread
        if unread:
            lines = "\n".join(
                f"- {s.display_name}: {s.unread_reason()}" for s in unread
            )
            parts.append(
                "### Sources NOT read\n"
                "You MUST NOT claim these sources contain nothing. They were not "
                "successfully read. If the answer depends on one of them, say so.\n"
                + lines
            )
        if not parts:
            return ""
        return "## Live source reads\n\n" + "\n\n".join(parts)


# ── per-source legs ──────────────────────────────────────────────────────────
#
# One named tool per provider, one call, no loop. The tool loop
# (`connector_lookup/answer.py`) still exists and is still the right shape when
# a question names ONE source and needs follow-up reads; this is the breadth
# path, where the model has already decided what to look at and each source gets
# asked exactly one question.


@dataclass(frozen=True)
class _Leg:
    provider: str
    tool: str
    #: (query, constraints) -> tool input
    build_input: Callable[[str, dict], dict]
    #: Constraint keys this adapter can actually express. Anything the planner
    #: emits outside this set is reported as dropped rather than silently lost.
    honours: frozenset = frozenset()


def _window_days(constraints: dict) -> Optional[int]:
    """`since` → whole days back from today, for the one adapter that takes it."""
    since = constraints.get("since")
    if not since:
        return None
    from datetime import date

    try:
        days = (date.today() - date.fromisoformat(since)).days
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


_LEGS: dict[str, _Leg] = {
    "jira": _Leg("jira", "jira_search", lambda q, c: {"text": q}),
    "clickup": _Leg("clickup", "clickup_search_tasks", lambda q, c: {"text": q}),
    "confluence": _Leg("confluence", "confluence_search", lambda q, c: {"text": q}),
    "slack": _Leg(
        "slack",
        "slack_search_messages",
        lambda q, c: (
            {"query": q, "sort": "relevance"}
            | ({"days": _window_days(c)} if _window_days(c) else {})
        ),
        honours=frozenset({"since"}),
    ),
    # HubSpot needs an object type and each type is its own HTTP call, so this
    # asks for DEALS and the display name says so. Contacts/companies/tickets
    # stay reachable through the named path's full toolset.
    "hubspot": _Leg(
        "hubspot",
        "hubspot_search",
        lambda q, c: {"object_type": "deals", "query": q},
    ),
}

#: Providers whose breadth read comes from OUR OWN storage rather than a live
#: API call, with the reason. These are not laziness — in both cases the live
#: API cannot answer a source-agnostic question at all:
#:
#:  * github — every live GitHub tool requires `repo` in 'owner/name' form
#:    (verified in connector_lookup/github.py), and there is no repo-enumeration
#:    tool. A keyword read across the company's code is therefore impossible
#:    live. The synced `github_pull_requests` rows need no repo and open PRs are
#:    the part of a repo a product question is usually about. Naming a repo
#:    still reaches the live adapter through the named path's tool loop.
#:  * fireflies / zoom — recorded calls. The live listing path is the one that
#:    measured 168s; the call index is the same data, one Postgres query, and
#:    freshness-bounded by the sync.
_LOCAL_LEGS: dict[str, str] = {
    "github": "github",
    "fireflies": "calls",
    "zoom": "calls",
}

#: Providers with no breadth read at all, live or local, with the honest reason
#: shown to the model. Named rather than omitted so a planner that picks one
#: produces "I could not read this" instead of silence — the same posture
#: `registry.DEFERRED` takes for a connector with no adapter.
_NO_LEG_REASON: dict[str, str] = {
    # Verified in connector_lookup/gdrive.py: the adapter offers
    # `drive_list_connected_files` (no arguments) and `drive_read_file` (needs a
    # file id). Its own description says there is NO Drive-wide search. Connected
    # Drive files already reach the prompt through `ask_runner.document_grounding`,
    # so a breadth leg here would duplicate that and add nothing.
    "google_drive": (
        "has no content search — its connected files are already available to "
        "this answer through document grounding"
    ),
}


def _display(provider: str) -> str:
    from app.connector_lookup import registry

    name = registry.display_name(provider)
    return name + (" (deals)" if provider == "hubspot" else "")


# ── local legs (our own storage — see _LOCAL_LEGS for why each is not live) ──


def _local_github(enterprise_id: str, query: str, constraints: dict) -> str:
    """Open pull requests from the synced rows. Matches PR TITLES only.

    A keyword miss still reports the size of the set, so the model can say
    "14 open PRs, none about this" rather than "no pull requests" — the same
    honesty rule the unread list enforces, applied to an empty match."""
    from app import db

    rows = db.list_open_pull_requests(enterprise_id) or []
    if not rows:
        return ""
    terms = [t for t in query.lower().split() if t]
    hits = [
        r for r in rows
        if any(
            t in f"{r.get('title') or ''} {r.get('repo_full_name') or ''}".lower()
            for t in terms
        )
    ]
    if not hits:
        return (
            f"{len(rows)} open pull request(s) are synced for this workspace, none "
            "whose title mentions this. Only PR titles are matched here, not "
            "diffs or comments."
        )
    listed = [
        f"- {r.get('repo_full_name') or 'repo'}#{r.get('pr_number')} · "
        f"{r.get('title') or 'untitled'}"
        + (" · draft" if r.get("is_draft") else "")
        for r in hits
    ]
    return f"{len(hits)} open pull request(s) match:\n" + "\n".join(listed)


def _local_calls(enterprise_id: str, query: str, constraints: dict) -> str:
    """Recorded calls from the INDEX — never the Fireflies/Zoom API.

    A keyword miss reports the index size, and says explicitly what the index
    does and does not hold: titles, dates and accounts, NOT transcripts. Without
    that the model reads "no matching calls" as "nothing was said about this"."""
    from app import call_index

    matches = call_index.resolve_calls(enterprise_id, query)
    if not matches:
        total = call_index.count_calls(enterprise_id)
        if not total:
            return ""
        return (
            f"{total} recorded calls are indexed for this workspace, but none of "
            "their titles or accounts match this. That says nothing about what "
            "was SAID on them — the index holds titles, dates and accounts, not "
            "transcripts."
        )
    listed = [
        f"- {c.call_date or 'undated'} · {c.title or 'untitled'}"
        + (f" · {c.account}" if c.account else "")
        + (f" · {c.summary}" if c.summary else "")
        for c in matches
    ]
    return f"{len(matches)} indexed call(s) match:\n" + "\n".join(listed)


_LOCAL_RUNNERS: dict[str, Callable[[str, str, dict], str]] = {
    "github": _local_github,
    "calls": _local_calls,
}

#: Display name for a local leg, since several providers can share one.
_LOCAL_DISPLAY: dict[str, str] = {
    "github": "GitHub (open pull requests)",
    "calls": "Recorded calls (indexed)",
}


def _read_one(enterprise_id: str, leg: _Leg, query: str, constraints: dict):
    """Open a session and run this leg's single tool call.

    The session opens INSIDE the worker, not before it: opening five sessions
    serially on the calling thread would put a slow token refresh outside the
    budget that is supposed to bound it.

    Prefers the adapter's optional `dispatch_records` (base.RecordsCapable) so
    the fetch happens ONCE for both the prompt text and the KG records, falling
    straight back to `dispatch` for an adapter without one.
    """
    from app.connector_lookup import registry

    adapter = registry.provider_for(leg.provider)
    if adapter is None:
        raise _SessionUnavailable(leg.provider)
    session = adapter.open_session(enterprise_id)
    if session is None:
        raise _SessionUnavailable(leg.provider)

    inp = leg.build_input(query, constraints)
    records_fn = getattr(adapter, "dispatch_records", None)
    if records_fn is not None:
        combined = records_fn(session, leg.tool, inp)
        if combined is not None:
            text, records = combined
            return str(text or ""), records
    return str(adapter.dispatch(session, leg.tool, inp) or ""), None


def _dropped_constraints(leg: _Leg, constraints: dict) -> list[str]:
    """Which of the planner's constraints this adapter cannot express.

    `top_n` is excluded: it is applied here as a result cap rather than pushed
    into the tool, so no adapter can fail to honour it. `entity` is excluded
    because it is folded into the query text itself.
    """
    return sorted(
        k for k in constraints
        if k in ("since", "until") and k not in leg.honours
    )


def read_sources(
    enterprise_id: str,
    providers: list[str],
    *,
    query: str,
    constraints: Optional[dict] = None,
    budget_s: float = BUDGET_S,
) -> LiveReadResult:
    """Read every named provider live, in parallel, under one shared deadline.

    `providers` is the planner's gated list — already intersected with what this
    company has connected. This function does NOT re-decide it; a provider with
    no live leg is reported as unreadable rather than dropped, because the
    planner named it and the user deserves to know it could not be reached.

    Never raises. A source that fails degrades to an unread entry; the whole
    read failing degrades to an empty result and a plain answer.
    """
    constraints = constraints or {}
    result = LiveReadResult()
    if not providers or not query.strip():
        return result

    runnable: list[_Leg] = []
    # (local leg key, the provider that asked for it). The provider is kept as
    # the SourceRead key so the planner's ordering still applies — two providers
    # can share one leg (fireflies + zoom both read the call index), and the
    # first one named wins the slot rather than the block rendering twice.
    local: list[tuple[str, str]] = []
    for provider in providers:
        leg = _LEGS.get(provider)
        if leg is not None:
            runnable.append(leg)
            continue
        local_key = _LOCAL_LEGS.get(provider)
        if local_key is not None:
            if local_key not in {k for k, _ in local}:
                local.append((local_key, provider))
            continue
        result.sources.append(SourceRead(
            key=provider,
            display_name=_display(provider),
            status=STATUS_NOT_READABLE,
            detail=_NO_LEG_REASON.get(provider, "cannot be read live by Sprntly"),
        ))

    # Local legs run first and on THIS thread: they are fast DB reads, so they
    # cost nothing when every live source later times out, and running them here
    # keeps the thread pool sized to the work that actually needs it.
    for local_key, provider in local:
        result.sources.append(
            _run_local(enterprise_id, local_key, provider, query, constraints)
        )

    if runnable:
        result.sources.extend(
            _fan_out(enterprise_id, runnable, query, constraints, budget_s)
        )
        result.budget_exhausted = any(
            s.status == STATUS_TIMEOUT for s in result.sources
        )

    # Preserve the planner's own ordering — it ranked them, and render priority
    # (which source survives the char budget) should follow that ranking.
    order = {p: i for i, p in enumerate(providers)}
    result.sources.sort(key=lambda s: order.get(s.key, len(order)))
    _apply_caps(result.sources, constraints)
    return result


def _run_local(
    enterprise_id: str, local_key: str, provider: str, query: str, constraints: dict
) -> SourceRead:
    """Run one leg served from our own storage. Never raises.

    Carries the PROVIDER as its key (so the planner's ordering applies) and the
    leg's own display name (so the model is told it is reading the index or the
    synced PR rows, not a live API — an answer that implies a live read it never
    made is the failure this whole module is shaped against)."""
    started = time.monotonic()
    read = SourceRead(
        key=provider,
        display_name=_LOCAL_DISPLAY.get(local_key, _display(provider)),
    )
    try:
        text = _LOCAL_RUNNERS[local_key](enterprise_id, query, constraints)
    except Exception as exc:  # noqa: BLE001 — one source degrades, chat does not
        logger.warning(
            "[planner] live-read local leg %s failed for %s",
            local_key, enterprise_id, exc_info=True,
        )
        read.status = STATUS_ERROR
        read.detail = f"could not be read ({type(exc).__name__})"
    else:
        if text and text.strip():
            read.status, read.text = STATUS_OK, text
        else:
            read.status = STATUS_EMPTY
            read.detail = "has nothing recorded for this workspace"
    read.elapsed_ms = int((time.monotonic() - started) * 1000)
    return read


def _fan_out(
    enterprise_id: str,
    legs: list[_Leg],
    query: str,
    constraints: dict,
    budget_s: float,
) -> list[SourceRead]:
    started = time.monotonic()
    deadline = started + budget_s
    out: list[SourceRead] = []

    executor = _futures.ThreadPoolExecutor(
        max_workers=len(legs), thread_name_prefix="live-read"
    )
    try:
        pending = {
            executor.submit(_read_one, enterprise_id, leg, query, constraints): leg
            for leg in legs
        }
        done, not_done = _futures.wait(
            pending, timeout=max(0.0, deadline - time.monotonic())
        )
        for future in done:
            leg = pending[future]
            read = SourceRead(
                key=leg.provider,
                display_name=_display(leg.provider),
                dropped_constraints=_dropped_constraints(leg, constraints),
            )
            try:
                text, records = future.result()
            except _SessionUnavailable:
                logger.info(
                    "[planner] live-read %s connected but not openable for %s",
                    leg.provider, enterprise_id,
                )
                read.status = STATUS_UNAVAILABLE
            except Exception as exc:  # noqa: BLE001 — one source degrades, chat does not
                logger.warning(
                    "[planner] live-read %s failed for %s",
                    leg.provider, enterprise_id, exc_info=True,
                )
                read.status = STATUS_ERROR
                read.detail = f"could not be read ({type(exc).__name__})"
            else:
                if text and text.strip():
                    read.status, read.text, read.records = STATUS_OK, text, records
                else:
                    read.status = STATUS_EMPTY
                    read.detail = "was read and had nothing matching this question"
            read.elapsed_ms = int((time.monotonic() - started) * 1000)
            out.append(read)

        for future in not_done:
            leg = pending[future]
            out.append(SourceRead(
                key=leg.provider,
                display_name=_display(leg.provider),
                status=STATUS_TIMEOUT,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                dropped_constraints=_dropped_constraints(leg, constraints),
            ))
    finally:
        # cancel_futures cancels only what never started; a running read is
        # deliberately NOT waited on — a hung upstream costs one worker thread
        # that dies on its own HTTP timeout, never a held chat answer.
        executor.shutdown(wait=False, cancel_futures=True)
    return out


def _apply_caps(sources: list[SourceRead], constraints: dict) -> None:
    """Per-source truncation with an honest marker, then a total ceiling that
    DROPS whole low-priority sources rather than cutting one mid-item — a
    half-rendered list of Jira issues reads as a complete one."""
    from app.connector_lookup.base import cap_text

    top_n = constraints.get("top_n")
    budget = TOTAL_CHARS
    for source in sources:
        if not source.usable:
            continue
        if isinstance(top_n, int) and top_n > 0:
            source.text = _cap_lines(source.text, top_n)
        source.text = cap_text(source.text, limit=PER_SOURCE_CHARS)
        if len(source.text) <= budget:
            budget -= len(source.text)
            continue
        source.status = STATUS_DROPPED
        source.text = ""
        source.records = None


def _cap_lines(text: str, top_n: int) -> str:
    """Honour a `top_n` the question asked for, on the one shape every adapter
    renders: a leading summary line followed by one line per item.

    Applied here rather than pushed into each tool because no adapter's search
    schema takes a limit, and a cap the app applies uniformly is more honest
    than one that silently works for some sources and not others."""
    lines = text.splitlines()
    items = [i for i, line in enumerate(lines) if line.lstrip().startswith(("-", "*"))]
    if len(items) <= top_n:
        return text
    cut = items[top_n]
    kept = "\n".join(lines[:cut]).rstrip()
    return f"{kept}\n(showing the first {top_n} of {len(items)}, as asked)"
