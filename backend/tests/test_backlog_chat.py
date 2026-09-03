"""The chat knows what the backlog is, what is on it, and can change it.

Asked for flat: "the chat system should understand backlog and ask questions
just like we did for PRD, Report, Ticket". Neither half existed and neither was
a bug — the capability was never wired. The word "backlog" appeared nowhere in
`ASK_SYSTEM`, no block listed an idea, the `/v1/ideation` routes were reachable
only from the Backlog screen, and the planner's action menu had nothing that
could add or complete one. So "what's in my backlog" was answered out of the
knowledge graph — where the nearest thing is a synced Jira board belonging to
someone else — and "add dark mode to the backlog" landed on `answer`.

Three parts, pinned below: the block (`app.backlog_context`), the narrowing
that keeps every OTHER kind of "backlog" out of the prompt while it answers,
and the `backlog_action` write path (`app.backlog_action`) reaching the client
wire with its questions intact.
"""
from __future__ import annotations

import app.ask_runner as ask_runner
import app.backlog_action as backlog_action
import app.backlog_context as backlog_context
import app.chat_intent as chat_intent
import app.qa_agent as qa
from app.ask_planner import Plan

COMPANY = "co-1"

BACKLOG_BLOCK = (
    "=== THIS COMPANY'S BACKLOG ===\n"
    "The BACKLOG in Sprntly is this company's pool of product ideas.\n"
    "- #1 CSV export fails on large sets — Bug — proposed — backlog item id: i-1"
)


def _item(**kw):
    row = {
        "id": "i-1", "rank": 1, "title": "CSV export fails on large sets",
        "tag": "something_broken", "status": "proposed",
    }
    row.update(kw)
    return row


def _rows(monkeypatch, rows):
    monkeypatch.setattr(
        "app.db.ideation.list_visible_ideation_items",
        lambda enterprise_id, **kw: list(rows),
    )


# ─── the block ───────────────────────────────────────────────────────────────


def test_the_block_explains_what_the_backlog_is(monkeypatch):
    """The half that was actually missing. A model that can list the ideas but
    cannot say what the backlog IS answers "what is the backlog" with the
    connected tracker's version."""
    _rows(monkeypatch, [_item()])

    block = backlog_context.backlog_block(COMPANY)

    assert "pool of product ideas" in block
    assert "NOT a Jira backlog" in block
    # And the line that stops it being confused with the OTHER Sprntly surface
    # people call a backlog.
    assert "NOT the same as" in block


def test_an_idea_line_carries_its_shape(monkeypatch):
    """The id is on the line because every write action starts by resolving a
    typed phrase to one row, and this block is where that mapping is learned."""
    _rows(monkeypatch, [_item()])

    block = backlog_context.backlog_block(COMPANY)

    assert "#1 CSV export fails on large sets" in block
    assert "Bug" in block
    assert "backlog item id: i-1" in block


def test_the_type_labels_match_the_screen(monkeypatch):
    """`something_better` is an enum, not a word anyone says. A model shown the
    raw value will echo it back at the user."""
    _rows(monkeypatch, [
        _item(id="i-2", rank=2, title="Tidy the settings screen", tag="something_better"),
        _item(id="i-3", rank=3, title="Ship SSO", tag="something_new", status="in_progress"),
    ])

    block = backlog_context.backlog_block(COMPANY)

    assert "UI" in block
    assert "New initiative" in block
    assert "in progress" in block
    assert "something_better" not in block


def test_an_empty_backlog_still_gets_the_explanation(monkeypatch):
    """Empty is the NORMAL state before the first brief — the pool is that
    brief's remainder. The answer is "here is what it is for", not silence."""
    _rows(monkeypatch, [])

    block = backlog_context.backlog_block(COMPANY)

    assert "pool of product ideas" in block
    assert "Empty" in block
    assert "offer to add an idea" in block


