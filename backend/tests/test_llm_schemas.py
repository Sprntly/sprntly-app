"""Lint: forced-tool schemas should describe their object nodes.

WHAT THIS IS. A style/robustness lint over every schema the app sends to
`app.llm.call_json`, not a bug detector. It flags object nodes that declare
neither `properties` nor `additionalProperties` — nodes whose contract exists
only in prose somewhere else. Such a node is perfectly legal and models DO fill
it; declaring the fields is simply a better default.

WHY IT IS A LINT AND NOT A BUG DETECTOR. This file was written after CIR run 8
(staging, 2026-08-03) came back with `state={}` and
`metadata={"status": "complete"}` beside a 28.8k-char review and 121 captured
records. The obvious theory was that a bare object node is UNFILLABLE. It is
not. FOUR candidate mechanisms were tested and ALL FOUR were refuted:

  1. Grammar constraint. `app.llm.call_json` does NOT set `strict: true` on the
     tool (see `llm.py`, the `submit_response` tool dict). Anthropic tool-use
     `input_schema` is advisory guidance without it, not an enforced grammar —
     so `{}` was always a legal completion.
  2. `required` membership. `app.graph.extractor._EXTRACT_SCHEMA` has exactly
     this shape at `$.signals[].properties`, is NOT in its item's `required`
     list, and fills anyway: 292/400 (73%) of one workspace's signals and
     235/333 (70%) of test-co signals have a non-empty properties object on the shared
     database — e.g. `{"poc_customer": "...", "recovery_eta_minutes": 15}`.
     CIR's two WERE required and came back empty: the opposite of the
     prediction.
  3. Description quality. The exact shipped shape, two arms differing only in
     description. Pointer descriptions ("see the system prompt") -> next_state
     1 key, metadata 6 keys. Self-contained descriptions carrying examples ->
     next_state 2 keys, metadata 6 keys. BOTH FILLED — and the pointer arm is
     literally the code that failed on staging.
  4. Answer length / position. On the streaming path production uses: a
     2,414-char answer filled both objects; a 32,568-char answer — LONGER than
     the real failure's 28,845 — filled them with more keys (3 and 14).

THE CAUSE IS NOT ESTABLISHED. Whatever it is lives in something specific to the
real call that the experiments did not carry: the actual `_REPORT_SYSTEM` text,
the 121-record input, the gateway wrapping, `long_output`, or a token budget
interaction. A finding here therefore means "underspecified, worth declaring" —
never "broken", and never "this is why run 8 failed".

Kept anyway, because declaring fields is sound on its own terms and the
consequences of an empty structured half are silent and compounding: an empty
`next_state` makes `choose_mode` treat every future run as a baseline, so the
company never gets a cheap Scan again; an empty `metadata` leaves
`_answer_from_run` with no window, no totals and no by-source breakdown.

FOR WHOEVER TOUCHES THIS NEXT: do not add a test asserting that bare objects
fail to fill — that claim is false four ways over. Test the OUTCOME instead,
as `test_a_schema_valid_response_persists_a_usable_state_and_metadata` in
`test_competitive_intel.py` does (a schema-valid response must leave the next
run able to Scan).
"""
from __future__ import annotations

import importlib
import pkgutil

import pytest

import app


def _walk(node, path: str):
    """Yield (path, node) for every schema node, following the containers a JSON
    Schema can nest through."""
    if not isinstance(node, dict):
        return
    yield path, node
    for name, sub in (node.get("properties") or {}).items():
        yield from _walk(sub, f"{path}.{name}")
    items = node.get("items")
    if isinstance(items, dict):
        yield from _walk(items, f"{path}[]")
    for key in ("$defs", "definitions"):
        for name, sub in (node.get(key) or {}).items():
            yield from _walk(sub, f"{path}#{name}")


