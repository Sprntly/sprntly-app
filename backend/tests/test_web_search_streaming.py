"""call_with_web_search must stream on the long read timeout.

A search-heavy request (server-side web_search runs up to max_searches
sweeps before composing) routinely outlives the default non-streaming read
timeout — the public-feedback capture pass hit exactly that httpx.ReadTimeout
on staging. Streaming is the SDK's required pattern for slow requests; these
tests pin that the call goes through the streaming path with the long timeout.
"""
from __future__ import annotations

from types import SimpleNamespace

import app.llm as llm


def test_web_search_streams_on_long_timeout(monkeypatch):
    seen: dict = {}

    def fake_create(client, *, stream=False, background=False, on_delta=None, **kwargs):
        seen["stream"] = stream
        seen["timeout"] = kwargs.get("timeout")
        seen["tools"] = kwargs.get("tools")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="found things")],
            usage=None, stop_reason="end_turn",
        )

    monkeypatch.setattr(llm, "_create_with_retries", fake_create)
    monkeypatch.setattr(llm, "get_client", lambda: object())

    meta: dict = {}
    out = llm.call_with_web_search(
        system="sys", user="u", max_searches=12, meta_out=meta,
    )
    assert out == "found things"
    assert seen["stream"] is True
    assert seen["timeout"] == llm.LONG_REQUEST_TIMEOUT_S
    assert seen["tools"][0]["max_uses"] == 12
    assert meta["stop_reason"] == "end_turn"


# --- cache breakpoint after the bound skill's method -----------------------
#
# This path used to send `method + module + caller_system` as ONE uncached
# string. Two of those three parts move on every call (the module, and the
# caller's per-pass "### THIS PASS" focus), so the concatenation was unusable
# as a cache key; the SKILL.md alone is byte-stable across passes, runs and
# tenants. These tests pin the split, and — most importantly — that splitting
# did not change the prompt the model actually reads.


def _spec(method: str, modules=None):
    return SimpleNamespace(
        id="cir", content_hash="abc123", method=method, modules=modules or {},
    )


def _capture(monkeypatch, spec, **kwargs):
    seen: dict = {}

    def fake_create(client, *, stream=False, background=False, on_delta=None, **kw):
        seen.update(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=None, stop_reason="end_turn",
        )

    monkeypatch.setattr(llm, "_create_with_retries", fake_create)
    monkeypatch.setattr(llm, "get_client", lambda: object())
    import app.skills.loader as loader
    monkeypatch.setattr(loader, "get_skill", lambda _id: spec)
    llm.call_with_web_search(skill="cir", **kwargs)
    return seen


def _flatten(system) -> str:
    """What the model reads, whether system is a str or a block list."""
    if isinstance(system, str):
        return system
    return "".join(b["text"] for b in system)


def test_method_gets_its_own_cached_system_block(monkeypatch):
    # Comfortably over sonnet's 1024-token floor (_is_cacheable works in chars).
    method_body = "M" * 8000
    seen = _capture(
        monkeypatch, _spec(method_body, {"deep": "MODULE BODY"}),
        system="caller layer", user="u", model="claude-sonnet-4-6",
        skill_module="deep",
    )
    system = seen["system"]
    assert isinstance(system, list) and len(system) == 2

    # Block 1 is the method ALONE and is the only cache breakpoint. If the
    # module or the caller's system leaked in here the entry would fork per
    # pass and the whole point would be lost.
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert method_body in system[0]["text"]
    assert "MODULE BODY" not in system[0]["text"]
    assert "caller layer" not in system[0]["text"]

    # Block 2 carries the volatile remainder and must NOT be cached.
    assert "cache_control" not in system[1]
    assert "MODULE BODY" in system[1]["text"]
    assert "caller layer" in system[1]["text"]


def test_split_preserves_the_prompt_byte_for_byte(monkeypatch):
    """The split is a cache-key change, not a prompt change.

    Pins the concatenation against the exact string the pre-split code built:
    `f"{method}\\n{system}"` with the module appended to `method`.
    """
    method_body = "M" * 8000
    seen = _capture(
        monkeypatch, _spec(method_body, {"deep": "MODULE BODY"}),
        system="caller layer", user="u", model="claude-sonnet-4-6",
        skill_module="deep",
    )
    expected = (
        f"## METHOD (skill: cir @abc123)\n{method_body}"
        f"\n\n### MODULE: deep\nMODULE BODY"
        f"\ncaller layer"
    )
    assert _flatten(seen["system"]) == expected


def test_sub_floor_method_is_not_split(monkeypatch):
    """A method under the model's minimum cacheable prefix stays a plain string.

    Marking it would burn one of the four breakpoints a request is allowed and
    make telemetry read as "we cache here" while producing no entry.
    """
    seen = _capture(
        monkeypatch, _spec("tiny method"),
        system="caller layer", user="u", model="claude-sonnet-4-6",
    )
    assert isinstance(seen["system"], str)
    assert seen["system"] == "## METHOD (skill: cir @abc123)\ntiny method\ncaller layer"


def test_method_less_call_is_unchanged(monkeypatch):
    """No skill bound -> the plain-string request shape it always had."""
    seen: dict = {}

    def fake_create(client, *, stream=False, background=False, on_delta=None, **kw):
        seen.update(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=None, stop_reason="end_turn",
        )

    monkeypatch.setattr(llm, "_create_with_retries", fake_create)
    monkeypatch.setattr(llm, "get_client", lambda: object())
    llm.call_with_web_search(system="just the caller", user="u")
    assert seen["system"] == "just the caller"


def test_unvendored_skill_still_runs_method_less(monkeypatch):
    """A skill id that names no vendored dir must not start emitting blocks."""
    seen: dict = {}

    def fake_create(client, *, stream=False, background=False, on_delta=None, **kw):
        seen.update(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=None, stop_reason="end_turn",
        )

    monkeypatch.setattr(llm, "_create_with_retries", fake_create)
    monkeypatch.setattr(llm, "get_client", lambda: object())
    import app.skills.loader as loader

    def boom(_id):
        raise loader.UnknownSkillError(_id)

    monkeypatch.setattr(loader, "get_skill", boom)
    llm.call_with_web_search(skill="gone", system="caller", user="u")
    assert seen["system"] == "caller"


def test_web_search_tool_version_is_pinned(monkeypatch):
    """The 20260209 variant measured 7.5x the tokens and 7x the latency.

    Pinned so an "upgrade" has to come with a fresh measurement.
    """
    seen: dict = {}

    def fake_create(client, *, stream=False, background=False, on_delta=None, **kw):
        seen.update(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=None, stop_reason="end_turn",
        )

    monkeypatch.setattr(llm, "_create_with_retries", fake_create)
    monkeypatch.setattr(llm, "get_client", lambda: object())
    llm.call_with_web_search(system="s", user="u")
    assert seen["tools"][0]["type"] == "web_search_20250305"
