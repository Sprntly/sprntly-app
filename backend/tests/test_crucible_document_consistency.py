"""The whole run as ONE document: `build_findings` -> `render_report_html`.

WHY THIS FILE EXISTS SEPARATELY FROM THE OTHER TWO. `test_crucible_pipeline.py`
proves the engine ranks and refutes correctly, from real `Claim` objects.
`test_crucible_report.py` proves the renderer says the right things, from
hand-written finding dicts. Nothing proved the two AGREE — and the defect this
feature has paid for most is not a wrong stage, it is a document whose
sentences disagree with each other or with the data underneath them:

  * "Ranked by reach" printed over findings that had no reach;
  * "the largest thing this reading found: Could not be sized";
  * a definition section claiming to govern findings the closing section says
    it did not select;
  * an overflow line promising a reach ranking the lede had just denied.

All four rendered cleanly, passed every unit test, and were found by a person
reading the page. So the assertions here are GENERAL — they read the document
back and check it against the findings it was built from, rather than pinning
one sentence — and they run over several shapes of corpus, because each of the
four was a branch that only one shape reaches.

Everything is driven from real claims through the real pipeline: no finding
dict is written by hand, so a number in the prose is only right if the engine
and the renderer agree about it. No network, no DB, no LLM.
"""
from __future__ import annotations

import ast
import inspect
import re
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional, Sequence
from unittest import mock

import pytest

from app.crucible import plan as plan_mod
from app.crucible.pipeline import PipelineResult, build_findings
from app.crucible.plan import SourceInventory, build_plan
from app.crucible.report import render_report_html
from app.crucible.types import Claim, PopulationFilter

NOW = datetime(2026, 8, 19, tzinfo=timezone.utc)


# ── Building a run out of claims ─────────────────────────────────────────────

def claim(
    cid: str, *, subject: str, days_ago: int = 1, accounts: Sequence[str] = (),
    authoritative: bool = True, strength: str = "reported",
    ctype: str = "mechanism", source: str = "customer_voice",
    direction: str = "neutral", assertion: Optional[str] = None,
    artifact: str = "doc-a",
) -> Claim:
    """One normalised signal. Same shape as `test_crucible_pipeline.claim`.

    `accounts` is what makes a finding SIZEABLE — `score_impact` sizes by how
    many named accounts a theme touches — so a claim with none of them is how
    this file produces the unsized case that I3 is about.
    """
    return Claim(
        id=cid, assertion=assertion or f"{subject} came up again",
        type=ctype, subject=subject, source_id=source, artifact_id=artifact,
        artifact_type="t", strength=strength,
        observed_at=NOW - timedelta(days=days_ago), authoritative=authoritative,
        population=PopulationFilter(
            segments={"accounts": tuple(accounts),
                      "customer_side": tuple(accounts)},
            estimated_size=len(accounts) or None,
        ),
        direction=direction,
    )


def _theme(name: str, accounts: Sequence[Sequence[str]], *,
           direction: Sequence[str] = ()) -> list[Claim]:
    """One theme's claims, spread over months and over source documents.

    Spread deliberately: `_refute` kills a cluster whose claims all land inside
    ten days AND come from one document, so a theme built any other way would
    be dropped before it could reach the renderer at all.
    """
    out = []
    for i, accts in enumerate(accounts):
        out.append(claim(
            f"{name}-{i}", subject=name, accounts=accts,
            days_ago=4 + i * 30, artifact=f"doc-{name}-{i}",
            direction=(direction[i] if i < len(direction) else "neutral"),
        ))
    return out


#: The synthetic companies this repo is allowed to name. The repo is public.
ACCOUNTS = ("Northwind", "Globex", "Initech", "Acme", "Vandelay Industries",
            "Tessellate")


def _sized_theme(name: str, n: int = 3) -> list[Claim]:
    return _theme(name, [(ACCOUNTS[i % len(ACCOUNTS)],) for i in range(n)])


def _unsized_theme(name: str, n: int = 3) -> list[Claim]:
    """A theme no account can be attached to — sizeable is not the same as
    small, and this is the corpus shape where that distinction is load-bearing."""
    return _theme(name, [() for _ in range(n)])


def _conflict_theme(name: str) -> list[Claim]:
    """Two authoritative sources that may both speak, disagreeing. `_rank`
    places it above everything that is not one, whatever its size."""
    return _theme(name, [("Acme",), ("Vandelay Industries",)],
                  direction=("positive", "negative"))


# ── The projection the route writes, and the guard that it still is ──────────

