"""app.chat_intent — resolver behavior + the intent regression gate.

Offline (runs everywhere, fake gateway): the prompt actually carries the
context the decision depends on (open-PRD line, attachment note, history
window with clamps, the newest message), and the guardrails hold (fail-open,
unknown intent, low-confidence downgrade, edit without target/instruction).

Live (`-m integration` with an API key): the labeled EVALS below run against
the real model. This set exists because the old keyword regression lists
cannot represent the actual reported failures — KEYWORD-FREE commands whose
meaning lives in the conversation ("draft it up", "break this into work
items", "make it shorter" beside an open PRD). Keyword phrasing tests all
pass while those keep breaking; these are the acceptance criteria for the
envelope dispatcher.

The EVALS run in both directions on purpose, because the two failure modes
pull against each other. Scoping generate_prd to PRODUCT WORK (a capability,
an improvement, a fix) is what keeps a report / summary / one-pager request
out of the PRD pipeline; keeping the deictic cases green is what stops that
scoping from gutting the envelope's reason to exist. A change that fixes one
direction and quietly breaks the other reads as "8/9 correct" unless both
halves are in the table.
"""
from __future__ import annotations

import os

import pytest

import app.chat_intent as ci

# ── Labeled evals ────────────────────────────────────────────────────────────
# Each case: (name, message, history, ctx, expected intent). ctx keys:
# prd_id/prd_title/has_attachments. History oldest-first, [{role, content}].

_FEATURE_THREAD = [
    {"role": "user", "content": "Users keep asking for CSV export of the weekly report."},
    {"role": "assistant", "content": "Signal backs that: 14 requests this quarter, mostly enterprise."},
    {"role": "user", "content": "It should respect the report filters, and cap at 50k rows. Finance wants scheduled exports too."},
    {"role": "assistant", "content": "Makes sense — filtered, capped exports plus a schedule option."},
]

_PRD_OPEN_CTX = {"prd_id": 42, "prd_title": "CSV export"}

# A second converging thread, this one about a DEFECT — the other half of what
# a PRD legitimately covers (a fix, not just a new capability). Its deictic
# closers are the exact twins of the report-shaped phrasings below: the words
# are identical, only the subject differs.
_BUG_THREAD = [
    {"role": "user",
     "content": "People get logged out of checkout when they apply a promo code."},
    {"role": "assistant",
     "content": "Support has 23 tickets on it this month — the session token is "
                "dropped when the promo re-renders the page."},
    {"role": "user",
     "content": "The cart has to stay intact and the user has to stay signed in "
                "through the whole promo flow."},
]

# A thread whose TOPIC is named once, at the very start, and then buried under
# enough unrelated-but-plausible discussion to overflow the char budget — so the
# model only sees turn 1 because the head is preserved and the middle elided.
# The closing message is bare ("okay, let's do it"), so the ONLY way to route it
# is to resolve the deixis against that first turn. Under the old newest-first
# budget turn 1 was the first thing discarded and this case was unroutable.
_BURIED_TOPIC_THREAD = (
    [
        {"role": "user",
         "content": "We need bulk seat management for enterprise admins — "
                    "assigning and revoking licences across a whole org at once, "
                    "instead of one user at a time."},
        {"role": "assistant",
         "content": "Understood — bulk seat assignment and revocation for "
                    "enterprise admins."},
    ]
    + [
        # Realistic filler: a long stretch of adjacent standup-ish chatter that
        # never re-names the feature.
        turn
        for i in range(14)
        for turn in (
            {"role": "user",
             "content": f"Also, unrelated: the billing page still shows stale "
                        f"invoice totals for account batch {i}. " + (
                            "Support has been re-running the reconciliation job by "
                            "hand every morning and it takes about forty minutes. "
                        ) * 12},
            {"role": "assistant",
             "content": f"Noted on batch {i} — that's the nightly reconciliation "
                        "job lagging. " + (
                            "It is tracked separately from what we were discussing "
                            "and does not change the earlier requirements. "
                        ) * 12},
        )
    ]
    + [
        {"role": "user", "content": "Right, let's get back to the main thing."},
        {"role": "assistant",
         "content": "Sure — shall I write that up properly so the team can "
                    "estimate it?"},
    ]
)

