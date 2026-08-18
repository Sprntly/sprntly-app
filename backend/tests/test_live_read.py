"""The planner's source executor — `app/live_read.py`.

This module replaces the keyword sweep's "probe everything connected" with
"read exactly what the planner named". Most of what is tested here is the part
that is NOT the planner's to decide, because those are the properties that stop
a breadth read from either lying to the user or hanging the chat:

  * an unread source is NAMED, with its reason — the failure that produces
    "nothing in Slack about that" when Slack was never actually read
  * the whole fan-out shares ONE deadline, and a slow source is abandoned
  * one source failing degrades that source, never the answer
  * the char budget drops WHOLE sources, never half a list

The adapter registry is stubbed throughout: these tests assert the executor's
contract, not any provider's API.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app import live_read


# ── stubbing the registry ────────────────────────────────────────────────────


class _StubAdapter:
    def __init__(self, *, text="", raises=None, delay=0.0, session=True):
        self._text = text
        self._raises = raises
        self._delay = delay
        self._session = session
        self.calls: list[tuple[str, dict]] = []

    def open_session(self, enterprise_id):
        return SimpleNamespace(handle=enterprise_id) if self._session else None

    def dispatch(self, session, name, inp):
        self.calls.append((name, inp))
        if self._delay:
            time.sleep(self._delay)
        if self._raises:
            raise self._raises
        return self._text


@pytest.fixture
def stub_registry(monkeypatch):
    """Swap `registry.provider_for` for a dict of stub adapters."""
    adapters: dict[str, _StubAdapter] = {}

    from app.connector_lookup import registry

    monkeypatch.setattr(registry, "provider_for", lambda p: adapters.get(p))
    monkeypatch.setattr(registry, "display_name", lambda p: p.title())
    return adapters


# ── it reads exactly what it was handed ──────────────────────────────────────


def test_reads_only_the_named_sources(stub_registry):
    """No keyword extraction, no term floor, no probe-everything. The planner
    decided; this module executes."""
    stub_registry["jira"] = _StubAdapter(text="- PROJ-1 · checkout bug")
    stub_registry["slack"] = _StubAdapter(text="- #eng: shipping friday")
    stub_registry["confluence"] = _StubAdapter(text="- Runbook")

    result = live_read.read_sources("co-1", ["jira"], query="checkout")

    assert [s.key for s in result.sources] == ["jira"]
    assert result.sources[0].usable
    assert stub_registry["slack"].calls == []
    assert stub_registry["confluence"].calls == []


def test_a_one_word_question_still_reads(stub_registry):
    """The sweep required two search terms and skipped everything below that,
    so "anything on Acme?" read nothing. The planner has no such floor."""
    stub_registry["jira"] = _StubAdapter(text="- PROJ-9 · Acme migration")

    result = live_read.read_sources("co-1", ["jira"], query="Acme")

    assert result.read and result.read[0].key == "jira"


def test_breadth_is_not_capped(stub_registry):
    """All five live adapters at once is a valid plan — the fan-out is parallel,
    so breadth costs the slowest source, not the sum."""
    providers = ["jira", "slack", "confluence", "clickup", "hubspot"]
    for p in providers:
        stub_registry[p] = _StubAdapter(text=f"- hit from {p}")

    result = live_read.read_sources("co-1", providers, query="pricing")

    assert {s.key for s in result.read} == set(providers)


def test_planner_order_is_preserved(stub_registry):
    """The planner ranked them; render priority (who survives the char budget)
    follows that ranking rather than completion order."""
    for p in ("jira", "slack", "confluence"):
        stub_registry[p] = _StubAdapter(text=f"- {p}")

    result = live_read.read_sources(
        "co-1", ["confluence", "jira", "slack"], query="x"
    )
    assert [s.key for s in result.sources] == ["confluence", "jira", "slack"]


# ── honesty: an unread source is always named ────────────────────────────────


def test_a_failing_source_is_reported_not_omitted(stub_registry):
    """The core honesty rule. Silently dropping Slack makes the answer say
    "nothing in Slack about that" — a lie the reader cannot detect."""
    stub_registry["jira"] = _StubAdapter(text="- PROJ-1")
    stub_registry["slack"] = _StubAdapter(raises=RuntimeError("boom"))

    result = live_read.read_sources("co-1", ["jira", "slack"], query="x")
    block = result.render_block()

    assert [s.key for s in result.read] == ["jira"]
    assert [s.key for s in result.unread] == ["slack"]
    assert "Sources NOT read" in block
    assert "Slack" in block
    assert "MUST NOT claim these sources contain nothing" in block


def test_an_unopenable_session_is_distinguished_from_an_error(stub_registry):
    """Connected on paper but the credential is dead. Nothing failed — the
    credential simply is not usable, and the user is told that."""
    stub_registry["slack"] = _StubAdapter(session=False)

    result = live_read.read_sources("co-1", ["slack"], query="x")

    assert result.sources[0].status == live_read.STATUS_UNAVAILABLE
    assert "credentials" in result.sources[0].unread_reason()


def test_an_empty_read_is_not_the_same_as_an_unread_one(stub_registry):
    """"Read it, found nothing" is a real answer; "never read it" is not. Both
    end up outside `read`, so the reason string is what separates them."""
    stub_registry["jira"] = _StubAdapter(text="")

    result = live_read.read_sources("co-1", ["jira"], query="x")

    assert result.sources[0].status == live_read.STATUS_EMPTY
    assert "nothing matching" in result.sources[0].unread_reason()


def test_a_source_with_no_breadth_leg_says_so(stub_registry):
    """Google Drive has no content search at all (verified in its adapter). A
    planner that names it must produce an honest line, not silence."""
    result = live_read.read_sources("co-1", ["google_drive"], query="spec")

    assert result.sources[0].status == live_read.STATUS_NOT_READABLE
    assert "no content search" in result.sources[0].unread_reason()
    assert "Sources NOT read" in result.render_block()


def test_outcome_summary_carries_status_never_content(stub_registry):
    """The whole observability story for a read: a read that found nothing and
    a read that never ran are indistinguishable from the answer alone."""
    stub_registry["jira"] = _StubAdapter(text="- PROJ-1 · SECRET")
    stub_registry["slack"] = _StubAdapter(raises=RuntimeError("x"))

    summary = live_read.read_sources(
        "co-1", ["jira", "slack"], query="x"
    ).outcome_summary()

    assert summary == "jira=ok slack=error"
    assert "SECRET" not in summary


# ── the shared deadline ──────────────────────────────────────────────────────


def test_a_slow_source_is_abandoned_and_the_rest_still_land(stub_registry):
    """One shared budget, not one per source. A hung upstream costs the budget
    once; it never holds the answer open."""
    stub_registry["jira"] = _StubAdapter(text="- fast")
    stub_registry["slack"] = _StubAdapter(text="- slow", delay=5.0)

    started = time.monotonic()
    result = live_read.read_sources(
        "co-1", ["jira", "slack"], query="x", budget_s=0.4
    )
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, "the fan-out waited on the slow source"
    assert [s.key for s in result.read] == ["jira"]
    slack = next(s for s in result.sources if s.key == "slack")
    assert slack.status == live_read.STATUS_TIMEOUT
    assert result.budget_exhausted


def test_nothing_is_read_when_the_plan_is_empty(stub_registry):
    assert live_read.read_sources("co-1", [], query="x").sources == []
    assert live_read.read_sources("co-1", ["jira"], query="  ").sources == []


# ── budgets ──────────────────────────────────────────────────────────────────


def test_the_char_budget_drops_whole_sources_not_half_a_list(stub_registry):
    """A half-rendered list of Jira issues reads as a complete one, so overflow
    drops the whole source — and the drop is reported like any other unread."""
    big = "\n".join(f"- item {i} " + "x" * 200 for i in range(200))
    stub_registry["jira"] = _StubAdapter(text=big)
    stub_registry["slack"] = _StubAdapter(text=big)
    stub_registry["confluence"] = _StubAdapter(text=big)
    stub_registry["clickup"] = _StubAdapter(text=big)
    stub_registry["hubspot"] = _StubAdapter(text=big)

    result = live_read.read_sources(
        "co-1", ["jira", "slack", "confluence", "clickup", "hubspot"], query="x"
    )

    rendered = sum(len(s.text) for s in result.read)
    assert rendered <= live_read.TOTAL_CHARS
    dropped = [s for s in result.sources if s.status == live_read.STATUS_DROPPED]
    assert dropped, "nothing was dropped despite exceeding the budget"
    assert "did not fit" in dropped[0].unread_reason()


def test_top_n_is_honoured_by_the_app_not_the_adapter(stub_registry):
    """No adapter's search schema takes a limit, so a cap the app applies
    uniformly is more honest than one that works for some sources only."""
    listing = "3 matches:\n" + "\n".join(f"- issue {i}" for i in range(10))
    stub_registry["jira"] = _StubAdapter(text=listing)

    result = live_read.read_sources(
        "co-1", ["jira"], query="x", constraints={"top_n": 3}
    )

    text = result.read[0].text
    assert text.count("- issue") == 3
    assert "showing the first 3 of 10" in text


# ── constraints land only where an adapter can express them ──────────────────


def test_a_window_reaches_slack_which_can_express_it(stub_registry):
    stub_registry["slack"] = _StubAdapter(text="- msg")

    live_read.read_sources(
        "co-1", ["slack"], query="pricing", constraints={"since": "2020-01-01"}
    )

    _tool, inp = stub_registry["slack"].calls[0]
    assert inp["days"] > 0


def test_a_window_jira_cannot_express_is_recorded_as_dropped(stub_registry):
    """`jira_search` takes text/project/status and no date range (verified in
    the adapter). The brief claims live reads carry constraints in tool args;
    that is true of exactly one adapter, so the rest report the gap rather than
    discarding it silently."""
    stub_registry["jira"] = _StubAdapter(text="- PROJ-1")

    result = live_read.read_sources(
        "co-1", ["jira"], query="x", constraints={"since": "2020-01-01"}
    )

    _tool, inp = stub_registry["jira"].calls[0]
    assert "since" not in inp and "days" not in inp
    assert result.sources[0].dropped_constraints == ["since"]


# ── local legs ───────────────────────────────────────────────────────────────


def test_calls_read_the_index_and_say_so(stub_registry, monkeypatch):
    """Recorded calls come from the index, not the Fireflies API — the live
    listing path is the one that measured 168s. The display name has to say
    "indexed" so the model never implies it read a transcript."""
    import app.call_index as call_index

    # The real `IndexedCall`, not a hand-rolled namespace: a partial stub goes
    # stale silently the moment this leg renders another column it has always
    # had (participants and duration, added 2026-08-16).
    monkeypatch.setattr(
        call_index, "resolve_calls",
        lambda eid, q: [_indexed_call("1", "2026-08-01", "Acme QBR", "Acme")],
    )

    result = live_read.read_sources("co-1", ["fireflies"], query="Acme")

    assert result.read[0].display_name == "Recorded calls (indexed)"
    assert "Acme QBR" in result.read[0].text


def test_fireflies_and_zoom_share_one_call_leg(stub_registry, monkeypatch):
    """Both read the same index. A plan naming both must not render it twice."""
    import app.call_index as call_index

    monkeypatch.setattr(call_index, "resolve_calls", lambda eid, q: [])
    monkeypatch.setattr(call_index, "count_calls", lambda eid: 4)

    result = live_read.read_sources("co-1", ["fireflies", "zoom"], query="x")

    assert len(result.sources) == 1


def test_an_empty_call_index_states_what_it_holds(stub_registry, monkeypatch):
    """"No matching calls" must not read as "nothing was said about this" — the
    index holds titles and accounts, never transcripts."""
    import app.call_index as call_index

    monkeypatch.setattr(call_index, "resolve_calls", lambda eid, q: [])
    monkeypatch.setattr(call_index, "count_calls", lambda eid: 13)

    result = live_read.read_sources("co-1", ["zoom"], query="churn")

    assert "13 recorded calls are indexed" in result.read[0].text
    assert "not transcripts" in result.read[0].text


def _indexed_call(external_id, call_date, title, account=None,
                  participants=None, duration_min=None):
    from app.call_index import IndexedCall

    return IndexedCall(
        external_id=external_id, title=title, call_date=call_date,
        duration_min=duration_min, participants=participants or [],
        account=account, summary="",
    )


def test_local_only_reads_our_tables_and_skips_the_network(
    stub_registry, monkeypatch
):
    """The stand-down's stated cost is third-party I/O, which a Postgres
    SELECT does not incur. In local-only mode the call index is still read and
    the networked legs are not opened at all — the fix for an indexed call
    history going unread behind a 3-day KG horizon (2026-08-15)."""
    import app.call_index as call_index

    stub_registry["slack"] = _StubAdapter(text="- #eng: shipping friday")
    monkeypatch.setattr(
        call_index, "resolve_calls",
        lambda eid, q: [_indexed_call("1", "2026-08-13T20:00:00+00:00",
                                      "Contoso + Northwind", "Contoso")],
    )

    result = live_read.read_sources(
        "co-1", ["fireflies", "slack"], query="Contoso", local_only=True,
    )

    by_key = {s.key: s for s in result.sources}
    assert "Contoso + Northwind" in by_key["fireflies"].text
    # Slack was NAMED but not opened — and says so, rather than being dropped,
    # which would read as "I looked and there was nothing".
    assert stub_registry["slack"].calls == []
    assert not by_key["slack"].usable
    assert "knowledge graph" in by_key["slack"].detail


def test_a_call_line_names_who_was_on_it(stub_registry, monkeypatch):
    """An answer about a named call apologised that attendee names were not
    available while the index row held all five addresses and a 51-minute
    duration. Both are stored; this leg simply never rendered them."""
    import app.call_index as call_index

    call = _indexed_call(
        "1", "2026-08-13T20:00:00+00:00", "Contoso + Northwind", "Contoso",
        participants=["dtung@northwind.example", "daniel.hagen@contoso.com"],
        duration_min=51.0,
    )
    monkeypatch.setattr(call_index, "resolve_calls", lambda eid, q: [call])

    result = live_read.read_sources("co-1", ["fireflies"], query="Contoso")

    text = result.read[0].text
    assert "with: dtung@northwind.example, daniel.hagen@contoso.com" in text
    assert "51 min" in text


def test_a_long_attendee_list_is_capped_with_a_remainder(
    stub_registry, monkeypatch
):
    """A big briefing must not spend the whole per-source budget on names."""
    import app.call_index as call_index

    call = _indexed_call(
        "1", "2026-08-13T20:00:00+00:00", "All hands", None,
        participants=[f"p{i}@acme.com" for i in range(12)],
    )
    monkeypatch.setattr(call_index, "resolve_calls", lambda eid, q: [call])

    result = live_read.read_sources("co-1", ["fireflies"], query="all hands")

    assert "(+4 more)" in result.read[0].text


def test_a_windowed_plan_renders_the_whole_window_not_a_keyword_probe(
    stub_registry, monkeypatch
):
    """The 2026-08-15 failure: "a table of how many customer calls I had each
    week" matched no call title, so the keyword probe answered "none match"
    and the model built its weekly table from KG signals — which only hold the
    ~25 newest meetings, so every older week rendered as zero while the index
    held the whole history. A plan carrying `since`/`until` must render the
    WINDOW: true counts per week, zero weeks included, without ever needing a
    title to match."""
    import app.call_index as call_index

    calls = [
        _indexed_call("1", "2026-07-13T15:00:00+00:00",
                      "TransUnion + Demo", "TransUnion"),
        _indexed_call("2", "2026-07-28T15:00:00+00:00",
                      "Woodward + Demo", "Woodward"),
    ]
    seen = {}

    def _list(eid, *, since=None, until=None, limit=50):
        seen["since"], seen["until"], seen["limit"] = since, until, limit
        return calls

    def _probe_must_not_decide(eid, q):
        raise AssertionError("a windowed plan fell back to the keyword probe")

    monkeypatch.setattr(call_index, "list_calls", _list)
    monkeypatch.setattr(
        call_index, "count_calls", lambda eid, since=None, until=None: 2
    )
    monkeypatch.setattr(call_index, "resolve_calls", _probe_must_not_decide)

    result = live_read.read_sources(
        "co-1", ["fireflies"], query="how many customer calls each week",
        constraints={"since": "2026-07-11", "until": "2026-08-01"},
    )

    text = result.read[0].text
    assert seen["since"] is not None and seen["until"] is not None
    assert "2 recorded calls in the index between 2026-07-11 and 2026-08-01" in text
    assert "week of 2026-07-13: 1 call(s) — TransUnion" in text
    # The zero week is rendered EXPLICITLY: a missing line and a zero are
    # different claims, and only the zero stops "no data" readings.
    assert "week of 2026-07-20: 0 call(s)" in text
    # The newest individual calls ride along so a "group last week's calls"
    # follow-up has titles, not just counts.
    assert "Woodward + Demo" in text


def test_an_empty_window_is_a_fact_not_a_missing_sync(stub_registry, monkeypatch):
    """Zero calls IN THE WINDOW on an index holding hundreds outside it must
    say exactly that — "nothing recorded for this workspace" would be false."""
    import app.call_index as call_index

    monkeypatch.setattr(
        call_index, "list_calls",
        lambda eid, *, since=None, until=None, limit=50: [],
    )
    monkeypatch.setattr(
        call_index, "count_calls",
        lambda eid, since=None, until=None: 0 if (since or until) else 522,
    )

    result = live_read.read_sources(
        "co-1", ["fireflies"], query="calls in june",
        constraints={"since": "2026-06-01", "until": "2026-06-30"},
    )

    text = result.read[0].text
    assert "0 recorded calls in the index between 2026-06-01 and 2026-06-30" in text
    assert "522 indexed calls outside this window" in text


def test_a_long_window_rolls_up_by_month(stub_registry, monkeypatch):
    """A multi-year window at one line per week would overflow the per-source
    budget and be truncated from the tail — deleting the newest periods, the
    ones the question is about. Past `_MAX_WEEK_LINES` weeks the digest groups
    by month instead."""
    import app.call_index as call_index

    calls = [
        _indexed_call("1", "2024-01-15T15:00:00+00:00", "Kickoff", "Acme"),
        _indexed_call("2", "2026-07-15T15:00:00+00:00", "Renewal", "Acme"),
    ]
    monkeypatch.setattr(
        call_index, "list_calls",
        lambda eid, *, since=None, until=None, limit=50: calls,
    )
    monkeypatch.setattr(
        call_index, "count_calls", lambda eid, since=None, until=None: 2
    )

    result = live_read.read_sources(
        "co-1", ["fireflies"], query="all our calls ever",
        constraints={"since": "2024-01-01", "until": "2026-08-01"},
    )

    text = result.read[0].text
    assert "January 2024: 1 call(s) — Acme" in text
    assert "July 2026: 1 call(s) — Acme" in text
    assert "week of" not in text


def test_github_reads_synced_prs_because_live_search_needs_a_repo(
    stub_registry, monkeypatch
):
    """Every live GitHub tool requires `repo` in owner/name form and there is no
    repo-enumeration tool, so a source-agnostic read cannot go live at all."""
    import app.db as db

    monkeypatch.setattr(
        db, "list_open_pull_requests",
        lambda eid: [
            {"repo_full_name": "acme/web", "pr_number": 12,
             "title": "checkout redesign", "is_draft": False},
            {"repo_full_name": "acme/api", "pr_number": 13,
             "title": "unrelated", "is_draft": False},
        ],
    )

    result = live_read.read_sources("co-1", ["github"], query="checkout")

    assert "acme/web#12" in result.read[0].text
    assert "acme/api#13" not in result.read[0].text


def test_a_local_leg_failure_degrades_that_source_only(stub_registry, monkeypatch):
    import app.db as db

    monkeypatch.setattr(
        db, "list_open_pull_requests",
        lambda eid: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    stub_registry["jira"] = _StubAdapter(text="- PROJ-1")

    result = live_read.read_sources("co-1", ["github", "jira"], query="x")

    assert [s.key for s in result.read] == ["jira"]
    assert next(s for s in result.sources if s.key == "github").status == (
        live_read.STATUS_ERROR
    )


# ── the rendered block ───────────────────────────────────────────────────────


def test_the_block_is_empty_when_nothing_was_planned(stub_registry):
    assert live_read.read_sources("co-1", [], query="x").render_block() == ""


def test_the_block_names_each_source_it_did_read(stub_registry):
    stub_registry["jira"] = _StubAdapter(text="- PROJ-1 · checkout")
    stub_registry["slack"] = _StubAdapter(text="- #eng: shipped")

    block = live_read.read_sources(
        "co-1", ["jira", "slack"], query="checkout"
    ).render_block()

    assert "## Live source reads" in block
    assert "### Jira" in block and "PROJ-1" in block
    assert "### Slack" in block and "shipped" in block
    # Everything landed, so there is no unread section to warn about.
    assert "Sources NOT read" not in block