def _rows(result: PipelineResult) -> list[dict]:
    """`PipelineResult` -> the finding rows `crucible_findings` stores.

    A COPY of `routes.crucible.execute_run`'s projection, because that
    projection is inline in a function that needs a database. The copy is what
    lets this file render a REAL pipeline result; the test below keeps it from
    drifting from the original, which is the only way a copy is safe.
    """
    rows = []
    for rank, (finding, impact, confidence) in enumerate(zip(
        result.findings, result.impacts, result.confidences
    )):
        rows.append({
            "statement": finding.statement,
            "claim_ids": list(finding.claim_ids),
            "adjudication": finding.adjudication,
            "impact_value": impact.value,
            "currency": impact.currency,
            "confidence_band": confidence.band,
            "surfaced_by": list(finding.confidence_inputs.surfaced_by),
            "assumed_params": [{"name": p.name, "basis": p.basis}
                               for p in impact.assumed_params],
            "impact": {"value": impact.value,
                       "affected_population": impact.affected_population},
            "confidence": {"band": confidence.band,
                           "weakest_leg": confidence.weakest_leg,
                           "weakest_leg_reason": confidence.weakest_leg_reason,
                           "cap_reason": confidence.cap_reason},
            "tier": "deep" if rank < result.deep_count else "shallow",
        })
    return rows


def _ledger(result: PipelineResult) -> list[dict]:
    return [{"label": r.label, "reason": r.reason,
             "stopped_at_stage": r.stopped_at, "claim_ids": list(r.claim_ids)}
            for r in result.rejected]


#: The source inventory these runs are planned against. Counts are arbitrary;
#: what matters is that the plan does its own arithmetic over them.
_INVENTORY = (
    SourceInventory("customer_voice", 812, "calls and customer tickets",
                    "what customers asked for and reported"),
    SourceInventory("project_mgmt", 1_204, "the tracker",
                    "what was built, broken, blocked or attempted"),
    SourceInventory("communication", 377, "Slack and email",
                    "what was discussed, hit and attempted"),
)


def _plan(excluded: tuple[str, ...] = (), hypotheses: tuple[str, ...] = ()) -> dict:
    """A real `build_plan`, over a synthetic inventory.

    Built by the plan builder rather than hand-written, so "the sources named
    in What was read are the ones the plan kept, net of exclusions" is a claim
    about the code path a user actually gets — the exclusion filter and the
    signal total are computed here, not asserted into existence by a fixture.
    """
    with mock.patch.object(
        plan_mod, "source_inventory",
        lambda company_id: (list(_INVENTORY),
                            sum(s.signal_count for s in _INVENTORY)),
    ):
        return build_plan(
            company_id="co-1", goal_text="increase revenue by 5%",
            definition_text="recognised revenue from paying accounts, net of "
                            "refunds, as finance books it",
            excluded_sources=excluded, hypotheses=hypotheses,
        ).to_json()


class Doc(NamedTuple):
    html: str
    rows: list[dict]
    ledger: list[dict]
    plan: dict
    result: PipelineResult


def _document(claims: Sequence[Claim], *, plan: Optional[dict] = None,
              goal: str = "increase revenue by 5%") -> Doc:
    """A run, end to end: claims -> pipeline -> stored rows -> document."""
    result = build_findings(list(claims), currency="accounts", now=NOW)
    rows, ledger = _rows(result), _ledger(result)
    plan = _plan() if plan is None else plan
    run = {
        "id": 1, "goal_text": goal,
        "coverage_notes": [
            {"reason": "evidence is dated by ingest, not by when it happened",
             "actual": "most signals carry the timestamp we read them at"},
        ],
        "prioritisation": {"plan": plan},
    }
    return Doc(render_report_html(run, rows, ledger, plan),
               rows, ledger, plan, result)


# ── Reading the document back ────────────────────────────────────────────────

_BLOCK = re.compile(r"<h3>(\d+)\. (.*?)</h3>(.*?)(?=<h[23]>|$)", re.S)
_OVERFLOW_ROW = re.compile(r"<li>(\d+)\. (.*?)</li>")
_PARAGRAPH = re.compile(r"<p>(.*?)</p>", re.S)


def _count(word: str) -> int:
    """"one" and "7" are the same number written two ways; the prose uses both."""
    return 1 if word.strip().lower() == "one" else int(word)


def _section(html: str, heading: str) -> str:
    """One `<h2>` section's text, up to the next one."""
    start = html.index(f"<h2>{heading}</h2>")
    return _from(html, start)


def _from(html: str, start: int) -> str:
    rest = html[start:]
    nxt = rest.find("<h2>", 1)
    return rest if nxt == -1 else rest[:nxt]


