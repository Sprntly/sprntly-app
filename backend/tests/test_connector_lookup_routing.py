"""Connector-lookup ROUTING — which questions are intercepted, and in what order.

Two halves:
  1. `is_connector_lookup` — the explicit-name trigger and its vetoes.
  2. `qa_agent.answer` interception ORDER — call-digest → VoC → DS → tracker →
     connector-lookup → generic router. That order is load-bearing: it is what
     stops this path from stealing phrasings the VoC/DS paths own, and what keeps
     ticket questions on the tracker path.

No network/LLM/DB: the gateway call, the digest and the lookup entry points are
patched.
"""
from __future__ import annotations

import app.db.custom_skills as custom_skills_db
import app.qa_agent as qa
import app.skills.resolver as resolver
from app.skill_router import is_connector_lookup, is_jira_lookup

# The bypass tests below need an id this build can actually dispatch. They used
# a vendored built-in (`prd-author`); a chat turn can no longer be routed to one,
# so the pin/slash was accepted, found nothing to run, and fell through to the
# DIRECT path — which those tests never stubbed, because it was never reached.
# In CI that meant a real Anthropic call and a 401. A company's own uploaded
# skill is the id that still walks the pinned/slash path.
CUSTOM_SKILL = "house-method"


def _seed_custom_skill(monkeypatch, slug: str = CUSTOM_SKILL):
    """Make `slug` a real custom skill for every company (both per-request
    reads: the library listing and the by-slug re-check)."""
    row = {
        "slug": slug, "name": slug, "description": "The house method.",
        "method": f"# {slug}\nmethod text", "modules": {}, "references": {},
        "content_hash": "hash" + slug,
    }
    monkeypatch.setattr(custom_skills_db, "list_custom_skills", lambda cid: [dict(row)])
    monkeypatch.setattr(
        resolver, "get_custom_skill",
        lambda cid, wanted: dict(row) if wanted == slug else None,
    )


# ── explicit-name trigger ────────────────────────────────────────────────────

def test_named_source_is_intercepted():
    cases = {
        "check slack for what was said about the pricing change": {"slack"},
        "what's in #product-eng this week": {"slack"},
        "which deals in hubspot mention onboarding": {"hubspot"},
        "what changed in the github repo this week": {"github"},
        "summarise the fireflies call where we discussed churn": {"fireflies"},
        "what's in my google drive files about the launch": {"google_drive"},
        "what did customers say in zendesk": {"zendesk"},
        "pull the latest gong calls about pricing": {"gong"},
        "any tasks in clickup about the checkout bug": {"clickup"},
    }
    for question, expected in cases.items():
        assert is_connector_lookup(question) == expected, question


def test_two_named_sources_yield_both():
    assert is_connector_lookup("check slack and jira for the pricing decision") == {
        "slack", "jira",
    }


def test_ambiguous_names_need_a_read_context():
    # "in linear" / "the notion doc" — a source being named.
    assert is_connector_lookup("what issues are in linear right now") == {"linear"}
    assert is_connector_lookup("find the notion doc about onboarding") == {"notion"}
    # …and ordinary English that happens to contain the word is not.
    assert is_connector_lookup("we saw linear growth in revenue") is None
    assert is_connector_lookup("i have no notion of what they meant") is None


def test_zoom_the_verb_never_routes_to_zoom_the_connector():
    """"Zoom in on the numbers" is ordinary product-analysis English, and it
    arrives WITH a read verb ("see", "look at"), so the read-context gate alone
    would not save it — the in/out veto is what does. A false positive here
    costs a real analysis question, which is the expensive direction, so `zoom`
    is ambiguous-tier rather than strong."""
    for question in [
        "zoom in on the churn numbers",
        "let's zoom in and see what the drop-off looks like",
        "can you zoom out and show me the whole quarter",
        "zooming in on enterprise accounts, what changed",
    ]:
        assert is_connector_lookup(question) is None, question


def test_zoom_the_source_is_intercepted():
    """A genuine ask about the recordings still has to reach the connector —
    false negatives are the expensive failure on the other side."""
    for question in [
        "what did we record in zoom last week",
        "check zoom for the call with acme",
        "find the zoom meeting where we discussed pricing",
    ]:
        assert is_connector_lookup(question) == {"zoom"}, question


