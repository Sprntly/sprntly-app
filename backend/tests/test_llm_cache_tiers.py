"""Prompt-cache placement and TTL tiering — `app.llm._build_base_kwargs`.

Three behaviours are pinned here, each of which was a real defect:

  * The system prompt was cache-controlled ONLY on the branch that also had a
    `user_cacheable_prefix`. Every skill-less caller with a large static system
    prompt therefore paid full input price forever, while the comment above the
    prompt said one cache entry served every company.
  * There was one cache-write rate where the API has two, and the code always
    requested the cheaper tier while the table charged the dearer one.
  * Nothing stopped a call site marking a prefix cacheable that is below the
    acting model's minimum, which caches nothing and says nothing about it.
"""
from __future__ import annotations

from unittest import mock

import pytest

from app import llm
from app.graph import gateway
from app.llm_metering import _requested_cache_ttl
from app.llm_telemetry import CACHE_TTL_1H, CACHE_TTL_5M

# Comfortably over the sonnet floor (1024 tokens ≈ 4096 chars) and comfortably
# under the haiku one (4096 tokens ≈ 16384 chars) — the gap the guard lives in.
MID_SYSTEM = "x" * 8000
BIG_SYSTEM = "x" * 20000
SHORT_SYSTEM = "a short system prompt"


def _kwargs(**over):
    base = dict(
        model="claude-sonnet-4-6",
        max_tokens=100,
        system=SHORT_SYSTEM,
        user="the question",
        user_cacheable_prefix=None,
    )
    base.update(over)
    return llm._build_base_kwargs(**base)


def _system_blocks(kwargs):
    system = kwargs["system"]
    return system if isinstance(system, list) else []


# ─── The system prompt caches without a user prefix (the F5 regression) ──────


def test_large_system_is_cached_with_no_user_prefix():
    """The bug: `system` was only ever cache-controlled on the prefixed branch.

    `app.ask_planner` passes no prefix and no skill, so its ~7k-char
    tenant-invariant block took the early-return path and was re-processed at
    full input price on every call — 400 calls a week with cache_read and
    cache_write both flat zero in production telemetry.
    """
    kwargs = _kwargs(system=MID_SYSTEM)

    blocks = _system_blocks(kwargs)
    assert len(blocks) == 1
    assert blocks[0]["text"] == MID_SYSTEM
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    # The user turn is untouched: no prefix was asked for, so none is invented.
    assert kwargs["messages"] == [{"role": "user", "content": "the question"}]


def test_small_system_keeps_the_plain_string_form():
    """A system prompt too small to cache produces a byte-identical request to
    the one this code sent before the fix — no gratuitous shape change for the
    many callers the fix does not apply to."""
    kwargs = _kwargs(system=SHORT_SYSTEM)
    assert kwargs["system"] == SHORT_SYSTEM
    assert _system_blocks(kwargs) == []


def test_ask_planner_system_clears_the_floor():
    """The documented case, asserted against the real prompt rather than a
    stand-in: if `_PLANNER_SYSTEM` ever shrinks below the sonnet floor, the
    caching its own comment promises stops happening, silently."""
    from app.ask_planner import _PLANNER_SYSTEM, PLANNER_MODEL

    assert llm._is_cacheable(_PLANNER_SYSTEM, PLANNER_MODEL)
    kwargs = _kwargs(system=_PLANNER_SYSTEM, model=PLANNER_MODEL)
    assert _system_blocks(kwargs)[0]["cache_control"] == {"type": "ephemeral"}


# ─── The per-model minimum cacheable prefix ─────────────────────────────────


def test_minimum_cacheable_prefix_is_model_dependent():
    """Same text, three models, two answers.

    A prefix under the model's floor is not cached and the API reports nothing
    — the response just comes back with `cache_creation_input_tokens: 0`. So
    marking one is not a harmless belt-and-braces: it spends one of the four
    breakpoints a request gets and makes the telemetry claim a cache that does
    not exist.
    """
    assert llm._is_cacheable(MID_SYSTEM, "claude-sonnet-4-6")     # floor 1024 tok
    assert not llm._is_cacheable(MID_SYSTEM, "claude-haiku-4-5")  # floor 4096 tok
    assert llm._is_cacheable(BIG_SYSTEM, "claude-haiku-4-5")


