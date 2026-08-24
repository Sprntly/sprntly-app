"""Message Batches — half price for work nobody is waiting on.

The contract these pin is mostly about what happens when batching does NOT
work: `run_batch` returns None and the caller runs its normal synchronous path.
A cost optimisation that can raise is worse than no optimisation, so every
failure mode here is a `None`, not an exception.

The two expensive mistakes have their own tests: billing twice (a batch that
misses its deadline must be CANCELLED before the caller retries synchronously)
and mis-metering (batched spend must be recorded at half, or the dashboard
over-reports it by exactly 2x and the saving looks like it never happened).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import llm_batch
from app.llm_batch import BatchRequest


def _msg(model="claude-sonnet-4-6", **usage):
    u = {"input_tokens": 100, "output_tokens": 50,
         "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    u.update(usage)
    return SimpleNamespace(model=model, usage=SimpleNamespace(**u))


class _FakeBatches:
    """Stands in for `client.messages.batches`."""

    def __init__(self, results, *, ends_after_polls=0, create_raises=None):
        self._results = results
        self._ends_after = ends_after_polls
        self._polls = 0
        self._create_raises = create_raises
        self.cancelled: list[str] = []
        self.created = 0

    def create(self, requests):
        self.created += 1
        if self._create_raises:
            raise self._create_raises
        self.requests = requests
        return SimpleNamespace(id="msgbatch_test")

    def retrieve(self, batch_id):
        self._polls += 1
        status = "ended" if self._polls > self._ends_after else "in_progress"
        return SimpleNamespace(id=batch_id, processing_status=status)

    def results(self, batch_id):
        return iter(self._results)

    def cancel(self, batch_id):
        self.cancelled.append(batch_id)


def _result(custom_id, message=None, type_="succeeded"):
    return SimpleNamespace(
        custom_id=custom_id,
        result=SimpleNamespace(type=type_, message=message),
    )


@pytest.fixture
def batched(monkeypatch):
    """Batching enabled, Anthropic provider, no real sleeping."""
    monkeypatch.setattr(llm_batch, "batching_enabled", lambda: True)
    monkeypatch.setattr(llm_batch.time, "sleep", lambda _s: None)
    import app.llm_keys as keys
    monkeypatch.setattr(keys, "current_provider", lambda: "anthropic")

    def _install(batches):
        client = SimpleNamespace(messages=SimpleNamespace(batches=batches))
        import app.llm as llm
        monkeypatch.setattr(llm, "get_client", lambda: client)
        return client

    return _install


# ── the "just do it normally" signals ────────────────────────────────────────


def test_disabled_flag_never_touches_the_api(monkeypatch, batched):
    fake = _FakeBatches([])
    batched(fake)
    monkeypatch.setattr(llm_batch, "batching_enabled", lambda: False)
    assert llm_batch.run_batch([BatchRequest("a", {})]) is None
    assert fake.created == 0, "a disabled switch must not submit anything"


def test_empty_request_list_is_a_no_op(batched):
    fake = _FakeBatches([])
    batched(fake)
    assert llm_batch.run_batch([]) is None
    assert fake.created == 0


def test_non_anthropic_company_falls_back(monkeypatch, batched):
    fake = _FakeBatches([])
    batched(fake)
    import app.llm_keys as keys
    monkeypatch.setattr(keys, "current_provider", lambda: "openai")
    assert llm_batch.run_batch([BatchRequest("a", {})]) is None
    assert fake.created == 0, "there is no OpenAI batches endpoint to call"


def test_api_error_returns_none_rather_than_raising(batched):
    fake = _FakeBatches([], create_raises=RuntimeError("batches are down"))
    batched(fake)
    assert llm_batch.run_batch([BatchRequest("a", {})]) is None


# ── the happy path ───────────────────────────────────────────────────────────


def test_results_come_back_keyed_by_custom_id(batched):
    fake = _FakeBatches([_result("a", _msg()), _result("b", _msg())])
    batched(fake)
    out = llm_batch.run_batch([BatchRequest("a", {}), BatchRequest("b", {})])
    assert set(out) == {"a", "b"}


def test_params_are_passed_through_untouched(batched):
    """The sync fallback sends the SAME dict to `messages.create`, so this
    module must not rewrite it — that is how the two paths stay identical."""
    fake = _FakeBatches([_result("a", _msg())])
    batched(fake)
    params = {"model": "claude-sonnet-4-6", "max_tokens": 64,
              "system": [{"type": "text", "text": "S",
                          "cache_control": {"type": "ephemeral"}}],
              "messages": [{"role": "user", "content": "hi"}]}
    llm_batch.run_batch([BatchRequest("a", params)])
    assert fake.requests == [{"custom_id": "a", "params": params}]


def test_only_succeeded_results_are_returned(batched):
    """A partially-failed batch still yields its good results; the caller
    handles the missing ids the way it handles any other failure."""
    fake = _FakeBatches([
        _result("ok", _msg()),
        _result("bad", None, type_="errored"),
    ])
    batched(fake)
    out = llm_batch.run_batch([BatchRequest("ok", {}), BatchRequest("bad", {})])
    assert set(out) == {"ok"}


# ── the two expensive mistakes ───────────────────────────────────────────────


def test_deadline_cancels_the_batch_before_falling_back(batched):
    """Billing twice is the failure mode. A submitted batch keeps processing
    (and billing) whether or not we wait for it, so the caller's synchronous
    retry must not run alongside a live batch of the same requests."""
    fake = _FakeBatches([], ends_after_polls=10_000)  # never ends
    batched(fake)
    out = llm_batch.run_batch([BatchRequest("a", {})], deadline_s=0.0)
    assert out is None
    assert fake.cancelled == ["msgbatch_test"], "must cancel before falling back"


def test_batched_results_are_metered_at_half_price(batched, monkeypatch):
    """`install_metering` wraps `messages.create` and never sees the batches
    endpoint, so these rows only exist because `_meter_results` writes them —
    and they must carry the batch multiplier."""
    seen: list[dict] = []
    import app.llm_metering as metering
    monkeypatch.setattr(metering, "record_external_usage",
                        lambda **kw: seen.append(kw))
    fake = _FakeBatches([_result("a", _msg()), _result("b", _msg())])
    batched(fake)
    llm_batch.run_batch([BatchRequest("a", {}), BatchRequest("b", {})])

    assert len(seen) == 2, "one usage row per batched result"
    assert all(k["cost_multiplier"] == 0.5 for k in seen)
    assert all(k["provider"] == "anthropic" for k in seen)


def test_metering_failure_never_breaks_the_batch(batched, monkeypatch):
    """Fail-soft, matching llm_metering: losing a usage row must not lose the
    work the row was describing."""
    import app.llm_metering as metering

    def boom(**kw):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(metering, "record_external_usage", boom)
    fake = _FakeBatches([_result("a", _msg())])
    batched(fake)
    out = llm_batch.run_batch([BatchRequest("a", {})])
    assert out is not None and set(out) == {"a"}


def test_cost_multiplier_halves_the_estimate(monkeypatch):
    """The discount lands on the PRICE, never the token counts — the counts are
    ground truth and must stay re-rateable."""
    import app.llm_metering as metering
    rows: list[dict] = []
    monkeypatch.setattr("app.db.llm_usage.record_usage",
                        lambda **kw: rows.append(kw))
    monkeypatch.setattr("app.llm_keys.current_company_id", lambda: "co-1")

    msg = _msg(input_tokens=1_000_000, output_tokens=0)
    for mult in (1.0, 0.5):
        metering.record_external_usage(
            key_mode="platform", provider="anthropic",
            model="claude-sonnet-4-6", message=msg,
            started_at=0.0, cost_multiplier=mult,
        )
    full, half = rows[0], rows[1]
    assert full["input_tokens"] == half["input_tokens"] == 1_000_000
    assert half["est_cost_usd"] == pytest.approx(full["est_cost_usd"] / 2)


# ── the seam in app.llm ──────────────────────────────────────────────────────
#
# `_create_maybe_batched` is where a call site's "nothing is waiting on this"
# turns into half price. The rules that matter are the REFUSALS: a shape that
# cannot survive batching must silently keep the live path rather than lose
# streaming or a timeout.


def _seam(monkeypatch, *, batch_returns=None):
    import app.llm as llm
    calls = {"sync": 0, "batch": 0}

    def fake_sync(client, **kw):
        calls["sync"] += 1
        return "SYNC"

    def fake_run_batch(requests, **kw):
        calls["batch"] += 1
        return batch_returns

    monkeypatch.setattr(llm, "_create_with_retries", fake_sync)
    monkeypatch.setattr(llm_batch, "run_batch", fake_run_batch)
    return llm, calls


def test_batch_false_always_takes_the_live_path(monkeypatch):
    llm, calls = _seam(monkeypatch)
    out = llm._create_maybe_batched(object(), batch=False, model="m")
    assert out == "SYNC" and calls["batch"] == 0


def test_batch_true_uses_the_batch_result(monkeypatch):
    llm, calls = _seam(monkeypatch, batch_returns={"r0": "BATCHED"})
    out = llm._create_maybe_batched(object(), batch=True, model="m")
    assert out == "BATCHED" and calls["sync"] == 0


def test_batch_falling_through_runs_the_live_path(monkeypatch):
    """run_batch returning None is the documented "just do it normally" signal."""
    llm, calls = _seam(monkeypatch, batch_returns=None)
    out = llm._create_maybe_batched(object(), batch=True, model="m")
    assert out == "SYNC" and calls["batch"] == 1


@pytest.mark.parametrize("kw", [
    {"stream": True},
    {"on_delta": lambda _t: None},
    {"on_json_delta": lambda _t: None},
    {"timeout": 30.0},
])
def test_shapes_that_cannot_batch_keep_the_live_path(monkeypatch, kw):
    """Streaming has no deltas to forward from a finished batch, and a
    per-request timeout bounds a synchronous read that no longer exists."""
    llm, calls = _seam(monkeypatch, batch_returns={"r0": "BATCHED"})
    out = llm._create_maybe_batched(object(), batch=True, model="m", **kw)
    assert out == "SYNC", f"{kw} must not be batched"
    assert calls["batch"] == 0


def test_batched_request_is_byte_identical_to_the_sync_one(monkeypatch):
    """Both paths consume the kwargs `_build_base_kwargs` produced, so they
    cannot drift — the batch must not rewrite them."""
    import app.llm as llm
    seen = {}
    monkeypatch.setattr(llm, "_create_with_retries", lambda c, **kw: "SYNC")
    monkeypatch.setattr(
        llm_batch, "run_batch",
        lambda reqs, **kw: seen.update(params=reqs[0].params) or {"r0": "B"},
    )
    kwargs = {"model": "claude-sonnet-4-6", "max_tokens": 64,
              "system": "S", "messages": [{"role": "user", "content": "u"}]}
    llm._create_maybe_batched(object(), batch=True, **kwargs)
    assert seen["params"] == kwargs


def test_call_md_also_honours_batch(monkeypatch):
    """`batch=True` must never be a SILENT no-op.

    Most markdown callers stream (long-output skills) and are correctly refused
    by the seam, but a non-streaming one that opts in has to actually batch —
    otherwise the flag reads as "on" while saving nothing.
    """
    import app.llm as llm
    seen = {}

    def fake_sync(client, **kw):
        seen["sync"] = True
        return SimpleNamespace(content=[], usage=None, stop_reason="end_turn",
                               model="claude-sonnet-4-6")

    def fake_run_batch(requests, **kw):
        seen["batched"] = kw.get("label")
        return {"r0": SimpleNamespace(
            content=[SimpleNamespace(type="text", text="# doc")],
            usage=None, stop_reason="end_turn", model="claude-sonnet-4-6")}

    monkeypatch.setattr(llm, "_create_with_retries", fake_sync)
    monkeypatch.setattr(llm_batch, "run_batch", fake_run_batch)
    monkeypatch.setattr(llm, "get_client", lambda: object())
    out = llm.call_md(system="s", user="u", batch=True, batch_label="cat.sum")
    assert out == "# doc"
    assert seen.get("batched") == "cat.sum"
    assert "sync" not in seen


def test_explicit_zero_deadline_is_not_treated_as_unset(monkeypatch):
    """0 is falsy; `if batch_deadline_s` would silently swap it for the 15-min
    default. An explicit deadline must be passed through as given."""
    import app.llm as llm
    seen = {}
    monkeypatch.setattr(llm, "_create_with_retries", lambda c, **kw: "SYNC")
    monkeypatch.setattr(llm_batch, "run_batch",
                        lambda reqs, **kw: seen.update(kw) or None)
    llm._create_maybe_batched(object(), batch=True, batch_deadline_s=0, model="m")
    assert seen.get("deadline_s") == 0
