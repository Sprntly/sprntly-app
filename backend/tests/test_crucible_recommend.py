"""A recommendation is prose beside a decision, never the decision.

Apurva ruled that the document must say what to do: "this is only the issues,
no suggestion on how to solve or what's the exact recommendation from it".

I2 — no LLM returns a score, a rank or a decision — is not waived by that. It
is kept by ORDERING: every number is computed and frozen before this module is
called, and nothing it returns is fed back. The first test here is the one that
enforces it; the rest are the checks that keep a suggestion inside what the
evidence can support.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.crucible.pipeline import build_findings
from app.crucible.recommend import (
    Recommendation, _acceptable, build_recommendations,
)
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 3, 1, tzinfo=timezone.utc)


def claim(cid, *, subject="export latency", accounts=("Northwind",),
          assertion=None, strength="reported", artifact_id="a", days_ago=1):
    return Claim(
        id=cid, assertion=assertion or f"claim {cid}", type="mechanism",
        subject=subject, source_id="customer_voice", artifact_id=artifact_id,
        artifact_type="t", strength=strength,
        observed_at=NOW - timedelta(days=days_ago), authoritative=True,
        population=PopulationFilter(
            segments={"accounts": tuple(accounts),
                      "customer_side": tuple(accounts)},
            estimated_size=len(accounts) or None,
        ),
    )


def _corpus():
    return [
        claim("c1", accounts=("Northwind",), artifact_id="call-a", days_ago=1,
              assertion="export runs time out past 10k rows"),
        claim("c2", accounts=("Vandelay",), artifact_id="call-b", days_ago=20,
              assertion="export runs time out past 10k rows"),
        claim("c3", accounts=("Initech",), artifact_id="call-c", days_ago=40,
              assertion="export runs time out past 10k rows"),
    ]


# ─── The invariant ───────────────────────────────────────────────────────────

def test_recommendations_never_move_the_ranking():
    """THE TEST THAT KEEPS I2 TRUE.

    A recommendation that could change what ranks first would be a decision, and
    the whole claim of this engine — that the same corpus gives the same
    ordering, defensibly — would go with it. Scores and order are computed
    before any suggestion exists; this asserts they are untouched by one.
    """
    corpus = _corpus()
    before = build_findings(corpus, currency="accounts", now=NOW)

    # A suggestion for every finding, as generous as the real path can be.
    recs = {
        f.id: Recommendation(f.id, "Do the thing", "because the sources said so")
        for f in before.findings
    }
    assert recs  # the fixture must actually produce findings

    after = build_findings(corpus, currency="accounts", now=NOW)
    assert [f.id for f in after.findings] == [f.id for f in before.findings]
    assert [i.value for i in after.impacts] == [i.value for i in before.impacts]
    assert [c.band for c in after.confidences] == [c.band for c in before.confidences]
    assert [c.score for c in after.confidences] == [c.score for c in before.confidences]


def test_the_run_survives_a_recommendation_layer_that_dies():
    """A suggestion layer that failed must not cost a reader the findings that
    succeeded. `build_recommendations` is TOTAL."""
    import app.crucible.recommend as mod

    def boom(**kw):
        raise RuntimeError("gateway down")

    mod_offline = mod._offline
    try:
        mod._offline = lambda: False
        import app.graph.gateway as gw
        real = gw.llm_call
        gw.llm_call = boom
        out = build_recommendations(
            enterprise_id="e", goal_text="g", definition_text="d",
            findings=build_findings(_corpus(), currency="accounts", now=NOW).findings,
            claims=_corpus(),
        )
        assert out == {}
    finally:
        mod._offline = mod_offline
        gw.llm_call = real


# ─── What a suggestion may not say ───────────────────────────────────────────

class _F:
    """The little a check needs from a finding."""
    id = "f-1"


def _check(action, because, strength="reported"):
    return _acceptable({"action": action, "because": because}, _F(), strength)


def test_a_recommendation_quoting_a_figure_is_dropped():
    """The corpus has no revenue mapped to accounts, so a currency amount or a
    percentage is invention — the same rule the plan step lives under."""
    assert _check("Fix the export path", "it recovers $240K of ARR") is None
    assert _check("Fix the export path", "it lifts retention by 12%") is None
    assert _check("Fix the export path", "three accounts named it") is not None


def test_a_recommendation_promising_an_outcome_is_dropped():
    """I5 forbids asserting cause below causally-tested evidence, and a
    recommendation is not a loophole for it."""
    assert _check("Fix export", "this will recover the renewal") is None
    assert _check("Fix export", "this guarantees the account renews") is None
    assert _check("Fix export", "it ensures the deal closes") is None
    # An action is fine; a promise about its result is not.
    assert _check("Fix export", "two accounts raised it in renewal calls") is not None


def test_an_empty_half_is_dropped_rather_than_half_rendered():
    """An action with no justification is the thing this feature exists to
    replace."""
    assert _check("", "because reasons") is None
    assert _check("Do something", "") is None


def test_a_causal_justification_is_dropped_by_the_lint():
    """The same gate a finding's statement passes."""
    assert _check("Fix export", "the timeout causes the churn") is None


