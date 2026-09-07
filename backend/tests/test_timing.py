"""The [timing] instrumentation contract — the log lines the latency work
reads. One greppable shape, start and end pairs, durations in ms, and a
wrapper that keeps every exit path of a decorated function reporting."""
from __future__ import annotations

import logging

import pytest

from app.timing import timed, timed_def, timed_fn


def test_timed_emits_start_and_end_with_duration(caplog):
    with caplog.at_level(logging.INFO, logger="app.timing"):
        with timed("qa:example", ask_id=7):
            pass
    lines = [r.getMessage() for r in caplog.records]
    assert any("[timing] block=qa:example event=start ask_id=7" in l for l in lines)
    end = next(l for l in lines if "event=end" in l)
    assert "block=qa:example" in end and "dur_ms=" in end and "ask_id=7" in end


def test_timed_reports_even_when_the_block_raises(caplog):
    """A failed block must still say how long failing took — that is the line
    a latency trace needs when a leg times out."""
    with caplog.at_level(logging.INFO, logger="app.timing"):
        with pytest.raises(ValueError):
            with timed("gather:kg"):
                raise ValueError("leg died")
    assert any(
        "block=gather:kg event=end dur_ms=" in r.getMessage() for r in caplog.records
    )


def test_timed_def_wraps_without_hiding_the_function(caplog):
    @timed_def("qa:decorated")
    def add(a, b=1):
        return a + b

    with caplog.at_level(logging.INFO, logger="app.timing"):
        assert add(2, b=3) == 5
    assert add.__name__ == "add"
    assert any("block=qa:decorated event=end" in r.getMessage() for r in caplog.records)


def test_timed_fn_wraps_a_gather_leg(caplog):
    leg = timed_fn("gather:corpus", lambda: "docs")
    with caplog.at_level(logging.INFO, logger="app.timing"):
        assert leg() == "docs"
    assert any("block=gather:corpus event=end" in r.getMessage() for r in caplog.records)


# ─── The pre-call region inside a gateway block ─────────────────────────────
#
# A measured run showed 120,192ms inside `llm:recommend_synthesis` BEFORE the
# metered API call began. The gateway's start/end pair cannot tell that apart
# from a slow model — both are one long block — so the two lines below split
# it. Verified at the time to be neither a retry (zero failed ledger rows) nor
# a concurrency-gate wait (no slot-wait warning was logged).


def test_the_key_resolution_says_how_long_it_took_and_whether_it_was_cached(caplog):
    """`llm_keys._resolve` is the one read on the model-call path that logs
    NOTHING on success: a hit is free, a miss is a database round trip on a
    30-second TTL, and both used to look identical from the outside."""
    from app import llm_keys

    llm_keys._cache.clear()
    company = "00000000-0000-0000-0000-0000000000aa"

    def _config(_cid):
        return type("C", (), {
            "provider": llm_keys.PROVIDER_ANTHROPIC,
            "cipher_for": lambda self, p: None,
        })()

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            "app.db.companies.get_company_llm_config", _config, raising=False)
        with caplog.at_level(logging.INFO, logger="app.timing"):
            llm_keys._resolve(company)   # miss — goes to the database
            llm_keys._resolve(company)   # hit  — served off the TTL cache
    finally:
        monkeypatch.undo()
        llm_keys._cache.clear()

    lines = [r.getMessage() for r in caplog.records
             if "block=llm_keys:resolve" in r.getMessage()]
    assert len(lines) == 2, lines
    assert "event=end" in lines[0] and "dur_ms=" in lines[0]
    # THE HIT AND THE MISS ARE DISTINGUISHABLE, which is the entire point:
    # without this a slow call cannot be attributed to the database read.
    assert "cached=no" in lines[0], lines[0]
    assert "cached=yes" in lines[1], lines[1]


def test_the_key_resolution_timing_never_carries_key_material(caplog):
    """Latency telemetry on a secret-resolving path states a duration and a
    cache outcome. It never states the key, whether one exists, or its shape."""
    from app import llm_keys

    llm_keys._cache.clear()
    company = "00000000-0000-0000-0000-0000000000bb"
    secret = "sk-ant-do-not-log-me-0123456789"

    def _config(_cid):
        return type("C", (), {
            "provider": llm_keys.PROVIDER_ANTHROPIC,
            "cipher_for": lambda self, p: "cipher",
        })()

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            "app.db.companies.get_company_llm_config", _config, raising=False)
        monkeypatch.setattr(
            "app.llm_keys.decrypt_token_json", lambda _c: secret)
        with caplog.at_level(logging.INFO, logger="app.timing"):
            resolved = llm_keys._resolve(company)
    finally:
        monkeypatch.undo()
        llm_keys._cache.clear()

    # The resolution really did carry the key, so the absence below is a
    # statement about the LOG rather than about there being nothing to leak.
    assert resolved.company_key == secret
    for record in caplog.records:
        line = record.getMessage()
        assert secret not in line
        assert "sk-ant" not in line


def test_the_gateway_reports_the_region_before_the_api_call(caplog):
    """Emitted INSIDE the block the start/end pair already brackets, so a long
    block can be read as "slow model" or "slow before the model" rather than
    only as "slow"."""
    import inspect

    from app.graph import gateway

    src = inspect.getsource(gateway.llm_call)
    assert "event=pre_call" in src
    # BEFORE the call, not after it — a line emitted after `call_json`
    # returned would measure the very thing it is meant to exclude.
    assert src.index("event=pre_call") < src.index("output: Any = call_json(")
