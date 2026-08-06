"""Connector-lookup framework — the adapter contract and its enforced caps.

An "ad-hoc connector lookup" is a chat question answered by reading a connected
tool LIVE, instead of from the periodic KG snapshot: "check Slack for what was
said about the pricing change", "what's the status of PROJ-142". The shape was
first built for Jira (app/jira_lookup.py); this package is that shape
generalized so every connector can be read the same way.

An adapter is deliberately small: it says how to open a tenant-bound session,
which tools it offers, how to run one tool, and what the model must be told
about the source's honest limits. Everything that must hold for EVERY connector
— tenancy, bounded HTTP, bounded tool results, deterministic degradation — is
enforced here and in answer.py, not per adapter, so a new connector cannot
regress it by omission.

Enforced caps (why each exists):

- `HTTP_TIMEOUT` (15s): one slow upstream must not hold a chat answer open. The
  bound is per HTTP call and adapters pass it to `requests`; the tool loop's
  own iteration bound (max_iters) caps the wall clock on top of it.
- `DEFAULT_RESULT_CHARS` (~10k): a tool result is prompt input. A 500-hit search
  or a 200-page doc would evict the rest of the conversation from context, so
  results are truncated with an HONEST marker — the model is told it is seeing
  part of the data, and can say so, rather than silently answering from a
  fragment it believes is complete.
- page-size caps: adapters cap what they ask the API for in the first place
  (`cap_items`), so truncation is the second line of defence, not the first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.kg_ingest.types import RawRecord

#: Per-HTTP-call timeout for every connector read reached from chat. New
#: adapters MUST pass this to `requests` (Jira's own client predates the
#: framework and keeps its 30s bound — see jira_fetch._TIMEOUT).
HTTP_TIMEOUT = 15

#: Default cap on ONE tool result, in characters. Overridable per adapter via
#: `result_char_cap` for a source whose own internal bounds are already known
#: and larger (Jira).
DEFAULT_RESULT_CHARS = 10_000


@dataclass
class LookupSession:
    """One tenant's live access to one connector for the duration of one answer.

    `handle` is whatever the provider's client needs (a token, a dataclass, a
    Google API service). It is opened from the enterprise_id the request was
    authenticated as and never rebuilt from model input — the model can name a
    channel or an issue key, never a tenant, an installation or a token.

    `extras` lets an adapter contribute keys to the outgoing Ask payload (the
    Jira adapter puts its pending-change proposal there, which the UI renders as
    a confirm card). `notes` carries honest mode information the answer must be
    able to state — e.g. that Slack search ran in channel-read mode because no
    user token was granted.
    """

    provider: str
    #: repr suppressed: `handle` is the credential-bearing object for EVERY
    #: adapter (a token string for HubSpot, a dataclass of tokens for Slack /
    #: ClickUp), and a LookupSession repr otherwise reaches log lines, exception
    #: context and test failure dumps. Adapters suppress their own fields too;
    #: this is the backstop that covers the ones that are bare strings.
    handle: Any = field(repr=False)
    extras: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@runtime_checkable
class LookupProvider(Protocol):
    """What a connector must implement to be readable from chat.

    Adapters are stateless singletons (module-level `PROVIDER`); all per-request
    state lives on the LookupSession.
    """

    #: Connection-row provider key ("jira", "slack", …) — also the tenancy key
    #: passed to db.get_connection, so it must match the stored row exactly.
    provider: str
    #: User-facing name, used verbatim in connect/not-connected copy.
    display_name: str
    #: Words that mean "this provider" in a question. Used by the router's
    #: explicit-name trigger and by the not-supported copy.
    keywords: tuple[str, ...]

    def open_session(self, enterprise_id: str) -> LookupSession | None:
        """Resolve live access for this tenant, or None when the connector isn't
        connected / the credential can't be read. Must never raise."""

    def tools(self) -> list[dict]:
        """Anthropic tool definitions this adapter dispatches."""

    def dispatch(self, session: LookupSession, name: str, inp: dict) -> str:
        """Run one tool and render its result as text for the model."""

    def system_block(self) -> str:
        """What the model must know about this source: its tools, and its HONEST
        limits (what it cannot see, and how to say so)."""


@runtime_checkable
class RecordsCapable(Protocol):
    """OPTIONAL capability, checked with `isinstance(adapter, RecordsCapable)`
    (this Protocol is `runtime_checkable`, so that checks for the method's
    presence only — not a full LookupProvider re-implementation).

    An adapter that implements this can hand back the structured `RawRecord`s
    behind ONE tool call, when it already has the rows in hand before it
    renders them to prose for `dispatch`. It sits ALONGSIDE `dispatch`, never
    instead of it: `dispatch() -> str` is the named-source path's only
    contract, and this ticket does not touch it. An adapter that does not
    implement `dispatch_records` is a complete, working `LookupProvider` on
    its own — the caller (the cross-connector sweep) falls back to `dispatch`'s
    prose, exactly as it did before this capability existed.

    Why records at all: the scheduled 6-hourly pull hashes `RawRecord.render()`
    to dedupe against re-extracting content it already ingested. A live lookup
    that only ever produces prose can never hash to the same value, even when
    it read the identical item — see `connector_lookup/sweep_persist.py` for
    where that hash is spent.
    """

    def dispatch_records(
        self, session: "LookupSession", name: str, inp: dict
    ) -> tuple[str, "list[RawRecord] | None"] | None:
        """Run one tool call and return `(text, records)`, or `None` when this
        provider/tool combination has nothing to add.

        `text` MUST be byte-identical to what `dispatch(session, name, inp)`
        would return for the same call — callers that find this method use it
        IN PLACE OF `dispatch`, precisely so the fetch happens once, not twice.
        The safe way to guarantee that equality is to build `text` from the
        same public render function `dispatch` itself calls, not to
        re-implement rendering here.

        `records` is `None` when the data behind this particular call is too
        lean to build an honest `RawRecord` from without a second HTTP call
        (e.g. a search hit missing a field the full puller fetch includes) —
        never fabricated to fill the gap. A non-empty list does not promise
        byte-identity with the scheduled pull's own record for that item;
        it promises only that no new network call was made to build it.
        Never raises — a caller building records opportunistically must be
        able to treat a failure here as "no records available".
        """


def cap_text(text: str, *, limit: int = DEFAULT_RESULT_CHARS) -> str:
    """Truncate one tool result to `limit` chars with an honest marker.

    The marker states the real size, so the model can tell the user its answer
    covers part of the data instead of implying it read all of it.
    """
    if not text:
        return text
    if len(text) <= limit:
        return text
    return (
        text[:limit].rstrip()
        + f"\n\n(showing the first {limit:,} of {len(text):,} characters — "
        "truncated. Say so if it matters, or narrow the query.)"
    )


def cap_items(items: list, limit: int) -> tuple[list, str]:
    """Cap a result list, returning `(kept, marker)`.

    `marker` is "" when nothing was dropped, else an honest "(showing N of M …)"
    line for the adapter to include in its rendered result.
    """
    total = len(items)
    if total <= limit:
        return items, ""
    return items[:limit], (
        f"(showing {limit} of {total} matches — truncated. "
        "Narrow the query for the rest.)"
    )