def _findings_text(html: str) -> str:
    """The findings section alone.

    Blocks and overflow rows are counted INSIDE it, because both patterns are
    generic enough to match elsewhere — a ledger label that happened to begin
    "3. " would otherwise be counted as a finding, and a count that can be
    inflated by tenant text is not a count.
    """
    m = re.search(r"<h2>What the evidence says \(\d+\)</h2>", html)
    return _from(html, m.start()) if m else ""


# ── The consistency assertions ───────────────────────────────────────────────

def _assert_the_counts_match_the_data(doc: Doc) -> None:
    """Every number the prose states is the number of the things it counts."""
    html, rows = doc.html, doc.rows

    heading = re.search(r"<h2>What the evidence says \((\d+)\)</h2>", html)
    if not rows:
        assert heading is None
        assert "Nothing survived verification" in html
    else:
        assert heading, "the findings section lost its count"
        assert int(heading.group(1)) == len(rows), (
            f"heading says {heading.group(1)} findings, the run has {len(rows)}"
        )

    listed = _findings_text(html)
    blocks = _BLOCK.findall(listed)
    overflow = _OVERFLOW_ROW.findall(listed)
    beyond = re.search(r"A further (\d+) findings are on the run", listed)
    unlisted = int(beyond.group(1)) if beyond else 0

    # NOTHING VANISHES. Full blocks + one-line rows + the counted remainder is
    # the whole run, or the document has quietly become an excerpt.
    assert len(blocks) + len(overflow) + unlisted == len(rows), (
        f"{len(blocks)} blocks + {len(overflow)} rows + {unlisted} unlisted "
        f"!= {len(rows)} findings"
    )
    # And the numbering is one unbroken sequence, so a reader who sees "37."
    # knows there are 36 above it.
    printed = [int(n) for n, _s, _b in blocks] + [int(n) for n, _s in overflow]
    assert printed == list(range(1, len(printed) + 1)), "the rank numbers skip"

    said = re.search(r"The next (\d+) findings are listed below", listed)
    assert bool(said) == bool(overflow), (
        "the overflow list and the sentence announcing it must appear together"
    )
    if said:
        assert int(said.group(1)) == len(overflow), (
            f"it said {said.group(1)} and printed {len(overflow)}"
        )

    # Each block's own numbers are its own row's.
    for rank, statement, body in blocks:
        row = rows[int(rank) - 1]
        assert statement[:60] in row["statement"] or statement.startswith(
            row["statement"][:60]
        ), f"block {rank} is not showing row {rank}"
        claims = re.search(r"(\d+) claims?</p>", body)
        assert claims, f"block {rank} lost its claim count"
        assert int(claims.group(1)) == len(row["claim_ids"]), (
            f"block {rank} says {claims.group(1)} claims, the row rests on "
            f"{len(row['claim_ids'])}"
        )

    if rows:
        # The headline is about the row the list puts first — otherwise the
        # document opens by summarising something other than what it ranks.
        headline = _section(html, "The short version")
        assert doc.rows[0]["statement"][:60] in headline.replace(
            "&#x27;", "'").replace("&quot;", '"').replace("&amp;", "&")
        rests = re.search(r"resting on (\d+) claims?", headline)
        assert rests, "the headline lost its claim count"
        assert int(rests.group(1)) == len(rows[0]["claim_ids"])

    # How many could not be sized, wherever the document says it.
    unsized = sum(1 for r in rows if r["impact_value"] is None)
    for stated in re.findall(
        r"(?:and )?(\w+) of (?:them|these) could not be sized", html
    ):
        assert _count(stated) == unsized, (
            f"the prose says {stated} unsized, the run has {unsized}"
        )


def _assert_what_was_read_is_what_the_plan_kept(doc: Doc) -> None:
    """The sources named are the plan's kept ones, net of exclusions — and the
    arithmetic over them holds."""
    read = _section(doc.html, "What was read")
    kept = doc.plan["sources"]
    excluded = doc.plan["excluded_sources"]

    stated = re.search(r"([\d,]+) signals? across (\d+) sources?", read)
    assert stated, "What was read lost its inventory line"
    assert int(stated.group(2)) == len(kept), (
        f"it says {stated.group(2)} sources and lists {len(kept)}"
    )
    assert int(stated.group(1).replace(",", "")) == doc.plan["total_signals"]

    # THE TOTAL IS THE SUM OF THE ROWS UNDER IT. A total taken before the
    # exclusion filter reads as a larger corpus than the one that was read, and
    # nothing else in the document could reveal it.
    bullets = [int(n.replace(",", ""))
               for n in re.findall(r"<li><strong>([\d,]+) ", read)]
    assert sum(bullets) == doc.plan["total_signals"], (
        f"the bullets sum to {sum(bullets)}, the total says "
        f"{doc.plan['total_signals']}"
    )
    for source in kept:
        assert source["label"] in read, f"{source['label']} was read and not named"

    # An excluded source is named ONCE, as excluded, and nothing it witnesses
    # is claimed as read.
    for key in excluded:
        # A multi-word key must be softened; a single-word one already reads as
        # English, so the column name and the reader's word are the same string
        # and its presence proves nothing either way.
        if "_" in key:
            assert key not in doc.html, "a source type leaked in its column name"
        human = key.replace("_", " ")
        assert f"You excluded {human}" in read or f", {human}" in read
        label, _witnesses = plan_mod._SOURCE_PROSE[key]
        assert label not in doc.html, (
            f"{key} was excluded and its evidence is still listed as read"
        )


