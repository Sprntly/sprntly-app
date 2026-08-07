"""A question is a READ. Answering one must not change a customer's workspace.

THE DEFECT THIS EXISTS FOR (2026-08-07, PR #1111 / T3). Slack's chat read path
called `slack_oauth.fetch_conversation_history(..., auto_join=True)`. So an
ordinary "what are customers saying about pricing" made the Sprntly bot
`conversations.join` channels in a CUSTOMER'S Slack workspace — posting a
visible "@Sprntly has joined the channel" notice into rooms nobody had invited
it to. Nothing in the PR said "write"; the flag was copied from the brief
DELIVERY path, where joining is correct, into the READ path, where it is not.

THE ADJACENT ONE (PRs #1088 / #1090). `sweep.py` opens a session per leg, and
for Jira, Confluence and HubSpot `open_session` refreshes and PERSISTS an OAuth
token (`db.update_connection_tokens`). Atlassian rotates refresh tokens, so two
concurrent sweeps can strand a tenant's Jira until a human reconnects — from a
call path whose PR body said "zero writes — every leg is read-only". See
`~/sprntly-brain/learnings/sweep-open-session-mutates-auth-state.md`.

WHY THIS ASSERTS ON THE FLAG AND NOT ON "NO JOIN HAPPENED". That is the whole
lesson of T3's review. A test that drives the read path with a fixture where the
bot is already in the channel never reaches the `not_in_channel` branch, so no
join occurs and the test passes — with `auto_join=True` still in the code,
waiting for the first customer whose channel the bot was not in. **Asserting the
absence of an effect only tells you about the fixture. Asserting the argument
tells you about the code.** So `test_a_read_path_never_enables_a_write_flag`
inspects what the call was made WITH, never what it did.

COVERAGE BY CONSTRUCTION. Two registries, both ratcheted:

  - `_WRITE_ENABLING_FLAGS` — call sites that take a flag which turns a read
    into a write. `test_every_write_enabling_flag_is_registered` scans the
    connector packages for parameters named in `_WRITE_FLAG_NAMES` and fails on
    any function not listed, so a NEW `auto_join`-style parameter cannot be
    added without a decision being recorded here.
  - `_WRITE_OPS` — functions that mutate a customer's system or our stored auth.
    `test_no_write_operation_is_reachable_from_a_sweep_leg` parametrises over
    `sweep._LIVE_LEGS`, so a new leg is covered the moment it is registered.

CURRENT KNOWN DEFECT: Slack's `slack_channel_history` read passes
`auto_join=True` on main today — recorded as a strict xfail below, with #1111
as the fix.

Fast lane: no network, no DB.
"""
from __future__ import annotations

import ast
import collections
import inspect
from pathlib import Path
from unittest import mock

import pytest
import requests

from app.connector_lookup import base as ca
from app.connector_lookup import registry, sweep
from app.connectors import slack_oauth

BACKEND = Path(__file__).resolve().parents[1]

_EID = "co-readpath-guard"
_QUESTION = "what is happening with pricing and churn this quarter"


# ---------------------------------------------------------------------------
# Registry 1 — flags that turn a read into a write.
#
# `(module, function, flag)` -> whether a READ path may pass it truthy.
# `False` means: every call reached from a chat read must pass it falsey.
# ---------------------------------------------------------------------------
_WRITE_FLAG_NAMES = frozenset(
    {"auto_join", "auto_create", "auto_invite", "create_if_missing", "persist"}
)

_WRITE_ENABLING_FLAGS: dict[tuple[str, str, str], str] = {
    ("app.connectors.slack_oauth", "post_message", "auto_join"): (
        "DELIVERY path only (weekly brief). A read must never call post_message "
        "at all — see _WRITE_OPS."
    ),
    ("app.connectors.slack_oauth", "fetch_conversation_history", "auto_join"): (
        "Legitimate for delivery-adjacent callers; NEVER for an IMPLICIT read. "
        "The implicit path is checked below."
    ),
    ("app.connector_lookup.slack", "read_channel_history", "auto_join"): (
        "#1111's resolution: caller-controlled, DEFAULT TRUE. True is allowed "
        "for the EXPLICIT `slack_channel_history` tool, where the user named "
        "the channel and asked for it, so joining is the obvious repair. It "
        "MUST be False on every IMPLICIT path — a question that named no "
        "channel and no source. That is the property "
        "`test_the_implicit_voc_read_does_not_enable_auto_join` pins."
    ),
}