def test_a_causal_action_is_now_dropped_by_the_lint():
    """CLOSES THE HOLE the spike found: `action` used to go unlinted, so
    "Fix the export path that is driving churn" passed while the identical
    phrasing in `because` was already caught. A causal claim is exactly as
    false in the imperative sentence as in the justification beneath it."""
    assert _check("Fix the export path that is driving churn", "three accounts named it") is None
    assert _check("Fix the timeout that causes churn", "three accounts named it") is None
    # The non-causal half of the same pair still passes.
    assert _check("Fix the export path", "three accounts named it") is not None


# ─── AC-1/AC-2/AC-3: the deep pass ────────────────────────────────────────────
#
# Apurva: "once we pick the top two, then we could just compare them." David:
# "the number of projects really has to be in context of the question and
# what the goal is." An earlier measurement of the flat pass found `changes`
# — the field that makes a recommendation useful — only 7.7-13.6% grounded
# in the evidence shown to the model; these are the checks that close that
# gap.

import app.crucible.recommend as recommend_mod
from app.crucible.recommend import (
    DEEP_RECOMMENDATION_SCHEMA,
    MAX_DEEP_RECOMMENDED,
    DeepRecommendation,
    RecommendationCount,
    _compare,
    _deep_acceptable,
    _grounded_in,
    _named_count,
    _named_target,
    build_deep_recommendations,
    resolve_recommendation_count,
)
from app.crucible.invariants import assert_llm_schema_returns_no_decision
from app.crucible.types import Impact


def _two_finding_corpus():
    """Two distinct, independently-corroborated themes, sized differently —
    enough for the ranking to have a real #1 and #2 to compare."""
    return [
        claim("c1", subject="export latency", accounts=("Northwind",),
              artifact_id="call-a", days_ago=1,
              assertion="export runs time out past 10k rows"),
        claim("c2", subject="export latency", accounts=("Vandelay",),
              artifact_id="call-b", days_ago=20,
              assertion="export runs time out past 10k rows"),
        claim("c3", subject="export latency", accounts=("Initech",),
              artifact_id="call-c", days_ago=40,
              assertion="export runs time out past 10k rows"),
        claim("d1", subject="onboarding delay", accounts=("Acme",),
              artifact_id="call-d", days_ago=2,
              assertion="onboarding takes six weeks to complete"),
        claim("d2", subject="onboarding delay", accounts=("Globex",),
              artifact_id="call-e", days_ago=15,
              assertion="onboarding takes six weeks to complete"),
    ]


def _build_two():
    corpus = _two_finding_corpus()
    result = build_findings(corpus, currency="accounts", now=NOW)
    assert len(result.findings) == 2  # the fixture must produce both
    return corpus, result


# ── AC-2: the count is arithmetic over frozen scores, never an LLM's choice ──

def test_named_count_is_honoured():
    assert _named_count("What are two things I can do about this?") == 2
    assert _named_count("Give me 3 recommendations") == 3
    assert _named_count("Nothing about a count here") is None