EVALS: list[tuple[str, str, list[dict], dict, str]] = [
    # The three reported acceptance phrases — keyword-free, context-only.
    ("draft-it-up", "okay, draft it up", _FEATURE_THREAD, {}, "generate_prd"),
    ("work-items", "break this into work items", _FEATURE_THREAD, _PRD_OPEN_CTX,
     "generate_tickets"),
    ("make-it-shorter", "make it shorter", [], _PRD_OPEN_CTX, "edit_prd"),
    # Long thread: the topic is named ONLY at turn 1 and the closing message is
    # bare. Routable only if the head of an over-budget thread survives.
    ("buried-topic-long-thread", "okay, let's do it", _BURIED_TOPIC_THREAD, {},
     "generate_prd"),
    # Position-in-thread: bare command at turn 1 is still a command.
    ("bare-command-turn1", "generate a PRD for usage-based pricing", [], {},
     "generate_prd"),
    # Same words mid-thread still a command (task from the thread — see
    # test_live_task_synthesis).
    ("bare-command-mid-thread", "generate a PRD", _FEATURE_THREAD, {},
     "generate_prd"),
    # Mention traps: ABOUT a PRD is a question, not a command.
    ("mention-question", "what's the acceptance criteria in the PRD for onboarding?",
     [], {}, "answer"),
    ("meta-question", "what makes a good PRD?", [], {}, "answer"),
    ("critique", "the PRD for dark mode is missing metrics", [], _PRD_OPEN_CTX,
     "answer"),
    # Edit vs generate beside an open PRD.
    ("edit-add-section", "add a rollout section", [], _PRD_OPEN_CTX, "edit_prd"),
    ("edit-metric", "change the success metric to weekly retention", [],
     _PRD_OPEN_CTX, "edit_prd"),
    ("new-prd-beside-open", "now write a prd for a totally different thing: audit logs",
     [], _PRD_OPEN_CTX, "generate_prd"),
    # Affirmative adoption of the assistant's own offer.
    ("yes-adopts-offer", "yes please",
     _FEATURE_THREAD + [{"role": "assistant",
                         "content": "Want me to draft a PRD for the CSV export?"}],
     {}, "generate_prd"),
    # Plain conversation stays answer.
    ("plain-question", "why are enterprise users asking for this?",
     _FEATURE_THREAD, {}, "answer"),
    ("greeting", "hey, what can you do?", [], {}, "answer"),
    # Report asks are ANSWERS, not envelope actions. A competitive review is
    # produced by the skill router on the answer path (app/competitive_intel.py);
    # there is no report intent, and classifying one of these as generate_prd
    # would open a PRD tab instead of running the review.
    ("cir-report-ask", "run a competitive intelligence report", [], {}, "answer"),
    ("cir-scan-ask", "monthly competitor scan please", [], {}, "answer"),
    ("cir-standing-ask", "where do we stand vs our competitors right now?",
     [], _PRD_OPEN_CTX, "answer"),
    ("cir-shipping-ask", "what have our competitors shipped this month?",
     _FEATURE_THREAD, {}, "answer"),
    # ── Subject-matter scope for generate_prd ────────────────────────────────
    # A PRD is for PRODUCT WORK — a new capability, an improvement, or a fix.
    # Everything below is document-SHAPED (a doc noun and/or a writing verb)
    # but ABOUT information that already exists, so it belongs to the answer
    # path, which writes reports and summaries itself. `product-one-pager` was
    # one of the vendored skills deleted in #1024; no skill produces one now,
    # which makes "answer" the only sane home for these.
    ("one-pager-pricing", "put together a one-pager on our pricing", [], {},
     "answer"),
    ("report-on-complaints", "generate a report on customer complaints", [], {},
     "answer"),
    ("summarize-calls-into-doc", "summarize last week's calls into a document",
     [], {}, "answer"),
    ("competitor-summary", "create a summary of our competitors", [], {},
     "answer"),
    ("top-issues-formatted-doc", "write up the top issues in a formatted doc",
     [], {}, "answer"),
    ("exec-update", "draft an exec update on this quarter", [], {}, "answer"),
    ("onboarding-briefing", "put together a briefing on how the onboarding "
     "funnel moved last month", [], {}, "answer"),
    # The sharpest discriminator: a report-shaped request sitting INSIDE a
    # feature thread. The thread is converging on a product change, but this
    # message asks for a recap of existing customer input — subject wins.
    ("recap-inside-feature-thread",
     "before that — write me a one-pager recapping what customers have told us "
     "about exports so far", _FEATURE_THREAD, {}, "answer"),
    # ── …and the other direction: product work still fires ───────────────────
    ("prd-checkout-abandonment", "generate a PRD for checkout abandonment", [],
     {}, "generate_prd"),
    ("spec-a-fix", "we keep losing users at the login timeout - spec a fix", [],
     {}, "generate_prd"),
    ("prd-bulk-export", "write a PRD for the new bulk export feature", [], {},
     "generate_prd"),
    # Multi-turn deictic: the message carries no subject at all, the thread
    # does. These are what the envelope EXISTS for — the scope rule must not
    # touch them. "put that together" here is word-for-word the one-pager
    # phrasing above; only the subject differs.
    ("spec-this-out-bug-thread", "spec this out", _BUG_THREAD, {},
     "generate_prd"),
    ("put-that-together-bug-thread", "okay, put that together", _BUG_THREAD, {},
     "generate_prd"),
    ("write-this-up-as-a-doc", "write this up as a doc", _FEATURE_THREAD, {},
     "generate_prd"),
    # ── Same scope test on generate_prototype ────────────────────────────────
    # A prototype shows a product change working; there is nothing to prototype
    # about a report. The deictic prototype path must survive it.
    ("prototype-this", "prototype this", _FEATURE_THREAD, _PRD_OPEN_CTX,
     "generate_prototype"),
    ("mock-it-up", "mock it up", _FEATURE_THREAD, _PRD_OPEN_CTX,
     "generate_prototype"),
    ("mock-up-a-report", "mock up a one-pager of our Q3 numbers", [], {},
     "answer"),
    # ── OPEN an existing artifact ────────────────────────────────────────────
    # The reported gap: these were understood well enough to find the document
    # and disambiguate, but there was no ACTION behind them, so the panel never
    # opened. They must route to open_artifact, never to a generation.
    ("open-prd-for-x", "open the PRD for compliance reporting", [], {},
     "open_artifact"),
    ("pull-up-the-x-prd", "pull up the checkout abandonment PRD", [], {},
     "open_artifact"),
    ("show-me-the-prd-about", "show me the PRD about onboarding", [], {},
     "open_artifact"),
    ("bring-up-that-spec", "bring up the bulk export spec", [], {},
     "open_artifact"),
    ("open-deictic-mid-thread", "open that PRD again", _FEATURE_THREAD, {},
     "open_artifact"),
    ("open-the-evidence", "pull up the evidence behind the CSV export request",
     [], {}, "open_artifact"),
    # …and the OTHER direction, which is the dangerous one. Same object, same
    # sentence shape, authoring verb — these must still generate. A change that
    # makes the open cases pass by widening open_artifact will fail here.
    ("write-a-prd-for-x-still-generates",
     "write a PRD for compliance reporting", [], {}, "generate_prd"),
    ("generate-a-prd-for-x-still-generates",
     "generate a PRD for checkout abandonment", [], {}, "generate_prd"),
    ("draft-a-prd-for-x-still-generates",
     "draft a PRD for onboarding", [], {}, "generate_prd"),
    # A question ABOUT a document is still an answer, not an open — the panel
    # is not the answer to "what's in it".
    ("whats-in-the-prd-is-not-an-open",
     "what's in the PRD for compliance reporting?", [], {}, "answer"),
    # An edit beside an open PRD must not be re-read as "open it" now that an
    # open action exists.
    ("edit-beside-open-prd-survives-open-action", "make it shorter", [],
     _PRD_OPEN_CTX, "edit_prd"),
]