#: Sentences that cannot both be true of one run. Each pair is a defect that
#: shipped: the document said both, in two places, and read fine in each.
_CONTRADICTIONS = (
    ("Ranked by reach", "Nothing in this reading could be sized"),
    ("largest thing this reading found", "Could not be sized"),
    ("rank lower by reach", "not by size, which nothing here had"),
    ("measured against that sentence", "not selected for your goal"),
    ("nothing has been dropped", "not listed here"),
)


def _assert_no_two_sentences_contradict(doc: Doc) -> None:
    for a, b in _CONTRADICTIONS:
        assert not (a in doc.html and b in doc.html), (
            f"the document says both {a!r} and {b!r}"
        )


#: Claims about the ORDER or the SIZE of what was found. Every one of them is
#: only true under a condition, and each shipped once without it.
_SUPERLATIVES = ("largest thing this reading found",
                 "largest of the ones that could be sized")


def _assert_every_size_claim_is_earned(doc: Doc) -> None:
    html, rows = doc.html, doc.rows
    sized = [r for r in rows if r["impact_value"] is not None]
    unsized = [r for r in rows if r["impact_value"] is None]
    top_is_conflict = bool(rows) and rows[0]["adjudication"] == "conflict"

    if not sized:
        for phrase in ("Ranked by reach", "rank lower by reach") + _SUPERLATIVES:
            assert phrase not in html, (
                f"{phrase!r} claims a size ranking over findings that have no "
                f"size at all"
            )
    if unsized:
        assert "largest thing this reading found" not in html, (
            "the superlative quantifies over every finding, and an unsized one "
            "is not a small one — its size is unknown, and an unknown can be "
            "bigger"
        )
    if top_is_conflict:
        for phrase in _SUPERLATIVES:
            assert phrase not in html, (
                "a conflict is placed first BY RULE, so its position says "
                "nothing about its size"
            )
    if "Ranked by reach" in html:
        assert sized, "nothing here had a reach to rank by"
    if "Not ranked by reach" in html:
        assert not sized, "findings had sizes and the document denied it"
    if "largest thing this reading found" in html:
        assert not unsized and not top_is_conflict

    # SAID IN ONE BREATH. The worst of these four was one sentence: "It is the
    # largest thing this reading found: Could not be sized." A paragraph is the
    # unit a reader takes as a single claim.
    for para in _PARAGRAPH.findall(html):
        if "Could not be sized" in para:
            for phrase in _SUPERLATIVES:
                assert phrase not in para, (
                    f"one paragraph calls something {phrase!r} and says in the "
                    f"same breath that it could not be sized: {para[:160]}"
                )

    # The overflow line's ranking basis is the lede's.
    if "The next" in html and "findings are listed below" in html:
        if sized:
            assert "they rank lower by reach" in html
        else:
            assert "they rank lower by reach" not in html
            assert "not by size, which nothing here had" in html


def _assert_null_is_never_zero_or_small(doc: Doc) -> None:
    """I3, end to end. "Could not be sized" and "0 accounts" lead to opposite
    decisions, and nothing downstream can tell them apart once they look alike."""
    html, rows = doc.html, doc.rows
    unsized_ranks = {i + 1 for i, r in enumerate(rows)
                     if r["impact_value"] is None}

    assert "0 account" not in html
    listed = _findings_text(html)
    blocks = _BLOCK.findall(listed)
    # Only the rows that got a full block can say anything about their size, so
    # the phrase appears exactly when one of THOSE could not be sized. Stated
    # over the rendered rows rather than over the run, because a large run
    # keeps its unsized tail in the one-line overflow list.
    rendered_unsized = any(int(rank) in unsized_ranks for rank, _s, _b in blocks)
    assert rendered_unsized == ("Could not be sized" in listed)

    for rank, _statement, body in blocks:
        meta = _PARAGRAPH.search(body)
        assert meta, f"block {rank} lost its meta line"
        reach = meta.group(1).split(" · ")[0]
        if int(rank) in unsized_ranks:
            assert reach == "Could not be sized", (
                f"an unsized finding rendered its size as {reach!r}"
            )
        else:
            expected = rows[int(rank) - 1]["impact_value"]
            assert re.fullmatch(r"[\d,]+ accounts?", reach), reach
            assert int(reach.split()[0].replace(",", "")) == int(expected)

    # AND NOT "SMALL" ANYWHERE EITHER. The only sanctioned uses of the word are
    # the two that deny it — an unmeasured theme described as a minor one is
    # the same defect as rendering it 0, one register softer.
    for m in re.finditer(r"\bsmall\w*\b", html):
        window = html[max(0, m.start() - 30):m.start()]
        assert "not a " in window or "without being " in window, (
            f"...{html[max(0, m.start() - 60):m.start() + 40]}..."
        )