def test_named_target_reads_dollars_and_accounts_and_percent():
    assert _named_target("I want to get a million dollars in revenue") == (1_000_000.0, "dollars")
    assert _named_target("Reach $500k ARR by Q4") == (500_000.0, "dollars")
    assert _named_target("I want to activate 1,000 accounts") == (1000.0, "accounts")
    assert _named_target("lift retention by 12%") == (12.0, "percent")
    assert _named_target("just make it better") is None


def test_default_recommendation_count_is_two():
    """Apurva's own baseline, absent any other signal."""
    result = resolve_recommendation_count("Help the business grow", [])
    assert result.count == 2
    assert not result.target_unsizeable


def test_named_count_overrides_the_default():
    impacts = [Impact(value=5.0, currency="accounts", affected_population=None,
                       movable_gap=None, value_per_unit=None)]
    result = resolve_recommendation_count("What are three things I can do?", impacts)
    assert result.count == 3
    assert "3" in result.basis


def test_named_count_is_capped_at_the_safety_limit():
    impacts = [Impact(value=5.0, currency="accounts", affected_population=None,
                       movable_gap=None, value_per_unit=None)]
    result = resolve_recommendation_count("Give me 40 recommendations", impacts)
    assert result.count == MAX_DEEP_RECOMMENDED
    assert "40" in result.basis  # says what was asked, even though capped


# ─── The chat hand-off bug: a count named in the reader's own sentence, ──────
# lost to the planner's extraction, was silently ignored. `goal_text` is what
# chat sends as the EXTRACTED goal ("reduce churn"); `asked_text` is the
# reader's literal sentence ("What are three things I can do to reduce
# churn?"). The count has to be read from the sentence that actually names it.

def test_asked_text_is_read_for_the_count_when_goal_text_dropped_it():
    """THE BUG, reproduced directly: the extracted goal names no count at
    all, and the literal ask names three."""
    result = resolve_recommendation_count(
        "reduce churn",
        [],
        asked_text="What are three things I can do to reduce churn?",
    )
    assert result.count == 3
    assert result.basis == (
        "you asked for 3, so the top 3 get a full recommendation."
    )


def test_a_blank_asked_text_falls_back_to_goal_text():
    """AC4: a run with no literal text (the direct API) is unaffected —
    blank and whitespace-only both mean "nothing to prefer"."""
    for blank in (None, "", "   "):
        result = resolve_recommendation_count(
            "give me three things I can do", [], asked_text=blank,
        )
        assert result.count == 3


def test_asked_text_also_carries_a_named_target_not_only_a_count():
    """The same seam covers a target ("get to 8 accounts"), not only a
    count — both are the reader's own arithmetic ask, dropped the same way
    by an extraction that keeps only the metric."""
    impacts = [
        Impact(value=5.0, currency="accounts", affected_population=None,
               movable_gap=None, value_per_unit=None),
        Impact(value=4.0, currency="accounts", affected_population=None,
               movable_gap=None, value_per_unit=None),
    ]
    result = resolve_recommendation_count(
        "grow accounts",
        impacts,
        asked_text="I want to activate 8 accounts by end of quarter",
    )
    assert result.count == 2  # 5 + 4 = 9 >= 8
    assert not result.target_unsizeable


def test_asked_text_never_overrides_a_count_goal_text_already_names():
    """When `goal_text` itself names a count — a direct-API caller, or a
    caller that passes the same text twice — nothing about `asked_text`
    changes the answer: it is a fallback SOURCE for the same regex, not a
    second vote."""
    without = resolve_recommendation_count("give me four options", [])
    with_same = resolve_recommendation_count(
        "give me four options", [], asked_text="give me four options",
    )
    assert without.count == with_same.count == 4


def test_build_deep_recommendations_threads_asked_text_into_the_count():
    """The seam `build_deep_recommendations` actually exposes to its caller
    (`routes/crucible.py`) — offline under pytest, so no model is called, but
    `resolve_recommendation_count` still runs before that check and its basis
    is what the report renders (AC-2)."""
    _, result = _build_two()
    deep = build_deep_recommendations(
        enterprise_id="co",
        goal_text="reduce churn",
        definition_text="LOGO churn, accounts that cancel or fail to renew",
        findings=result.findings,
        impacts=result.impacts,
        confidences=result.confidences,
        claims=[],
        asked_text="What are three things I can do to reduce churn?",
    )
    assert deep.count.count == 3
    assert deep.count.basis == (
        "you asked for 3, so the top 3 get a full recommendation."
    )


