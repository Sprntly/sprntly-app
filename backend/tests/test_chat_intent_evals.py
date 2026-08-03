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

EVALS: list[tuple[str, str, list[dict], dict, str]] = [
    # The three reported acceptance phrases — keyword-free, context-only.
    ("draft-it-up", "okay, draft it up", _FEATURE_THREAD, {}, "generate_prd"),
    ("work-items", "break this into work items", _FEATURE_THREAD, _PRD_OPEN_CTX,
     "generate_tickets"),
    ("make-it-shorter", "make it shorter", [], _PRD_OPEN_CTX, "edit_prd"),
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


def test_history_budget_keeps_the_newest_turns(monkeypatch):
    """When the total budget overflows, the OLDEST turns fall off — never the
    recent ones a deictic message resolves against."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    filler = [
        {"role": "user", "content": f"old-{i} " + "y" * ci._HISTORY_TURN_CHARS}
        for i in range(ci._HISTORY_TURNS - 1)
    ]
    history = filler + [{"role": "user", "content": "NEWEST: ship the CSV export"}]
    ci.resolve_chat_intent("ent-1", "draft it up", history)
    prompt = calls[0]["input"]
    assert "NEWEST: ship the CSV export" in prompt
    assert "old-0 " not in prompt  # the oldest filler fell off, not the newest


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
    """The decision log has to distinguish verdicts made under the scoped
    prompt from the ones before it — the eval table is the only other record
    of which rule was live."""
    calls: list[dict] = []
    _patch_llm(monkeypatch, {"intent": "answer", "confidence": 0.9, "reason": "q"},
               calls)
    ci.resolve_chat_intent("ent-1", "put together a one-pager on our pricing", [])
    assert calls[0]["prompt_version"] == "chat-intent-v2"


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