# ── Offline: prompt assembly + guardrails (fake gateway) ─────────────────────

class _FakeResult:
    def __init__(self, output):
        self.output = output


def _patch_llm(monkeypatch, output, calls=None):
    def _fake(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        if isinstance(output, Exception):
            raise output
        return _FakeResult(output)

    monkeypatch.setattr(ci, "llm_call", _fake)


def test_prompt_carries_context_history_and_message(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent(
        "ent-1", "make it shorter",
        [{"role": "user", "content": "hello"},
         {"role": "assistant", "content": "hi there"}],
        prd_id=42, prd_title="CSV export", has_attachments=True,
    )
    (call,) = calls
    prompt = call["input"]
    assert 'PRD #42 — "CSV export" is open' in prompt
    assert "documents attached" in prompt
    assert "User: hello" in prompt and "Assistant: hi there" in prompt
    assert prompt.rstrip().endswith("Newest message: make it shorter")
    # Decision-log identity for the audit spine.
    assert call["enterprise_id"] == "ent-1"
    assert call["purpose"] == "chat_intent"


def test_prompt_without_prd_says_so(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent("ent-1", "hello", [])
    assert "No PRD is open" in calls[0]["input"]


def test_history_is_clamped(monkeypatch):
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    long_turn = "x" * 10_000
    ci.resolve_chat_intent(
        "ent-1", "q", [{"role": "assistant", "content": long_turn}]
    )
    prompt = calls[0]["input"]
    # Per-turn clamp applied — the giant answer arrives truncated.
    assert long_turn not in prompt
    assert "x" * ci._HISTORY_TURN_CHARS in prompt


def test_history_budget_compacts_the_middle_and_keeps_both_ends(monkeypatch):
    """When the total budget overflows, the middle is elided — not the head.

    This is the whole point of the change: "draft it up" at turn 60 resolves
    against the FEATURE named at turn 1, and a newest-first budget discarded
    exactly that turn. Both ends must survive, and the gap must be declared."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    filler = [
        {"role": "user", "content": f"old-{i} " + "y" * ci._HISTORY_TURN_CHARS}
        for i in range(60)
    ]
    history = (
        [{"role": "user", "content": "TOPIC: the CSV export of the weekly report"}]
        + filler
        + [{"role": "user", "content": "NEWEST: ship the CSV export"}]
    )
    ci.resolve_chat_intent("ent-1", "draft it up", history)
    prompt = calls[0]["input"]

    assert "TOPIC: the CSV export" in prompt, "the topic turn must survive"
    assert "NEWEST: ship the CSV export" in prompt, "recency must survive"
    assert "old-30 " not in prompt, "the middle is what goes"
    assert "earlier turns from the middle" in prompt, "elision is declared"


def test_a_short_thread_carries_every_turn_with_no_marker(monkeypatch):
    """No regression for the common case: a thread inside the budget is
    rendered whole, in order, with nothing elided."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent("ent-1", "draft it up", _FEATURE_THREAD)
    prompt = calls[0]["input"]

    assert "Conversation so far:\nUser: Users keep asking for CSV export" in prompt
    for turn in _FEATURE_THREAD:
        assert turn["content"][:40] in prompt
    assert "omitted" not in prompt


def test_a_thread_past_the_old_20_turn_window_is_fully_carried(monkeypatch):
    """40 short turns overflow the OLD turn cap but not the byte budget, so
    every one survives — the case the cap was silently truncating."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    history = [{"role": "user", "content": f"turn-{i:03d} detail"} for i in range(40)]
    ci.resolve_chat_intent("ent-1", "draft it up", history)
    prompt = calls[0]["input"]

    for i in range(40):
        assert f"turn-{i:03d}" in prompt
    assert "omitted" not in prompt


def test_valid_action_envelope_passes_through(monkeypatch):
    _patch_llm(monkeypatch, {
        "intent": "generate_prd", "confidence": 0.9,
        "task": "CSV export: filtered, 50k-row cap, scheduled exports",
        "reason": "thread converged on the feature",
    })
    env = ci.resolve_chat_intent("ent-1", "draft it up", _FEATURE_THREAD)
    assert env["intent"] == "generate_prd"
    assert env["source"] == "llm"
    assert "50k-row cap" in env["task"]


def test_gateway_error_fails_open_to_answer(monkeypatch):
    _patch_llm(monkeypatch, RuntimeError("gateway down"))
    env = ci.resolve_chat_intent("ent-1", "draft it up", _FEATURE_THREAD)
    assert env["intent"] == "answer"
    assert env["confidence"] == 0.0
    assert env["source"] == "fallback"


def test_unknown_intent_fails_open(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "launch_rocket", "confidence": 0.99,
                             "reason": "?"})
    env = ci.resolve_chat_intent("ent-1", "hi", [])
    assert env["intent"] == "answer"
    assert env["source"] == "fallback"


def test_low_confidence_action_downgrades_to_answer(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "generate_prd", "confidence": 0.4,
                             "task": "something", "reason": "weak"})
    env = ci.resolve_chat_intent("ent-1", "maybe a doc?", [])
    assert env["intent"] == "answer"
    assert env["source"] == "low_confidence"


def test_low_confidence_answer_is_not_downgraded(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.3, "reason": "meh"})
    env = ci.resolve_chat_intent("ent-1", "hmm", [])
    assert env["intent"] == "answer"
    assert env["source"] == "llm"


def test_edit_without_target_prd_downgrades(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "edit_prd", "confidence": 0.9,
                             "instruction": "shorten it", "reason": "edit"})
    env = ci.resolve_chat_intent("ent-1", "make it shorter", [])
    assert env["intent"] == "answer"
    assert env["source"] == "no_target_prd"


def test_edit_without_instruction_downgrades(monkeypatch):
    _patch_llm(monkeypatch, {"intent": "edit_prd", "confidence": 0.9,
                             "instruction": "  ", "reason": "edit"})
    env = ci.resolve_chat_intent("ent-1", "tweak it", [], prd_id=42)
    assert env["intent"] == "answer"
    assert env["source"] == "no_instruction"


def test_prompt_scopes_prd_to_product_work():
    """The model's verdict can only be checked live, but the RULE it is given
    is plain text and assertable everywhere.

    Both halves have to be present, and this test fails if a future edit drops
    either one: the scope test (a PRD is a change to the product) and the
    exclusion list (report / summary / one-pager / exec update are answer).
    Naming the excluded document types explicitly matters — the old prompt
    described generate_prd purely by artifact name, so a document-shaped noun
    plus a document-shaped verb fired it whatever the document was about.
    """
    system = ci._SYSTEM.lower()
    # The subject-matter test itself.
    assert "change to the product" in system
    for product_work in ("new capability", "improvement", "fix"):
        assert product_work in system, product_work
    # The excluded document types, named so the model cannot infer the rule
    # from the shape of the request.
    for excluded in ("report", "summary", "one-pager", "exec update",
                     "briefing"):
        assert excluded in system, excluded
    # …and the exclusion is stated as answer, not merely mentioned.
    assert "however document-shaped" in system


def test_prompt_still_carries_the_deictic_phrasings():
    """The envelope's whole reason to exist: keyword-free commands whose
    referent lives in the thread. Scoping generate_prd by subject matter must
    not cost us these — the discriminator is what the document is about, not
    how bare the sentence is."""
    system = ci._SYSTEM
    for phrase in ("draft it up", "spec this out", "write this up as a doc",
                   "put that together"):
        assert phrase in system, phrase
    # The worked contrast that makes the cut concrete for the model: the same
    # deictic words go both ways depending on subject.
    assert "put that together" in system and "one-pager on our pricing" in system


def test_prompt_version_records_the_scoped_rule(monkeypatch):
    """The decision log has to distinguish verdicts made under the current
    prompt from the ones before it — the eval table is the only other record
    of which rule was live. v3 adds the open_artifact action and the
    OPEN-vs-GENERATE rule; a verdict logged under v2 was made by a prompt that
    could not tell "open the PRD for X" from "write one"."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent("ent-1", "put together a one-pager on our pricing", [])
    assert calls[0]["prompt_version"] == "chat-intent-v3"


# ── open_artifact: opening an existing document is not writing a new one ─────

def test_open_artifact_envelope_carries_the_type_and_subject(monkeypatch):
    _patch_llm(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.95, "artifact_type": "prd",
        "artifact_query": "compliance reporting", "reason": "open",
    })
    env = ci.resolve_chat_intent(
        "ent-1", "open the PRD for compliance reporting", []
    )
    assert env["intent"] == "open_artifact"
    assert env["artifact_type"] == "prd"
    assert env["artifact_query"] == "compliance reporting"
    # The resolver names a subject; it does NOT look one up (that is
    # app.artifact_open, called by the route where the tenant scope lives).
    assert "open" not in env


def test_open_artifact_accepts_evidence(monkeypatch):
    _patch_llm(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.9, "artifact_type": "evidence",
        "artifact_query": "bulk export demand", "reason": "open",
    })
    env = ci.resolve_chat_intent("ent-1", "pull up the evidence for exports", [])
    assert env["artifact_type"] == "evidence"


def test_open_artifact_without_a_subject_downgrades_to_answer_not_generate(
    monkeypatch,
):
    """THE guard. An open request that names nothing has to fall back to the
    harmless action. Falling back to generate_prd would answer "open a PRD"
    with a brand-new document — the single failure this action exists to
    prevent."""
    _patch_llm(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.95, "artifact_type": "prd",
        "artifact_query": "   ", "reason": "open",
    })
    env = ci.resolve_chat_intent("ent-1", "open a PRD", [])
    assert env["intent"] == "answer"
    assert env["source"] == "no_artifact_query"