def test_named_target_sums_impacts_in_rank_order_until_met():
    """David's own example, in miniature: sum the top-ranked, ALREADY-SIZED
    findings until the target is met — never re-sorted, never an LLM call."""
    impacts = [
        Impact(value=5.0, currency="accounts", affected_population=None,
               movable_gap=None, value_per_unit=None),
        Impact(value=4.0, currency="accounts", affected_population=None,
               movable_gap=None, value_per_unit=None),
        Impact(value=3.0, currency="accounts", affected_population=None,
               movable_gap=None, value_per_unit=None),
    ]
    result = resolve_recommendation_count("I want to activate 8 accounts", impacts)
    assert result.count == 2   # 5 + 4 = 9 >= 8; the third is not needed
    assert not result.target_unsizeable


def test_named_target_says_when_the_evidence_falls_short():
    impacts = [
        Impact(value=1.0, currency="accounts", affected_population=None,
               movable_gap=None, value_per_unit=None),
    ]
    result = resolve_recommendation_count("I want to activate 500 accounts", impacts)
    assert result.target_unsizeable
    assert "short" in result.basis.lower()


def test_named_target_in_the_wrong_unit_falls_back_and_says_so():
    """THE NORMAL CASE per the spike: a goal named in dollars, over a corpus
    that can only size in accounts. Must not imply the sum reaches a target
    it never measured."""
    impacts = [
        Impact(value=5.0, currency="accounts", affected_population=None,
               movable_gap=None, value_per_unit=None),
    ]
    result = resolve_recommendation_count(
        "I want to get a million dollars in revenue", impacts,
    )
    assert result.count == 2  # falls back to the default
    assert result.target_unsizeable
    assert "dollars" in result.basis and "accounts" in result.basis


# ── AC-3: the groundedness gate ──────────────────────────────────────────────

def test_grounded_in_accepts_real_overlap_and_rejects_invention():
    claim_text = "Tier 1 enterprise buyers require court-admissible citation chains"
    assert _grounded_in(
        "Buyers require citation chains for compliance review", claim_text,
    )
    # THE SPIKE'S OWN FAILURE MODE: plausible, on-topic, invented — no word
    # in the evidence.
    assert not _grounded_in(
        "Ship a Bluebook-formatted export with dedicated AE onboarding",
        claim_text,
    )


def _deep_rec(**overrides):
    base = {
        "finding_id": "f-1",
        "action": "Ship contradiction detection as a named feature",
        "because": "Buyers named it as a requirement",
        "changes": [{
            "claim_id": "c1",
            "evidence": "export runs time out past 10k rows",
            "change": "Raise the export row limit past 10k",
        }],
        "open_questions": ["Is this already partially built?"],
        "what_would_falsify": "No account raises this again in the next quarter",
    }
    base.update(overrides)
    return base


class _FindingStub:
    id = "f-1"
    label = "export latency"
    statement = "export latency statement"


def test_a_change_citing_an_unshown_claim_is_dropped():
    """A `claim_id` the model never saw for THIS finding is exactly as
    untrustworthy as one it invented outright."""
    _, result = _build_two()
    claims_by_id = {c.id: c for c in _two_finding_corpus()}
    rec = _deep_rec(changes=[{
        "claim_id": "d1",  # shown for the OTHER finding, not this one
        "evidence": "onboarding takes six weeks to complete",
        "change": "Shorten onboarding",
    }])
    ok = _deep_acceptable(rec, _FindingStub(), "reported", {"c1", "c2", "c3"}, claims_by_id)
    assert ok is None


def test_a_change_whose_evidence_does_not_overlap_the_claim_is_dropped():
    claims_by_id = {c.id: c for c in _two_finding_corpus()}
    rec = _deep_rec(changes=[{
        "claim_id": "c1",
        # A real claim id, an UNRELATED restatement — the spike's failure
        # mode: plausible, on-topic, unsupported by the cited evidence.
        "evidence": "Bluebook citation formatting is an industry standard",
        "change": "Adopt Bluebook citation formatting",
    }])
    ok = _deep_acceptable(rec, _FindingStub(), "reported", {"c1", "c2", "c3"}, claims_by_id)
    assert ok is None