def test_one_ambiguous_names_veto_does_not_suppress_the_others():
    """REGRESSION. The ambiguous veto used to be one whole-message pattern
    gating the entire loop, so any veto phrase dropped EVERY ambiguous provider.
    Adding the zoom-verb veto therefore broke Linear: "zoom in on the linear
    tickets for payments" plainly names Linear and plainly asks to read it, and
    it started returning nothing. A veto says one PRODUCT NAME is being used as
    ordinary English — it can only ever suppress that name."""
    assert is_connector_lookup(
        "zoom in on the linear tickets for payments") == {"linear"}
    assert is_connector_lookup(
        "lets zoom out and see which notion docs cover pricing") == {"notion"}
    # The same latent flaw in the other direction: Linear's veto must not eat
    # Notion, and Notion's must not eat Linear.
    assert is_connector_lookup(
        "we saw linear growth — which notion doc covers it") == {"notion"}
    assert is_connector_lookup(
        "i have no notion why — check what issues are in linear") == {"linear"}
    # And a genuine two-source read still yields both.
    assert is_connector_lookup(
        "check linear and notion for the pricing decision") == {"linear", "notion"}


def test_zoom_has_a_live_lookup_adapter_not_absent_or_deferred():
    """Zoom connects, syncs into the KG, and (as of the live connector_lookup
    adapter) can be read live in chat too — so a question naming Zoom gets an
    actual answer, not "that isn't a Sprntly connector" and not the older
    "it syncs but I can't query it live yet" placeholder."""
    from app.connector_lookup.registry import (
        DEFERRED,
        LOOKUP_PROVIDERS,
        NO_CONNECTOR,
        display_name,
        provider_for,
    )

    assert "zoom" in LOOKUP_PROVIDERS
    assert "zoom" not in DEFERRED
    assert "zoom" not in NO_CONNECTOR
    assert display_name("zoom") == "Zoom"
    provider = provider_for("zoom")
    assert provider is not None and provider.provider == "zoom"


def test_meet_the_verb_never_routes_to_google_meet_the_connector():
    """"Meet" is far more dangerous than "zoom" was, and it is why this provider
    is STRONG-tier with a multi-word pattern rather than ambiguous-tier.

    The ambiguous tier requires a read-context match — but
    `_CONNECTOR_READ_CONTEXT` includes `meetings?`, `calls?`, `find` and
    `check`, so "can we meet to go over the tickets" would satisfy BOTH halves
    of that gate and be hijacked, turning a scheduling question into "Google
    Meet syncs into your knowledge graph, but I can't query it live". Requiring
    the "google"/"g" qualifier makes that impossible rather than unlikely."""
    for question in [
        "can we meet to go over the tickets",
        "let's meet tomorrow to check the release",
        "who should meet with the customer about this",
        "find a time to meet about the roadmap",
        "we meet every monday to review open issues",
        "should we meet or just send the doc",
    ]:
        assert is_connector_lookup(question) is None, question


def test_google_meet_the_source_is_intercepted():
    """A genuine ask about the transcripts still has to reach the connector —
    false negatives are the expensive failure on the other side."""
    for question in [
        "what did we say in google meet yesterday",
        "check google meet for the call with acme",
        "pull the gmeet transcript about pricing",
        "what came up in the g-meet call last week",
    ]:
        assert is_connector_lookup(question) == {"google_meet"}, question


def test_google_meet_is_not_confused_with_google_drive():
    """Two providers sharing a first word and an OAuth client. Naming one must
    never return the other — a Drive answer to a meetings question reads as a
    confidently wrong search."""
    assert is_connector_lookup(
        "what did we say in google meet yesterday") == {"google_meet"}
    assert is_connector_lookup(
        "find the google drive doc about pricing") == {"google_drive"}


def test_building_the_google_meet_integration_is_not_a_lookup():
    """The artifact veto. A customer who BUILDS integrations asks this shape
    constantly, and answering "Google Meet syncs into your KG but I can't query
    it live" is a dead end with the skill that does answer it never reached."""
    for question in [
        "should we build the google meet integration",
        "how long would the google meet connector take",
    ]:
        assert is_connector_lookup(question) is None, question
    # And the comparison veto, for the same reason.
    assert is_connector_lookup(
        "how does google meet compare to zoom for our customers") is None


