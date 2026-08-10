"""Self-tests for `tests/_must_reach.py`.

A helper whose whole job is to detect a vacuous test must itself be run against
a known-bad input, or it is one — see
`~/sprntly-brain/learnings/validate-a-guard-against-known-bad-input.md`.
"""
from __future__ import annotations

import types

import pytest

from tests._must_reach import must_reach


def _module_with_two_branches():
    """A miniature of the shape that produced the real defects: an EARLY branch
    that handles the common input, and a fallback nobody's fixture reaches."""
    mod = types.ModuleType("probe")

    def sniff(payload: str) -> str:
        return "sniffed"

    def render(payload: str) -> str:
        if payload.startswith("<style>"):
            return "recognised"  # the early branch that swallowed the fixture
        return mod.sniff(payload)

    mod.sniff = sniff
    mod.render = render
    return mod


def test_passes_when_the_named_function_is_reached():
    mod = _module_with_two_branches()
    with must_reach(mod, "sniff") as calls:
        assert mod.render("no leading style tag") == "sniffed"
    assert len(calls) == 1


def test_fails_when_an_earlier_branch_swallows_the_fixture():
    """KNOWN-BAD INPUT — the `_SNIFF_RE` shape, exactly.

    The test 'is named for' the sniff fallback, but its payload already starts
    with `<style>`, so the early branch returns first and the fallback is never
    entered. Without `must_reach` this passes; with it, it does not.
    """
    mod = _module_with_two_branches()
    with pytest.raises(AssertionError, match="never reached"):
        with must_reach(mod, "sniff"):
            assert mod.render("<style>a{}</style>body") == "recognised"


def test_exposes_the_arguments_each_call_was_made_with():
    """The stronger form: assert on the FLAG, not on the absence of an effect."""
    mod = _module_with_two_branches()
    with must_reach(mod, "sniff") as calls:
        mod.render("plain")
    (args, kwargs), = calls
    assert args == ("plain",) and kwargs == {}


def test_restores_the_original_even_when_the_body_raises():
    mod = _module_with_two_branches()
    original = mod.sniff
    with pytest.raises(ValueError):
        with must_reach(mod, "sniff"):
            raise ValueError("boom")
    assert mod.sniff is original


def test_a_caller_that_bound_the_name_at_import_time_is_INVISIBLE():
    """The documented false positive, pinned so the docstring stays honest.

    `must_reach` replaces an attribute on the module object. A caller holding a
    direct reference — the `from mod import fn` shape — never goes through it,
    so the function runs and this still reports "never reached". Asserting the
    limit rather than claiming it does not exist.
    """
    mod = _module_with_two_branches()
    bound = mod.sniff  # the `from mod import sniff` shape, in miniature
    ran: list[str] = []

    with pytest.raises(AssertionError, match="never reached"):
        with must_reach(mod, "sniff"):
            ran.append(bound("payload"))

    assert ran == ["sniffed"], (
        "the function really did run — this is a FALSE POSITIVE, and it is "
        "expected. See _must_reach.py's docstring for the workaround."
    )


def test_at_least_enforces_a_call_count():
    mod = _module_with_two_branches()
    with pytest.raises(AssertionError, match="needed 2"):
        with must_reach(mod, "sniff", at_least=2):
            mod.render("once")