def test_a_grounded_change_is_kept_with_its_citation():
    claims_by_id = {c.id: c for c in _two_finding_corpus()}
    rec = _deep_rec()
    ok = _deep_acceptable(rec, _FindingStub(), "reported", {"c1", "c2", "c3"}, claims_by_id)
    assert ok is not None
    assert ok.changes[0].claim_id == "c1"
    assert ok.changes[0].cited_claim == "export runs time out past 10k rows"


def test_a_deep_change_quoting_a_figure_is_dropped():
    claims_by_id = {c.id: c for c in _two_finding_corpus()}
    rec = _deep_rec(changes=[{
        "claim_id": "c1",
        "evidence": "export runs time out past 10k rows",
        "change": "This recovers $50K in retained ARR",
    }])
    ok = _deep_acceptable(rec, _FindingStub(), "reported", {"c1", "c2", "c3"}, claims_by_id)
    assert ok is None  # the only change was dropped, so the whole deep rec is


def test_a_finding_with_no_survivable_change_gets_no_deep_recommendation():
    """A deep pass with nothing left to add is not more useful than the flat
    recommendation it would otherwise replace — so it does not render at
    all, and the flat one (computed separately) still can."""
    claims_by_id = {c.id: c for c in _two_finding_corpus()}
    rec = _deep_rec(changes=[{
        "claim_id": "c1",
        "evidence": "invented content sharing nothing with the claim",
        "change": "Do something",
    }])
    ok = _deep_acceptable(rec, _FindingStub(), "reported", {"c1", "c2", "c3"}, claims_by_id)
    assert ok is None


def test_open_questions_are_not_required_to_be_grounded():
    """"Keep open_questions and what_would_falsify — being ungrounded is
    appropriate for a question", per the ticket. Still lint-checked."""
    claims_by_id = {c.id: c for c in _two_finding_corpus()}
    rec = _deep_rec(open_questions=[
        "Is there a system that could resolve this we have never discussed?",
    ])
    ok = _deep_acceptable(rec, _FindingStub(), "reported", {"c1", "c2", "c3"}, claims_by_id)
    assert ok is not None
    assert ok.open_questions == (
        "Is there a system that could resolve this we have never discussed?",
    )


# ── AC-1: the top two are compared, deterministically, never by a model ─────

def test_deep_schema_declares_no_decision_field():
    """I2, checked against the ACTUAL schema sent to the model — the same
    harness the plan step's framework schema passes."""
    assert_llm_schema_returns_no_decision(
        DEEP_RECOMMENDATION_SCHEMA, "crucible.recommend.deep",
    )


def test_deep_system_prompt_states_the_citation_rule():
    """A property test on the prompt content, per the standing rule for
    LLM-facing description quality: the rule this whole gate depends on must
    actually be asked for, not just enforced after the fact."""
    assert len(recommend_mod._DEEP_SYSTEM) > 200
    assert "claim_id" in recommend_mod._DEEP_SYSTEM
    assert "never invent" in recommend_mod._DEEP_SYSTEM.lower()


def test_compare_is_computed_from_frozen_scores_never_the_model():
    """`_compare` takes no LLM output at all — it is pure Python over
    `Finding`/`Impact`/`Confidence`, and never prints `Confidence.score`
    (internal only, never rendered anywhere in this codebase)."""
    _, result = _build_two()
    f1, f2 = result.findings[0], result.findings[1]
    i1, i2 = result.impacts[0], result.impacts[1]
    c1, c2 = result.confidences[0], result.confidences[1]
    text = _compare(f1, f2, i1, i2, c1, c2)
    assert isinstance(text, str) and text
    assert str(c1.score) not in text and str(c2.score) not in text
    # Deterministic: same inputs, same sentence.
    assert text == _compare(f1, f2, i1, i2, c1, c2)


