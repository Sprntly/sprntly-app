"""A sweep leg that FAILED must not write its failure into a tenant's knowledge graph.

THE DEFECT THIS EXISTS FOR (2026-08-07, PR #1113 / T1). The Google Meet leg
returned PROSE on failure — a string like "(Google Meet did not respond in
time)" — instead of raising. `sweep._run_live` classifies any non-empty text as
`STATUS_OK`, so the leg was reported to the model as READ, its text was rendered
into the prompt as source data, and `sweep_persist.kickoff_sweep_persist`
promoted it into `extract_document` — writing a timeout notice into tenants'
knowledge graphs as though it were call content.

WHY A PARAMETRISED CHECK AND NOT A GOOGLE-MEET TEST. Nothing about that bug was
specific to Google Meet. It is a property of the boundary between `dispatch()`
and `_run_live`: EVERY adapter that reports failure by returning a string rather
than raising lands in `STATUS_OK`. So this file parametrises over
`sweep._LIVE_LEGS` itself — a leg added tomorrow is covered the moment it is
registered, with nobody needing to remember this file exists. That is the shape
the batch's review asked for: cover future additions by construction, not from a
hand-maintained list.

HOW THE FAILURE IS INJECTED, and why it needs no per-adapter knowledge. Every
adapter reaches its upstream in one of two ways: through `requests`, or through
whatever credential object it was handed. So the harness breaks BOTH — every
`requests` entrypoint raises `requests.Timeout`, and the session handle is an
object on which any interaction raises the same. Whatever the adapter tries
first, it fails, without this file knowing which adapter does which. That is
what makes it construction-general instead of a five-entry lookup table.

NON-VACUITY. A harness that never reaches the adapter would pass every assertion
below while proving nothing — the exact "the fixture does not contain what the
test is named for" failure this batch produced four times. Two things prevent
it here: `dispatch` is CALLED DIRECTLY (so entry is not in doubt), and
`test_the_harness_actually_breaks_the_transport` pins that the injected failure
is genuinely reached rather than short-circuited by an input guard.

CURRENT KNOWN DEFECTS: `slack` and `hubspot` both return prose on timeout TODAY,
on main — see `_RETURNS_PROSE_ON_FAILURE`. They are recorded as strict xfails so
this file is green on a green main while still failing the moment a THIRD
adapter joins them, and failing loudly the moment either is fixed without the
registry being updated.

Fast lane: no network, no DB, milliseconds.
"""
from __future__ import annotations

import collections
from unittest import mock

import pytest
import requests

from app.connector_lookup import base as ca
from app.connector_lookup import registry, sweep, sweep_persist

_EID = "co-sweep-guard"
_QUESTION = "what is happening with pricing and churn this quarter"


# ---------------------------------------------------------------------------
# Adapters that report a transport failure by RETURNING TEXT instead of raising.
#
# Each of these is a live defect on main with the same shape as #1113's Google
# Meet leg: a timeout notice reaches the model as source data and is written to
# the tenant's KG. They are pinned here so this file is green on a green main —
# NOT because the behaviour is acceptable.
#
# To fix an adapter: make its `except requests.Timeout` re-raise (or raise a
# typed error the sweep classifies) rather than `return f"(... timed out ...)"`.
# Then delete its entry here; the strict xfail will fail until you do.
# ---------------------------------------------------------------------------
_RETURNS_PROSE_ON_FAILURE: dict[str, str] = {
    "slack": (
        "connector_lookup/slack.py `except requests.Timeout: return f\"(Slack "
        "timed out on {name} ...)\"` — a timeout becomes STATUS_OK text."
    ),
    "hubspot": (
        "connector_lookup/hubspot.py `except requests.Timeout: return f\"(HubSpot "
        "timed out on {name} ...)\"` — same shape."
    ),
}

_LEG_IDS = sorted(sweep._LIVE_LEGS)