def underspecified_objects(schema: dict) -> list[str]:
    """Paths of object nodes that declare no fields and no open-bag marker.

    `additionalProperties` counts as specified: it is the correct way to declare
    a genuinely open-ended map, as opposed to an object whose fields were simply
    never written down.
    """
    bad = []
    for path, node in _walk(schema, "$"):
        if node.get("type") != "object":
            continue
        if not node.get("properties") and "additionalProperties" not in node:
            bad.append(path)
    return bad


def _app_schemas() -> list[tuple[str, dict]]:
    """Every module-level dict named *SCHEMA* across `app`, with its source.

    Discovered rather than hand-listed, so a schema added tomorrow is covered
    without anyone remembering to add it here.
    """
    found: list[tuple[str, dict]] = []
    seen: set[tuple[str, str]] = set()
    for mod_info in pkgutil.walk_packages(app.__path__, "app."):
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:  # noqa: BLE001 — an unimportable module is another test's problem
            continue
        for attr in dir(mod):
            if "SCHEMA" not in attr.upper():
                continue
            value = getattr(mod, attr, None)
            if not isinstance(value, dict) or value.get("type") != "object":
                continue
            key = (mod_info.name, attr)
            if key in seen:
                continue
            seen.add(key)
            found.append((f"{mod_info.name}.{attr}", value))
    return sorted(found)


# ACCEPTED EXCEPTIONS — measured, not assumed.
#
# `$.signals[].properties` is a genuinely open-ended bag of per-signal numeric
# and categorical details; there is no fixed field list to declare, so the lint's
# usual advice ("declare the fields") does not apply. The measurement in this
# module's docstring — 73% / 70% non-empty on the shared database — is the
# evidence that the model fills it in practice, so there is no bug to fix here.
# `additionalProperties: true` would still express the intent more clearly, but
# this schema sits on the KG ingest path for every connector, so changing what
# that extractor emits deserves its own change and its own live verification.
#
# Pinned to the exact known paths rather than skipped: a NEW underspecified node
# appearing anywhere in this schema still fails the test.
_ACCEPTED: dict[str, list[str]] = {
    "app.graph.extractor._EXTRACT_SCHEMA": ["$.signals[].properties"],
}


@pytest.mark.parametrize("name,schema", _app_schemas(), ids=lambda v: v if isinstance(v, str) else "")
def test_schema_object_nodes_are_specified(name, schema):
    expected = _ACCEPTED.get(name, [])
    bad = underspecified_objects(schema)
    assert bad == expected, (
        f"{name} declares object node(s) with no `properties` and no "
        f"`additionalProperties`: {sorted(set(bad) - set(expected))}. This is a "
        "style finding, not a proven defect — models do fill such nodes (see "
        "this module's docstring). Declare the fields so the contract lives with "
        "the schema, or set `additionalProperties: true` if the bag is genuinely "
        "open-ended and add it to _ACCEPTED with the evidence."
    )


def test_the_lint_flags_the_shape_that_shipped():
    """Guard for the guard: the exact schema that went to staging must be flagged."""
    shipped = {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "next_state": {"type": "object", "description": "see the system prompt"},
            "metadata": {"type": "object", "description": "see the system prompt"},
        },
        "required": ["answer", "next_state", "metadata"],
    }
    assert underspecified_objects(shipped) == ["$.next_state", "$.metadata"]


def test_the_lint_accepts_a_declared_open_bag():
    """`additionalProperties` is a legitimate answer, and must not be flagged."""
    ok = {
        "type": "object",
        "properties": {"bag": {"type": "object", "additionalProperties": True}},
    }
    assert underspecified_objects(ok) == []


def test_the_lint_reaches_inside_arrays():
    """The accepted extractor exception lives at `$.signals[].properties`, so the
    walk has to descend through `items` or it would never see it — and the
    pinning in `_ACCEPTED` would be vacuous."""
    nested = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"bag": {"type": "object"}},
                },
            },
        },
    }
    assert underspecified_objects(nested) == ["$.rows[].bag"]
