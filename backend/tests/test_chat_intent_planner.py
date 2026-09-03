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
        # `create_artifact`'s KIND rides along on the same terms: present on
        # every verdict, None on the ones it does not belong to. This exact
        # assertion is what caught the key being added — which is the job it
        # exists to do, so it is updated rather than loosened.
        "artifact_kind": None,
        # And `share_to_slack`'s destination pair, on the same
        # present-on-every-verdict terms. A channel riding an unrelated verdict
        # would name a destination nothing is going to, so the planner's gate
        # clears the pair everywhere else and this pins that it does.
        "share_channel": None,
        "share_note": None,
        # Likewise the requested FORMAT: present on every verdict, None when the
        # message named none — which is the normal case and means the executor
        # resolves the company's active format exactly as it always has.
        "artifact_template_id": None,
        "artifact_template_name": None,
        # And list_artifacts' KIND + COUNT, on the same
        # present-on-every-verdict terms.
        "list_kind": None,
        "list_limit": None,
        "list_mode": None,
        "reason": "asked for a spec",
        "source": "planner",
        # WHERE the answer gets written. Present on every verdict, False on the
        # ones it does not belong to — a report pipeline is the only thing that
        # turns it on, and the client reads it to open the panel that writes the
        # document instead of letting a report scroll through the thread.
        "report": False,
        # WHICH document an `edit_artifact` targets — the report or team
        # document the tab has open, re-read server-side. None on every other
        # verdict, on the same present-on-every-verdict terms as the rest.
        "open_artifact": None,
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


def test_a_tickets_format_switch_reaches_the_client_with_its_target():
    """change_tickets_template dispatches POST /v1/stories/change-template with
    the envelope's artifact_template_id — and it is deliberately NOT gated on
    prd_id: the target may be a standalone ticket set, which the backend cannot
    see from a prd_id-shaped envelope, so target resolution is the client's.
    prd_id=None here IS the standalone-set case, and the switch must survive it
    (the exact downgrade that would kill it is what change_prd_template gets)."""
    envelope = ci._plan_to_envelope(
        _plan(
            "change_tickets_template", action_confidence=0.9,
            artifact_template_id="tpl-t1",
            artifact_template_name="Acme Tickets",
        ),
        prd_id=None,
    )

    assert envelope["intent"] == "change_tickets_template"
    assert envelope["artifact_template_id"] == "tpl-t1"
    assert envelope["artifact_template_name"] == "Acme Tickets"


def test_a_tickets_switch_with_no_format_never_reaches_the_client():
    """Same rule as the PRD switch: a change-template dispatch with no format
    id is an executor call with nothing to execute."""
    envelope = ci._plan_to_envelope(
        _plan("change_tickets_template", action_confidence=0.9),
        prd_id=42,
    )

    assert envelope["intent"] == "answer"
    assert envelope["source"] == "no_target_format"


def test_a_tickets_switch_naming_an_unknown_format_asks_which():
    envelope = ci._plan_to_envelope(
        _plan(
            "change_tickets_template", action_confidence=0.9,
            template_query="the Contoso ticket format",
        ),
        prd_id=42,
    )

    assert envelope["intent"] == "answer"
    assert envelope["source"] == "template_not_found"


def test_a_listing_verdict_reaches_the_client_with_its_kind():
    """list_artifacts is a client intent — the rows are attached by the ROUTE
    (where tenancy lives), so the adapter's whole job is passing the intent,
    the kind and the asked-for count through un-downgraded. No PRD gate:
    listing needs no target."""
    envelope = ci._plan_to_envelope(
        _plan("list_artifacts", action_confidence=0.9, list_kind="prd",
              constraints={"top_n": 5}),
        prd_id=None,
    )

    assert envelope["intent"] == "list_artifacts"
    assert envelope["list_kind"] == "prd"
    # "my last 5 PRDs" — the count rides the envelope so the route can trim.
    assert envelope["list_limit"] == 5


def test_a_count_extracted_for_an_answer_never_leaks_into_the_listing_field():
    envelope = ci._plan_to_envelope(
        _plan("answer", constraints={"top_n": 3}),
        prd_id=None,
    )
    assert envelope["list_limit"] is None


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


def test_delegate_maps_to_answer_so_the_client_resends_the_original_message():
    """The client's union does not know `delegate` either, and — unlike
    `update_ticket` — the reason is not just "the executor runs server-side":
    it is that rewriting to `answer` is what MAKES the reuse work. The client
    falls through to its grounded ask on `answer`, which resends the user's
    ORIGINAL message, unparaphrased, to `/v1/ask` — exactly what
    `skill_router.is_project_tool_request`'s regex gate and the delegating
    model both need to see. A synthesized `instruction` never reaches the
    tool loop at all; only `intent` does the work here."""
    envelope = ci._plan_to_envelope(
        _plan(
            "delegate", action_confidence=0.92,
            instruction="ask David to review the evidence doc",
        ),
        prd_id=None,
    )
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "planner"


