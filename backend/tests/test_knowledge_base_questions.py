"""Asking what Sprntly KNOWS gets an answer, not a shrug.

Reported (2026-09-03): "the chat system does not understand KG". The chat could
reason WITH the company's memory and could not answer a question ABOUT it —
every retrieval path pulls signals relevant to a TOPIC, so "what do you know
about export failures" worked and "what do you know at all" had nothing behind
it. The reader also uses our internal word for it, which the house voice guard
forbids the assistant from ever saying back.

Three things are under test:

  * THE COUNTS ARE REAL AND EXACT — read from the tenant's own rows, never
    estimated, and never another company's.
  * AN UNREADABLE COUNT IS ZERO, NEVER A GUESS — a block that cannot read
    degrades to "nothing", which the prose then says plainly.
  * THE PLAN CAN ASK FOR IT, and asking for it narrows the grounding the same
    way the library / team / projects / backlog blocks do.
"""
from __future__ import annotations

from app.ask_planner import apply_gates
from app.db.client import require_client
from app.knowledge_base_context import knowledge_base_block

CO = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


_NOW = "2026-09-03T00:00:00+00:00"


def _signal(company_id: str, source_type: str = "customer_voice", **kw):
    row = {
        "enterprise_id": company_id,
        "source_type": source_type,
        "kind": "complaint",
        "content": "exports time out for large accounts",
        # The fake DB seeds from the real migration and applies its NOT NULLs
        # without Postgres' defaults, so the timestamps are supplied here.
        "valid_at": _NOW,
        "transaction_at": _NOW,
    }
    row.update(kw)
    require_client().table("kg_signal").insert(row).execute()


def _entity(company_id: str, type_: str = "theme", label: str = "Exports"):
    require_client().table("kg_entity").insert({
        "enterprise_id": company_id, "type": type_, "canonical_label": label,
        "valid_at": _NOW, "transaction_at": _NOW,
    }).execute()


def test_the_block_counts_what_is_actually_there(isolated_settings):
    _signal(CO)
    _signal(CO)
    _signal(CO, "project_mgmt")
    _entity(CO)
    _entity(CO, "account", "Acme")

    block = knowledge_base_block(CO)
    assert "Facts learned: 3" in block
    assert "customer conversations: 2" in block
    assert "tickets and project tools: 1" in block
    assert "themes: 1" in block
    assert "accounts: 1" in block


def test_another_company_is_not_in_the_count(isolated_settings):
    """The counts are tenant-scoped like every other read here. A number that
    included someone else's rows would be a cross-tenant leak wearing the
    disguise of a statistic."""
    _signal(CO)
    for _ in range(5):
        _signal(OTHER)
    assert "Facts learned: 1" in knowledge_base_block(CO)


def test_a_source_with_nothing_in_it_is_not_listed(isolated_settings):
    """A breakdown padded with zeroes reads as a system reporting on itself
    rather than on the reader's data."""
    _signal(CO, "customer_voice")
    block = knowledge_base_block(CO)
    assert "customer conversations: 1" in block
    assert "revenue and CRM" not in block


def test_an_empty_memory_says_so_rather_than_describing_nothing(isolated_settings):
    block = knowledge_base_block(CO)
    assert "Facts learned: 0" in block
    assert "NOTHING HAS BEEN LEARNED YET" in block
    # …and it says what to do about it, because that is the whole answer for a
    # workspace that has connected nothing yet.
    assert "Sources" in block


def test_an_unreadable_count_is_zero_not_a_guess(isolated_settings, monkeypatch):
    """The rule every block in this family follows: a number we could not read
    must never become a number we made up."""
    import app.knowledge_base_context as kb

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.client.require_client", boom)
    block = kb.knowledge_base_block(CO)
    assert "Facts learned: 0" in block
    assert "NOTHING HAS BEEN LEARNED YET" in block


def test_no_tenant_means_no_block(isolated_settings):
    assert knowledge_base_block(None) == ""
    assert knowledge_base_block("") == ""


