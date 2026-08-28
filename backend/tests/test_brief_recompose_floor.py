"""The recompose floor on `synthesis_brief.generate_brief_for`.

The refresh gate used to skip synthesis only when ZERO new signals had landed
since the current brief. Seeding runs before the check and connector syncs
write signals continuously, so the gate almost never fired: production ran
`compose_top_insights` 520 times in 30 days across 11 companies — several per
company per active hour — for a brief the product delivers weekly, each run an
opus composition over a 32.5k-token method block.

These tests pin the second condition that makes the gate mean something, and
the two things that must NOT change with it: an explicit user regenerate still
composes now, and a company can never be wedged into never regenerating.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import synthesis_brief


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) + delta).isoformat()


@pytest.fixture
def gated(monkeypatch):
    """`generate_brief_for` with everything but the refresh gate stubbed out.

    Returns a dict the test reads `composed` off — True when the expensive
    `run_synthesis` path was reached.
    """
    state = {"composed": False, "prior_ts": _iso(timedelta(hours=-1))}

    monkeypatch.setattr(
        synthesis_brief, "resolve_company", lambda x: ("co-1", "northwind")
    )
    monkeypatch.setattr(synthesis_brief, "GraphFacade", lambda: _Facade())
    monkeypatch.setattr(synthesis_brief, "seed_incremental", lambda *a, **k: {})
    monkeypatch.setattr(
        synthesis_brief, "has_brief_data_source", lambda *a, **k: True
    )
    monkeypatch.setattr(
        synthesis_brief, "get_current_brief",
        lambda slug: {"id": "brief-1", "generated_at": state["prior_ts"]},
    )

    def _run_synthesis(*a, **k):
        state["composed"] = True
        return {"id": "brief-2"}

    monkeypatch.setattr(synthesis_brief, "run_synthesis", _run_synthesis)
    return state


class _Facade:
    """Always reports a new signal — the condition that used to be the whole
    gate, and the one that is nearly always true in production."""

    def has_signals_since(self, *a, **k) -> bool:
        return True


def test_fresh_brief_is_not_recomposed(gated):
    """A brief minutes old is inside the floor: new signals alone no longer
    justify another opus composition."""
    gated["prior_ts"] = _iso(timedelta(minutes=-20))
    result = synthesis_brief.generate_brief_for("northwind")

    assert gated["composed"] is False
    assert result["_from_cache"] is True
    assert result["id"] == "brief-1"


def test_brief_past_the_floor_is_recomposed(gated):
    gated["prior_ts"] = _iso(timedelta(hours=-7))
    result = synthesis_brief.generate_brief_for("northwind")

    assert gated["composed"] is True
    assert result["id"] == "brief-2"


def test_force_bypasses_the_floor(gated):
    """A user clicking Regenerate composes now. The floor bounds incidental
    churn — scheduler ticks, connector syncs — never a click."""
    gated["prior_ts"] = _iso(timedelta(minutes=-2))
    result = synthesis_brief.generate_brief_for("northwind", force=True)

    assert gated["composed"] is True
    assert result["id"] == "brief-2"


def test_force_does_not_bypass_the_unchanged_kg_check(gated, monkeypatch):
    """`force` skips the floor, not the whole gate. With nothing new in the
    graph there is nothing to recompose into, and the output would be identical
    at full cost — so the click returns the existing brief."""
    class _Unchanged(_Facade):
        def has_signals_since(self, *a, **k) -> bool:
            return False

    monkeypatch.setattr(synthesis_brief, "GraphFacade", lambda: _Unchanged())
    gated["prior_ts"] = _iso(timedelta(days=-3))

    result = synthesis_brief.generate_brief_for("northwind", force=True)

    assert gated["composed"] is False
    assert result["_from_cache"] is True


def test_first_ever_brief_always_composes(gated, monkeypatch):
    """No prior brief means no floor to be inside of — the first brief must not
    be gated behind a timestamp that does not exist yet."""
    monkeypatch.setattr(synthesis_brief, "get_current_brief", lambda slug: None)
    assert synthesis_brief.generate_brief_for("northwind")["id"] == "brief-2"
    assert gated["composed"] is True


# ─── The floor fails open ───────────────────────────────────────────────────


@pytest.mark.parametrize("bad_ts", ["not-a-timestamp", "", None, 12345])
def test_unparseable_timestamp_composes_rather_than_wedging(gated, bad_ts):
    """Fails OPEN, deliberately. One extra composition is an acceptable failure
    mode; a brief frozen forever because its timestamp did not parse is not."""
    gated["prior_ts"] = bad_ts
    synthesis_brief.generate_brief_for("northwind")
    assert gated["composed"] is True


def test_future_dated_brief_composes(gated):
    """Clock skew must not read as 'always inside the floor', which would be
    the wedged-forever case arriving by a different route."""
    gated["prior_ts"] = _iso(timedelta(hours=3))
    synthesis_brief.generate_brief_for("northwind")
    assert gated["composed"] is True


# ─── Config resolution ──────────────────────────────────────────────────────


def test_floor_is_per_company_configurable(monkeypatch):
    monkeypatch.setattr(
        synthesis_brief, "config_get", lambda *a, **k: 24
    )
    assert synthesis_brief._min_recompose_hours("co-1") == 24


def test_zero_disables_the_floor(gated, monkeypatch):
    """0 restores the old any-new-signal behaviour, for a company that wants it."""
    monkeypatch.setattr(synthesis_brief, "config_get", lambda *a, **k: 0)
    gated["prior_ts"] = _iso(timedelta(minutes=-1))
    synthesis_brief.generate_brief_for("northwind")
    assert gated["composed"] is True


@pytest.mark.parametrize("junk", ["six", None, -3, object()])
def test_junk_config_falls_back_to_the_default(monkeypatch, junk):
    """A negative or non-numeric override must not disable the floor by
    accident — only a literal 0 does that."""
    monkeypatch.setattr(synthesis_brief, "config_get", lambda *a, **k: junk)
    assert synthesis_brief._min_recompose_hours("co-1") == float(
        synthesis_brief.MIN_RECOMPOSE_HOURS
    )
