"""`POST /v1/chat/intent`, backed by the Ask Planner.

When decide mode is on, the action verdict comes from the planner instead of
this module's own model call. The envelope shape does NOT change — the client
reducers in ChatScreen/BriefChat and `ChatIntentEnvelope` in web/app/lib/api.ts
keep working untouched — so what has to be proven here is that the swap
preserves every downgrade rule the old resolver enforced.

Those rules are re-applied at the adapter rather than trusted to the planner
because each needs something the planner does not have: conviction (it validates
that an action is known and carries its argument, not that the model meant it),
a tenant-scoped `prd_id`, and the empty-instruction guard.
"""
from __future__ import annotations

import pytest

import app.chat_intent as ci
from app.ask_planner import Plan


def _plan(action="answer", **kw):
    return Plan(action=action, **kw)


# ── the mapping ──────────────────────────────────────────────────────────────


def test_a_plan_becomes_the_envelope_the_client_already_reads():
    """Same keys, same types. This is a swap of what is behind the endpoint,
    not a new contract."""
    envelope = ci._plan_to_envelope(
        _plan("generate_prd", task="Build checkout v2", action_confidence=0.9,
              confidence=0.0, reason="asked for a spec"),
        prd_id=None,
    )
    assert envelope == {
        "intent": "generate_prd",
        # The ACTION's confidence, not the pipeline's — `confidence=0.0` above
        # is the pipeline pick ("there isn't one") and must not appear here.
        "confidence": 0.9,
        "task": "Build checkout v2",
        "instruction": None,
        # `open_artifact`'s arguments ride the envelope for every verdict and
        # are None on the ones they do not belong to.
        "artifact_type": None,
        "artifact_query": None,
        # Likewise the requested FORMAT: present on every verdict, None when the
        # message named none — which is the normal case and means the executor
        # resolves the company's active format exactly as it always has.
        "artifact_template_id": None,
        "artifact_template_name": None,
        "reason": "asked for a spec",
        "source": "planner",
    }


def test_a_named_format_rides_the_envelope_to_the_client():
    """The client is the only thing that can forward it: the executor endpoints
    are called from the browser, so a format dropped here is a document written
    in the wrong one with nothing on screen to say so."""
    envelope = ci._plan_to_envelope(
        _plan(
            "generate_prd", task="Build checkout v2", confidence=0.9,
            artifact_template_id="tpl-1", artifact_template_name="Acme PRD v2",
        ),
        prd_id=None,
    )

    assert envelope["intent"] == "generate_prd"
    assert envelope["artifact_template_id"] == "tpl-1"
    assert envelope["artifact_template_name"] == "Acme PRD v2"


def test_a_format_we_could_not_find_stops_the_build_and_asks():
    """Owner's decision (2026-08-10): building in the ACTIVE format instead is
    the silent substitution this feature exists to end. The downgrade to
    `answer` is what turns it into a question, and the planner has already
    forced the library onto the plan so the answer can list what they do have."""
    envelope = ci._plan_to_envelope(
        _plan(
            "generate_prd", task="Build checkout v2", confidence=0.95,
            template_query="the Contoso format",
        ),
        prd_id=None,
    )

    assert envelope["intent"] == "answer"
    assert envelope["source"] == "template_not_found"


def test_a_format_switch_reaches_the_client_with_its_target(  # noqa: D103
):
    """change_prd_template dispatches POST /v1/prd/{id}/change-template with
    the envelope's artifact_template_id; both halves must survive the trip."""
    envelope = ci._plan_to_envelope(
        _plan(
            "change_prd_template", action_confidence=0.9,
            artifact_template_id="tpl-1", artifact_template_name="Acme PRD v2",
        ),
        prd_id=42,
    )

    assert envelope["intent"] == "change_prd_template"
    assert envelope["artifact_template_id"] == "tpl-1"
    assert envelope["artifact_template_name"] == "Acme PRD v2"