def test_google_meet_is_now_readable_live_not_deferred():
    """REGRESSION. This test asserted the opposite until Meet gained a live-read
    adapter (connector_lookup/google_meet.py).

    Meet used to sit in DEFERRED, whose copy says "it syncs into your knowledge
    graph, but I can't query it live in chat yet". Leaving it there would now be
    a false apology about a source chat CAN read. It still must not be in
    NO_CONNECTOR — that would claim Sprntly has no Meet connector at all — and
    it must resolve to a real adapter, or naming Meet would fall through to the
    generic path and be answered with a KG-flavoured guess.
    """
    from app.connector_lookup.registry import (
        DEFERRED,
        LOOKUP_PROVIDERS,
        NO_CONNECTOR,
        display_name,
        provider_for,
    )

    assert "google_meet" not in DEFERRED
    assert "google_meet" in LOOKUP_PROVIDERS
    assert "google_meet" not in NO_CONNECTOR
    assert display_name("google_meet") == "Google Meet"
    assert provider_for("google_meet") is not None


def test_zoom_recordings_still_belong_to_the_voice_of_customer_skill():
    """`zoom recordings` is a VoC skill trigger and has been since before this
    connector existed. The two do not fight: qa_agent.answer runs the VoC
    interception BEFORE connector-lookup, exactly as it already does for
    Fireflies and Gong, which are strong connector names AND VoC triggers."""
    from app.skill_router import detect_intent

    for question in [
        "summarise our zoom recordings from last month",
        "what feedback came out of the call recordings",
    ]:
        match = detect_intent(question)
        assert match is not None, question
        assert match.skill_id == "voice-of-customer-report", question


def test_unnamed_questions_are_not_intercepted():
    for question in [
        "what are customers complaining about?",      # VoC owns this
        "analyze my data",                            # DS owns this
        "what's our churn rate?",
        "generate a PRD for onboarding",
        "prioritize these features",
        "summarize this document",
        "what did we decide about pricing?",           # no source named
    ]:
        assert is_connector_lookup(question) is None, question


def test_naming_a_tool_as_a_SUBJECT_is_not_a_lookup():
    """A competitive-intelligence request lists products as subjects. is_jira_lookup
    already refuses these (its own negative fixtures); the connector router must
    too, or it steals every CIR that happens to name a tool."""
    for question in [
        "do a competitive analysis of Linear, Jira and Asana",
        "how does our roadmap compare to Jira and Asana?",
        "what are the alternatives to zendesk",
        "should we migrate from clickup to jira",
        "is hubspot better than salesforce for us",
    ]:
        assert is_connector_lookup(question) is None, question


def test_a_tool_named_as_a_possessed_artifact_is_not_a_lookup():
    """"The Stripe integration" is a thing we build, not a source we open.

    Reported 2026-08-02: "should we prioritise the stripe integration or the
    notion one?" matched `stripe`, and since Stripe has no live adapter the user
    got "Stripe isn't a Sprntly connector yet" — a dead end for a prioritisation
    question, with the skill that answers it never reached. Customers who BUILD
    integrations ask this shape constantly.
    """
    for question in [
        "should we prioritise the stripe integration or the notion one?",
        "what growth loops does our figma plugin unlock",
        "what are the riskiest assumptions behind the slack integration",
        "run a pre-mortem on the asana connector launch",
        "how should we scope the google drive integration",
        "is the notion extension worth building this quarter",
        "which of our integrations drive the most retention",
    ]:
        assert is_connector_lookup(question) is None, question


def test_artifact_veto_does_not_swallow_ordinary_lookups():
    """The veto is narrow on purpose. A real read that merely uses one of these
    nouns' NEIGHBOURS — channel, doc, repo, app, api — must still intercept, or
    the fix would cost more lookups than the bug costs skills."""
    for question, expected in [
        ("check the slack channel for the pricing decision", "slack"),
        ("what changed in the github repo this week", "github"),
        ("read the notion doc on onboarding", "notion"),
        ("what does the stripe api say about failed charges", "stripe"),
        ("open the confluence page about retention", "confluence"),
    ]:
        hints = is_connector_lookup(question)
        assert hints is not None, question
        assert expected in hints, question