def test_delegate_with_no_instruction_is_downgraded_with_a_reason():
    """Same rule as assign_tickets/edit_prd: a hand-off naming nobody and
    nothing has nothing for the tool loop to act on."""
    envelope = ci._plan_to_envelope(
        _plan("delegate", action_confidence=0.9, instruction=""), prd_id=None
    )
    assert envelope["intent"] == "answer"


def test_delegate_needs_no_target_prd_unlike_assign_tickets():
    """Unlike `assign_tickets` (`_NEEDS_PRD`-gated — its universe is the
    thread's generated tickets), a delegation needs no PRD at all: a
    `prd_id=None` delegate plan must NOT be downgraded on `no_target_prd` —
    that source is reserved for assign_tickets's own PRD-less case."""
    envelope = ci._plan_to_envelope(
        _plan(
            "delegate", action_confidence=0.9,
            instruction="tell David to figure out which requirements are important",
        ),
        prd_id=None,
    )
    assert envelope["intent"] == "answer"
    assert envelope["source"] != "no_target_prd"


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


def test_assign_tickets_reaches_the_client_with_its_instruction():
    """The client resolves the instruction against the thread PRD's tickets
    (POST /v1/tickets/assign-plan) — both halves must survive the trip."""
    envelope = ci._plan_to_envelope(
        _plan(
            "assign_tickets", action_confidence=0.9,
            instruction="assign the login ticket to Dave",
        ),
        prd_id=42,
    )
    assert envelope["intent"] == "assign_tickets"
    assert envelope["instruction"] == "assign the login ticket to Dave"


def test_assign_tickets_without_a_prd_is_answered_instead():
    """Its ticket universe IS the thread's PRD: with none in context there is
    nothing to assign, and the downgrade can say so honestly."""
    envelope = ci._plan_to_envelope(
        _plan(
            "assign_tickets", action_confidence=0.9,
            instruction="assign the login ticket to Dave",
        ),
        prd_id=None,
    )
    assert envelope["intent"] == "answer"
    assert envelope["source"] == "no_target_prd"


