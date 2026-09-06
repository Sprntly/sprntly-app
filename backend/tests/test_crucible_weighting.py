"""Weighing evidence by the revenue behind it, rather than counting mentions.

THE BENCHMARK SENTENCE THIS FILE EXISTS TO PROVE: "three mentions from large
accounts beat forty from small ones." Two findings in the same claim-type
bucket, one touching 18 accounts worth $2,979,609 and one touching 42 worth
$2,873,701, must come back in one order counted and the OTHER order weighted.

Every fixture below goes through the real `build_findings` and the real
`_size_ranks`. The counted half is the identical code path with the map absent
— never a branch switched off — because a red produced by a different path
proves nothing about the path that ships.

THE TIE-BREAKER IS PINNED. `_rank`'s tail is `-confidence.score`, and an
18-claim cluster and a 42-claim cluster differ on coverage and recency unless
they are built not to. Every claim here carries the same strength, the same
type, the same source, the same instant and is authoritative, so the two
findings score identically and the flip is provably on `size_rank`.

THREE REFUTATIONS WOULD OTHERWISE DELETE THE FIXTURE SILENTLY: a distinct
`artifact_id` per claim (else `echo`), at least two accounts (else
`single_account`), and at least one authoritative claim (else `no_authority`).

No network, no DB, no LLM.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.crucible.claims import account_key
from app.crucible.pipeline import (
    ACCOUNT_VALUE_UNIT,
    PRICED_ACCOUNTS_UNIT,
    UNPRICED_ACCOUNTS_UNIT,
    build_findings,
)
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 9, 6, tzinfo=timezone.utc)

#: The two clusters, at the pack's real magnitudes. FEWER ACCOUNTS, MORE
#: MONEY on the first — which is the whole question.
BIG_MONEY_ACCOUNTS = tuple(f"Northwind {i:02d}" for i in range(18))
BIG_COUNT_ACCOUNTS = tuple(f"Contoso {i:02d}" for i in range(42))

BIG_MONEY_TOTAL = 2_979_609.0
BIG_COUNT_TOTAL = 2_873_701.0


def _book() -> dict[str, float]:
    """`account_key -> annual value`, exactly as `recon.account_value_map`
    returns it. Both totals land on the figure named above to the dollar, so a
    reader of a failure can tell a ranking bug from an arithmetic one."""
    values: dict[str, float] = {}
    for name in BIG_MONEY_ACCOUNTS[:-1]:
        values[account_key(name)] = 100_000.0
    values[account_key(BIG_MONEY_ACCOUNTS[-1])] = (
        BIG_MONEY_TOTAL - 100_000.0 * (len(BIG_MONEY_ACCOUNTS) - 1))
    for name in BIG_COUNT_ACCOUNTS[:-1]:
        values[account_key(name)] = 70_000.0
    values[account_key(BIG_COUNT_ACCOUNTS[-1])] = (
        BIG_COUNT_TOTAL - 70_000.0 * (len(BIG_COUNT_ACCOUNTS) - 1))
    return values


def _claim(cid: str, subject: str, accounts: tuple[str, ...]) -> Claim:
    """One claim, built so nothing but size can separate two findings of them.

    `preference` on both sides keeps `type_bucket` constant — it is `_rank`'s
    key ABOVE size, so a bucket difference would produce a ranking change that
    could not be attributed to weighting. One instant and one strength keep
    recency and `strongest` identical; one source id keeps the corroboration
    bonus at zero for both; authoritative keeps `authority` and `coverage` at
    1.0 whatever the claim count.
    """
    return Claim(
        id=cid,
        assertion=f"customers on {subject} asked for a faster export",
        type="preference",
        subject=subject,
        source_id="customer_voice",
        # DISTINCT PER CLAIM. One artifact across a cluster is `echo`, and the
        # fixture would be refuted before it could be ranked.
        artifact_id=f"doc-{cid}",
        artifact_type="feature_request",
        strength="reported",
        observed_at=NOW - timedelta(days=30),
        authoritative=True,
        population=PopulationFilter(
            segments={"accounts": accounts, "customer_side": accounts},
            estimated_size=len(accounts),
        ),
        direction="neutral",
    )


def _corpus() -> list[Claim]:
    """One claim per account, so the two clusters carry 18 and 42 claims —
    the shape a real cross-account theme has (`pipeline._accounts` yields
    these routinely)."""
    return (
        [_claim(f"m{i}", "export latency", BIG_MONEY_ACCOUNTS)
         for i in range(len(BIG_MONEY_ACCOUNTS))]
        + [_claim(f"c{i}", "report scheduling", BIG_COUNT_ACCOUNTS)
           for i in range(len(BIG_COUNT_ACCOUNTS))]
    )


#: A third, SMALL priced cluster. It exists so the value population has more
#: than one member: with a single member every population's top sits at 1.0
#: and a two-finding fixture cannot tell "ranked first" from "tied first and
#: broken on confidence".
SMALL_MONEY_ACCOUNTS = tuple(f"Fabrikam {i:02d}" for i in range(5))
SMALL_MONEY_TOTAL = 200_000.0


def _cluster(prefix: str, subject: str, accounts: tuple[str, ...]):
    return [_claim(f"{prefix}{i}", subject, accounts)
            for i in range(max(len(accounts), 5))]


def _three_cluster_corpus() -> list[Claim]:
    return (_corpus()
            + _cluster("s", "billing exports", SMALL_MONEY_ACCOUNTS))


def _run(**kw):
    return build_findings(_corpus(), currency="accounts", now=NOW, **kw)


def _labels(result) -> list[str]:
    return [f.label for f in result.findings]


# ── THE GATE: the ranking must change, and change for the stated reason ─────


def test_counted_the_forty_two_account_theme_leads():
    """RED HALF. Nothing is switched off here — this is the shipping path with
    no contracts attached, which is what every tenant without an uploaded book
    gets today."""
    out = _run()
    assert len(out.findings) == 2
    assert _labels(out)[0] == "report scheduling"
    counted = {f.label: i.affected_population
               for f, i in zip(out.findings, out.impacts)}
    assert counted == {"export latency": 18.0, "report scheduling": 42.0}


def test_weighted_the_eighteen_account_theme_leads():
    """GREEN HALF, AND THE BENCHMARK CLAIM IN ONE ASSERTION. Same claims, same
    function, same populations — the only difference is a book the run is
    allowed to weigh by."""
    out = _run(value_map=_book(), weighted=True)
    assert _labels(out)[0] == "export latency"
    # AND IT LEADS ON SIZE, STRICTLY. Without this the test can pass by
    # accident: a `_size_ranks` that keeps reach in the candidate list puts
    # BOTH findings at 1.0 — one top of the value population, one top of the
    # reach population — and the order then falls through to `_rank`'s
    # confidence tail, where a 1e-16 float residue decides the document. That
    # is the no-op this change exists to refuse, passing its own headline test.
    ranks = [i.size_rank for i in out.impacts]
    assert ranks[0] > ranks[1], (
        f"the leading finding must lead on size, not on a tie-break: {ranks}")


def test_the_flip_is_on_size_rank_and_not_on_confidence():
    """The tie-breaker, PINNED RATHER THAN ASSUMED — twice over.

    First: weighting does not touch confidence at all, so the same finding
    scores identically counted and weighted and cannot be what moved.

    Second: the two findings score the same as each other, to within the
    accumulated float error of summing eighteen identical terms against
    forty-two identical ones (`_problem_leg`'s recency mean). That residue is
    ~1e-16 and lives in `_rank`'s LAST key term, which is never reached here
    because `size_rank` already separates them — but it is asserted at a
    tolerance rather than exactly, because an exact assertion here would fail
    for a reason that has nothing to do with evidence."""
    counted = _run()
    weighted = _run(value_map=_book(), weighted=True)
    before = {f.label: c.score for f, c in zip(counted.findings,
                                               counted.confidences)}
    after = {f.label: c.score for f, c in zip(weighted.findings,
                                              weighted.confidences)}
    assert before == after, "weighting moved confidence, so the flip is not size"
    scores = sorted(after.values())
    assert abs(scores[0] - scores[-1]) < 1e-9, (
        f"the two findings must be equally well evidenced, got {scores}")
    ranks = {f.label: i.size_rank
             for f, i in zip(weighted.findings, weighted.impacts)}
    assert ranks["export latency"] == 1.0
    assert ranks["report scheduling"] == 0.5


def test_the_bucket_is_held_constant_so_the_change_is_attributable():
    """`type_bucket` is `_rank`'s key ABOVE size. A fixture that moved it
    would produce a ranking change weighting had nothing to do with."""
    from app.crucible.moscow import type_bucket

    out = _run()
    buckets = {type_bucket(f.confidence_inputs.claim_types)
               for f in out.findings}
    assert buckets == {1}


def test_the_sums_are_the_accounts_own_contracted_value():
    out = _run(value_map=_book(), weighted=True)
    money = {f.label: f.impact_inputs.native_units[ACCOUNT_VALUE_UNIT]
             for f in out.findings}
    assert money == {"export latency": BIG_MONEY_TOTAL,
                     "report scheduling": BIG_COUNT_TOTAL}


def test_reach_is_untouched_by_weighting():
    """Weighting adds a population; it must not overwrite one. `impact.value`
    stays a count of accounts, `affected_population` stays what it measured,
    and nothing anywhere becomes a dollar figure wearing an account's unit."""
    counted = _run()
    weighted = _run(value_map=_book(), weighted=True)
    for out in (counted, weighted):
        by_label = {f.label: i for f, i in zip(out.findings, out.impacts)}
        assert by_label["export latency"].value == 18.0
        assert by_label["report scheduling"].value == 42.0
        assert all(i.value_per_unit is None for i in out.impacts)


# ── THE OFF SWITCH, AS A PURE-FUNCTION EQUALITY ────────────────────────────


def test_a_map_without_the_verdict_changes_absolutely_nothing():
    """THE ONLY PROOF THAT THE GATE GATES, AND IT CANNOT FLAKE. A live probe
    comparing two staging runs would move for three reasons that have nothing
    to do with this change — the corpus grows between runs, the relevance gate
    draws fresh, and `decay_factor` is continuous in seconds. This is the same
    inputs through the same function twice."""
    without = build_findings(_corpus(), currency="accounts", now=NOW)
    with_map = build_findings(_corpus(), currency="accounts", now=NOW,
                              value_map=_book(), weighted=False)
    assert with_map.findings == without.findings
    assert with_map.impacts == without.impacts
    assert with_map.confidences == without.confidences
    assert with_map.rejected == without.rejected
    assert with_map.stats == without.stats
    assert with_map.deep_count == without.deep_count
    assert with_map.priced == without.priced


def test_the_verdict_without_a_map_invents_nothing_and_says_so():
    """The other half of the switch. A run told to weight with nothing to
    weigh by must fall back to the count — not to a zero, and not to a crash —
    and the one thing it is allowed to change is the sentence explaining that
    is what happened."""
    plain = build_findings(_corpus(), currency="accounts", now=NOW)
    empty = build_findings(_corpus(), currency="accounts", now=NOW,
                           value_map={}, weighted=True)
    assert [f.label for f in empty.findings] == [f.label for f in plain.findings]
    assert ([i.size_rank for i in empty.impacts]
            == [i.size_rank for i in plain.impacts])
    assert ([i.value for i in empty.impacts] == [i.value for i in plain.impacts])
    assert all(ACCOUNT_VALUE_UNIT not in f.impact_inputs.native_units
               for f in empty.findings)
    assert "not priced" not in plain.findings[0].impact_inputs.assumed_params[0].basis
    assert ("is in your contracts"
            in empty.findings[0].impact_inputs.assumed_params[0].basis)


# ── PRODUCTION'S ACTUAL SHAPES: no coverage, and partial coverage ──────────


def test_both_findings_unpriced_orders_exactly_as_a_counted_run_does():
    """A REGRESSION GUARD FOR THE COMMON CASE. Measured coverage on staging is
    3.9%: the normal weighted-run finding is one the book cannot reach at all,
    and it must land where it always did rather than at a sentinel."""
    stranger_book = {account_key("Someone Else Entirely"): 5_000_000.0}
    counted = _run()
    weighted = _run(value_map=stranger_book, weighted=True)
    assert _labels(weighted) == _labels(counted)
    assert [i.size_rank for i in weighted.impacts] == [
        i.size_rank for i in counted.impacts]
    assert weighted.priced == (False, False)


def _partial_book() -> dict[str, float]:
    """Prices the eighteen-account theme and the five-account one, and NOT the
    forty-two-account one — which is the biggest thing in the run by reach."""
    book = {account_key(a): 100_000.0 for a in BIG_MONEY_ACCOUNTS[:-1]}
    book[account_key(BIG_MONEY_ACCOUNTS[-1])] = (
        BIG_MONEY_TOTAL - 100_000.0 * (len(BIG_MONEY_ACCOUNTS) - 1))
    for a in SMALL_MONEY_ACCOUNTS:
        book[account_key(a)] = SMALL_MONEY_TOTAL / len(SMALL_MONEY_ACCOUNTS)
    return book


def _partial_run(**kw):
    return build_findings(_three_cluster_corpus(), currency="accounts",
                          now=NOW, value_map=_partial_book(), weighted=True,
                          **kw)


def test_partial_coverage_gives_two_lists_that_do_not_interleave():
    """The partial-coverage case, ASSERTED RATHER THAN ASSUMED — and built so
    the assertion cannot pass by accident.

    The unpriced theme is the LARGEST thing in the run by accounts touched, so
    on one undivided list it would sit at the top of the reach population and
    land level with, or ahead of, a priced finding. The two lists are what stop
    that: "could not be priced" is a labelled section, not a tail."""
    out = _partial_run()
    assert _labels(out) == ["export latency", "billing exports",
                            "report scheduling"]
    assert out.priced == (True, True, False), (
        "an unpriced finding interleaved with the revenue-ranked ones")
    # The unpriced finding still ranks, by accounts touched, and carries no
    # money unit at all — never a zero.
    unpriced = out.findings[2]
    assert ACCOUNT_VALUE_UNIT not in unpriced.impact_inputs.native_units
    assert out.impacts[2].size_rank is not None


def test_the_deep_slice_never_spills_into_the_could_not_be_priced_list():
    """`findings[:deep_count]` is what gets written up in full. Spilling it
    into list two would present an unpriced finding as one of the run's
    headline revenue-ranked answers."""
    out = _partial_run(deep_cap=5)
    assert out.deep_count == 2
    assert all(out.priced[:out.deep_count])


def test_a_counted_run_reports_every_finding_as_one_list():
    """`priced` is all-True on a counted run, which is what every caller
    written before the two lists existed reads back as."""
    out = _run()
    assert out.priced == (True, True)


# ── I3 AND I1 AT ACCOUNT LEVEL ─────────────────────────────────────────────


def test_a_partial_sum_is_disclosed_with_the_names_it_could_not_reach():
    """A partial that does not say it is partial is worse than no number. The
    alternative rule — `None` below a coverage threshold — is NON-MONOTONE:
    one more claim from an unpriced account would REMOVE a finding's value."""
    # EIGHT OF EIGHTEEN, DELIBERATELY BELOW HALF. A fixture sitting exactly on
    # the boundary would be satisfied by the rejected `>= 0.5 or None` rule and
    # would prove nothing about which rule shipped.
    thin = {account_key(a): 100_000.0 for a in BIG_MONEY_ACCOUNTS[:8]}
    out = _run(value_map=thin, weighted=True)
    money = out.findings[0] if out.findings[0].label == "export latency" \
        else out.findings[1]
    units = money.impact_inputs.native_units
    assert units[ACCOUNT_VALUE_UNIT] == 800_000.0
    assert units[PRICED_ACCOUNTS_UNIT] == 8.0
    assert units[UNPRICED_ACCOUNTS_UNIT] == 10.0
    basis = money.impact_inputs.assumed_params[0].basis
    assert "8 of 18 accounts" in basis
    assert BIG_MONEY_ACCOUNTS[9] in basis, (
        "the accounts a sum could not reach have to be named, not counted")


def test_more_evidence_from_an_unpriced_account_never_removes_the_value():
    """MONOTONICITY, STATED AS A TEST. This is the property the `None`-below-a-
    threshold rule would break, and the reason it was not taken."""
    # Eight priced accounts throughout. The finding grows from ten accounts to
    # eighteen, so its priced SHARE falls from 0.8 to 0.44 — across the bar the
    # rejected rule would have used. The sum must not move, and must not vanish.
    thin = {account_key(a): 100_000.0 for a in BIG_MONEY_ACCOUNTS[:8]}
    before = build_findings(
        [_claim(f"m{i}", "export latency", BIG_MONEY_ACCOUNTS[:10])
         for i in range(10)],
        currency="accounts", now=NOW, value_map=thin, weighted=True)
    after = build_findings(
        [_claim(f"m{i}", "export latency", BIG_MONEY_ACCOUNTS)
         for i in range(18)],
        currency="accounts", now=NOW, value_map=thin, weighted=True)
    assert before.findings[0].impact_inputs.native_units[ACCOUNT_VALUE_UNIT] \
        == after.findings[0].impact_inputs.native_units[ACCOUNT_VALUE_UNIT] \
        == 800_000.0


def test_size_does_not_move_with_how_many_claims_said_it():
    """I1 AT `build_findings` LEVEL. `test_crucible_invariants` asserts this at
    `score_impact` level, which cannot see a value computed upstream here. Same
    accounts, same book, one claim against thirty-seven."""
    accounts = BIG_MONEY_ACCOUNTS[:3]
    book = {account_key(a): 100_000.0 for a in accounts}
    quiet = build_findings(
        [_claim(f"q{i}", "export latency", accounts) for i in range(2)],
        currency="accounts", now=NOW, value_map=book, weighted=True)
    loud = build_findings(
        [_claim(f"l{i}", "export latency", accounts) for i in range(37)],
        currency="accounts", now=NOW, value_map=book, weighted=True)
    assert (quiet.findings[0].impact_inputs.native_units[ACCOUNT_VALUE_UNIT]
            == loud.findings[0].impact_inputs.native_units[ACCOUNT_VALUE_UNIT]
            == 300_000.0)
    assert quiet.impacts[0].size_rank == loud.impacts[0].size_rank


# ── THE VERDICT: decided once, stored, and never re-derived ────────────────

from app.crucible import planner, recon  # noqa: E402
from app.crucible.plan import (  # noqa: E402
    WEIGHTING_UNIT_COUNT,
    WEIGHTING_UNIT_VALUE,
    business_model_unit,
    weighting_verdict,
)


def _coverage_obs(share: float):
    return recon.Observation(
        id="08_sales_data:contracts:priceable_coverage:total_acv_usd",
        kind="priceable_coverage", severity="medium",
        source="08_sales_data:contracts", fields=("account", "total_acv_usd"),
        what="…",
        figures={"priceable_share": share,
                 "threshold": recon.WEIGHTING_MIN_PRICEABLE_SHARE,
                 "book_accounts": 60.0, "priced_accounts": 5.0,
                 "named_accounts": 340.0},
    )


def test_nothing_priceable_counts_and_says_which_of_the_three_reasons():
    v = weighting_verdict([], business_model=WEIGHTING_UNIT_VALUE)
    assert v.unit == WEIGHTING_UNIT_COUNT
    assert "Nothing read here prices an account" in v.because
    assert v.priceable_share is None


def test_below_the_bar_counts_and_states_the_share_in_the_readers_words():
    """B82 verbatim. Measured staging coverage is 3.9%, and this is the
    sentence that ships first."""
    v = weighting_verdict([_coverage_obs(0.039)],
                          business_model=WEIGHTING_UNIT_VALUE)
    assert v.unit == WEIGHTING_UNIT_COUNT
    assert "Only 3.9% of what I read could be priced" in v.because
    assert v.priceable_share == 0.039
    assert v.threshold == 0.5
    assert v.denominator


def test_an_unrecorded_business_model_counts_rather_than_assuming_how_you_sell():
    """Inferring B2B from the presence of a contracts spreadsheet is a guess
    dressed as a fact, and the unit is the most consequential thing the reader
    approves."""
    v = weighting_verdict([_coverage_obs(0.9)], business_model="")
    assert v.unit == WEIGHTING_UNIT_COUNT
    assert "your business model is not recorded" in v.because


def test_a_self_serve_business_counts_and_states_the_deviation_with_its_reason():
    """B100. The benchmark asks a self-serve business for two lists —
    self-serve counted, sales-assisted revenue-weighted — and this cannot
    split them yet. Deferring that is defensible; leaving it unsaid is not."""
    v = weighting_verdict([_coverage_obs(0.9)],
                          business_model=WEIGHTING_UNIT_COUNT)
    assert v.unit == WEIGHTING_UNIT_COUNT
    assert "self-serve or consumer business" in v.because
    assert "not something this can do yet" in v.because


def test_a_sales_assisted_business_with_coverage_weighs():
    v = weighting_verdict([_coverage_obs(0.62)],
                          business_model=WEIGHTING_UNIT_VALUE)
    assert v.unit == WEIGHTING_UNIT_VALUE
    assert v.weighted
    assert "62.0% of what I read names an account" in v.because


def test_the_business_model_is_read_from_free_text_and_never_guessed():
    assert business_model_unit("B2B SaaS") == WEIGHTING_UNIT_VALUE
    assert business_model_unit("Enterprise software") == WEIGHTING_UNIT_VALUE
    assert business_model_unit("self-serve") == WEIGHTING_UNIT_COUNT
    assert business_model_unit("B2C marketplace") == WEIGHTING_UNIT_COUNT
    assert business_model_unit("PLG") == WEIGHTING_UNIT_COUNT
    # An unrecognised value is treated EXACTLY as an absent one — the contract
    # `db.companies.business_type_for_company` states for every reader.
    assert business_model_unit("marketplace") == ""
    assert business_model_unit("") == ""
    # Ambiguous strings resolve to the conservative reading.
    assert business_model_unit("B2B self-serve") == WEIGHTING_UNIT_COUNT
    # THE READER'S ANSWER OUTRANKS THE RECORDED FIELD. Reading the two as one
    # string would let a stale onboarding value carrying "self-serve" overrule
    # somebody who has just been shown the question and answered it.
    assert business_model_unit(
        "self-serve", answer="Sales-assisted or enterprise (B2B)"
    ) == WEIGHTING_UNIT_VALUE
    # ...and an unrecognised answer falls back to the recorded value rather
    # than to nothing.
    assert business_model_unit("B2B SaaS", answer="not sure") \
        == WEIGHTING_UNIT_VALUE


def test_the_gate_question_is_asked_only_when_the_answer_would_change_something():
    """A gate that asks six questions gets six blanks. This one is asked when
    the book could price enough of the corpus to weight AND nobody has said
    how they sell — and never otherwise."""
    from app.crucible.framework import questions_for

    def ids(observations, business_model=""):
        return {q.id for q in questions_for("RICE", observations,
                                            business_model=business_model)}

    assert "business_model" in ids([_coverage_obs(0.9)])
    assert "business_model" not in ids([_coverage_obs(0.9)],
                                       business_model=WEIGHTING_UNIT_VALUE)
    assert "business_model" not in ids([_coverage_obs(0.039)])
    assert "business_model" not in ids([])


def test_the_gate_questions_options_are_the_ones_the_answer_is_read_back_with():
    """A prompt and a parser that carry two different option lists record an
    answer nobody gave."""
    from app.crucible.framework import BUSINESS_MODEL_OPTIONS, questions_for

    q = next(q for q in questions_for("RICE", [_coverage_obs(0.9)])
             if q.id == "business_model")
    assert q.options == BUSINESS_MODEL_OPTIONS
    assert business_model_unit("", answer=BUSINESS_MODEL_OPTIONS[0]) \
        == WEIGHTING_UNIT_VALUE
    assert business_model_unit("", answer=BUSINESS_MODEL_OPTIONS[1]) \
        == WEIGHTING_UNIT_COUNT


# ── THE FINISHED DOCUMENT MUST NOT MISSTATE ITS OWN ORDERING ───────────────


def _row(*, priced_value=None, reach=3.0, claim_types=("preference",)):
    units = {} if priced_value is None else {ACCOUNT_VALUE_UNIT: priced_value}
    return {
        "statement": "customers asked for a faster export",
        "impact_value": reach, "confidence_band": "medium",
        "claim_types": list(claim_types),
        "impact": {"value": reach, "affected_population": reach,
                   "native_units": units},
    }


def test_the_memo_says_it_ranked_by_reach_only_when_it_did():
    """"Ranked by reach — how many accounts each theme touches" is simply
    false on a weighted run, where the count was deliberately EXCLUDED from
    the ordering. A document that misstates its own ordering is the same
    overclaim as one promising a capability the engine lacks, and it is the
    sentence a reader checks the ranking against."""
    from app.crucible.report import _ordering_note

    counted = _ordering_note([_row(), _row()])
    assert "Ranked by reach" in counted
    assert "revenue" not in counted

    weighted = _ordering_note([_row(priced_value=2_979_609.0), _row()])
    assert "Ranked by the revenue behind them" in weighted
    assert "Ranked by reach" not in weighted


def test_the_memo_names_the_second_list_rather_than_letting_it_look_like_a_tail():
    """`_rank` sorts an unpriced finding behind every priced one, so without
    this sentence the second list is indistinguishable from the bottom of the
    first — the unlabelled column this engine refuses to produce."""
    from app.crucible.report import _ordering_note

    note = _ordering_note([_row(priced_value=1.0), _row(), _row()])
    assert "2 further themes could not be priced" in note
    assert "ranked among themselves by accounts touched" in note
    assert "not a size of zero" in note


def test_a_fully_priced_weighted_run_promises_no_second_list():
    from app.crucible.report import _ordering_note

    note = _ordering_note([_row(priced_value=2.0), _row(priced_value=1.0)])
    assert "could not be priced" not in note


def test_the_bucket_and_conflict_clauses_are_identical_on_both_paths():
    """They describe `_rank`'s first two key terms, which weighting does not
    touch. Two copies would be two sentences about one rule."""
    from app.crucible.report import _bucket_and_conflict_clauses, _ordering_note

    rows = [_row(claim_types=("preference",)), _row(claim_types=("constraint",))]
    shared = _bucket_and_conflict_clauses(rows)
    assert "What blocks an account is placed above" in shared
    assert shared.strip() in _ordering_note(rows)
    assert shared.strip() in _ordering_note(
        [_row(priced_value=1.0, claim_types=("preference",)),
         _row(claim_types=("constraint",))])


# ── A QUESTION MAY ONLY BE ASKED IF ITS ANSWER IS READ ─────────────────────


def _divergence_obs():
    return recon.Observation(
        id="08_sales_data:contracts:concentration_divergence:account",
        kind="concentration_divergence", severity="high",
        source="02_support_tickets:tickets", fields=("account", "total_acv_usd"),
        what="…",
        figures={"top_n": 5.0, "groups": 40.0, "volume_share": 0.62,
                 "value_share": 0.18, "even_share": 0.125, "ratio": 3.4},
    )


def test_no_question_offers_an_answer_the_engine_never_reads():
    """THE INVARIANT, APPLIED TO THE ONE PLACE THE READER IS ASKED TO ACT.

    A plan step may only promise work the run performs. A gate QUESTION is the
    same promise with the reader's own effort attached, and breaking it is
    worse: a wrong default is a decision the engine owns and discloses, while
    an unread answer makes the reader believe they owned it.

    Asserted by searching the backend for each question id rather than by
    listing the ones known to be wired, so a question added later is caught by
    the same rule."""
    import pathlib
    import subprocess

    from app.crucible.framework import questions_for

    root = pathlib.Path(recon.__file__).resolve().parents[1]
    asked = {q.id for q in questions_for(
        "RICE", [_coverage_obs(0.9), _divergence_obs()])}
    assert asked, "fixture must produce questions or this is vacuous"

    unread = []
    for qid in sorted(asked):
        hits = subprocess.run(
            ["grep", "-rl", qid, str(root)],
            capture_output=True, text=True).stdout.split()
        # Its own definition in `framework.py` does not count as a reader.
        readers = [h for h in hits if not h.endswith("crucible/framework.py")]
        if not readers:
            unread.append(qid)
    assert not unread, (
        f"these questions collect an answer nothing in the engine reads: "
        f"{unread}")


def test_the_divergence_is_kept_as_evidence_rather_than_as_a_question():
    """Retiring the question must not lose the finding. The divergence is a
    real, checkable fact about the reader's own book and it belongs on the
    plan — it is the strongest motivation for the question that IS wired."""
    from app.crucible.framework import questions_for

    obs = [_coverage_obs(0.9), _divergence_obs()]
    ids = {q.id for q in questions_for("RICE", obs)}
    assert "weighting_choice" not in ids

    q = next(q for q in questions_for("RICE", obs) if q.id == "business_model")
    assert "top 5 of 40 accounts" in q.what_i_saw
    assert "62.0% of the activity" in q.what_i_saw
    assert "18.0% of the money" in q.what_i_saw


def test_the_divergence_step_still_reaches_the_plan():
    """The observation and its step are untouched — only the question went."""
    import tests._tabular_recon_fixtures as tfx

    report = recon.observe([tfx.contracts(), tfx.tickets()])
    assert "concentration_divergence" in {o.kind for o in report.observations}
    steps = planner.minimal_plan(
        goal_text="grow revenue", currency="accounts", report=report,
        source_types=("revenue",))
    assert "compare_measures_across_groups" in {s.primitive for s in steps}