def test_write_commands_are_vetoed():
    """We can read. A command to write must fall through to normal routing rather
    than a read path implying it posted something."""
    for question in [
        "post this in slack",
        "send a message to #general",
        "please share the brief in slack",
        "create a hubspot deal for acme",
        "push these stories to clickup",
        "schedule a slack reminder for friday",
    ]:
        assert is_connector_lookup(question) is None, question


def test_questions_about_past_writes_are_still_reads():
    """The write veto is anchored to a COMMAND at the start of the message, so
    questions about what people already posted stay readable."""
    assert is_connector_lookup("what did they share in slack about pricing") == {"slack"}
    assert is_connector_lookup("did anyone post in #general about the outage") == {"slack"}


def test_channel_reference_needs_to_look_like_a_channel():
    assert is_connector_lookup("what's in #general") == {"slack"}
    # A number, a heading marker, or a URL fragment is not a channel.
    assert is_connector_lookup("what about #1 on the list") is None
    assert is_connector_lookup("see https://x.dev/a#section for context") is None


def test_sticky_thread_carries_the_source_into_a_followup():
    thread = [
        {"role": "user", "content": "check slack for the pricing discussion"},
        {"role": "assistant", "content": "In #product-eng, Ada said pricing v2 ships Friday."},
    ]
    for question in ["who said that?", "any more details on that", "what's the full thread"]:
        assert is_connector_lookup(question, thread) == {"slack"}, question


def test_sticky_thread_stops_at_a_pivot():
    thread = [
        {"role": "user", "content": "check slack for the pricing discussion"},
        {"role": "assistant", "content": "Ada said pricing v2 ships Friday."},
    ]
    for question in ["prioritize these features", "generate a PRD for this",
                     "what's our churn rate?"]:
        assert is_connector_lookup(question, thread) is None, question


def test_a_followup_with_no_prior_source_is_not_intercepted():
    thread = [{"role": "user", "content": "what's our retention?"},
              {"role": "assistant", "content": "It's 84%."}]
    assert is_connector_lookup("more details on that", thread) is None


# ── no cross-talk with the tracker path ──────────────────────────────────────

def test_slack_questions_do_not_trip_the_tracker_path():
    """`is_jira_lookup` fires on a PM noun + read verb, so a Slack question that
    mentions "messages" must not be dragged onto the tracker path."""
    for question in [
        "check slack for messages about the pricing change",
        "what's in #general this week",
        "what did they say in slack about the outage",
    ]:
        assert not is_jira_lookup(question), question


def test_ticket_questions_stay_on_the_tracker_path():
    """…and the converse: a ticket question is the tracker's, even though the
    connector router would happily claim a named tracker."""
    assert is_jira_lookup("show me my open tickets")
    assert is_jira_lookup("what's the status of PROJ-142")


# ── interception order in qa_agent.answer ────────────────────────────────────

def _no_llm(monkeypatch):
    """Fail loudly if the generic router/answer is reached."""
    calls: list = []
    monkeypatch.setattr(qa, "llm_call", lambda **k: calls.append(k) or (_ for _ in ()).throw(
        AssertionError("generic router must not be reached")))
    return calls


def test_connector_lookup_intercepts_before_the_generic_router(monkeypatch):
    from app.connector_lookup import registry

    _no_llm(monkeypatch)
    seen = {}
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: seen.update(k) or {"answer": "slack", "_skill_source": "connector-lookup"})
    out = qa.answer(enterprise_id="ent", question="check slack for the pricing thread",
                    dataset="acme")
    assert out["_skill_source"] == "connector-lookup"
    assert seen["hints"] == {"slack"}
    assert seen["enterprise_id"] == "ent"


def test_call_digest_still_wins_over_a_named_source(monkeypatch):
    """Case 9 / plan precedence: the digest owns "summarize last week's calls"
    even when the question names a call source."""
    import app.call_digest as cd
    from app.connector_lookup import registry

    monkeypatch.setattr(cd, "answer", lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: {"answer": "lookup", "_skill_source": "connector-lookup"})
    out = qa.answer(
        enterprise_id="ent",
        question="summarize the customer calls from last week in fireflies",
        dataset="acme",
    )
    assert out["_skill_source"] == "call-digest"