def _xfail_if_known(provider: str):
    reason = _RETURNS_PROSE_ON_FAILURE.get(provider)
    if reason is None:
        return []
    return [
        pytest.mark.xfail(
            strict=True,
            reason=(
                f"KNOWN DEFECT, live on main: {reason} Fix the adapter and "
                "delete its entry from _RETURNS_PROSE_ON_FAILURE."
            ),
        )
    ]


def _leg_params():
    return [
        pytest.param(p, marks=_xfail_if_known(p), id=p) for p in _LEG_IDS
    ]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _boom(*_a, **_k):
    raise requests.Timeout("sweep-guard: upstream is down")


class _DeadHandle:
    """A credential handle whose every use fails like a dead upstream.

    Adapters hold wildly different handle types — a bare token string, a
    dataclass of tokens, a Google API service object. Rather than build five
    fakes (which would rot the moment a sixth adapter lands), this fails on ANY
    interaction, so it stands in for all of them.
    """

    def __getattr__(self, _name):
        return _boom

    def __call__(self, *_a, **_k):
        _boom()

    def __str__(self):  # some adapters interpolate the handle into a header
        return "dead-token"


class _DeadTransport:
    """Every `requests` entrypoint raises, and the attempt is counted."""

    def __init__(self):
        self.attempts = 0
        self._patches: list = []

    def _raise(self, *_a, **_k):
        self.attempts += 1
        raise requests.Timeout("sweep-guard: upstream is down")

    def __enter__(self):
        for fn in ("request", "get", "post", "put", "patch", "delete", "head"):
            self._patches.append(mock.patch.object(requests, fn, self._raise))
        self._patches.append(
            mock.patch.object(requests.Session, "request", self._raise)
        )
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *_exc):
        for patch in self._patches:
            patch.stop()
        return False


def _session_for(provider: str) -> ca.LookupSession:
    # `extras` is a defaultdict because at least one adapter (jira) indexes it
    # for a pending-change container before doing anything else. A KeyError
    # there would raise, which this file scores as PASS — so it would hide the
    # very case it is checking. Defaulting keeps the adapter running to the
    # point where it actually touches the upstream.
    return ca.LookupSession(
        provider=provider, handle=_DeadHandle(), extras=collections.defaultdict(dict)
    )


def _dispatch_under_failure(provider: str, entry: str = "dispatch"):
    """`("raised", exc)` or `("returned", text)` for one leg's dead-upstream call.

    `entry` selects which of the adapter's TWO entry points to drive.
    `_AdapterLeg.run` prefers the optional `dispatch_records` and only falls
    back to `dispatch`, so checking `dispatch` alone leaves the path the sweep
    actually takes for most adapters unexamined — and it is that path whose
    return value becomes `SourceResult.text`.
    """
    adapter = registry.provider_for(provider)
    assert adapter is not None, f"{provider} is in _LIVE_LEGS but has no adapter"
    fn = getattr(adapter, entry, None)
    if fn is None:
        return "absent", None
    leg = sweep._LIVE_LEGS[provider]
    inp = leg.build_input(sweep.sweep_terms(_QUESTION))
    with _DeadTransport():
        try:
            out = fn(_session_for(provider), leg.tool, inp)
        except Exception as exc:  # noqa: BLE001 — the outcome under test
            return "raised", exc
    # `dispatch_records` returns `(text, records) | None`; `None` means "this
    # leg cannot produce records for this call", which is a fallback to
    # `dispatch`, not a failure report.
    if entry == "dispatch_records":
        if out is None:
            return "absent", None
        out = out[0]
    return "returned", out


# ---------------------------------------------------------------------------
# Non-vacuity pins — these run BEFORE anyone may trust the checks below
# ---------------------------------------------------------------------------


def test_the_registry_is_not_empty():
    """Parametrising over an empty registry generates zero tests and reports
    success. Pin the floor so this file cannot silently become a no-op."""
    assert len(_LEG_IDS) >= 5, f"_LIVE_LEGS looks wrong: {_LEG_IDS}"