def test_a_failed_read_renders_nothing_rather_than_an_empty_backlog(monkeypatch):
    """"Your backlog is empty" because a query timed out is a confident lie
    about the user's own data; no block at all degrades to the old answer."""
    def _boom(*a, **k):
        raise RuntimeError("supabase down")

    monkeypatch.setattr("app.db.ideation.list_visible_ideation_items", _boom)

    assert backlog_context.backlog_block(COMPANY) == ""
    assert backlog_context.backlog_block(None) == ""


def test_a_truncated_list_declares_the_truncation(monkeypatch):
    over = backlog_context._MAX_ITEMS + 2
    _rows(monkeypatch, [
        _item(id=f"i-{i}", rank=i, title=f"Idea {i:03d}") for i in range(over)
    ])

    block = backlog_context.backlog_block(COMPANY)

    assert block.count("\n- ") == backlog_context._MAX_ITEMS
    assert "+2 more not shown" in block


# ─── the plan's verdict ──────────────────────────────────────────────────────


def test_the_pure_backlog_plan_is_backlog_only():
    assert qa._backlog_only_plan(Plan(action="answer", include_backlog=True)) is True


def test_any_other_grounding_keeps_the_full_compose():
    assert qa._backlog_only_plan(None) is False
    assert qa._backlog_only_plan(Plan(action="answer", include_backlog=False)) is False
    # "what's in our Jira backlog" — the tracker, so its source stays.
    assert qa._backlog_only_plan(
        Plan(action="answer", include_backlog=True, sources=["jira"])
    ) is False
    assert qa._backlog_only_plan(
        Plan(action="answer", include_backlog=True, include_knowledge_graph=True)
    ) is False


def test_the_planner_flag_is_what_gates_the_read(monkeypatch):
    monkeypatch.setattr(backlog_context, "backlog_block", lambda cid: BACKLOG_BLOCK)

    assert qa._planned_backlog_context(COMPANY, Plan(action="answer")) == ""
    assert qa._planned_backlog_context(
        None, Plan(action="answer", include_backlog=True)
    ) == ""
    assert "CSV export" in qa._planned_backlog_context(
        COMPANY, Plan(action="answer", include_backlog=True)
    )


# ─── the compose ─────────────────────────────────────────────────────────────


def _payload():
    return {
        "answer": "x", "key_points": [], "citations": [],
        "confidence": 0.5, "unanswered": "",
    }


def _spy(calls, name, result=None):
    def _fn(*a, **k):
        calls.append(name)
        return result

    return _fn


def test_a_backlog_only_ask_reads_no_index_no_kg_no_corpus(
    isolated_settings, fake_llm, monkeypatch
):
    """Every connected tracker HAS a backlog — the block is the whole
    grounding, so none of the three is read."""
    calls: list[str] = []
    monkeypatch.setattr(ask_runner, "load_corpus", _spy(calls, "corpus"))
    monkeypatch.setattr(ask_runner, "document_grounding", _spy(calls, "docs", ("", [])))
    monkeypatch.setattr(ask_runner, "_retrieve_kg_bundle", _spy(calls, "kg", None))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "what's in my backlog?", enterprise_id=COMPANY,
        backlog_context_fn=lambda: BACKLOG_BLOCK, library_only=True,
    )

    assert calls == []
    call = fake_llm["calls"][0]
    assert "THIS COMPANY'S BACKLOG" in call["user"]
    assert "CSV export" in call["user"]
    # The addendum that says how to read the block rides with it.
    assert "THIS COMPANY'S BACKLOG" in call["system"]


def test_a_prd_tab_ask_receives_the_backlog_block_too(
    isolated_settings, fake_llm, monkeypatch
):
    """"Is this already on the backlog" is asked from beside a PRD constantly."""
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "is this already on the backlog?", enterprise_id=COMPANY,
        prd_context="CURRENT PRD CONTEXT\nThe document.",
        backlog_context_fn=lambda: BACKLOG_BLOCK, library_only=True,
    )

    call = fake_llm["calls"][0]
    assert "THIS COMPANY'S BACKLOG" in call["user"]
    assert "THIS COMPANY'S BACKLOG" in call["system"]
    assert "CURRENT PRD CONTEXT" in (
        call["kwargs"].get("user_cacheable_prefix") or ""
    )