# ── naming a live source beats the TOPICAL interceptors ──────────────────────
#
# Reported 2026-08-03: a user with Slack connected asked chat for the latest
# from Slack and got an answer built from Fireflies call transcripts. Three
# separate interceptors above the connector lookup claimed the phrasings —
# call-digest, VoC and the call-index listing — and none of them said which
# source it had actually read. Naming a source is the most explicit routing
# signal a person can give, and it used to lose to a keyword match.
#
# The gate is narrow on purpose: the named source must be one we can OPEN (an
# adapter, and a live connection), and it must not be a call source. Those two
# narrowings are pinned below too, because without them this fix trades three
# wrong answers for a different set of wrong answers.

HIJACKED = [
    # was: call-digest (_DIGEST_VERB "summari[sz]e" + _CALL_NOUN "syncs")
    "summarize the slack channel syncs from this week",
    # was: VoC (_VOC_CUSTOMER_FEEDBACK_RULE, "latest … customer feedback")
    "what's the latest customer feedback in slack",
    # was: call-index listing (_LISTING_RULE, "what … conversations")
    "what are the latest customer conversations in slack",
]


def _slack_connected(monkeypatch, providers=("slack",)):
    """Make the connector registry report `providers` as connected, without a DB."""
    from app.connector_lookup import registry

    monkeypatch.setattr(registry, "connected_providers", lambda eid: list(providers))
    return registry


def _trap_call_paths(monkeypatch):
    """Fail loudly if any call path answers — that IS the bug."""
    import app.call_digest as cd
    import app.call_index as ci

    def _boom(name):
        def _f(*a, **k):
            raise AssertionError(f"{name} claimed a question that named Slack")
        return _f

    monkeypatch.setattr(cd, "answer", _boom("call_digest.answer"))
    monkeypatch.setattr(ci, "answer_listing", _boom("call_index.answer_listing"))


def test_naming_a_connected_source_beats_the_topical_interceptors(monkeypatch):
    registry = _slack_connected(monkeypatch)
    _no_llm(monkeypatch)
    _trap_call_paths(monkeypatch)
    seen = {}
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: seen.update(k) or {"answer": "slack",
                                                       "_skill_source": "connector-lookup"})
    for question in HIJACKED:
        seen.clear()
        out = qa.answer(enterprise_id="ent", question=question, dataset="acme")
        assert out["_skill_source"] == "connector-lookup", question
        assert seen["hints"] == {"slack"}, question