def test_a_named_but_unopenable_kind_is_kept_never_coerced_to_prd(monkeypatch):
    """"Open the dark mode prototype" must not quietly become the dark mode PRD.

    The kind the user NAMED survives all the way to the resolver, which answers
    `unsupported_type` and lets the client say where prototypes actually live.
    Coercing here would hand over a different document with nothing to signal
    the substitution — the silent-wrong-document failure this whole action is
    built to avoid."""
    _patch_llm(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.9, "artifact_type": "prototype",
        "artifact_query": "dark mode", "reason": "open",
    })
    env = ci.resolve_chat_intent("ent-1", "open the dark mode prototype", [])
    assert env["intent"] == "open_artifact"
    assert env["artifact_type"] == "prototype"


def test_an_unnamed_kind_still_defaults_to_prd(monkeypatch):
    """Nothing NAMED is different from something named-but-unsupported: a bare
    "open that doc" has no kind to preserve, and PRDs are what people mean."""
    _patch_llm(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.9, "artifact_type": None,
        "artifact_query": "dark mode", "reason": "open",
    })
    env = ci.resolve_chat_intent("ent-1", "open the dark mode doc", [])
    assert env["artifact_type"] == "prd"


def test_a_kind_outside_the_schema_is_dropped_then_defaulted(monkeypatch):
    """Junk from a malformed response is not a kind the user named."""
    _patch_llm(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.9, "artifact_type": "spaceship",
        "artifact_query": "dark mode", "reason": "open",
    })
    env = ci.resolve_chat_intent("ent-1", "open the dark mode thing", [])
    assert env["artifact_type"] == "prd"