def test_the_harness_actually_breaks_the_transport():
    """Proof the injected failure is REACHED, not short-circuited.

    Without this, an adapter that returned early on an input guard — or a
    `requests` patch that missed the entrypoint the adapter uses — would make
    every check below vacuous while green. Jira is the pin because it is known
    to propagate; if this stops raising a Timeout, the harness is broken, not
    Jira.
    """
    outcome, payload = _dispatch_under_failure("jira")
    assert outcome == "raised", (
        f"the dead-transport harness no longer reaches Jira's upstream — it "
        f"returned {payload!r} instead of raising. Every other check in this "
        "file is vacuous until this is fixed."
    )


def test_sweep_terms_survive_the_probe_question():
    """The probe question must clear MIN_TERMS, or `sweep()` returns early and
    the end-to-end checks below exercise nothing."""
    terms = sweep.sweep_terms(_QUESTION)
    assert len(terms) >= sweep.MIN_TERMS, (
        f"probe question yields only {terms} — below MIN_TERMS "
        f"({sweep.MIN_TERMS}), so sweep() would short-circuit"
    )


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", ["dispatch", "dispatch_records"])
@pytest.mark.parametrize("provider", _leg_params())
def test_a_dead_upstream_raises_rather_than_returning_prose(provider, entry):
    """A leg's failure must be an EXCEPTION, not a sentence.

    `sweep._run_live` classifies by emptiness: `if text and text.strip():
    result.status = STATUS_OK`. There is no other signal. So an adapter that
    returns "(X timed out ...)" has told the sweep it succeeded, and the sweep
    has no way to know better. Raising is the only way to say "this failed".

    Run for BOTH entry points, because `_AdapterLeg.run` prefers
    `dispatch_records` and either one's return value can become the source
    text. An adapter that raises from `dispatch` but returns prose from
    `dispatch_records` is still broken on the path the sweep takes.
    """
    outcome, payload = _dispatch_under_failure(provider, entry)
    if outcome == "absent":
        pytest.skip(f"{provider} implements no {entry} for this tool")
    assert outcome == "raised", (
        f"{provider}'s {entry} RETURNED {payload!r} when its upstream was "
        "dead. sweep._run_live will read that as STATUS_OK, render it into the "
        "prompt as source data, and sweep_persist will consider it for the "
        "tenant's knowledge graph. Raise instead of returning a failure notice."
    )