def test_the_block_teaches_the_readers_own_words(isolated_settings):
    """The reader asks about their "knowledge graph" or "KG" — our internal
    words, which `prompts.VOICE_GUARD` forbids in output. The block has to make
    the question answerable without making the answer a lesson in our
    architecture."""
    _signal(CO)
    block = knowledge_base_block(CO)
    assert "knowledge graph" in block.lower()
    assert "KG" in block
    assert "product memory" in block
    assert "never explain Sprntly" in block


# ── the plan ─────────────────────────────────────────────────────────────────


def _gate(**payload):
    base = {"action": "answer", "include_knowledge_base": True}
    base.update(payload)
    return apply_gates(base, enterprise_id=CO, connected=[])


def test_the_plan_can_ask_for_it():
    assert _gate().include_knowledge_base is True
    assert _gate(include_knowledge_base=False).include_knowledge_base is False
    # A payload predating the field is a question about the product, which is
    # the overwhelming majority.
    assert apply_gates(
        {"action": "answer"}, enterprise_id=CO, connected=[],
    ).include_knowledge_base is False


def test_asking_about_the_memory_narrows_the_grounding():
    """The same exclusivity the other four own-records blocks get, and here it
    guards against this system's OWN content: a bundle of retrieved signals
    beside the counts makes a model answer "what do you know about us" with
    the handful of topics it can see, which is a confident wrong answer to a
    question about scale."""
    from app.qa_agent import _knowledge_base_only_plan

    assert _knowledge_base_only_plan(_gate()) is True
    # A question that genuinely crosses both keeps the graph.
    assert _knowledge_base_only_plan(_gate(include_knowledge_graph=True)) is False
    assert _knowledge_base_only_plan(_gate(include_knowledge_base=False)) is False
    assert _knowledge_base_only_plan(None) is False


def test_the_thunk_only_runs_when_the_plan_asked(isolated_settings, monkeypatch):
    from app import qa_agent

    calls: list = []
    monkeypatch.setattr(
        "app.knowledge_base_context.knowledge_base_block",
        lambda cid: calls.append(cid) or "BLOCK",
    )
    assert qa_agent._planned_knowledge_base_context(CO, _gate()) == "BLOCK"
    assert calls == [CO]
    assert qa_agent._planned_knowledge_base_context(
        CO, _gate(include_knowledge_base=False)
    ) == ""
    assert qa_agent._planned_knowledge_base_context(None, _gate()) == ""
    assert calls == [CO]


def test_a_failing_block_never_breaks_the_answer(isolated_settings, monkeypatch):
    from app import qa_agent

    def boom(cid):
        raise RuntimeError("nope")

    monkeypatch.setattr("app.knowledge_base_context.knowledge_base_block", boom)
    assert qa_agent._planned_knowledge_base_context(CO, _gate()) == ""


# ── the prompts ──────────────────────────────────────────────────────────────


def test_the_planner_menu_separates_about_it_from_from_it():
    """The distinction the whole feature turns on, and the one a model gets
    wrong by default: `include_knowledge_graph` retrieves what is relevant to a
    topic; this describes the whole thing."""
    from app.ask_planner import _PLANNER_SYSTEM

    lowered = " ".join(_PLANNER_SYSTEM.lower().split())
    assert "questions about what sprntly knows" in lowered
    for phrasing in ("what do you know about us", "knowledge graph", "kg"):
        assert phrasing in lowered, phrasing
    assert "what are customers complaining about" in lowered


def test_the_answer_addendum_forbids_estimating():
    from app.prompts import ASK_SYSTEM_KNOWLEDGE_BASE_ADDENDUM

    assert "never estimate" in ASK_SYSTEM_KNOWLEDGE_BASE_ADDENDUM.lower()
    assert "product memory" in ASK_SYSTEM_KNOWLEDGE_BASE_ADDENDUM


def test_the_addendum_is_composed_only_when_the_block_is_there():
    """Wired at both sites — the PRD-tab branch and the ordinary one — because
    "what do you know about us" is asked from beside a PRD too."""
    import inspect

    from app import ask_runner

    src = inspect.getsource(ask_runner.compose_ask_answer)
    assert src.count("ASK_SYSTEM_KNOWLEDGE_BASE_ADDENDUM") == 2
    assert "knowledge_base_context_fn" in src