# ---------------------------------------------------------------------------
# Registry 2 — operations that mutate a customer's system or our stored auth.
#
# Deliberately explicit rather than inferred: 'what counts as a write' is a
# judgement, and an inferred list would either miss `update_connection_tokens`
# (which writes our DB, not theirs) or flag every helper that happens to POST.
# ---------------------------------------------------------------------------
_WRITE_OPS: list[tuple[str, str, str]] = [
    ("app.connectors.slack_oauth", "join_channel", "conversations.join — the bot enters a customer's channel"),
    ("app.connectors.slack_oauth", "leave_channel", "conversations.leave"),
    ("app.connectors.slack_oauth", "post_message", "chat.postMessage — visible to the customer"),
    ("app.connectors.slack_oauth", "post_to_target", "chat.postMessage wrapper"),
    ("app.connectors.slack_oauth", "post_dm_to_user", "chat.postMessage wrapper"),
    ("app.connectors.slack_oauth", "upload_file", "files.upload"),
    ("app.connectors.slack_oauth", "revoke_token", "auth.revoke — kills the connection"),
    ("app.db.connections", "update_connection_tokens", "rotates stored OAuth tokens; racy under concurrency (#1088/#1090)"),
]


# ---------------------------------------------------------------------------
# Harness (shared shape with test_sweep_failure_is_not_evidence.py)
# ---------------------------------------------------------------------------


def _boom(*_a, **_k):
    raise requests.Timeout("read-path guard: upstream is down")


class _DeadHandle:
    def __getattr__(self, _name):
        return _boom

    def __call__(self, *_a, **_k):
        _boom()

    def __str__(self):
        return "dead-token"


class _DeadTransport:
    def __init__(self):
        self._patches: list = []

    def __enter__(self):
        for fn in ("request", "get", "post", "put", "patch", "delete", "head"):
            self._patches.append(mock.patch.object(requests, fn, _boom))
        self._patches.append(mock.patch.object(requests.Session, "request", _boom))
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *_exc):
        for patch in self._patches:
            patch.stop()
        return False


def _session_for(provider: str) -> ca.LookupSession:
    return ca.LookupSession(
        provider=provider, handle=_DeadHandle(), extras=collections.defaultdict(dict)
    )


def _import(module_path: str):
    import importlib

    return importlib.import_module(module_path)


class _WriteSpies:
    """Replace every `_WRITE_OPS` entry with a recorder that also refuses to run.

    Refusing (rather than passing through) matters: if a read path DOES reach a
    write, this must not actually issue it against a real customer workspace on
    someone's laptop, and the recorded call is all the evidence needed.
    """

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self._patches: list = []

    def __enter__(self):
        for module_path, name, _why in _WRITE_OPS:
            module = _import(module_path)
            if not hasattr(module, name):
                continue

            def _record(*_a, _m=module_path, _n=name, **_k):
                self.calls.append((_m, _n))
                raise AssertionError(f"read path invoked write op {_m}.{_n}")

            patch = mock.patch.object(module, name, _record)
            self._patches.append(patch)
            patch.start()
        return self

    def __exit__(self, *_exc):
        for patch in self._patches:
            patch.stop()
        return False


# ---------------------------------------------------------------------------
# Non-vacuity pins
# ---------------------------------------------------------------------------


def test_the_write_op_registry_resolves():
    """Every `_WRITE_OPS` entry must name a function that exists.

    A typo'd name patches nothing, records nothing, and passes everything —
    the spy equivalent of a fixture that does not contain what it claims.
    """
    missing = [
        f"{m}.{n}" for m, n, _ in _WRITE_OPS if not hasattr(_import(m), n)
    ]
    assert not missing, (
        f"_WRITE_OPS names functions that do not exist: {missing}. Renamed or "
        "removed? Update the registry — until you do, those writes are unspied."
    )


def test_the_spy_harness_actually_catches_a_write():
    """Proof the spy fires. Without this, every 'no writes' assertion below is
    a claim about the harness, not about the code."""
    with _WriteSpies() as spies:
        with pytest.raises(AssertionError):
            slack_oauth.join_channel("xoxb-fake", "C123")
    assert spies.calls == [("app.connectors.slack_oauth", "join_channel")]