def test_an_unconnected_named_source_leaves_routing_exactly_as_it_was(monkeypatch):
    """The capability half of the gate. If Slack isn't connected, the lookup
    would only be able to say so — worse than the digest's answer — so the
    interceptor keeps the turn and nothing changes for that company."""
    import app.call_digest as cd

    _slack_connected(monkeypatch, providers=("fireflies",))
    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    monkeypatch.setattr(cd, "answer",
                        lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    out = qa.answer(enterprise_id="ent",
                    question="summarize the slack channel syncs from this week",
                    dataset="acme")
    assert out["_skill_source"] == "call-digest"


def test_naming_a_CALL_source_does_not_displace_the_call_paths(monkeypatch):
    """Fireflies and Gong ARE the call corpus, so naming one is not a request to
    look somewhere else — it names the source the digest already reads."""
    import app.call_digest as cd

    _slack_connected(monkeypatch, providers=("fireflies",))
    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    monkeypatch.setattr(cd, "answer",
                        lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    out = qa.answer(enterprise_id="ent",
                    question="summarize the customer calls from last week in fireflies",
                    dataset="acme")
    assert out["_skill_source"] == "call-digest"


def test_the_artifact_veto_still_stands_the_lookup_down(monkeypatch):
    """80b0b4d8's narrowing is upstream of this gate and must survive it: a tool
    named as a possessed artifact is a product question, not a read request — so
    it neither reaches the lookup nor suppresses anything."""
    from app.connector_lookup import registry

    _slack_connected(monkeypatch)
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: {"answer": "lookup", "_skill_source": "connector-lookup"})
    for question in [
        "latest on the slack integration",
        "should we prioritise the stripe integration or the notion one?",
        "how does our roadmap compare to slack?",
    ]:
        assert is_connector_lookup(question) is None, question


def test_a_pin_or_a_slash_never_triggers_the_suppression(monkeypatch):
    """The gate sits behind the same pinned/slash guard the lookup itself uses.
    That pairing is the point: a message the lookup will NOT claim must not have
    the interceptors knocked out from under it, or the turn falls through a hole
    neither path catches."""
    import app.call_digest as cd

    _seed_custom_skill(monkeypatch)
    _slack_connected(monkeypatch)
    monkeypatch.setattr(cd, "has_call_source", lambda eid: True)
    monkeypatch.setattr(cd, "answer",
                        lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    monkeypatch.setattr(qa, "llm_call", lambda **k: _skill_answer())

    # Pinned: every interceptor is skipped wholesale, exactly as before.
    out = qa.answer(enterprise_id="ent",
                    question="summarize the slack channel syncs from this week",
                    dataset="acme", pinned_skill=CUSTOM_SKILL)
    assert out.get("_skill") == CUSTOM_SKILL

    # Slash: the lookup declines a slash command, so the gate declines too and
    # the digest keeps the turn it has always had here.
    out = qa.answer(
        enterprise_id="ent",
        question=f"/{CUSTOM_SKILL} summarize the slack channel syncs from this week",
        dataset="acme",
    )
    assert out["_skill_source"] == "call-digest"


def test_the_call_digest_now_needs_a_call_source_like_its_neighbours(monkeypatch):
    """The digest was the only interceptor on the ladder claiming its turn
    unconditionally. With no corpus it declines and the question falls through
    to routing that can serve it.

    CHANGED 2026-08-05 with the voice-of-customer merge. This used to assert the
    stronger consequence — that the answer never came back from the digest at
    all — which held only because the VoC dispatch downstream was ALSO gated on
    `has_call_source`. That second gate was the reported bug: it made live calls
    and the knowledge graph an either/or, so connecting Zoom silently dropped
    Slack out of every voice-of-customer answer. The dispatch now runs the
    merged path unconditionally and degrades per-source inside
    `call_digest.answer`.

    What the INTERCEPTION's capability gate still buys is unchanged, and is what
    this pins: it yields the turn to the router rather than short-circuiting
    ahead of it, so a company skill or another pipeline still gets its say.
    """
    import app.call_digest as cd

    _slack_connected(monkeypatch, providers=())
    monkeypatch.setattr(cd, "has_call_source", lambda eid: False)
    monkeypatch.setattr(
        cd, "answer",
        lambda **k: {"answer": "merged", "_skill_source": "call-digest"},
    )
    routed: list = []
    real_route = qa.route
    monkeypatch.setattr(
        qa, "route", lambda q, **k: routed.append(q) or real_route(q, **k)
    )
    monkeypatch.setattr(qa, "llm_call", lambda **k: _skill_answer())

    qa.answer(enterprise_id="ent",
              question="summarize the customer calls from last week",
              dataset="acme")

    # The interception declined and the router got the question — the whole
    # point of the gate. Where routing then sends it is routing's call.
    assert routed == ["summarize the customer calls from last week"]


def test_an_unreadable_capability_check_keeps_the_digest(monkeypatch):
    """A routing check that cannot complete must not read as "no capability" —
    a transient DB failure would otherwise silently re-route every digest."""
    import app.call_digest as cd

    _slack_connected(monkeypatch, providers=())
    monkeypatch.setattr(cd, "has_call_source", lambda eid: (_ for _ in ()).throw(
        RuntimeError("supabase down")))
    monkeypatch.setattr(cd, "answer",
                        lambda **k: {"answer": "digest", "_skill_source": "call-digest"})
    out = qa.answer(enterprise_id="ent",
                    question="summarize the customer calls from last week",
                    dataset="acme")
    assert out["_skill_source"] == "call-digest"


def test_data_analysis_still_wins_over_a_named_source(monkeypatch, tmp_path):
    """Tabular data must actually exist for the DS interceptor to claim the
    turn at all (Part 3 capability gate, AC10) — a real raw/ dir with a file
    in it, mirroring `test_ds_chat_analysis.py`'s `workspace` fixture."""
    from app.connector_lookup import registry
    from app.ds import chat_analysis

    raw = tmp_path / "acme" / "raw"
    raw.mkdir(parents=True)
    (raw / "usage.csv").write_text("user_id,used_export\nu1,1\n")
    monkeypatch.setattr(qa.datasets, "raw_path", lambda slug: tmp_path / slug / "raw")
    monkeypatch.setattr(chat_analysis, "answer", lambda **k: {"answer": "ds", "_skill_source": "ds"})
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: {"answer": "lookup", "_skill_source": "connector-lookup"})
    out = qa.answer(enterprise_id="ent", question="analyze my data", dataset="acme")
    assert out["_skill_source"] == "ds"


def test_tracker_path_wins_over_the_connector_router(monkeypatch):
    """A ticket question routes to the tracker even when it names a tracker the
    connector registry also knows."""
    from app.connector_lookup import registry, tracker

    monkeypatch.setattr(tracker, "answer",
                        lambda **k: {"answer": "tracker", "_skill_source": "jira-lookup"})
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: {"answer": "lookup", "_skill_source": "connector-lookup"})
    out = qa.answer(enterprise_id="ent", question="show me my open tickets in jira",
                    dataset="acme")
    assert out["_skill_source"] == "jira-lookup"


