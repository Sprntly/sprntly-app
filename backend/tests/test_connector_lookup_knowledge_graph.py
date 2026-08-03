"""The knowledge graph as a tool inside the connector-lookup loop.

Two things are pinned here, and both are honesty properties rather than
plumbing:

  1. "the graph holds nothing" and "the graph could not be read" are DIFFERENT
     strings. Collapsing a failed read into an empty result is the same class of
     bug the Confluence adapter's SEARCH_UNAVAILABLE exists to prevent — chat
     stating with confidence that we know nothing about a topic we simply failed
     to look up.
  2. The tool is wired into the loop only when asked for, so the named-source
     path (and Jira's verbatim prompt) is untouched.

No network/LLM/DB: retrieval is patched at the seam.
"""
from __future__ import annotations

import app.connector_lookup.answer as answer_mod
import app.connector_lookup.knowledge_graph as kg


# ── the tool itself ──────────────────────────────────────────────────────────

def test_a_populated_graph_is_rendered_and_labelled(monkeypatch):
    monkeypatch.setattr(
        kg, "search",
        lambda eid, q: "Sprntly knowledge graph (extracted signals, NOT document "
                       "text):\nsignal: onboarding drop-off at step 3",
    )
    out = kg.dispatch("ent", kg.TOOL_NAME, {"query": "onboarding"})
    assert "onboarding drop-off" in out


def test_query_is_required():
    assert "'query' is required" in kg.dispatch("ent", kg.TOOL_NAME, {})
    assert "'query' is required" in kg.dispatch("ent", kg.TOOL_NAME, {"query": "  "})


def _patch_graph(monkeypatch, *, retrieve=None, render=None) -> None:
    """Patch the two seams kg.search imports at call time.

    GraphFacade is constructed before retrieval runs and needs a live DB, so it
    has to be stubbed even for tests that only care about the bundle.
    """
    import app.graph.facade as facade
    import app.graph.retrieval as retrieval

    monkeypatch.setattr(facade, "GraphFacade", lambda *a, **k: object())
    if retrieve is not None:
        monkeypatch.setattr(retrieval, "retrieve_context", retrieve)
    if render is not None:
        monkeypatch.setattr(retrieval, "render_context_section", render)


def test_an_empty_graph_says_empty_not_broken(monkeypatch):
    _patch_graph(monkeypatch, retrieve=lambda *a, **k: {"empty": True})
    out = kg.search("ent", "onboarding")
    assert "nothing on this query" in out
    assert "real empty result" in out


def test_a_failed_read_is_not_reported_as_no_results(monkeypatch):
    """The distinction the whole module exists to preserve: "we found nothing"
    and "we could not look" must not read the same to the model."""
    def boom(*_a, **_k):
        raise RuntimeError("pgvector unavailable")

    _patch_graph(monkeypatch, retrieve=boom)
    out = kg.search("ent", "onboarding")
    assert "could not be read" in out
    assert "NOT a no-results answer" in out
    assert "nothing on this query" not in out


def test_no_tenant_is_never_a_cross_tenant_read():
    # Retrieval is tenant-scoped; an absent enterprise_id must short-circuit
    # rather than reach the graph unscoped.
    assert kg.search("", "onboarding") == kg.EMPTY


def test_a_graph_hit_demands_a_live_read_of_its_own_sources(monkeypatch):
    """The rule: a graph hit is a lead, not a conclusion.

    Signals are extracted at sync time, so a page edited since is described
    exactly as it was — how three populated Confluence pages were reported as
    empty. The result names the sources so the model knows which live tool to
    open, and says which reader wins on disagreement.
    """
    _patch_graph(
        monkeypatch,
        retrieve=lambda *a, **k: {
            "empty": False,
            "signals": [
                {"content": "onboarding drop-off", "source_type": "confluence"},
                {"content": "renewal risk", "source_type": "hubspot"},
                {"content": "more onboarding", "source_type": "confluence"},
            ],
        },
        render=lambda _b: "signal: onboarding drop-off at step 3",
    )
    out = kg.search("ent", "onboarding")

    assert "MUST read it live" in out
    assert "LIVE READ WINS" in out
    # Sources named explicitly, deduped, so the model can pick the right tool.
    assert "confluence, hubspot" in out