# ---------------------------------------------------------------------------
# Check 1 — the flag, not the effect
# ---------------------------------------------------------------------------


def _spy_history_calls():
    """A `fetch_conversation_history` spy plus a handle that RESOLVES.

    `_DeadHandle` is wrong for this check: the reader calls
    `handle.resolve_channel(ref)` BEFORE the call under test, so a dead handle
    raises first and the test reports a green "no auto_join" having exercised
    nothing. That happened in this file's first draft and was caught only by the
    `assert seen` pin below — which is why that pin is not optional.
    """
    seen: list[dict] = []

    def _spy(*_a, **kwargs):
        seen.append(kwargs)
        return {"messages": []}

    handle = mock.MagicMock()
    handle.resolve_channel.side_effect = lambda ref: "C0DEMOS"
    handle.bot_token = "xoxb-fake"
    return seen, handle, _spy


def test_the_implicit_voc_read_does_not_enable_auto_join():
    """The IMPLICIT path must pass auto_join=False.

    #1111 resolved this flag as caller-controlled: True is legitimate for the
    EXPLICIT `slack_channel_history` tool, where the user named the channel, and
    forbidden on any implicit path. So the property worth pinning is not "the
    flag is never True" but "the path that never asked for a channel never sets
    it" — and `slack_voc._read_one` is that path: it runs on "what are our
    customers saying?", which named no channel and no source.

    Asserting the ARGUMENT, deliberately. A test that asserted "no join
    happened" would pass against any fixture where the bot is already a member —
    which is every fixture anyone writes — while the flag sat in the code
    waiting for the first customer channel the bot was not in.
    """
    from app.connector_lookup import slack as slack_lookup
    from app.connector_lookup import slack_voc

    seen, handle, spy = _spy_history_calls()

    channel = slack_voc.VocChannel(id="C0DEMOS", name="demos")
    with mock.patch.object(slack_oauth, "fetch_conversation_history", spy):
        try:
            slack_voc._read_one(handle, channel, 7)
        except Exception:  # noqa: BLE001 — the shape of the reply is not the point
            pass

    assert seen, (
        "the implicit VoC read never reached fetch_conversation_history — this "
        "test proves nothing about auto_join until it does. Fix the harness, do "
        "not delete the test."
    )
    offenders = [kw for kw in seen if kw.get("auto_join")]
    assert not offenders, (
        "the IMPLICIT voice-of-customer read called fetch_conversation_history "
        "with auto_join=True. That makes the Sprntly bot conversations.join a "
        "channel in the CUSTOMER'S workspace and post a visible join notice, as "
        "a side effect of someone asking 'what are customers saying' — a "
        "question that named no channel at all. Only a path where the user "
        "NAMED the channel may pass it True."
    )
    assert slack_lookup.read_channel_history is not None  # the reader still exists


def test_every_write_enabling_flag_is_registered():
    """A new `auto_join`-shaped parameter cannot be added unnoticed.

    Scans the connector packages for function parameters named in
    `_WRITE_FLAG_NAMES`. Anything not in `_WRITE_ENABLING_FLAGS` fails, forcing
    whoever adds the flag to state which paths may pass it truthy — which is the
    conversation that did not happen when auto_join was copied from delivery
    into the read path.
    """
    found: set[tuple[str, str, str]] = set()
    for pkg in ("app/connectors", "app/connector_lookup"):
        for path in sorted((BACKEND / pkg).glob("*.py")):
            module_path = f"{pkg.replace('/', '.')}.{path.stem}"
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                args = node.args
                for arg in list(args.args) + list(args.kwonlyargs):
                    if arg.arg in _WRITE_FLAG_NAMES:
                        found.add((module_path, node.name, arg.arg))

    assert found, (
        "the write-flag scan found nothing at all — the packages moved or the "
        "scan is broken. It must find at least slack_oauth's auto_join."
    )
    unregistered = sorted(found - set(_WRITE_ENABLING_FLAGS))
    assert not unregistered, (
        "these functions take a parameter that can turn a read into a write, "
        "and no decision is recorded for them:\n"
        + "\n".join(f"  {m}.{fn}({flag}=...)" for m, fn, flag in unregistered)
        + "\n\nAdd each to _WRITE_ENABLING_FLAGS saying which paths may pass it "
        "truthy, and add a test that the READ path passes it falsey. This is "
        "how auto_join reached the chat read path (#1111) — copied from the "
        "delivery path, with nothing forcing the question."
    )