def test_tracker_path_is_the_generalized_picker(monkeypatch):
    """The fast-path now goes through the tracker picker, not straight to Jira —
    this is what lets a ClickUp-only company get an answer.

    A tracker must be CONNECTED for a generic "show me my open tickets" (no
    tracker named) to claim the turn at all — the Part 3 capability gate
    (AC9): the bare PM-noun-plus-verb match alone is not enough, only a real
    connection or an explicitly-named tracker is."""
    import app.db as db
    from app.connector_lookup import tracker

    monkeypatch.setattr(
        db, "get_connection",
        lambda cid, prov: {"token_json_encrypted": "enc"} if prov == "jira" else None,
    )
    seen = {}
    monkeypatch.setattr(tracker, "answer", lambda **k: seen.update(k) or {"answer": "t"})
    qa.answer(enterprise_id="ent", question="show me my open tickets", dataset="acme")
    assert seen["enterprise_id"] == "ent"


def test_pinned_skill_bypasses_the_connector_lookup(monkeypatch):
    """An explicit skill invocation that merely mentions a tool isn't hijacked."""
    from app.connector_lookup import registry

    _seed_custom_skill(monkeypatch)
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: {"answer": "lookup", "_skill_source": "connector-lookup"})
    monkeypatch.setattr(qa, "llm_call", lambda **k: _skill_answer())
    out = qa.answer(enterprise_id="ent", question="check slack for the pricing thread",
                    dataset="acme", pinned_skill=CUSTOM_SKILL)
    assert out.get("_skill_source") != "connector-lookup"
    # ...and it really did run the pinned skill, rather than merely dodging the
    # lookup on its way to the generic answer. Without this the assertion above
    # passes for the wrong reason — which is exactly how it started making a
    # live API call.
    assert out.get("_skill") == CUSTOM_SKILL


def test_pinned_pipeline_also_bypasses_the_connector_lookup(monkeypatch):
    """The other kind of id a pin may carry. Slack's `/competitive` command pins
    CIR outright (routes/connectors.py), and its question can easily name a
    tool — so the bypass has to hold for a pipeline id too."""
    from app.connector_lookup import registry

    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: {"answer": "lookup", "_skill_source": "connector-lookup"})
    import app.competitive_intel as ci
    monkeypatch.setattr(ci, "answer", lambda **k: {"answer": "review",
                                                   "_skill_source": "competitive-intel"})
    out = qa.answer(enterprise_id="ent", question="how do we compare to slack?",
                    dataset="acme",
                    pinned_skill="competitive-intelligence-review")
    assert out["_skill_source"] == "competitive-intel"


def test_slash_command_bypasses_the_connector_lookup(monkeypatch):
    from app.connector_lookup import registry

    _seed_custom_skill(monkeypatch)
    monkeypatch.setattr(registry, "answer_for_hints",
                        lambda **k: {"answer": "lookup", "_skill_source": "connector-lookup"})
    monkeypatch.setattr(qa, "llm_call", lambda **k: _skill_answer())
    out = qa.answer(enterprise_id="ent",
                    question=f"/{CUSTOM_SKILL} about slack integration",
                    dataset="acme")
    assert out.get("_skill_source") != "connector-lookup"
    assert out.get("_skill") == CUSTOM_SKILL


def _skill_answer():
    class _R:
        output = {"answer": "ok", "key_points": [], "citations": [],
                  "confidence": 0.9, "unanswered": ""}

    return _R()