def test_the_directive_survives_an_unfamiliar_bundle_shape(monkeypatch):
    _patch_graph(
        monkeypatch,
        retrieve=lambda *a, **k: {"empty": False, "signals": [None, "junk", {}]},
        render=lambda _b: "some context",
    )
    out = kg.search("ent", "onboarding")
    assert "the source these came from" in out
    assert "MUST read it live" in out


def test_a_long_bundle_is_truncated_visibly(monkeypatch):
    _patch_graph(
        monkeypatch,
        retrieve=lambda *a, **k: {"empty": False},
        render=lambda _b: "x" * 20_000,
    )
    out = kg.search("ent", "onboarding")
    assert len(out) < 20_000
    assert "truncated" in out


# ── wiring into the loop ─────────────────────────────────────────────────────

class _FakeSession:
    def __init__(self):
        self.notes, self.extras = [], {}


class _FakeProvider:
    provider, display_name = "confluence", "Confluence"

    def open_session(self, _eid):
        return _FakeSession()

    def tools(self):
        return [{"name": "confluence_search", "input_schema": {}, "description": "d"}]

    def system_block(self):
        return "confluence rules"

    def dispatch(self, _s, _n, _i):
        return "page hit"


def _capture_loop(captured: dict):
    def loop(**kwargs):
        captured.update(kwargs)
        return "answered"

    return loop


def test_the_kg_tool_is_offered_when_requested():
    captured: dict = {}
    answer_mod.answer(
        enterprise_id="ent", question="what does our onboarding spec say?",
        providers=[_FakeProvider()], include_knowledge_graph=True,
        run_loop=_capture_loop(captured), log=lambda *_a: None,
    )
    names = [t["name"] for t in captured["tools"]]
    assert kg.TOOL_NAME in names
    assert "confluence_search" in names
    assert "Sprntly knowledge graph" in captured["system"]


def test_the_kg_tool_is_absent_by_default():
    captured: dict = {}
    answer_mod.answer(
        enterprise_id="ent", question="check confluence for the spec",
        providers=[_FakeProvider()],
        run_loop=_capture_loop(captured), log=lambda *_a: None,
    )
    assert kg.TOOL_NAME not in [t["name"] for t in captured["tools"]]
    assert "Sprntly knowledge graph" not in captured["system"]


def test_an_adapter_with_verbatim_copy_never_gets_a_tool_its_prompt_omits():
    """Jira passes its own long-tuned system_text. Handing that loop a tool the
    prompt never describes wastes an iteration at best."""
    captured: dict = {}
    answer_mod.answer(
        enterprise_id="ent", question="what does our onboarding spec say?",
        providers=[_FakeProvider()], include_knowledge_graph=True,
        system_text="verbatim jira prompt",
        run_loop=_capture_loop(captured), log=lambda *_a: None,
    )
    assert kg.TOOL_NAME not in [t["name"] for t in captured["tools"]]
    assert captured["system"] == "verbatim jira prompt"


def test_the_kg_tool_dispatches_with_the_callers_tenant(monkeypatch):
    captured: dict = {}
    seen: dict = {}
    monkeypatch.setattr(
        kg, "search", lambda eid, q: seen.update(eid=eid, q=q) or "graph text",
    )
    answer_mod.answer(
        enterprise_id="ent-42", question="what does our onboarding spec say?",
        providers=[_FakeProvider()], include_knowledge_graph=True,
        run_loop=_capture_loop(captured), log=lambda *_a: None,
    )
    out = captured["dispatch"](kg.TOOL_NAME, {"query": "onboarding"})
    assert out.startswith("graph text")
    assert seen == {"eid": "ent-42", "q": "onboarding"}