def test_haiku_system_below_its_floor_is_not_marked_cacheable():
    kwargs = _kwargs(system=MID_SYSTEM, model="claude-haiku-4-5")
    assert kwargs["system"] == MID_SYSTEM
    assert _system_blocks(kwargs) == []


def test_unknown_model_gets_the_strictest_floor():
    """Fail safe, not open: a model tier nobody has added yet can only ever
    under-cache (a missed saving), never emit dead breakpoints."""
    assert not llm._is_cacheable(MID_SYSTEM, "claude-something-new")
    assert llm._is_cacheable(BIG_SYSTEM, "claude-something-new")


# ─── TTL tiering ────────────────────────────────────────────────────────────


def test_default_ttl_is_the_five_minute_tier():
    """A bare ephemeral block, which is what 1.25x buys. Pinned because the
    pricing bug was precisely a mismatch between what the code requested here
    and what the table charged for it."""
    kwargs = _kwargs(system=MID_SYSTEM, user_cacheable_prefix="p" * 20000)
    blocks = _system_blocks(kwargs) + [
        b for b in kwargs["messages"][0]["content"] if "cache_control" in b
    ]
    assert len(blocks) == 2
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in blocks)


def test_one_hour_ttl_applies_to_every_cached_block():
    """One tier per request, deliberately — `app.llm_metering` prices a single
    `cache_creation_input_tokens` total and cannot split a mixed request."""
    kwargs = _kwargs(
        system=MID_SYSTEM, user_cacheable_prefix="p" * 20000, cache_ttl=CACHE_TTL_1H
    )
    blocks = _system_blocks(kwargs) + [
        b for b in kwargs["messages"][0]["content"] if "cache_control" in b
    ]
    assert len(blocks) == 2
    assert all(
        b["cache_control"] == {"type": "ephemeral", "ttl": "1h"} for b in blocks
    )


def test_uncached_user_turn_never_gets_a_breakpoint():
    """The volatile half of the prompt stays out of the cached prefix — putting
    a breakpoint after it would key the entry to content that changes every
    call, which caches nothing and reads as if it does."""
    kwargs = _kwargs(user_cacheable_prefix="p" * 20000, cache_ttl=CACHE_TTL_1H)
    content = kwargs["messages"][0]["content"]
    assert content[1] == {"type": "text", "text": "the question"}
    assert "cache_control" not in content[1]


# ─── Which skills earn the 1-hour tier ──────────────────────────────────────


def test_no_skill_is_on_the_one_hour_tier():
    """Every skill sits on the 5-minute default, top-insights included.

    It used to be the one exception, on the strength of its gap distribution
    (86.3% of calls within an hour). That distribution still reproduces — but
    the 1-hour entries did not survive the hour: measured hit rate was 67% under
    a 5-minute gap and 11% between 5 and 15 minutes, a cliff exactly on the
    5-minute boundary, while 69.5% of calls arrive in the 5-60 minute band. The
    tier billed 2x and behaved like 5m. See gateway._LONG_CACHE_SKILLS for what
    was ruled out before removing it.

    A gap distribution is NOT sufficient evidence for this tier — hit rate
    against gap-since-previous-call is. Anything added back needs the latter.
    """
    assert gateway._LONG_CACHE_SKILLS == frozenset()
    assert gateway._cache_ttl_for("top-insights") is None
    assert gateway._cache_ttl_for("prd-author") is None
    assert gateway._cache_ttl_for(None) is None


def test_the_one_hour_tier_still_works_if_a_skill_is_added_back():
    """Emptying the set must not quietly break the mechanism — the plumbing is
    still exercised so a future re-add gets a working 1h tier, not a silent
    no-op."""
    tier = frozenset({"some-skill"})
    with mock.patch.object(gateway, "_LONG_CACHE_SKILLS", tier):
        assert gateway._cache_ttl_for("some-skill") == CACHE_TTL_1H
        assert gateway._cache_ttl_for("other-skill") is None