def test_no_stale_write_flag_registrations():
    """The registry may only describe flags that still exist."""
    live: set[tuple[str, str, str]] = set()
    for module_path, fn_name, flag in _WRITE_ENABLING_FLAGS:
        module = _import(module_path)
        fn = getattr(module, fn_name, None)
        if fn is None:
            continue
        if flag in inspect.signature(fn).parameters:
            live.add((module_path, fn_name, flag))
    stale = sorted(set(_WRITE_ENABLING_FLAGS) - live)
    assert not stale, (
        f"_WRITE_ENABLING_FLAGS entries no longer exist in the code: {stale}. "
        "Delete them; the registry is a ratchet."
    )


# ---------------------------------------------------------------------------
# Check 2 — no write op is reachable from a sweep leg, over the whole registry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("provider", sorted(sweep._LIVE_LEGS), ids=str)
def test_no_write_operation_is_reachable_from_a_sweep_leg(provider):
    """Opening and reading a source for a chat answer writes NOTHING.

    Covers both halves of the sweep leg — `open_session` (where the OAuth
    token-refresh write lives, #1088/#1090) and `dispatch` (where auto_join
    lives, #1111) — for every provider in `_LIVE_LEGS`, so a leg added later is
    included without anyone remembering this file.

    HONEST LIMIT — this catches SHALLOW writes only, and the boundary is
    narrower than "a write added to a dispatch path". Both `_DeadHandle` and
    `_DeadTransport` make the FIRST outward interaction fail, so each adapter
    stops at its first handle attribute access or first HTTP call. Concretely:

      - a write reachable BEFORE that first failure  -> CAUGHT (exit 1);
      - a write in a RECOVERY branch — `except ConfluenceAuthExpiredError:`,
        an `if error == "not_in_channel":` retry — is NOT caught, because a
        `requests.Timeout` never produces the error body those branches need.
        That is precisely the shape of the #1111 auto_join defect, so the miss
        is not hypothetical;
      - `open_session`'s refresh branch is NOT reached either: with no
        connection row it returns None before the stale-token check.

    Covering the recovery branches needs a per-provider fixture that returns a
    real error BODY rather than a transport failure. Worth building; not built
    here. Check 1 above (`auto_join`, asserted on the ARGUMENT) is what covers
    the #1111 shape today, and `_reached` below keeps this test from quietly
    degrading further.
    """
    adapter = registry.provider_for(provider)
    assert adapter is not None, f"{provider} is in _LIVE_LEGS but has no adapter"
    leg = sweep._LIVE_LEGS[provider]
    inp = leg.build_input(sweep.sweep_terms(_QUESTION))
    reached: list[str] = []

    with _WriteSpies() as spies, _DeadTransport():
        try:
            adapter.open_session(_EID)
            reached.append("open_session")
        except Exception:  # noqa: BLE001 — no credentials here; the spies are the point
            reached.append("open_session")
        try:
            adapter.dispatch(_session_for(provider), leg.tool, inp)
            reached.append("dispatch")
        except Exception:  # noqa: BLE001
            reached.append("dispatch")

    # Non-vacuity pin. The sibling file learned this the hard way (67d483ec):
    # its end-to-end check silently stopped covering 5 of 7 legs when new gates
    # landed on main, and passed green throughout. Entry is not in doubt here
    # because both calls are direct — but pinning it means a future refactor
    # that routes the sweep through some third entry point fails HERE instead of
    # turning this into a test of nothing.
    assert reached == ["open_session", "dispatch"], (
        f"{provider}: expected to enter both open_session and dispatch, got "
        f"{reached}. This test asserts nothing until both are entered."
    )
    assert not spies.calls, (
        f"answering a question by reading {provider} invoked write "
        f"operation(s) {spies.calls}. A read must not mutate a customer's "
        "workspace or rotate stored credentials — see "
        "sweep-open-session-mutates-auth-state.md."
    )