def test_compare_names_the_actual_reach_difference():
    _, result = _build_two()
    f1, f2 = result.findings[0], result.findings[1]
    i1, i2 = result.impacts[0], result.impacts[1]
    assert i1.value == 3.0 and i2.value == 2.0  # the fixture's real ranking
    text = _compare(f1, f2, i1, i2, result.confidences[0], result.confidences[1])
    assert "3" in text and "2" in text


def test_deep_pass_gives_the_top_two_a_real_recommendation_with_a_comparison(monkeypatch):
    """End to end through `build_deep_recommendations`, with a stubbed model —
    proves the wiring: count -> which findings are asked -> gate -> the
    comparison attached to the higher-ranked survivor."""
    _, result = _build_two()

    def fake_llm_call(**kwargs):
        assert kwargs["json_schema"] is DEEP_RECOMMENDATION_SCHEMA
        prompt_input = kwargs["input"]
        # Every claim id shown to the model appears in brackets, so it has
        # something real to cite.
        assert "[c1]" in prompt_input or "[d1]" in prompt_input

        class _R:
            output = {"deep_recommendations": [
                {
                    "finding_id": result.findings[0].id,
                    "action": "Raise the export row cap",
                    "because": "Three accounts hit the same timeout",
                    "changes": [{
                        "claim_id": "c1",
                        "evidence": "export runs time out past 10k rows",
                        "change": "Raise the export row limit past 10k",
                    }],
                    "open_questions": [],
                    "what_would_falsify": "No account raises this again",
                },
                {
                    "finding_id": result.findings[1].id,
                    "action": "Cut onboarding to two weeks",
                    "because": "Two accounts named the six-week onboarding",
                    "changes": [{
                        "claim_id": "d1",
                        "evidence": "onboarding takes six weeks to complete",
                        "change": "Automate the onboarding checklist",
                    }],
                    "open_questions": [],
                    "what_would_falsify": "No account raises this again",
                },
            ]}
        return _R()

    monkeypatch.setattr(recommend_mod, "_offline", lambda: False)
    monkeypatch.setattr("app.graph.gateway.llm_call", fake_llm_call)

    out = build_deep_recommendations(
        enterprise_id="e", goal_text="What are two things I can do?",
        definition_text="d", findings=result.findings, impacts=result.impacts,
        confidences=result.confidences, claims=_two_finding_corpus(),
    )
    assert out.count.count == 2
    assert set(out.by_id) == {result.findings[0].id, result.findings[1].id}
    # THE COMPARISON lands on the higher-ranked of the two, computed — never
    # asked of the model (the fake response above carries no such field).
    assert out.by_id[result.findings[0].id].comparison
    assert out.by_id[result.findings[1].id].comparison == ""


def test_the_basis_reflects_drops_when_kept_deep_is_less_than_promised(monkeypatch):
    """A genuine count wrongness: `resolve_recommendation_count` writes its
    sentence BEFORE the citation gate runs, so it cannot know one of the
    promised deep recommendations is about to be dropped. Ticket's own AC:
    cover kept-deep < promised-deep, not just deep < flat. Here the goal
    promises 2; the model's SECOND finding cites nothing shown, so
    `_deep_acceptable` drops it and only 1 survives — the basis must say so.
    """
    _, result = _build_two()

    def fake_llm_call(**kwargs):
        class _R:
            output = {"deep_recommendations": [
                {
                    "finding_id": result.findings[0].id,
                    "action": "Raise the export row cap",
                    "because": "Three accounts hit the same timeout",
                    "changes": [{
                        "claim_id": "c1",
                        "evidence": "export runs time out past 10k rows",
                        "change": "Raise the export row limit past 10k",
                    }],
                    "open_questions": [],
                    "what_would_falsify": "No account raises this again",
                },
                {
                    "finding_id": result.findings[1].id,
                    "action": "Cut onboarding to two weeks",
                    "because": "Two accounts named the six-week onboarding",
                    "changes": [{
                        # NEVER SHOWN for this finding — the citation gate
                        # (AC-3) must drop the whole deep recommendation.
                        "claim_id": "not-a-real-claim-id",
                        "evidence": "onboarding takes six weeks to complete",
                        "change": "Automate the onboarding checklist",
                    }],
                    "open_questions": [],
                    "what_would_falsify": "No account raises this again",
                },
            ]}
        return _R()

    monkeypatch.setattr(recommend_mod, "_offline", lambda: False)
    monkeypatch.setattr("app.graph.gateway.llm_call", fake_llm_call)

    out = build_deep_recommendations(
        enterprise_id="e", goal_text="What are two things I can do?",
        definition_text="d", findings=result.findings, impacts=result.impacts,
        confidences=result.confidences, claims=_two_finding_corpus(),
    )
    # The promised count is still 2 (that arithmetic did not change) — only
    # what SURVIVED did.
    assert out.count.count == 2
    assert set(out.by_id) == {result.findings[0].id}
    assert "you asked for 2, so the top 2 get a full recommendation." in (
        out.count.basis
    )
    # The correction is appended, not a silent contradiction the reader has
    # to notice on their own.
    assert "only 1" in out.count.basis.lower()
    assert "2" in out.count.basis  # still names how many were promised