# ── The deterministic open-vs-generate backstop (runs in CI, no API key) ─────
# The labeled EVALS above are the real proof the prompt works, and they need a
# live model — so they cannot gate a merge. These can: the one direction that
# actually costs the user something is checked here without a model at all.

@pytest.mark.parametrize("message", [
    "open the PRD for compliance reporting",
    "Open the PRD for compliance reporting",
    "pull up the checkout abandonment PRD",
    "show me the PRD about onboarding",
    "bring up the bulk export spec",
    "can you open the billing PRD",
    "please pull up that requirements doc",
    "hey, show me the dark mode PRD",
    "find the PRD for magic-link sign-in",
    "take me to the onboarding PRD",
    "where is the PRD for checkout?",
])
def test_open_shaped_messages_are_detected_deterministically(message):
    assert ci.looks_like_open_request(message), message


@pytest.mark.parametrize("message", [
    # Authoring — the whole point is that these are untouched.
    "write a PRD for compliance reporting",
    "generate a PRD for checkout abandonment",
    "draft a PRD for onboarding",
    "create a requirements doc for billing",
    "put together a PRD for magic-link sign-in",
    "spec this out",
    # An opening verb that does NOT open the message.
    "draft the email once you've opened the PRD",
    "we should write this up after you show the data",
    # Compound: an opening verb AND an authoring verb — too ambiguous for a
    # deterministic rule, so it is left entirely to the model.
    "pull up the billing PRD and then write one for checkout",
    # Ordinary conversation.
    "why are enterprise users asking for this?",
    "",
    "   ",
])
def test_non_open_messages_are_left_to_the_model(message):
    assert not ci.looks_like_open_request(message), message