#: Positive claims that the confirmed definition decided what appears below.
#: `_load_signals` reads the whole corpus and `build_findings` is called with
#: no goal argument, so every one of these is false — and the closing section
#: says so, which is what made the definition section's version a contradiction
#: rather than merely an overstatement.
_GOVERNANCE_CLAIMS = (
    "measured against that sentence",
    "everything below is measured against",
    "selected for your goal",       # only ever appears as "NOT selected for…"
    "filtered or ranked by relevance to your definition",
)


def _assert_the_definition_does_not_claim_to_have_selected(doc: Doc) -> None:
    html = doc.html
    for phrase in _GOVERNANCE_CLAIMS:
        for m in re.finditer(re.escape(phrase), html):
            window = html[max(0, m.start() - 90):m.start()]
            assert re.search(r"\b(not|nothing|never|did not)\b", window), (
                f"{phrase!r} is asserted rather than denied: "
                f"...{html[max(0, m.start() - 90):m.end()]}"
            )
    if "<blockquote>" in html:
        # A recorded definition MUST carry its own disclaimer, in its own
        # section — the correction three sections lower is not a correction if
        # the claim above it is the one a reader takes away.
        establish = _section(html, "What this was asked to establish")
        assert "did not decide which findings appear below" in establish
    assert "These findings were not selected for your goal." in html


def _assert_the_ledger_adds_up(doc: Doc) -> None:
    html, ledger = doc.html, doc.ledger
    heading = re.search(r"<h2>Considered and ruled out \((\d+)\)</h2>", html)
    if not ledger:
        assert heading is None
        return
    assert heading and int(heading.group(1)) == len(ledger)
    grouped = sum(int(n) for n in re.findall(r"<p><strong>(\d+)</strong> died", html))
    further = re.search(r"(\d+) further rejections", html)
    assert grouped + (int(further.group(1)) if further else 0) == len(ledger), (
        f"the reason groups account for {grouped} of {len(ledger)} rejections"
    )
    for row in ledger[:20]:
        assert row["label"][:80] in html


def assert_internally_consistent(doc: Doc) -> None:
    """Every check in this file, over one document.

    Applied to every corpus shape rather than to one, because each of the four
    contradictions lived in a branch that only some shapes reach.
    """
    _assert_the_counts_match_the_data(doc)
    _assert_what_was_read_is_what_the_plan_kept(doc)
    _assert_no_two_sentences_contradict(doc)
    _assert_every_size_claim_is_earned(doc)
    _assert_null_is_never_zero_or_small(doc)
    _assert_the_definition_does_not_claim_to_have_selected(doc)
    _assert_the_ledger_adds_up(doc)


# ── The corpora. One per shape the four defects lived in. ───────────────────

def _mixed_corpus() -> list[Claim]:
    """Sized and unsized side by side — where "the largest thing this reading
    found" was printed over a run that could not size half of itself."""
    return (
        _sized_theme("export runs time out", 4)
        + _sized_theme("seat provisioning is manual", 3)
        + _unsized_theme("invite emails land in spam")
        + _unsized_theme("csv column order changes between exports")
        # A one-claim candidate, so the ledger is populated too.
        + [claim("lone-1", subject="a single passing remark",
                 accounts=("Acme",), days_ago=12, artifact="doc-lone")]
    )


def _all_unsized_corpus() -> list[Claim]:
    """No account attribution anywhere — the shape "Ranked by reach" and the
    overflow line were both printed over."""
    return (
        _unsized_theme("invite emails land in spam", 4)
        + _unsized_theme("csv column order changes between exports", 3)
        + _unsized_theme("search results are stale after an import", 2)
    )


def _all_sized_corpus() -> list[Claim]:
    return _sized_theme("export runs time out", 5) + _sized_theme(
        "seat provisioning is manual", 3)


def _conflict_led_corpus() -> list[Claim]:
    """An authoritative disagreement, with a bigger finding beneath it —
    `_rank`'s dominant term, and the case where "largest" was claimed for a row
    that was placed first by rule."""
    return (_conflict_theme("seat pricing")
            + _sized_theme("export runs time out", 5)
            + _unsized_theme("invite emails land in spam"))


