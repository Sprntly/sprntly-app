"""Every answer path must know what day it is.

Reported 2026-08-02: "give me top 3 product requests from last week" was
answered with data from Jan 1-10, 2026 — seven months stale, off an uploaded
simulated CSV, presented as though it were last week. The model was never told
the date, so it could not check the evidence against the question and silently
substituted whatever period the data covered.

Stating the date is necessary but not sufficient; the instruction to FLAG a
period mismatch is what turns a wrong answer into an honest one.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.prompts import today_line


def test_today_line_states_the_actual_date():
    line = today_line(datetime(2026, 8, 2, tzinfo=timezone.utc))
    assert "02 August 2026" in line
    assert "Sunday" in line


def test_today_line_demands_relative_expressions_be_resolved():
    line = today_line()
    assert "last week" in line
    assert "Resolve every relative time expression" in line


def test_today_line_forbids_substituting_another_period():
    """The half that matters. Knowing the date does not stop a model from
    answering with the wrong period unless it is told not to."""
    line = today_line()
    assert "SAY SO EXPLICITLY" in line
    assert "state the period the evidence actually covers" in line
    assert "wrong answer, not a partial one" in line


def test_generic_ask_path_injects_the_date():
    """The path that produced the stale answer."""
    import inspect

    import app.ask_runner as ask_runner

    src = inspect.getsource(ask_runner)
    assert src.count("today_line()") >= 3, "not every ASK_SYSTEM variant is dated"


def test_qa_single_shot_injects_the_date():
    import inspect

    import app.qa_agent as qa

    assert "today_line()" in inspect.getsource(qa._answer_single_shot)


def test_ds_engine_dates_without_breaking_its_prompt_cache():
    """The DS system prompt is a cached block. The date must ride in a separate
    uncached block, or every DS run pays a fresh cache write."""
    import inspect

    import app.ds.claude_analysis as ds

    src = inspect.getsource(ds)
    assert "today_line()" in src
    # the cached block must still be the bare _SYSTEM_PROMPT
    assert '{"type": "text", "text": _SYSTEM_PROMPT, "cache_control"' in src


# ── source grounding ─────────────────────────────────────────────────────────
#
# Two reported wrong answers blamed the user's setup for a routing failure:
#   "you'd need to connect ... Fireflies"     — while Fireflies was connected
#   "No connected source covers the period"   — while the index held that week
# Both would send a PM to configure something they already have.

def test_connected_sources_line_lists_live_providers(monkeypatch):
    monkeypatch.setattr(
        "app.db.connections.list_connections",
        lambda cid: [{"provider": "fireflies", "status": "active"},
                     {"provider": "jira", "status": "active"}],
    )
    from app.prompts import connected_sources_line

    line = connected_sources_line("ent-A")
    assert "fireflies" in line and "jira" in line
    assert "Never tell the user to connect one of them" in line


def test_connected_sources_line_is_explicit_when_nothing_is_connected(monkeypatch):
    monkeypatch.setattr("app.db.connections.list_connections", lambda cid: [])
    from app.prompts import connected_sources_line

    assert "none" in connected_sources_line("ent-A")


def test_connected_sources_line_never_breaks_an_answer(monkeypatch):
    """A lookup failure must degrade to an empty string, not raise — this runs
    on the hot path of every answer."""
    def boom(cid):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.connections.list_connections", boom)
    from app.prompts import connected_sources_line

    assert connected_sources_line("ent-A") == ""


def test_every_grounding_call_uses_a_bound_variable():
    """Regression guard for a NameError introduced while wiring this in:
    compose_ask_answer takes `enterprise_id`, not `company_id`, so
    connected_sources_line(company_id) there would raise at runtime on every
    ask. A signature mismatch like that is invisible until the path executes."""
    import ast
    import inspect

    import app.ask_runner as ask_runner

    source = inspect.getsource(ask_runner)
    tree = ast.parse(source)
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        bound = {a.arg for a in fn.args.args} | {a.arg for a in fn.args.kwonlyargs}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for target in ast.walk(node):
                    if isinstance(target, ast.Name):
                        bound.add(target.id)
        body = ast.get_source_segment(source, fn) or ""
        for var in ("company_id", "enterprise_id"):
            if f"connected_sources_line({var})" in body:
                assert var in bound, (
                    f"{fn.name}() calls connected_sources_line({var}) but {var} "
                    f"is not bound there"
                )


# ── the unknown-inventory case ───────────────────────────────────────────────

def test_no_company_id_says_nothing_rather_than_claiming_nothing_is_connected():
    """The warm/predefined Ask path carries only a dataset slug, and an
    unresolvable slug leaves company_id None.

    Emitting the "nothing is connected" branch there would assert a falsehood
    with the full authority of a system prompt — on a company that may have
    every connector wired. Silence is the only safe output when we do not KNOW
    the inventory, which is the same failure this function exists to remove,
    pointed the other way.
    """
    from app.prompts import connected_sources_line

    assert connected_sources_line(None) == ""
    assert connected_sources_line("") == ""


def test_a_read_failure_says_nothing_rather_than_guessing(monkeypatch):
    """Same rule for a DB hiccup: an inventory we failed to read is unknown,
    not empty."""
    import app.db.connections as conns
    from app.prompts import connected_sources_line

    def _boom(*_a, **_k):
        raise RuntimeError("PostgREST unavailable")

    monkeypatch.setattr(conns, "list_connections", _boom)
    assert connected_sources_line("ent-A") == ""


def test_a_genuinely_empty_inventory_does_say_so(monkeypatch):
    """The negative branch still has to work — a company with nothing connected
    must not have data implied for it."""
    import app.db.connections as conns
    from app.prompts import connected_sources_line

    monkeypatch.setattr(conns, "list_connections", lambda *_a, **_k: [])
    out = connected_sources_line("ent-A")
    assert "none" in out.lower()