def test_a_format_switch_with_no_open_prd_is_answered_instead():
    """Switching the format of no document is meaningless — same rule as
    edit_prd, same tenant-scoped fact the planner cannot check itself."""
    envelope = ci._plan_to_envelope(
        _plan(
            "change_prd_template", action_confidence=0.9,
            artifact_template_id="tpl-1",
        ),
        prd_id=None,
    )

    assert envelope["intent"] == "answer"
    assert envelope["source"] == "no_target_prd"


def test_a_format_switch_naming_an_unknown_format_asks_which():
    envelope = ci._plan_to_envelope(
        _plan(
            "change_prd_template", action_confidence=0.9,
            template_query="the Contoso format",
        ),
        prd_id=42,
    )

    assert envelope["intent"] == "answer"
    assert envelope["source"] == "template_not_found"


def test_a_format_switch_with_no_target_at_all_never_reaches_the_client():
    """The planner downgrades this itself; re-applied here because this
    function owns what the client is told to do, and a change-template dispatch
    with no format id is an executor call with nothing to execute."""
    envelope = ci._plan_to_envelope(
        _plan("change_prd_template", action_confidence=0.9),
        prd_id=42,
    )

    assert envelope["intent"] == "answer"
    assert envelope["source"] == "no_target_format"


def test_the_format_reason_wins_over_a_low_confidence_downgrade():
    """Both land on `answer`; only one of them tells the user something they can
    act on."""
    envelope = ci._plan_to_envelope(
        _plan(
            "generate_prd", task="Build checkout v2", confidence=0.1,
            template_query="the Contoso format",
        ),
        prd_id=None,
    )

    assert envelope["intent"] == "answer"
    assert envelope["source"] == "template_not_found"


@pytest.mark.parametrize(
    "action", ["answer", "generate_prd", "edit_prd", "generate_tickets", "generate_prototype"]
)
def test_every_client_known_action_survives_the_mapping(action):
    """The client's union type is the contract. An action it cannot dispatch
    must never reach it."""
    plan = _plan(
        action,
        task="a brief", instruction="a change", confidence=0.95,
    )
    envelope = ci._plan_to_envelope(plan, prd_id=7)
    assert envelope["intent"] == action
    assert envelope["intent"] in ci.INTENTS


def test_update_ticket_maps_to_answer_for_now():
    """The client's union does not know `update_ticket`, and the ticket-update
    executor already serves it from the answer path — so mapping it there is a
    no-op for behaviour and keeps the client untouched."""
    envelope = ci._plan_to_envelope(
        _plan("update_ticket", instruction="add the PRD details", confidence=0.9),
        prd_id=7,
    )
    assert envelope["intent"] == "answer"


def test_an_action_outside_the_client_vocabulary_falls_back():
    envelope = ci._plan_to_envelope(_plan("summon_dragon", confidence=0.99), prd_id=1)
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "fallback"


# ── the downgrade rules survive the swap ─────────────────────────────────────


def test_a_low_confidence_action_is_downgraded():
    """Acting on a 0.2-confidence `generate_prd` is disruptive in a way a
    0.2-confidence answer is not. Same floor, same value as before — but read
    off `action_confidence`, which is the number about the ACTION."""
    envelope = ci._plan_to_envelope(
        _plan("generate_prd", task="x", action_confidence=0.2), prd_id=None
    )
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "low_confidence"


def test_the_pipelines_confidence_can_never_downgrade_an_action():
    """THE LIVE BUG. `confidence` sits under `pipeline_id` and answers "how sure
    are you about this PIPELINE" — and most messages need none, so it is low by
    design. Reading it as the action's conviction turned "generate prd for me
    and please use the template 1 template" into a plain answer at 0.5, with a
    `reason` field saying the model knew exactly what was being asked for."""
    envelope = ci._plan_to_envelope(
        _plan("generate_prd", task="x", action_confidence=1.0, confidence=0.0),
        prd_id=None,
    )

    assert envelope["intent"] == "generate_prd"
    assert envelope["source"] == "planner"