def _wholly_rejected_corpus() -> list[Claim]:
    """Everything dies in verification. The ledger IS the result."""
    return [claim(f"c{i}", subject=f"passing remark {i}", accounts=("Acme",),
                  days_ago=3 + i, artifact=f"doc-{i}") for i in range(6)]


def _many_themes_corpus(n: int = 170) -> list[Claim]:
    """Enough themes to overrun `MAX_FULL_FINDING_BLOCKS`, half of them
    unsized — the overflow paragraph's branch, driven by the engine."""
    out: list[Claim] = []
    for i in range(n):
        name = f"theme {i} — a realistically long label of the kind the graph produces"
        out += (_sized_theme(name, 3) if i % 2 else _unsized_theme(name, 3))
    return out


_CORPORA = {
    "mixed": _mixed_corpus,
    "all unsized": _all_unsized_corpus,
    "all sized": _all_sized_corpus,
    "conflict led": _conflict_led_corpus,
    "wholly rejected": _wholly_rejected_corpus,
}


# ── 1. The document agrees with itself, whatever the run found ──────────────

@pytest.mark.parametrize("shape", sorted(_CORPORA))
def test_the_document_a_real_run_produces_is_internally_consistent(shape):
    """THE POINT OF THIS FILE. Real claims, the real pipeline, the real
    renderer — and then the document read back and checked against the findings
    it was built from, on every shape of corpus the four known defects lived in.
    """
    assert_internally_consistent(_document(_CORPORA[shape]()))


def test_a_run_that_overruns_the_block_cap_is_still_internally_consistent():
    """The overflow path, from claims rather than from fixtures. The count in
    "The next N findings" is the number of rows printed, the ranks continue
    unbroken from the last full block, and nothing is lost between them."""
    doc = _document(_many_themes_corpus())
    from app.crucible.report import MAX_FULL_FINDING_BLOCKS

    assert len(doc.rows) > MAX_FULL_FINDING_BLOCKS, (
        "this corpus no longer overruns the cap, so the test is not exercising "
        "what it claims"
    )
    assert "The next" in doc.html
    assert_internally_consistent(doc)


def test_a_run_too_large_even_for_the_overflow_list_counts_every_finding():
    """PAST THE SECOND CAP. Beyond the one-line list there is a third state —
    counted and not listed — and the sentence announcing it was once written
    from the wrong set: the document promised "the remaining 681 are listed
    below … nothing has been dropped" and then printed 400 rows and conceded
    281 were missing, in two adjacent paragraphs.

    So the identity has to hold across all three states at once: full blocks
    plus listed rows plus the counted remainder is the whole run.
    """
    from app.crucible.report import MAX_FULL_FINDING_BLOCKS, MAX_OVERFLOW_ROWS

    doc = _document(_many_sized_themes(
        MAX_FULL_FINDING_BLOCKS + MAX_OVERFLOW_ROWS + 60))
    assert re.search(r"A further (\d+) findings are on the run", doc.html), (
        "this corpus no longer overruns the overflow list, so the third state "
        "is not being exercised"
    )
    assert "nothing has been dropped" not in doc.html
    assert_internally_consistent(doc)


def test_the_document_is_consistent_when_the_reader_dropped_a_source():
    """GATE 2's untick has to survive to the report. The excluded source is
    named once as excluded, its evidence is never listed as read, and the
    signal total is the total of what remains."""
    plan = _plan(excluded=("communication",),
                 hypotheses=("onboarding is too slow",))
    doc = _document(_mixed_corpus(), plan=plan)

    assert "Slack and email" not in doc.html
    assert "You excluded communication" in doc.html
    assert doc.plan["total_signals"] == 812 + 1_204
    assert_internally_consistent(doc)


# ── 2. The four contradictions, generally ───────────────────────────────────

def test_no_document_claims_a_reach_ranking_over_findings_that_have_no_reach():
    """PAIR ONE. "Ranked by reach — how many accounts each theme touches" was
    printed as the findings lede on a run whose every finding was unsized, two
    lines under a headline that had just said nothing here could be sized."""
    doc = _document(_all_unsized_corpus())
    assert all(r["impact_value"] is None for r in doc.rows) and doc.rows

    assert "Ranked by reach" not in doc.html
    assert "Not ranked by reach" in doc.html
    _assert_every_size_claim_is_earned(doc)
    _assert_no_two_sentences_contradict(doc)