def test_an_ask_with_no_backlog_block_is_unchanged(
    isolated_settings, fake_llm, monkeypatch
):
    monkeypatch.setattr(ask_runner, "document_grounding", lambda *a, **k: ("", []))
    fake_llm["payload"] = _payload()

    ask_runner.compose_ask_answer(
        "tailspin", "what are the requirements?", enterprise_id=COMPANY,
        prd_context="CURRENT PRD CONTEXT\nThe document.",
    )

    call = fake_llm["calls"][0]
    assert "THIS COMPANY'S BACKLOG" not in call["user"]
    assert "THIS COMPANY'S BACKLOG" not in call["system"]


# ─── backlog_action reaches the client ───────────────────────────────────────


def test_backlog_action_is_on_the_wire():
    """The set IS the wire: an action missing here falls through to `answer` —
    and the answer path now CARRIES the backlog block, so the chat would
    confidently describe a change nothing made."""
    assert "backlog_action" in chat_intent._CLIENT_INTENTS


def test_an_instruction_survives_to_the_envelope():
    envelope = chat_intent._plan_to_envelope(
        Plan(
            action="backlog_action", action_confidence=0.9,
            instruction="mark the CSV export bug as done",
        ),
        prd_id=None,
    )

    assert envelope["intent"] == "backlog_action"
    assert envelope["instruction"] == "mark the CSV export bug as done"


def test_a_change_with_no_instruction_degrades_to_an_answer():
    """`answer` is the recoverable landing in the strongest sense here: it
    carries the backlog, so the reply can list it and ask which idea.

    Asserted at the GATE, where the rule lives (`_NEEDS_INSTRUCTION`) and where
    every other instruction-taking action is checked — `_plan_to_envelope` is
    fed an already-gated plan, so a bare envelope call would test nothing."""
    import app.ask_planner as ap

    assert "backlog_action" in ap._NEEDS_INSTRUCTION
    assert ap._gate_action("backlog_action", "", "")[0] == "answer"
    assert ap._gate_action("backlog_action", "", "mark it done") == (
        "backlog_action", "", "mark it done",
    )


# ─── the plan: what it applies, and what it refuses to ───────────────────────


def _plan(monkeypatch, rows, output):
    _rows(monkeypatch, rows)
    monkeypatch.setattr(
        backlog_action, "llm_call",
        lambda **kw: type("R", (), {"output": output})(),
    )
    return backlog_action.plan_backlog_ops(COMPANY, "do the thing")


def test_an_unambiguous_add_becomes_an_operation(monkeypatch):
    plan = _plan(monkeypatch, [_item()], {
        "operations": [{"op": "add", "title": "Dark mode", "tag": "something_new"}],
        "questions": [], "note": "",
    })

    assert plan["operations"] == [
        {"op": "add", "title": "Dark mode", "tag": "something_new"}
    ]
    assert plan["questions"] == []


def test_a_status_move_carries_the_title_it_resolved(monkeypatch):
    """The client's summary says what changed by NAME; without the title it
    could only report an id at the person who typed a phrase."""
    plan = _plan(monkeypatch, [_item()], {
        "operations": [{"op": "status", "item_id": "i-1", "status": "done"}],
        "questions": [], "note": "",
    })

    assert plan["operations"][0]["title"] == "CSV export fails on large sets"


def test_an_invented_item_id_never_reaches_a_write(monkeypatch):
    """The model is shown ids and can still make one up. A PATCH on an id this
    company does not own must not be attempted at all."""
    plan = _plan(monkeypatch, [_item()], {
        "operations": [{"op": "status", "item_id": "i-999", "status": "done"}],
        "questions": [], "note": "",
    })

    assert plan["operations"] == []
    assert plan["note"]