def test_a_generate_verdict_on_an_open_shaped_message_is_vetoed(monkeypatch):
    """THE regression gate for the headline safety property.

    If the model ever answers "open the PRD for X" with generate_prd, the user
    must not get a new document written. The veto lands on open_artifact, whose
    worst case is "I couldn't find that" — which opens nothing."""
    _patch_llm(monkeypatch, {
        "intent": "generate_prd", "confidence": 0.97,
        "task": "compliance reporting", "reason": "misread as authoring",
    })
    env = ci.resolve_chat_intent(
        "ent-1", "open the PRD for compliance reporting", []
    )
    assert env["intent"] == "open_artifact"
    assert env["source"] == "open_verb_veto"
    assert env["artifact_query"] == "compliance reporting"
    # The generation brief is dropped — nothing downstream may read it.
    assert env["task"] is None


def test_the_veto_never_fires_on_a_real_authoring_request(monkeypatch):
    """The other direction: widening the veto until it eats genuine commands
    would be a worse bug than the one it prevents."""
    _patch_llm(monkeypatch, {
        "intent": "generate_prd", "confidence": 0.95,
        "task": "compliance reporting", "reason": "authoring verb",
    })
    for message in (
        "write a PRD for compliance reporting",
        "generate a PRD for checkout abandonment",
        "draft a PRD for onboarding",
        "okay, draft it up",
    ):
        env = ci.resolve_chat_intent("ent-1", message, [])
        assert env["intent"] == "generate_prd", message
        assert env["source"] == "llm", message