def test_assign_tickets_with_no_instruction_is_downgraded():
    """Same rule as edit_prd: a dispatch with nothing to execute."""
    envelope = ci._plan_to_envelope(
        _plan("assign_tickets", action_confidence=0.9, instruction=""), prd_id=42
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


# ── the report flag (WHERE the answer is written) ───────────────────────────
# A report pipeline answers with a DOCUMENT. The intent stays `answer` — the ask
# path runs it exactly as before — but the client needs to know at send time so
# it opens the panel's Reports tab in its generating state and streams the
# document there, instead of scrolling a report through the chat thread it is
# about to appear beside.


def test_a_report_pipeline_is_flagged_on_the_envelope():
    for pipeline in ("voice-of-customer-report", "competitive-intelligence-review"):
        envelope = ci._plan_to_envelope(
            _plan("answer", pipeline_id=pipeline, action_confidence=0.95,
                  confidence=0.85, reason="wants a VoC report"),
            prd_id=None,
        )
        assert envelope["intent"] == "answer", pipeline
        assert envelope["report"] is True, pipeline


def test_an_ordinary_answer_is_not_a_report():
    envelope = ci._plan_to_envelope(
        _plan("answer", action_confidence=0.95, reason="a question"), prd_id=None,
    )
    assert envelope["report"] is False


def test_a_non_report_pipeline_is_not_a_report():
    """`_REPORT_PIPELINE_IDS` is narrower than "a pipeline ran" — the lookup and
    utility machinery ids are deliberately outside it."""
    envelope = ci._plan_to_envelope(
        _plan("answer", pipeline_id="tracker-lookup", action_confidence=0.9,
              confidence=0.9, reason="jira lookup"),
        prd_id=None,
    )
    assert envelope["report"] is False


def test_the_flag_reads_the_dispatch_set_itself():
    """One set, not two: a name in `qa_agent._REPORT_PIPELINE_IDS` that this
    endpoint didn't know about would print a report into the chat, and one it
    knew about that the answer path didn't would open a panel over an answer."""
    from app import qa_agent

    for pipeline in sorted(qa_agent._REPORT_PIPELINE_IDS):
        assert ci._is_report_pipeline(pipeline) is True, pipeline
    assert ci._is_report_pipeline(None) is False
    assert ci._is_report_pipeline("") is False


# ── the count-engine carve-out — bug 2: the count answer masquerading as a
#    report ─────────────────────────────────────────────────────────────────
# `call-digest` is the ONE pipeline id both the full voice-of-customer report
# AND the map-reduce count engine resolve to (the planner classifies by
# question shape before the answer path decides which of the two it will
# run). A count-shaped question must never open the Reports drawer or show
# report-generation copy — it answers inline, in the SAME turn's chat reply.


def test_a_mapreducible_count_question_is_not_flagged_as_a_report(monkeypatch):
    # Imported fresh, matching exactly what `_is_report_pipeline` itself
    # reads (a lazy `from app.config import settings`) — NOT
    # `app.call_digest.settings`, whose own module-level binding predates
    # this test's per-test config reload and would silently miss the patch.
    from app.config import settings

    monkeypatch.setattr(settings, "voc_count_engine_enabled", True)
    envelope = ci._plan_to_envelope(
        _plan("answer", pipeline_id="call-digest", action_confidence=0.9,
              confidence=0.85, reason="a count question"),
        prd_id=None,
        question="how many calls raised product issues this month",
    )
    assert envelope["intent"] == "answer"
    assert envelope["report"] is False


def test_a_report_shaped_call_digest_question_is_still_flagged_as_a_report(monkeypatch):
    """The carve-out is exactly `call_digest.is_voc_query` — the fork the
    answer path itself takes — so a question that NAMES the document keeps
    opening the Reports drawer, and one that will be answered in the thread no
    longer does.

    The comparative case moved sides on 2026-09-03 and that is the point of the
    widening: "did complaints increase" was always answered inline
    (`is_voc_query` claims it), while this endpoint promised a report — so the
    drawer opened over an answer that arrived as chat text. Only an artifact
    ask survives here now."""
    # Imported fresh, matching exactly what `_is_report_pipeline` itself
    # reads (a lazy `from app.config import settings`) — NOT
    # `app.call_digest.settings`, whose own module-level binding predates
    # this test's per-test config reload and would silently miss the patch.
    from app.config import settings

    monkeypatch.setattr(settings, "voc_count_engine_enabled", True)
    for question in (
        "give me a voice of customer report for this month",
        "write last week's calls up as a one-pager",
    ):
        envelope = ci._plan_to_envelope(
            _plan("answer", pipeline_id="call-digest", action_confidence=0.9,
                  confidence=0.85, reason="a report question"),
            prd_id=None, question=question,
        )
        assert envelope["report"] is True, question
    # …and the shapes the answer path answers INLINE do not open the drawer:
    # a summary (the owner's 2026-09-03 rule), a comparative, a count.
    for question in (
        "give me summary on last week's customer conversations",
        "did complaints about exports increase this week?",
        "how many calls raised product issues this month",
    ):
        envelope = ci._plan_to_envelope(
            _plan("answer", pipeline_id="call-digest", action_confidence=0.9,
                  confidence=0.85, reason="a calls question"),
            prd_id=None, question=question,
        )
        assert envelope["report"] is False, question


def test_the_carve_out_no_longer_depends_on_the_count_engine_flag(monkeypatch):
    """The dark-ship coupling is gone, and removing it is a fix rather than a
    relaxation.

    The old carve-out was `is_mapreducible_count` AND the engine flag, because
    with the engine off a count question fell through to the QUERY pass — and
    the reasoning at the time read that pass as report-eligible. It is not: the
    query pass returns `_report: False` and writes no document, so the drawer
    opened over an inline answer whenever the flag was off. Keying on
    `is_voc_query` — which both branches of that fallback satisfy — makes the
    verdict independent of the flag, exactly as the answer path's own
    behaviour is."""
    from app.config import settings

    for flag in (False, True):
        monkeypatch.setattr(settings, "voc_count_engine_enabled", flag)
        envelope = ci._plan_to_envelope(
            _plan("answer", pipeline_id="call-digest", action_confidence=0.9,
                  confidence=0.85, reason="a count question"),
            prd_id=None,
            question="how many calls raised product issues this month",
        )
        assert envelope["report"] is False, flag


def test_the_carve_out_never_fires_with_no_question_supplied(monkeypatch):
    """`question` defaults to "" for every caller that predates this fix —
    the pre-existing (report=True) behaviour for `call-digest`, never a
    silent behaviour change for a caller this fix does not know about."""
    # Imported fresh, matching exactly what `_is_report_pipeline` itself
    # reads (a lazy `from app.config import settings`) — NOT
    # `app.call_digest.settings`, whose own module-level binding predates
    # this test's per-test config reload and would silently miss the patch.
    from app.config import settings

    monkeypatch.setattr(settings, "voc_count_engine_enabled", True)
    assert ci._is_report_pipeline("call-digest") is True
    assert ci._is_report_pipeline("call-digest", "") is True


def test_a_non_call_digest_report_pipeline_ignores_question_shape(monkeypatch):
    """The carve-out is scoped to `call-digest` alone — a count-shaped
    PHRASING pointed at a different report pipeline (e.g. competitive
    intelligence, which has no count engine of its own) still opens the
    drawer; the question text is never a generic report override."""
    # Imported fresh, matching exactly what `_is_report_pipeline` itself
    # reads (a lazy `from app.config import settings`) — NOT
    # `app.call_digest.settings`, whose own module-level binding predates
    # this test's per-test config reload and would silently miss the patch.
    from app.config import settings

    monkeypatch.setattr(settings, "voc_count_engine_enabled", True)
    envelope = ci._plan_to_envelope(
        _plan("answer", pipeline_id="competitive-intelligence-review",
              action_confidence=0.9, confidence=0.85, reason="wants a review"),
        prd_id=None,
        question="how many competitors raised pricing concerns this month",
    )
    assert envelope["report"] is True