def test_the_floor_does_not_downgrade_a_plain_answer():
    envelope = ci._plan_to_envelope(_plan("answer", confidence=0.0), prd_id=None)
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "planner"


def test_edit_prd_without_a_target_is_downgraded():
    """Whether a target PRD exists is a tenant-scoped DB fact the planner runs
    without and could not check if it wanted to."""
    envelope = ci._plan_to_envelope(
        _plan("edit_prd", instruction="make it shorter", confidence=0.95),
        prd_id=None,
    )
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "no_target_prd"


def test_edit_prd_with_a_target_is_kept():
    envelope = ci._plan_to_envelope(
        _plan("edit_prd", instruction="make it shorter", confidence=0.95),
        prd_id=42,
    )
    assert envelope["intent"] == "edit_prd"
    assert envelope["instruction"] == "make it shorter"


def test_edit_prd_with_no_instruction_is_downgraded():
    """An edit with nothing to apply at least gets answered."""
    envelope = ci._plan_to_envelope(
        _plan("edit_prd", instruction="", confidence=0.95), prd_id=42
    )
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "no_instruction"


# ── degradation ──────────────────────────────────────────────────────────────


def test_decide_mode_off_uses_the_original_resolver(monkeypatch):
    """Everyone not enrolled takes the path they always took."""
    import app.ask_planner as ap

    monkeypatch.setattr(ap, "decide_enabled", lambda eid: False)
    assert ci._resolve_via_planner("ent", "hello", None, prd_id=None) is None


def test_a_planner_outage_falls_through_to_the_resolver(monkeypatch):
    """The endpoint is on the send path. A planner failure must degrade it to
    exactly the behaviour it had before — the original resolver still runs and
    still answers — never break a send."""
    import app.ask_planner as ap

    class _Result:
        output = {
            "intent": "generate_prd", "confidence": 0.9,
            "task": "Build checkout v2", "instruction": None, "reason": "asked",
        }

    monkeypatch.setattr(
        ap, "plan_for_answer",
        lambda **k: (_ for _ in ()).throw(RuntimeError("planner is down")),
    )
    reached: list = []
    monkeypatch.setattr(
        ci, "llm_call", lambda **k: reached.append(k) or _Result()
    )

    envelope = ci.resolve_chat_intent("ent", "write it up", None)

    assert reached, "the original resolver was never reached"
    assert envelope["intent"] == "generate_prd"
    assert envelope["source"] == "llm"


def test_a_total_outage_still_returns_a_sendable_envelope(monkeypatch):
    """Both the planner AND the resolver down. The send must still go through
    as a plain answer rather than 500."""
    import app.ask_planner as ap

    monkeypatch.setattr(
        ap, "plan_for_answer",
        lambda **k: (_ for _ in ()).throw(RuntimeError("planner is down")),
    )
    monkeypatch.setattr(
        ci, "llm_call",
        lambda **k: (_ for _ in ()).throw(RuntimeError("gateway is down")),
    )

    envelope = ci.resolve_chat_intent("ent", "hello", None)
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "fallback"


def test_the_planner_verdict_wins_when_it_returns_one(monkeypatch):
    import app.ask_planner as ap

    monkeypatch.setattr(
        ap, "plan_for_answer",
        lambda **k: _plan("generate_tickets", task="split the PRD", confidence=0.9),
    )
    monkeypatch.setattr(
        ci, "llm_call",
        lambda **k: pytest.fail("the intent resolver ran despite a planned verdict"),
    )

    envelope = ci.resolve_chat_intent("ent", "break this into tickets", None)
    assert envelope["intent"] == "generate_tickets"
    assert envelope["task"] == "split the PRD"
    assert envelope["source"] == "planner"