def test_a_partial_reorder_is_refused(monkeypatch):
    """`reorder_ideation_items` writes rank = position, so a list missing rows
    would silently re-rank the backlog around the gaps."""
    rows = [_item(), _item(id="i-2", rank=2, title="Ship SSO")]
    plan = _plan(monkeypatch, rows, {
        "operations": [{"op": "reorder", "ordered_ids": ["i-2"]}],
        "questions": [], "note": "",
    })

    assert plan["operations"] == []


def test_a_full_reorder_is_kept(monkeypatch):
    rows = [_item(), _item(id="i-2", rank=2, title="Ship SSO")]
    plan = _plan(monkeypatch, rows, {
        "operations": [{"op": "reorder", "ordered_ids": ["i-2", "i-1"]}],
        "questions": [], "note": "",
    })

    assert plan["operations"] == [{"op": "reorder", "ordered_ids": ["i-2", "i-1"]}]


def test_an_ambiguous_idea_becomes_a_question_with_real_options(monkeypatch):
    """The ASK half of the feature. Options are rendered as cards, so every one
    must be an idea that still exists — a card offering a stale id resolves to
    a PATCH that 404s after the user has already picked."""
    rows = [_item(), _item(id="i-2", rank=2, title="Export is slow")]
    plan = _plan(monkeypatch, rows, {
        "operations": [],
        "questions": [{
            "header": "Which idea", "prompt": "Which export idea did you mean?",
            "fills": "item_id", "op": "status", "status": "done",
            "option_item_ids": ["i-1", "i-2", "i-gone"], "multi": False,
        }],
        "note": "",
    })

    q = plan["questions"][0]
    assert [o["value"] for o in q["options"]] == ["i-1", "i-2"]
    assert q["options"][0]["label"] == "CSV export fails on large sets"
    assert q["status"] == "done"
    assert q["multi"] is False


def test_a_question_with_nothing_to_choose_between_is_dropped(monkeypatch):
    """One surviving option is not a choice — it is an operation the plan
    should have stated, or a candidate that validated away."""
    plan = _plan(monkeypatch, [_item()], {
        "operations": [],
        "questions": [{
            "header": "Which idea", "prompt": "Which one?",
            "fills": "item_id", "op": "status", "status": "done",
            "option_item_ids": ["i-1"], "multi": False,
        }],
        "note": "",
    })

    assert plan["questions"] == []


def test_an_untyped_add_asks_for_the_type(monkeypatch):
    """The type is a real choice with three answers — asking is right, and
    guessing files a defect as a new initiative."""
    plan = _plan(monkeypatch, [_item()], {
        "operations": [],
        "questions": [{
            "header": "Type", "prompt": "What kind of idea is “Dark mode”?",
            "fills": "tag", "op": "add", "title": "Dark mode", "multi": True,
        }],
        "note": "",
    })

    q = plan["questions"][0]
    assert [o["value"] for o in q["options"]] == [
        "something_broken", "something_new", "something_better",
    ]
    assert [o["label"] for o in q["options"]] == ["Bug", "New initiative", "UI"]
    assert q["title"] == "Dark mode"
    # One idea has one type — a multi-pick here could only produce a row that
    # is a Bug and a UI change at once, whatever the model asked for.
    assert q["multi"] is False


def test_a_failed_model_call_degrades_to_a_note(monkeypatch):
    """The chat must degrade, not error: a 500 here would lose the user's
    message along with the plan."""
    _rows(monkeypatch, [_item()])

    def _boom(**kw):
        raise RuntimeError("anthropic down")

    monkeypatch.setattr(backlog_action, "llm_call", _boom)

    plan = backlog_action.plan_backlog_ops(COMPANY, "mark it done")

    assert plan["operations"] == []
    assert plan["questions"] == []
    assert "couldn't work out" in plan["note"]