# ─── Metering reads the tier off the request ────────────────────────────────


def test_metering_reads_the_requested_tier_from_the_request():
    """Ground truth, not an ambient label: the response reports one
    cache-creation total and never says which tier billed it."""
    one_hour = _kwargs(
        system=MID_SYSTEM, user_cacheable_prefix="p" * 20000, cache_ttl=CACHE_TTL_1H
    )
    five_min = _kwargs(system=MID_SYSTEM, user_cacheable_prefix="p" * 20000)

    assert _requested_cache_ttl(one_hour) == CACHE_TTL_1H
    assert _requested_cache_ttl(five_min) == CACHE_TTL_5M


def test_metering_defaults_to_five_minutes_on_an_uncached_request():
    assert _requested_cache_ttl(_kwargs()) == CACHE_TTL_5M
    assert _requested_cache_ttl({}) == CACHE_TTL_5M


def test_metering_prices_a_hand_mixed_request_at_the_dearer_tier():
    """`_build_base_kwargs` never emits a mixed request, but a hand-built one
    cannot be split across a single total — over-reporting is the safer error."""
    mixed = {
        "system": [{"type": "text", "text": "s",
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "p",
             "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        ]}],
    }
    assert _requested_cache_ttl(mixed) == CACHE_TTL_1H


@pytest.mark.parametrize("messages", [None, "a plain string", [{"role": "user"}]])
def test_metering_tolerates_request_shapes_it_did_not_build(messages):
    """Metering is fail-soft by contract — it must never be the reason a call
    raises, including on a request shape it does not recognise."""
    assert _requested_cache_ttl({"messages": messages}) == CACHE_TTL_5M


# ─── Unattributed spend is loud ─────────────────────────────────────────────


def test_unattributed_call_logs_a_warning_naming_the_shape(caplog, monkeypatch):
    """`usage_context` documents an unattributed slice as "a visible prompt to
    go add the scope" — but nothing made it visible, so ~900 calls a week
    accumulated under that label with no way to tell which path produced them.

    The warning is the way: it names the model and token shape, so the path is
    greppable from one log window instead of reconstructable by correlating
    timestamps against the decision log.
    """
    import logging
    from types import SimpleNamespace

    from app import llm_keys, llm_metering

    # `_record` returns early on an unbound stack (CLI, startup probe) — there
    # is no tenant to attribute to, so there is no row and nothing to warn about.
    # Bind one so the path under test is actually reached.
    monkeypatch.setattr(llm_keys, "current_company_id", lambda: "co-1")

    message = SimpleNamespace(
        model="claude-sonnet-4-6",
        usage=SimpleNamespace(
            input_tokens=4717, output_tokens=495,
            cache_creation_input_tokens=7418, cache_read_input_tokens=1446,
        ),
    )

    with caplog.at_level(logging.WARNING, logger="app.llm_metering"):
        llm_metering._record(
            key_mode="platform", provider="anthropic",
            model="claude-sonnet-4-6", message=message, started_at=0.0,
        )

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("unattributed" in w for w in warnings), warnings
    unattributed = next(w for w in warnings if "unattributed" in w)
    assert "claude-sonnet-4-6" in unattributed
    assert "7418" in unattributed  # the cache-write shape that fingerprints it


def test_a_scoped_call_logs_no_such_warning(caplog, monkeypatch):
    import logging
    from types import SimpleNamespace

    from app import llm_keys, llm_metering
    from app.usage_context import Feature, usage_scope

    monkeypatch.setattr(llm_keys, "current_company_id", lambda: "co-1")

    message = SimpleNamespace(model="claude-sonnet-4-6", usage=SimpleNamespace(
        input_tokens=10, output_tokens=5,
        cache_creation_input_tokens=0, cache_read_input_tokens=0,
    ))

    with caplog.at_level(logging.WARNING, logger="app.llm_metering"):
        with usage_scope(feature=Feature.PRD, operation="generate"):
            llm_metering._record(
                key_mode="platform", provider="anthropic",
                model="claude-sonnet-4-6", message=message, started_at=0.0,
            )

    assert not [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and "unattributed" in r.getMessage()
    ]