@pytest.mark.parametrize("provider", _leg_params())
def test_a_dead_upstream_is_reported_unread_and_never_persisted(provider):
    """End-to-end: run the real sweep against a dead upstream and prove the
    failure reaches neither the prompt nor the knowledge graph.

    This is the assertion that matches the customer-visible harm. The one above
    pins the mechanism; this one pins the consequence, so a future fix that
    changes HOW failure is signalled still has to keep the outcome.
    """
    adapter = registry.provider_for(provider)
    persisted: list = []
    dispatched: list = []

    # Spy BOTH entry points. `_AdapterLeg.run` prefers the OPTIONAL
    # `dispatch_records` and only falls back to `dispatch`, so a spy on
    # `dispatch` alone sees nothing for every records-capable adapter — which is
    # most of them. Watching one of two entry points and calling that "the leg
    # ran" is the same mistake in miniature as the gates above.
    _spies = []
    for _name in ("dispatch", "dispatch_records"):
        _real = getattr(adapter, _name, None)
        if _real is None:
            continue

        def _watched(*args, _r=_real, **kwargs):
            dispatched.append((args, kwargs))
            return _r(*args, **kwargs)

        _spies.append(mock.patch.object(adapter, _name, _watched))

    for _spy in _spies:
        _spy.start()

    with mock.patch.object(
        registry, "connected_providers", lambda _eid: [provider]
    ), mock.patch.object(
        registry, "provider_for", lambda _name: adapter
    ), mock.patch.object(
        adapter, "open_session", lambda _eid: _session_for(provider)
    ), mock.patch.object(
        # Two gates stand between `sweep()` and the leg, and BOTH are orthogonal
        # to what this test asserts. Neutralising them is not weakening the
        # test — leaving them in place is what made it vacuous:
        #
        #  - `provider_enabled` is #1113's rollout flag, DEFAULT OFF for asana
        #    and google_meet, so those legs never ran here at all;
        #  - `_credential_is_refresh_free` is #1113's open_session write guard,
        #    which declines any provider in `_REFRESHES_ON_OPEN` whose token
        #    this harness cannot prove fresh — i.e. jira, confluence, hubspot,
        #    asana and google_meet.
        #
        # Together those meant this test only ever reached TWO of seven legs,
        # and passed green for the other five without executing them. It was
        # caught by the strict xfail on hubspot flipping to XPASS — hubspot
        # "passed" because it was declined before it could return prose, not
        # because it stopped returning prose. That is exactly the
        # fixture-does-not-contain-what-the-name-claims class this batch is
        # about, reproduced inside a guard written against it, which is why the
        # `assert dispatched` below is now mandatory rather than tidy.
        sweep, "provider_enabled", lambda _p: True
    ), mock.patch.object(
        sweep, "_credential_is_refresh_free", lambda _eid, _p: True
    ), mock.patch.object(
        sweep, "_has_calls", lambda _eid: False
    ), mock.patch.object(
        sweep, "_has_github", lambda _eid: False
    ), _DeadTransport():
        try:
            result = sweep.sweep(_EID, _QUESTION)
        finally:
            for _spy in _spies:
                _spy.stop()

    assert dispatched, (
        f"the sweep never dispatched to {provider} — a rollout flag, a "
        "credential gate or provider discovery dropped the leg before it ran. "
        "Every assertion below would pass without exercising anything. Fix the "
        "harness; do not weaken the assertions."
    )

    read_keys = [s.key for s in result.read]
    assert provider not in read_keys, (
        f"{provider} FAILED but the sweep reported it as READ. Its failure text "
        f"will be rendered into the prompt as source data: "
        f"{[s.text for s in result.read if s.key == provider]!r}"
    )

    unread = {s.key: s for s in result.unread}
    assert provider in unread, (
        f"{provider} vanished from the sweep entirely — a source that failed "
        "must be REPORTED as not-read, not silently dropped, or the model "
        "cannot say which sources it did not see."
    )
    assert unread[provider].unread_reason().strip(), (
        f"{provider} is listed as unread with no reason — the model needs the "
        "reason to distinguish 'searched and found nothing' from 'never opened'"
    )

    # The persistence half. `kickoff_sweep_persist` reads `result.read`, so a
    # source correctly excluded above can never reach it — but assert on the
    # thread it starts rather than re-deriving that, because the coupling is
    # exactly what a future refactor could break without touching either side.
    with mock.patch.object(sweep_persist.threading, "Thread") as thread:
        thread.side_effect = lambda **kw: persisted.append(kw) or mock.MagicMock()
        started = sweep_persist.kickoff_sweep_persist(_EID, result)

    assert not started and not persisted, (
        f"a sweep whose only source ({provider}) FAILED still kicked off "
        "knowledge-graph persistence. A failure notice written to the KG is "
        "indistinguishable from real content on every future read."
    )


def test_known_prose_registry_has_no_stale_entries():
    """The registry may only shrink, and only for providers that still exist."""
    unknown = sorted(set(_RETURNS_PROSE_ON_FAILURE) - set(_LEG_IDS))
    assert not unknown, (
        "these _RETURNS_PROSE_ON_FAILURE entries name providers that are no "
        f"longer live legs: {unknown}. Delete them."
    )