def test_the_veto_only_touches_generate_prd(monkeypatch):
    """It vetoes in ONE direction. An `answer` verdict on an open-shaped
    message stays an answer — the backstop may never promote anything."""
    _patch_llm(monkeypatch, {
        "intent": "answer", "confidence": 0.9, "reason": "question",
    })
    env = ci.resolve_chat_intent("ent-1", "show me what you can do", [])
    assert env["intent"] == "answer"


def test_a_vetoed_message_with_no_subject_lands_on_answer(monkeypatch):
    """With nothing to look for, the veto's own downgrade rule takes over —
    and it too refuses to generate."""
    _patch_llm(monkeypatch, {
        "intent": "generate_prd", "confidence": 0.95, "task": None,
        "reason": "no topic",
    })
    env = ci.resolve_chat_intent("ent-1", "open it", [])
    assert env["intent"] == "answer"
    assert env["source"] == "no_artifact_query"


def test_low_confidence_open_downgrades_to_answer(monkeypatch):
    _patch_llm(monkeypatch, {
        "intent": "open_artifact", "confidence": 0.3, "artifact_type": "prd",
        "artifact_query": "dark mode", "reason": "unsure",
    })
    env = ci.resolve_chat_intent("ent-1", "the dark mode thing?", [])
    assert env["intent"] == "answer"
    assert env["source"] == "low_confidence"