def test_no_superlative_is_ever_attached_to_a_finding_that_has_no_size():
    """PAIR TWO. "It is the largest thing this reading found: Could not be
    sized." One sentence, both halves rendered by the same branch.

    Asserted over every shape, and at the paragraph level, because the
    superlative and the size are only ever a contradiction when they are read
    together."""
    for shape, build in sorted(_CORPORA.items()):
        doc = _document(build())
        _assert_every_size_claim_is_earned(doc)
        for para in _PARAGRAPH.findall(doc.html):
            assert not ("Could not be sized" in para
                        and "largest" in para), f"{shape}: {para[:200]}"


def test_the_definition_section_never_claims_to_have_chosen_the_findings():
    """PAIR THREE. The definition section said "everything below is measured
    against that sentence and nothing else" while the limits section said
    nothing was filtered or ranked by it — the false half three sections above
    its own correction, in the more prominent position."""
    doc = _document(_mixed_corpus())
    assert "<blockquote>" in doc.html, "this run recorded no definition"

    _assert_the_definition_does_not_claim_to_have_selected(doc)
    _assert_no_two_sentences_contradict(doc)
    # Both halves, in the order a reader meets them.
    assert doc.html.index("did not decide which findings appear below") < \
        doc.html.index("These findings were not selected for your goal.")


def test_the_overflow_line_never_claims_a_ranking_the_lede_denied():
    """PAIR FOUR. The overflow paragraph called the remainder "ranked lower by
    reach" unconditionally, directly beneath a lede that had just said nothing
    here could be sized."""
    unsized = _document(_many_unsized_themes())
    assert "The next" in unsized.html, "expected the overflow paragraph"
    assert "rank lower by reach" not in unsized.html
    assert "not by size, which nothing here had" in unsized.html
    _assert_every_size_claim_is_earned(unsized)

    # The control: a run that CAN size its findings keeps the sentence.
    sized = _document(_many_sized_themes())
    assert "they rank lower by reach" in sized.html
    _assert_every_size_claim_is_earned(sized)


def _many_unsized_themes(n: int = 170) -> list[Claim]:
    return [c for i in range(n)
            for c in _unsized_theme(f"unsized theme {i}", 3)]


def _many_sized_themes(n: int = 170) -> list[Claim]:
    return [c for i in range(n)
            for c in _sized_theme(f"sized theme {i}", 3)]


# ── 3. I3, from claim to rendered page ──────────────────────────────────────

def test_a_theme_with_no_named_account_is_unsized_everywhere_it_appears():
    """I3, end to end and in one assertion: the claims carry no account, the
    engine sizes the theme `None`, and every place the document mentions it
    says so — never 0, never "small", never a number."""
    doc = _document(_mixed_corpus())
    unsized = [r for r in doc.rows if r["impact_value"] is None]
    assert unsized, "this corpus no longer produces an unsized finding"

    _assert_null_is_never_zero_or_small(doc)
    # And the engine's own view agrees with the page: the sized count the
    # pipeline published is the number of rows the document renders with a size.
    assert doc.result.stats["sizeable"] == sum(
        1 for r in doc.rows if r["impact_value"] is not None)


def test_an_unsized_finding_ranked_above_a_sized_one_denies_neither():
    """A conflict is placed first by rule, so an unsized row can sit above a
    412-account one. The document must not then say nothing could be sized, and
    must not call the top row the largest."""
    claims = _conflict_theme("seat pricing") + _sized_theme(
        "export runs time out", 5)
    # The conflict theme names two accounts, so make the top row unsized by
    # stripping its accounts while keeping the disagreement.
    claims = [c for c in claims if not c.id.startswith("seat pricing")] + [
        claim("seat pricing-0", subject="seat pricing", direction="positive",
              days_ago=4, artifact="doc-k0"),
        claim("seat pricing-1", subject="seat pricing", direction="negative",
              days_ago=44, artifact="doc-k1"),
    ]
    doc = _document(claims)
    assert doc.rows[0]["adjudication"] == "conflict"
    assert doc.rows[0]["impact_value"] is None
    assert any(r["impact_value"] is not None for r in doc.rows)

    assert "Nothing in this reading could be sized" not in doc.html
    assert "largest" not in doc.html
    assert_internally_consistent(doc)


# ── 4. The copy this file renders is the one the server writes ──────────────

def test_the_projection_this_suite_renders_is_the_one_the_route_stores():
    """`_rows` above is a copy of `execute_run`'s finding projection, and a
    copy is only safe while something notices it drifting.

    A field added to the stored row and not to this file would mean every
    assertion here runs against a document the server never produces — the
    exact shape of "green tests, broken page" this suite exists to close.
    """
    from app.routes import crucible as routes

    tree = ast.parse(inspect.cleandoc(inspect.getsource(routes.execute_run)))
    stored: list[str] = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "append"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "rows"):
            stored = [k.value for k in node.args[0].keys]
    assert stored, "could not find execute_run's finding projection"

    mine = list(_rows(build_findings(
        _sized_theme("export runs time out", 3),
        currency="accounts", now=NOW))[0])
    assert mine == stored, (
        f"this file renders {mine}, the route stores {stored}"
    )

