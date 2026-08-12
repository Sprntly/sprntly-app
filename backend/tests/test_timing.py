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