def test_non_open_intents_carry_null_artifact_fields(monkeypatch):
    """The envelope shape is stable across intents, so the client reducer never
    has to guess whether a key is missing or genuinely empty."""
    _patch_llm(monkeypatch, {"intent": "generate_prd", "confidence": 0.9,
                             "task": "dark mode", "reason": "cmd"})
    env = ci.resolve_chat_intent("ent-1", "write a PRD for dark mode", [])
    assert env["artifact_type"] is None and env["artifact_query"] is None


def test_prompt_states_the_open_versus_generate_rule():
    """The distinction is made in ONE place — this prompt — so the rule and
    both verb lists have to be literally present. A future edit that trims
    either side of it reintroduces the failure mode."""
    system = ci._SYSTEM.lower()
    assert "open is not generate" in system
    # Opening verbs: what must route to open_artifact.
    for verb in ("open", "pull up", "bring up", "show me", "take me to"):
        assert verb in system, verb
    # Authoring verbs: what must stay generate_prd.
    for verb in ("write", "draft", "create", "generate"):
        assert verb in system, verb
    # …and the tie-break, which is what makes the rule decidable rather than
    # a list of examples: the object is shared, so only the verb can decide,
    # and an unclear verb resolves to the recoverable side.
    assert "the object tells you nothing" in system
    assert "prefer open_artifact over generate_prd" in system


def test_tickets_without_target_keeps_intent(monkeypatch):
    """Tickets with no PRD keeps the intent — the client owns the
    'generate a PRD first' prerequisite flow."""
    _patch_llm(monkeypatch, {"intent": "generate_tickets", "confidence": 0.9,
                             "reason": "tickets"})
    env = ci.resolve_chat_intent("ent-1", "break this into work items", [])
    assert env["intent"] == "generate_tickets"


# ── Live regression gate (real model; opt-in) ────────────────────────────────

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
@pytest.mark.parametrize(
    "name,message,history,ctx,expected", EVALS, ids=[e[0] for e in EVALS]
)
def test_live_intent_accuracy(name, message, history, ctx, expected):
    env = ci.resolve_chat_intent(
        "eval", message, history,
        prd_id=ctx.get("prd_id"), prd_title=ctx.get("prd_title"),
        has_attachments=ctx.get("has_attachments", False),
    )
    assert env["intent"] == expected, (
        f"{name}: expected {expected}, got {env['intent']} "
        f"(source={env['source']}, reason={env['reason']!r})"
    )


def test_the_buried_topic_thread_really_does_overflow(monkeypatch):
    """Guard for the live case above: if the fixture fit the budget, the eval
    would prove nothing about head preservation — it would just be a normal
    thread. Assert the elision actually fires and that turn 1 survives it."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent("ent-1", "okay, let's do it", _BURIED_TOPIC_THREAD)
    prompt = calls[0]["input"]

    assert "earlier turns from the middle" in prompt, "fixture must overflow"
    assert "bulk seat management" in prompt, "turn 1 must survive the elision"


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
def test_live_task_from_a_buried_topic_names_the_feature():
    """The deictic payoff: a bare closing message on a long thread must yield a
    task naming the feature from turn 1, not a pronoun or the filler topic."""
    env = ci.resolve_chat_intent("eval", "okay, let's do it", _BURIED_TOPIC_THREAD)
    assert env["intent"] == "generate_prd", env
    task = (env["task"] or "").lower()
    assert "seat" in task or "licence" in task or "license" in task, task
    assert "invoice" not in task and "reconciliation" not in task, task


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="needs live model")
def test_live_task_synthesis_is_self_contained():
    """The vagueness fix itself: a deictic command must yield a task composed
    from the thread — the topic and the concrete requirements, not a pronoun."""
    env = ci.resolve_chat_intent("eval", "okay, draft it up", _FEATURE_THREAD)
    assert env["intent"] == "generate_prd"
    task = (env["task"] or "").lower()
    assert "csv" in task or "export" in task
    assert "50k" in task or "50,000" in task
    assert "it" != task.strip()