def test_the_basis_says_none_survived_when_the_whole_deep_pass_is_dropped(
    monkeypatch,
):
    """The zero-survivor edge the drops test above does not cover: every
    promised deep recommendation fails the gate."""
    _, result = _build_two()

    def fake_llm_call(**kwargs):
        class _R:
            output = {"deep_recommendations": [
                {
                    "finding_id": result.findings[0].id,
                    "action": "Raise the export row cap",
                    "because": "Three accounts hit the same timeout",
                    "changes": [{
                        "claim_id": "not-shown-1",
                        "evidence": "export runs time out past 10k rows",
                        "change": "Raise the export row limit past 10k",
                    }],
                    "open_questions": [], "what_would_falsify": "",
                },
                {
                    "finding_id": result.findings[1].id,
                    "action": "Cut onboarding to two weeks",
                    "because": "Two accounts named the six-week onboarding",
                    "changes": [{
                        "claim_id": "not-shown-2",
                        "evidence": "onboarding takes six weeks to complete",
                        "change": "Automate the onboarding checklist",
                    }],
                    "open_questions": [], "what_would_falsify": "",
                },
            ]}
        return _R()

    monkeypatch.setattr(recommend_mod, "_offline", lambda: False)
    monkeypatch.setattr("app.graph.gateway.llm_call", fake_llm_call)

    out = build_deep_recommendations(
        enterprise_id="e", goal_text="What are two things I can do?",
        definition_text="d", findings=result.findings, impacts=result.impacts,
        confidences=result.confidences, claims=_two_finding_corpus(),
    )
    assert out.by_id == {}
    assert "none" in out.count.basis.lower()


def test_deep_pass_survives_a_gateway_that_dies(monkeypatch):
    """TOTAL, same contract as the flat pass: a suggestion layer that failed
    must not cost a reader the findings that succeeded."""
    _, result = _build_two()

    def boom(**kw):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(recommend_mod, "_offline", lambda: False)
    monkeypatch.setattr("app.graph.gateway.llm_call", boom)

    out = build_deep_recommendations(
        enterprise_id="e", goal_text="two things", definition_text="d",
        findings=result.findings, impacts=result.impacts,
        confidences=result.confidences, claims=_two_finding_corpus(),
    )
    assert out.by_id == {}
    assert out.count.count == 2  # the count itself is arithmetic, not the call


def test_deep_recommendation_never_moves_the_ranking():
    """AC-6, extended to the deep pass: the pipeline run before and after is
    identical, exactly like `test_recommendations_never_move_the_ranking`
    above."""
    corpus = _two_finding_corpus()
    before = build_findings(corpus, currency="accounts", now=NOW)
    deep = {
        f.id: DeepRecommendation(
            finding_id=f.id, action="Do the thing", because="because reasons",
            changes=(), comparison="",
        )
        for f in before.findings
    }
    assert deep  # the fixture must actually produce findings
    after = build_findings(corpus, currency="accounts", now=NOW)
    assert [f.id for f in after.findings] == [f.id for f in before.findings]
    assert [i.value for i in after.impacts] == [i.value for i in before.impacts]
    assert [c.band for c in after.confidences] == [c.band for c in before.confidences]
    assert [c.score for c in after.confidences] == [c.score for c in before.confidences]