# ── Bookkeeping is not a candidate ───────────────────────────────────────────

def _aggregate_ledger():
    """100 real rejections plus the two rows `build_findings` appends when the
    list overflows and when signals could not be grouped at all."""
    from app.crucible.pipeline import OVERFLOW_STAGE, UNGROUPED_STAGE
    return (
        [{"id": i, "label": f"candidate {i}",
          "reason": "only 1 supporting claim — an anecdote, not a finding",
          "stopped_at_stage": "clustering"} for i in range(100)]
        + [{"id": 900, "label": "1476 further candidates",
            "reason": "1476 more groups were considered and dropped",
            "stopped_at_stage": OVERFLOW_STAGE}]
        + [{"id": 901, "label": "2777 ungroupable signals",
            "reason": "2777 signals have no usable embedding",
            "stopped_at_stage": UNGROUPED_STAGE}]
    )


def _aggregate_doc() -> str:
    """The document a ledger with both bookkeeping rows produces."""
    run = {"id": 1, "goal_text": "increase revenue by 5%", "coverage_notes": [],
           "prioritisation": {"plan": _plan()}}
    return render_report_html(run, [], _aggregate_ledger(), _plan())


def test_the_ledger_count_is_candidates_not_rows():
    """A run that considered 1,576 candidates reported "Considered and ruled
    out (102)" — because the overflow summary and the ungroupable row were
    counted as two more rejections."""
    html = _aggregate_doc()
    assert "Considered and ruled out (100)" in html
    assert "Considered and ruled out (102)" not in html


def test_bookkeeping_rows_do_not_become_their_own_reasons():
    """Grouped by reason, the two aggregates each formed a group of one, so a
    ledger with ONE cause reported three."""
    html = _aggregate_doc()
    assert "every one of them died for the same one" in html
    assert "3 of them across" not in html


def test_the_bookkeeping_numbers_are_still_stated():
    """Excluding them from the count must not delete them: they carry the only
    statement of how much was NOT listed."""
    html = _aggregate_doc()
    assert "1476 further candidates" in html
    assert "2777 ungroupable signals" in html


def test_the_report_does_not_promise_it_listed_everything():
    """"Everything that was considered is listed below" is false the moment the
    list overflows — which is the ordinary case on a real corpus."""
    html = _aggregate_doc()
    assert "Everything that was considered is listed below" not in html
    assert "the remainder is counted with it" in html

# ── Excluding a source narrows what the run PROMISES, not just what it reads ──

def test_dropping_the_numeric_sources_flips_the_gap_and_its_remedy():
    """Approval narrowed `sources` and `total_signals` in place and left
    `cannot_answer` / `will_produce` at their pre-exclusion values. A reader who
    unticked analytics and revenue still got "your analytics/revenue data is
    connected and will be read" in the same document that said those sources
    were excluded — and LOST the gap that had just become true, along with the
    remedy that would close it, handed "no action needed from you" instead."""
    from app.crucible.plan import SourceInventory, derive_gaps_and_promises
    full = (
        SourceInventory("customer_voice", 132, "calls", "what customers asked"),
        SourceInventory("analytics", 197, "product analytics", "how much moved"),
        SourceInventory("revenue", 28, "revenue data", "how much moved"),
    )
    with_numbers, produce_with = derive_gaps_and_promises(full)
    without, produce_without = derive_gaps_and_promises(full[:1])

    # With the numeric sources: the engine cannot size yet, and that is on us.
    assert "connected and will be read" in with_numbers[0].because
    assert "no action needed from you" in with_numbers[0].remedy
    assert any("connected and will be read" in p for p in produce_with)

    # Without them: a different gap, and one the reader can actually close.
    assert "nothing connected here carries numbers" in without[0].because
    assert "Amplitude" in without[0].remedy
    assert not any("connected and will be read" in p for p in produce_without), (
        "the plan promised to read a source the reader had just dropped"
    )


def test_the_promise_and_the_gap_come_from_one_function():
    """The plan gate and the approve path derive these separately; if they can
    drift, the document contradicts the card the reader approved. One pure
    function, so drift is not representable."""
    from app.crucible.plan import SourceInventory, derive_gaps_and_promises
    kept = (SourceInventory("customer_voice", 1, "calls", "what customers said"),)
    assert derive_gaps_and_promises(kept) == derive_gaps_and_promises(kept)
