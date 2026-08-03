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


def test_data_analysis_still_wins_over_a_named_source(monkeypatch):
    from app.connector_lookup import registry
    from app.ds import chat_analysis

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
    this is what lets a ClickUp-only company get an answer."""
    from app.connector_lookup import tracker

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
