"""The finished run, rendered as a document. (Users never see "Crucible".)

WHY THIS IS SERVER-SIDE AND NOT IN TYPESCRIPT. `GoalAnalysisReport.tsx` already
renders a run for the panel. This renders the SAME run for everything that is
not the panel: the editable document, chat grounding, export. Two renderers
means two places where "an unsized finding renders as 'could not be sized',
never as 0" has to be true, and the way that rule stops being true is that
somebody fixes it in one of them. So the panel keeps its React tree for the
LIVE run and this produces the frozen document, and the tests below assert the
same invariants against this one that `GoalAnalysisReport.dom.test.tsx` asserts
against that one.

WHAT THE OUTPUT HAS TO SURVIVE. This HTML is stored in `custom_artifacts.
body_html`, which sanitizes on every write (`app/custom_artifact_html.py`) to
the allowlist the rich-text editor can produce. So:

  * no `class`, no `data-*`, no `id` — every one of them is stripped, and a
    renderer that leans on them produces markup that reads correctly here and
    arrives at the editor bare;
  * no `<section>`/`<article>`/`<header>`/`<details>` — those are UNWRAPPED
    (content kept, tag dropped), which is harmless but means the structure you
    write is not the structure that is stored;
  * headings, paragraphs, lists, blockquotes and inline emphasis only.

That is a real constraint on how much a report can say with layout, so it says
it with words instead — which is the right trade for a document a person is
about to edit by hand.

EVERY PIECE OF TENANT TEXT IS ESCAPED. A finding statement is projected from a
customer's own documents, so it is untrusted input by the time it reaches here;
`_esc` runs on all of it, and the sanitizer downstream is a second line rather
than the only one.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from html import escape
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from app.html_style import inject_canonical_css

from app.crucible.data_gaps import (
    DATA_GAPS_HEADING, ONE_TOPIC_NOTE, data_gaps_for, option_header,
    option_numbers, options_are_one_topic,
)
from app.crucible.moscow import (
    CALL_COUNT_FLOOR_NOTE, TYPE_BUCKET_BLOCKER, TYPE_BUCKET_PREFERENCE,
    has_call_count, type_bucket,
)

#: Rendered into `custom_artifacts.title`. The goal text follows it, so a
#: reader scanning the shared library can tell one run's report from another's.
TITLE_PREFIX = "Goal analysis"

#: `custom_artifacts.kind` for a Goal Analysis document. The library groups by
#: this and the chat edit tool resolves its target by it, so it is a constant
#: rather than a string typed at each call site.
ARTIFACT_KIND = "goal_analysis"


def body_fingerprint(body_html: str) -> str:
    """The detach detector: sha256 of the body EXACTLY as stored.

    Stored on the run at render time (`crucible_runs.report_body_hash`), and
    compared against the artifact's current body to decide whether the report
    has been edited. A HASH rather than a boolean flag, because the flag would
    have to be written by whoever edits — and the ordinary hand edit goes
    through `PATCH /v1/custom-artifacts/{id}`, a route that knows nothing about
    Goal Analysis and should not have to. Deriving it means an edit made
    through ANY writer — the editor's autosave, the chat tool, a future
    importer — detaches the report without that writer being told to.

    Hashed AFTER sanitizing, i.e. of the string the database actually holds:
    the sanitizer rewrites markup (it escapes `&`, drops attributes), so a
    fingerprint taken of the pre-sanitize HTML would never match the stored
    body and every report would read as edited the moment it was created.
    """
    return hashlib.sha256((body_html or "").encode("utf-8")).hexdigest()


def _esc(value: Any) -> str:
    """Escaped text, and "" for None. Every tenant string goes through here."""
    if value is None:
        return ""
    return escape(str(value), quote=True)


def _as_dict(value: Any) -> dict:
    """A jsonb column as a dict, whatever the driver handed back.

    Supabase returns real dicts; the SQLite mirror the tests run against
    decodes the columns it is told about, and a column it was not told about
    arrives as JSON text. Tolerating both here means a renderer bug cannot hide
    behind a driver difference.
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _reach(finding: dict) -> str:
    """A finding's size, in words.

    NULL IS "COULD NOT BE SIZED" AND IS NEVER A NUMBER. I3: an unmeasured
    theme and a measured-and-tiny one lead to opposite decisions, and rendering
    the first as 0 asserts the second. This is the single rule this file exists
    to keep, and it is why the value is formatted here rather than at each call
    site.
    """
    value = finding.get("impact_value")
    if value is None:
        return "Could not be sized"
    currency = (finding.get("currency") or "").strip()
    if currency == "accounts":
        try:
            count = int(value)
        except (TypeError, ValueError):
            count = value
        return f"{count} account{'' if count == 1 else 's'}"
    return f"{value}{f' {currency}' if currency else ''}"


def _p(text: str) -> str:
    return f"<p>{text}</p>"


def _ul(items: Iterable[str]) -> str:
    body = "".join(f"<li>{item}</li>" for item in items)
    return f"<ul>{body}</ul>" if body else ""


#: A chart, drawn in characters. The exported document is sanitized to an
#: allowlist (`app/custom_artifact_html.py`) that DROPS `<svg>` with its
#: contents and keeps no width, display or padding CSS — so a bar made of
#: `<div style="width:62%">` arrives at the editor as an empty box, and a bar
#: made of characters arrives as itself. Characters also print: these survive
#: Chrome's print-to-PDF unchanged, which is how a report gets archived.
#:
#: The live panel draws the same bars from the same proportion, in CSS rather
#: than glyphs; the two renderers share no code, so they are kept in step by
#: hand like every other rule in this file.
_BAR_GLYPH = "\u2588"
_BAR_CELLS = 20

#: The bar's colour and the numeral face, lifted from the canonical report
#: stylesheets (`backend/skills/prd-author/assets/prd.css`'s `--green` and its
#: IBM Plex Mono numerals) so this reads as the same family as the PRD and the
#: evidence brief.
#:
#: CARRIED AS AN INLINE `style`, NOT A CLASS, and that is forced rather than
#: chosen. This HTML is stored in `custom_artifacts.body_html`, whose sanitizer
#: strips `class` off every tag and drops `<style>` blocks with their contents,
#: so a stylesheet class here would arrive at the editor as bare markup.
#: `color` and `font-family` are on that sanitizer's short CSS allowlist;
#: `width`, `height`, `border` and `background-image` are not — which is why a
#: bar is drawn in characters rather than as a sized box, and why it also
#: survives print-to-PDF, where Chrome omits backgrounds by default.
_BAR_STYLE = "color: #1A6B47; font-family: 'IBM Plex Mono', monospace"


def _bar_cell(value: Any, largest: Any) -> str:
    """A bar, wrapped for the page. "" when there is nothing honest to draw."""
    bar = _bar(value, largest)
    return f'<span class="bar" style="{_BAR_STYLE}">{bar}</span>' if bar else ""


def _bar(value: Any, largest: Any) -> str:
    """A bar proportional to `largest`, or "" when there is nothing to draw.

    NEVER DRAWN FOR AN UNSIZED VALUE (I3). `None` returns "" and the caller
    prints "Not measured" instead — a zero-length bar in a column of long ones
    asserts "small", which is the one thing an unknown size must never say.

    A non-zero value always gets at least one cell, so a real-but-tiny number
    is visible rather than indistinguishable from nothing. The number itself
    renders beside every bar, so the bar is never the only statement of size.
    """
    try:
        v, top = float(value), float(largest)
    except (TypeError, ValueError):
        return ""
    if v <= 0 or top <= 0:
        return ""
    cells = max(1, min(_BAR_CELLS, round(_BAR_CELLS * v / top)))
    return _BAR_GLYPH * cells


def _human_source(source_type: str) -> str:
    """`project_mgmt` reads as "project mgmt", not as a column name.

    An excluded source is only a KEY by the time the report runs — its label
    went with the plan entry the run dropped. Softened rather than looked up,
    for the reason the panel gives: a second copy of the backend's source prose
    would drift from the first.
    """
    return _esc((source_type or "").replace("_", " "))


def _goal_definition(run: dict, plan: dict) -> str:
    """The sentence the run was given to work from, or ""."""
    return (
        (plan.get("definition_text") or "").strip()
        or (_as_dict(run.get("prioritisation")).get("proposed_definition") or "").strip()
    )


def _ask_section(run: dict, plan: dict) -> str:
    """What you asked, and what you told us it meant.

    IT USED TO OPEN THE DOCUMENT AND NOW IT CLOSES IT. The reader's own
    verdict on the old shape was that the answer should be the first thing on
    the page and that everything establishing what the run was given belongs
    behind it. The goal is still the title, so restating the question at the
    top was also saying it twice.

    WHAT IS RECORDED HERE IS UNCHANGED, including the branch for a run with no
    confirmed definition — that one is a disclosure, not throat-clearing, and
    it moves without being softened.
    """
    goal = (run.get("goal_text") or "").strip()
    definition = _goal_definition(run, plan)
    out = ["<h3>What you asked, and what you said it meant</h3>"]
    if goal:
        out.append(_p(_esc_clipped(goal, MAX_STATEMENT_CHARS)))
    if definition:
        out.append(_p("In your own words, this meant:"))
        out.append(f"<blockquote>{_esc(definition)}</blockquote>")
    else:
        # STATED, NOT SKIPPED. A report with no recorded definition is a
        # report whose subject is unknown, and omitting the line would make
        # that look like the ordinary case.
        out.append(_p(
            "You never confirmed what the goal means, so we held the memo to "
            "the goal exactly as you typed it and nothing narrower."
        ))
    return "".join(out)


#: How a small count reads inside a sentence. The reference memo writes
#: "Two are high confidence", not "2 are high confidence" — a numeral at the
#: head of a sentence reads as a data point and a word reads as prose. Only
#: the handful this document ever needs; anything larger stays a numeral,
#: which is also what the reference does ("214 named companies").
_SMALL_NUMBERS = {
    1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five",
}


def _count_word(n: int, *, capital: bool = True) -> str:
    word = _SMALL_NUMBERS.get(n)
    if not word:
        return f"{n:,}"
    return word if capital else word.lower()


def _claim_sentence(finding: dict) -> str:
    """The finding as a sentence about the reader's business.

    THE SENTENCE THIS REPLACES WAS ABOUT US. Every finding carries a stored
    `statement` in the engine's own bookkeeping voice — "64 claims across 14
    accounts concern a reported theme: Sales Pipeline" — which leads with how
    much evidence we hold and reaches the thing the reader cares about in the
    last clause, behind a colon. The reader's verdict on it was that the
    document read like an audit trail.

    SO THE CLAIM LEADS AND THE COUNT SITS INSIDE IT. Nothing new is asserted:
    every clause is a read of a field the engine already computed — the
    theme's own label, how many accounts it reaches (`_reach`, so an unsized
    theme still says so), the KIND of claim behind it (`type_bucket`), and
    whether two sources that may both speak contradict each other. What
    changed is the order of the words.

    "" WHEN THERE IS NO LABEL, and that is deliberate rather than a fallback:
    a labelless finding's own heading IS its stored statement, so a
    constructed sentence here would say the same thing twice, three lines
    apart, which is the duplication the previous pass was asked to remove.
    """
    label = (finding.get("label") or "").strip()
    if not label:
        return ""
    topic = _esc_clipped(label, MAX_STATEMENT_CHARS)
    bucket = type_bucket([str(t) for t in _as_list(finding.get("claim_types"))])
    sized = finding.get("impact_value") is not None
    unit = (finding.get("currency") or "accounts").strip()

    if bucket == TYPE_BUCKET_BLOCKER:
        verb = f"report being blocked by {topic}"
    elif bucket == TYPE_BUCKET_PREFERENCE:
        verb = f"have asked for {topic}"
    else:
        verb = f"talk about {topic}"

    # THE SUBJECT IS ONLY EVER A COUNT OF ACCOUNTS. A theme sized in some
    # other unit cannot be the subject of "… have asked for" without the
    # sentence claiming the unit is a group of people, so that size is stated
    # separately instead.
    if sized and unit == "accounts":
        parts = [f"{_esc(_reach(finding))} {verb}."]
    elif sized:
        parts = [
            f"Accounts {verb}.",
            f"This reading sizes it at {_esc(_reach(finding))}.",
        ]
    else:
        # I3, said as a sentence rather than as a label. An unsized theme is
        # one whose size is unknown, and the sentence has to carry that or the
        # absence reads as smallness.
        parts = [
            f"Accounts {verb}, and nothing we read says how many.",
            "How far it reaches is unknown here, which is not the same as "
            "small.",
        ]

    if bucket not in (TYPE_BUCKET_BLOCKER, TYPE_BUCKET_PREFERENCE):
        parts.append(
            "Nothing in it reads as blocked or as a request, so take it as "
            "context rather than as something stopping you."
        )
    if (finding.get("adjudication") or "") == "conflict":
        parts.append(
            "Two sources that may both speak on this contradict each other, "
            "which is why we put it first."
        )
    return _p(" ".join(parts))


def _grounded_money(finding: dict) -> Optional[tuple[float, float, Optional[int]]]:
    """`(committed, of which read back from summaries, accounts)` or `None`.

    ONE READ OF `native_units`, SHARED. The figure appears twice now — as a
    chip on the recommendation screen and as a paragraph inside the write-up
    — and two reads of the same nested dict is how the two end up disagreeing
    about whether a number exists.
    """
    commercial = _as_dict(_as_dict(finding.get("impact")).get("native_units"))
    usd = commercial.get("commercial_committed_usd")
    if not isinstance(usd, (int, float)):
        return None
    derived = commercial.get("commercial_committed_usd_derived")
    derived = float(derived) if isinstance(derived, (int, float)) else 0.0
    accounts = commercial.get("commercial_grounded_accounts")
    accounts = (
        int(accounts) if isinstance(accounts, (int, float)) and accounts
        else None
    )
    return float(usd), derived, accounts


def _option_chips(finding: dict, *, full: bool = False) -> str:
    """The facts under an option, as a strip rather than as a clause.

    THIS IS THE HONESTY-VERSUS-READABILITY TRADE, MADE ONCE. "Measured at: 14
    accounts · 64 claims · 1 source document" was a sentence about our own
    method sitting in the middle of a paragraph about the reader's business.
    None of those numbers is dropped — they move to a strip under the claim,
    where a reader takes them in at a glance and the prose beside them can be
    about the finding.
    """
    bits = [_esc(_reach(finding))]
    band = (finding.get("confidence_band") or "").strip()
    if band:
        bits.append(f"{_esc(band)} confidence")
    money = _grounded_money(finding)
    if money:
        bits.append(f"${money[0]:,.0f} named by customers")
    if (finding.get("adjudication") or "") == "conflict":
        bits.append("<strong>sources disagree</strong>")
    if full:
        claims = len(_as_list(finding.get("claim_ids")))
        if claims:
            bits.append(f"{claims} claim{'' if claims == 1 else 's'}")
        sources = len([x for x in _as_list(finding.get("surfaced_by")) if x])
        if sources:
            bits.append(
                f"{sources} source document{'' if sources == 1 else 's'}"
            )
    return " · ".join(bits)


def _unlocks_block(finding: dict, account_value: Any) -> str:
    """What acting on this is worth — or, far more often, that we cannot say.

    THE SLOT IS FILLED HONESTLY OR IT SAYS IT IS EMPTY. On this corpus money
    is attributable on very few findings, and the two failure modes either
    side of that are both worse than the gap: padding it with a projection
    invents revenue, and hiding it lets a reader assume the number was
    considered and came out small. So the empty case is a sentence in the
    memo's own voice that says which of the two things is unknown.

    THREE SOURCES, IN DESCENDING STRENGTH, and each says which it is:
    figures customers actually stated, then the reader's own per-account
    estimate carried through, then nothing.
    """
    money = _grounded_money(finding)
    if money:
        usd, derived, accounts = money
        where = (
            f" across {accounts} named account{'' if accounts == 1 else 's'}"
            if accounts else ""
        )
        # PROVENANCE IS PART OF THE CLAIM, NOT A FOOTNOTE. A figure recovered
        # from a written summary is not the same evidence as one captured
        # against a verified verbatim quote: the summary was itself written
        # under a grounding gate, so the number came from real text, but it
        # was copied once more than a quoted figure was and could have been
        # copied wrong. "Customers named $X" is only true of the quoted kind.
        if derived >= usd:
            return _p(
                f"Customers put <strong>${usd:,.0f}</strong>{where} against "
                f"this. We read those figures back out of written summaries "
                f"rather than matching them to a verified quote, so each is "
                f"only as good as the summary it came from. It is a sum of "
                f"what was stated, not a projection of what you would gain."
            )
        if derived:
            return _p(
                f"Customers put <strong>${usd:,.0f}</strong>{where} against "
                f"this — a sum of figures they actually quoted, not a "
                f"projection of what you would gain. ${derived:,.0f} of it we "
                f"read back out of written summaries rather than matching to "
                f"a verified quote."
            )
        return _p(
            f"Customers put <strong>${usd:,.0f}</strong>{where} against this "
            f"— a sum of figures they actually quoted, not a projection of "
            f"what you would gain."
        )

    if finding.get("impact_value") is not None:
        reach_n = float(finding.get("impact_value") or 0)
        estimate = _finding_money_estimate(reach_n, account_value)
        if estimate:
            return _p(
                f"Nobody we read named a figure for this one. On your own "
                f"numbers it is {_stop(estimate)}"
            )
        return _p(
            "Nothing we read puts money on this, so neither do we. What we "
            "can tell you is how far it reaches, above."
        )
    return _p(
        "Nothing we read puts money on this, and nothing sized it either. "
        "Both are unknown here rather than zero, and we would rather leave "
        "the gap than fill it with an estimate."
    )


def _definition_method_note(run: dict) -> str:
    """What the confirmed definition did and did not decide. APPENDIX, because
    it is method: the definition itself is at the top of the memo, in
    `_ask_section`, where a reader needs it.

    DID A RELEVANCE GATE ACTUALLY RUN ON THIS RUN? Written by the route the
    moment `judge_relevance` completes without raising — never guessed from
    whether anything ended up set aside, because a gate that ran and kept
    everything is still a gate that ran, and reads the "it did not decide
    which findings appear" sentence as false the moment it exists.

    Claim SELECTION never sees the definition on either branch
    (`build_findings` takes a `goal_accounts` filter production does not
    pass). What changed when the gate shipped is which findings a reader is
    SHOWN, and a run that ran the gate must not print the sentence denying it.
    """
    gate_ran = bool(_as_dict(run.get("prioritisation")).get("relevance_gate_ran"))
    out = ["<h3>What the definition decided</h3>"]
    if gate_ran:
        out.append(_p(
            "Every theme was checked against your confirmed definition for "
            "whether it bears on the goal, and what did not is listed below "
            "with the reason. Nothing was SELECTED by it: a theme reaches "
            "that check because it is in the evidence you approved."
        ))
    else:
        out.append(_p(
            "Nothing here was filtered or ranked by your definition — a theme "
            "appears because it is in the evidence you approved, not because "
            "it bears on what you asked about."
        ))
    return "".join(out)


def _what_was_read_section(run: dict, plan: dict) -> str:
    out = ["<h3>What was read</h3>"]
    if plan:
        sources = [s for s in _as_list(plan.get("sources")) if isinstance(s, dict)]
        total = plan.get("total_signals") or 0
        out.append(_p(
            f"{total:,} signal{'' if total == 1 else 's'} across {len(sources)} "
            f"source{'' if len(sources) == 1 else 's'}, listed separately "
            f"because each witnesses different things."
        ))
        out.append(_ul(
            f"<strong>{(s.get('signal_count') or 0):,} {_esc(s.get('label'))}</strong>"
            f" — {_esc(s.get('witnesses'))}"
            for s in sources
        ))
        excluded = [e for e in _as_list(plan.get("excluded_sources")) if e]
        if excluded:
            out.append(_p(
                "You excluded "
                + ", ".join(_human_source(e) for e in excluded)
                + " before this ran, so nothing below rests on it."
            ))
    else:
        out.append(_p(
            "This run kept no record of which sources it read, so what is "
            "below cannot be checked against its own inputs."
        ))

    # COVERAGE SITS HERE, above the findings, not in a footer. A note that a
    # third of the evidence was undated changes how every line beneath it
    # should be read, and a degradation discovered after the conclusion has
    # already done its damage.
    notes = [n for n in _as_list(run.get("coverage_notes")) if isinstance(n, dict)]
    if notes:
        out.append("<h4>What was missing from it</h4>")
        out.append(_ul(
            f"<strong>{_esc(n.get('reason'))}</strong> — {_esc(n.get('actual'))}"
            for n in notes
        ))
    return "".join(out)


#: How many rows the scoring table shows. The reference memo scores two options
#: and lists the rest in an appendix; a table of 149 rows is not a table.
MAX_RICE_ROWS = 10


def _stat_strip(
    plan: dict, considered: list[dict], kept: list[dict],
) -> str:
    """The headline numbers, on one line, before the prose.

    Memo p1 closes its cover with a strip: CURRENT ARR · 2% TARGET ·
    INITIATIVES FOUND · HIGH CONFIDENCE · RECOMMENDED · DATA WINDOW. Every
    number in it exists somewhere in this document already and is currently
    spread across four paragraphs, which is the difference between a reader
    knowing the shape of the answer in one glance and assembling it themselves.

    ONLY WHAT IS COUNTED. The memo's ARR and target are money; this corpus has
    neither unless the reader supplied a per-account figure at the gate, so the
    money cell appears only then and says whose number it is. There is
    deliberately no DATA WINDOW cell: claim dates are the INGEST clock on this
    substrate — `call_digest` and the coverage notes both say so — and a window
    printed from them would be the date we read the evidence, presented as the
    period it covers.
    """
    if not considered:
        return ""
    sized = [f for f in kept if f.get("impact_value") is not None]
    high = [f for f in kept if (f.get("confidence_band") or "") == "high"]
    recommended = [
        f for f in kept
        if _as_dict(f.get("recommendation")).get("action")
        or _as_dict(f.get("deep_recommendation")).get("action")
    ]
    reach = sum(float(f.get("impact_value") or 0) for f in sized)

    cells: list[tuple[str, str]] = [
        ("Signals read", f"{int(plan.get('total_signals') or 0):,}"),
        ("Themes found", f"{len(considered):,}"),
        ("Bear on this goal", f"{len(kept):,}"),
        ("Sized", f"{len(sized):,}"),
        ("High confidence", f"{len(high):,}"),
    ]
    if recommended:
        # NOT "with A recommendation" — that read as the same count the prose
        # a few lines down names ("the top 2 get a full recommendation"),
        # which counts only the DEEP pass. This cell counts flat OR deep — the
        # union — so a run can show 8 here and 2 there, both true, about two
        # different senses of the word. The label says which one this is.
        cells.append(("Carry any suggestion", f"{len(recommended):,}"))
    value = plan.get("account_value")
    if sized and isinstance(value, (int, float)) and value > 0:
        # LABELLED IN THE CELL ITSELF. A number in a strip is read as a fact,
        # and this one is the reader's own estimate multiplied out — so the
        # label carries "your estimate" rather than leaving it to a footnote
        # three sections down.
        cells.append(("Reach × your estimate", f"{reach * float(value):,.0f}"))

    body = "".join(
        f"<td><strong>{_esc(v)}</strong><br><span>{_esc(k)}</span></td>"
        for k, v in cells
    )
    # NO `class`. The document sanitizer strips attributes — a styling hook
    # here would be silently removed from the saved artifact, so the strip is
    # built from tags the sanitizer keeps and reads as a row either way.
    return f'<table class="strip"><tbody><tr>{body}</tr></tbody></table>'


def _funnel_chart(
    plan: dict, considered: list[dict], kept: list[dict], written: int,
) -> str:
    """Signals read → themes found → bear on this goal → written up, as bars.

    Every number here already exists in the document. What did not exist was
    the SHAPE: the narrowing was three paragraphs of prose in three different
    sections, and a funnel is a thing you see rather than a thing you read.

    ONE UNIT PER SCALE, WHICH IS WHY SIGNALS GET NO BAR. Signals and themes
    are not the same thing counted twice — thousands of signals cluster into
    tens of themes — so drawing both against one axis would put a full-width
    bar beside three single cells and hide the 30→22 step the reader actually
    needs. The signal count still leads the funnel, as the number it is; the
    three theme stages share a scale and can therefore be compared.

    HONEST BY CONSTRUCTION. Every bar carries its own number, and a stage with
    nothing in it is omitted rather than drawn at zero.
    """
    signals = int(plan.get("total_signals") or 0)
    stages = [
        ("Themes found", len(considered)),
        ("Bear on this goal", len(kept)),
        ("Written up here", written),
    ]
    stages = [(k, v) for k, v in stages if v]
    if len(stages) < 2:
        return ""
    largest = max(v for _, v in stages)
    rows = ""
    if signals:
        rows += (
            f"<tr><td>Signals read</td><td></td>"
            f"<td><strong>{signals:,}</strong></td></tr>"
        )
    rows += "".join(
        f"<tr><td>{_esc(k)}</td><td>{_bar_cell(v, largest)}</td>"
        f'<td><strong>{v:,}</strong></td></tr>'
        for k, v in stages
    )
    return f'<table class="chart"><tbody>{rows}</tbody></table>'


def _decision_section(plan: dict, findings: list[dict]) -> str:
    """The memo's decision box: who signs off, by when, and what is at stake.

    ONLY WHAT WAS ANSWERED. The plan gate asks questions the run cannot
    answer for itself; a reader who skipped them gets no box rather than a box
    of blanks, because a decision box with an empty owner is worse than none —
    it implies the decision has a home when it does not.

    WHAT IS AT STAKE IS DERIVED, NOT ASSERTED. The memo writes "if we do
    nothing" as a forecast. This corpus cannot forecast, so the line states
    what the evidence COUNTS — accounts touched by findings that bear on the
    goal — which is a fact rather than a prediction.
    """
    owner = str(plan.get("decision_owner") or "").strip()
    needed = str(plan.get("needed_by") or "").strip()
    if not owner and not needed:
        return ""
    sized = [f for f in findings if f.get("impact_value") is not None]
    reach = sum(float(f.get("impact_value") or 0) for f in sized)
    value = plan.get("account_value")
    out = ["<h2>Who decides, and by when</h2>"]
    bits = []
    if owner:
        bits.append(f"<strong>Owner</strong> {_esc_clipped(owner, MAX_PARAM_NAME_CHARS)}")
    if needed:
        bits.append(f"<strong>Needed by</strong> {_esc_clipped(needed, MAX_PARAM_NAME_CHARS)}")
    out.append(_p(" · ".join(bits)))
    if sized:
        money = ""
        if isinstance(value, (int, float)) and value > 0:
            # ONE MULTIPLICATION, AND IT IS LABELLED. `account_value` is an
            # estimate somebody typed, so the product is an estimate too — said
            # here in the same breath rather than in a footnote.
            money = (
                f" — about {reach * float(value):,.0f} on your own figure of "
                f"{float(value):,.0f} per account, which is an estimate you "
                f"gave rather than something measured"
            )
        out.append(_p(
            f"What is in front of you touches <strong>{reach:g} "
            f"accounts</strong>{money}. That is what we counted, not a "
            f"forecast of what changes if you act on it."
        ))
    return "".join(out)


def _recommendation_basis_section(basis: str) -> str:
    """The count of deep recommendations is arithmetic, not a bare
    number — the number of projects really has to be in context of the
    question and what the goal is. Silent when there is nothing to say
    (no deep pass ran, or every finding on the run predates it).
    """
    basis = (basis or "").strip()
    if not basis:
        return ""
    # CAPITALISED AND TERMINATED, because it follows a bold full stop and is
    # therefore the start of a sentence. `basis` is authored as a clause — it
    # also renders mid-sentence elsewhere — so left alone it produced
    # "How many got a full recommendation. you named a target of…".
    return _p(
        f"<strong>How many got a full write-up.</strong> "
        f"{_stop(_upper_first(_esc_clipped(basis, MAX_STATEMENT_CHARS)))}"
    )


#: `citations[]` rows a synthesized recommendation renders. Mirrors
#: `recommend.MAX_SYNTHESIS_CITATIONS` — not imported, for the same reason
#: `MAX_DEEP_CHANGES` below is not: this renders a STORED row and must bound
#: it even for one written before the constant existed or changed value.
MAX_SYNTHESIS_CITATIONS_RENDERED = 6


def _why_this_section(synth: dict, written: list[dict]) -> str:
    """The argument behind the answer — and NOT a second answer.

    ONLY ONE THING IN THIS DOCUMENT IS CALLED THE RECOMMENDATION, and it is
    the screen at the top. This section used to be headed "The recommendation"
    and to open with "Recommended. <action>", two screens above a set of
    numbered options headed "Option 1 — recommended." Those were the same
    sentence twice: `recommend.build_synthesized_recommendation` copies rank
    one's action VERBATIM and asks the model only for the prose around it. A
    reader met the word "recommended" in three places and could not tell which
    of them was the ask.

    SO THE ACTION IS PRINTED HERE ONLY WHEN IT IS NOT ALREADY ON THE PAGE.
    Compared against the first write-up's own action rather than assumed
    identical, because a row stored before that binding existed could carry
    something else, and silently dropping it would lose a sentence rather than
    de-duplicate one.

    Silent when there is nothing to show: a run with zero or exactly one kept
    deep recommendation (see `recommend.build_synthesized_recommendation`'s
    own "only when there is more than one" rule), or one whose call/citation
    gate produced nothing usable.
    """
    action = (synth.get("action") or "").strip()
    because = (synth.get("because") or "").strip()
    if not action or not because:
        return ""
    out = ["<h2>Why we would start here</h2>"]
    on_page = {
        " ".join((_as_dict(f.get("deep_recommendation")).get("action") or "").split())
        for f in written
    }
    if " ".join(action.split()) not in on_page:
        out.append(_p(
            f"<strong>What we would do.</strong> "
            f"{_esc_clipped(action, MAX_STATEMENT_CHARS)}"
        ))
    out.append(_p(_esc_clipped(because, MAX_ARGUMENT_CHARS)))
    citations = [
        c for c in _as_list(synth.get("citations"))
        if isinstance(c, dict) and (c.get("evidence") or "").strip()
    ]
    if citations:
        out.append("<p><strong>What that rests on.</strong></p>")
        out.append(_ul(
            f"{_esc_clipped(c.get('evidence'), MAX_STATEMENT_CHARS)} "
            f'<em class="src">{_SOURCE_LEAD_IN} '
            f"{_esc_clipped(c.get('cited_claim'), MAX_PARAM_BASIS_CHARS)}"
            f"</em>"
            for c in citations[:MAX_SYNTHESIS_CITATIONS_RENDERED]
        ))
    return "".join(out)


def _framework_section(
    findings: list[dict], plan: dict,
    *, with_table: bool = True,
) -> str:
    """Dispatch to the ranking table for whichever framework this run
    actually used — RICE or MoSCoW (`app.crucible.framework.
    SUPPORTED_FRAMEWORKS`). ADDITIVE: the RICE branch below is
    `_rice_section`, byte-for-byte the section that already existed, plus
    one line stating WHY it was chosen — the reason it was chosen appears
    in the plan and in the final report. Only the MoSCoW branch is new.
    """
    from app.crucible.framework import display_name

    framework = str(plan.get("framework") or "")
    reason = str(plan.get("framework_reason") or "")
    # THE HEADING IS SAID, NOT THE ENUM. `framework` on the stored plan is the
    # storage/comparison value ("rice", "moscow") — display_name() is what a
    # reader is shown ("RICE", "MoSCoW"). Passed down already-converted so
    # neither section function has to remember to do it.
    label = display_name(framework) if framework else framework
    if framework.strip().lower() == "moscow":
        return _moscow_section(
            findings, label, reason, with_table=with_table,
        )
    return _rice_section(findings, label, reason, with_table=with_table)


def _rice_section(
    findings: list[dict], framework: str, framework_reason: str = "",
    *, with_table: bool = True,
) -> str:
    """The ranking, and the arithmetic behind it — the memo's §04.

    THE SKILL'S OUTPUT SPEC, followed: "The main artifact is always the ranked
    list … accompanied by a 'how we scored it' table so the ranking is
    reviewable, never a black box", with "every input marked real vs
    [ASSUMPTION]" and "a sensitivity note naming the 1-2 items whose rank flips
    on a shaky input".

    THE TABLE DOES NOT SET THE ORDER. `_rank` froze that before this ran; these
    rows are rendered in the order they arrive. A scoring table that re-sorted
    would be the prioritisation step mutating the ranking, which is I10.
    """
    from app.crucible.rice import EFFORT_ABSENT, RICE_INPUTS, rice_for, sensitivity

    if not findings or not framework:
        return ""
    rows = [
        rice_for(
            label=(f.get("label") or "").strip() or _statement_text(f),
            # `impact_value`, the SAME field `_reach` reads. Taking it from
            # the nested `impact` dict instead let the table disagree with the
            # sentence three inches above it about whether a finding was sized.
            reach=f.get("impact_value"),
            reach_unit=(f.get("currency") or "accounts"),
            claim_types=[str(t) for t in _as_list(f.get("claim_types"))],
            confidence_band=(f.get("confidence_band") or ""),
        )
        for f in findings[:MAX_RICE_ROWS]
    ]
    if not rows:
        return ""

    out = [
        f"<h2>The ranking ({_esc(framework)})</h2>" if with_table
        else f"<h3>How the ranking works ({_esc(framework)})</h3>"
    ]
    if not with_table:
        # THE MECHANICS, NOT THE ANSWER. The table itself sits in the memo
        # above; how its terms are defined is method, and method reads after
        # the decision rather than in front of it.
        if framework_reason:
            out.append(_p(framework_reason))
        out.append(_ul([
            "<strong>Reach</strong> — how many of your accounts the theme "
            "touches. Counted, not estimated.",
            "<strong>Impact</strong> — how directly it bears on the metric, "
            "read from the kind of claim behind it: something blocked "
            "outranks something asked for, which outranks something "
            "described. <em>That ordering is ours, not your data's.</em>",
            "<strong>Confidence</strong> — the band the evidence earned, "
            "lowered once for each input this table could not fill.",
            f"<strong>Effort</strong> — <em>{EFFORT_ABSENT}</em>. Nothing "
            "connected carries a person-month, and inventing one would put a "
            "number in front of you that no evidence supports.",
        ]))
        if all(not r.effort for r in rows):
            # EVERY ROW IS SCORED ON THE SAME MISSING TERM, so say what that
            # costs. Keyed on effort alone: `scored_without_effort` also wants
            # a reach, so one unsized row used to swallow the sentence.
            out.append(_p(
                "With no effort anywhere, the score is reach × impact × "
                "confidence. An effort applied equally to every row divides "
                "them all by the same number and cannot change their order."
            ))
        return "".join(out)

    # THE BAR'S SCALE: the widest reach among the rows actually rendered, so a
    # bar is read against its own table and never against a row that is not
    # on the page. An unsized row contributes nothing to it and draws nothing
    # (I3) — a zero-length bar in a column of long ones asserts "small".
    largest = max(
        [r.reach for r in rows if isinstance(r.reach, (int, float))] or [0]
    )
    body = "".join(
        "<tr>"
        f"<td>{_esc_clipped(r.label, MAX_PARAM_NAME_CHARS)}</td>"
        f"<td>{'Not measured' if r.reach is None else f'{r.reach:g} {_esc(r.reach_unit)}<br>{_bar_cell(r.reach, largest)}'}</td>"
        f"<td>{r.impact:g}</td>"
        f"<td>{_esc(r.confidence_band)}</td>"
        f"<td>{_esc(EFFORT_ABSENT)}</td>"
        f"<td>{'—' if r.score is None else f'{r.score:.1f}'}</td>"
        f"<td>{r.inputs_present} of {len(RICE_INPUTS)}</td>"
        "</tr>"
        for r in rows
    )
    out.append(
        '<table class="rank"><thead><tr>'
        "<th>Theme</th><th>Reach</th><th>Impact</th><th>Confidence</th>"
        "<th>Effort</th><th>Score</th><th>Inputs</th>"
        "</tr></thead><tbody>" + body + "</tbody></table>"
    )

    # NO SILENT CAPS. A table that stops at ten without saying so reads as the
    # whole ranking.
    if len(findings) > len(rows):
        out.append(_p(
            f"The other {len(findings) - len(rows)} are listed below, not "
            f"scored out here — a table this long stops being one."
        ))
    flips = sensitivity(rows)
    if flips:
        out.append(_p(
            "<strong>Sensitive to an estimate we do not have:</strong> "
            + _esc(", ".join(flips))
            + " — where their effort lands decides where they sit."
        ))
    return "".join(out)


def _moscow_section(
    findings: list[dict], framework: str, framework_reason: str = "",
    *, with_table: bool = True,
) -> str:
    """The MUST/SHOULD/COULD ranking, for a corpus RICE cannot size.

    SAME NON-REORDERING DISCIPLINE AS `_rice_section` (I10): `_rank` already
    froze the order this ran with, so `moscow.group_by_bucket` groups rows by
    bucket without disturbing the sequence within a bucket.
    """
    from app.crucible.moscow import moscow_for

    if not findings or not framework:
        return ""
    rows = [
        moscow_for(
            label=(f.get("label") or "").strip() or _statement_text(f),
            reach=f.get("impact_value"),
            reach_unit=(f.get("currency") or "accounts"),
            claim_types=[str(t) for t in _as_list(f.get("claim_types"))],
            surfaced_by=[str(s) for s in _as_list(f.get("surfaced_by"))],
        )
        for f in findings[:MAX_RICE_ROWS]
    ]
    if not rows:
        return ""

    out = [
        f"<h2>The ranking ({_esc(framework)})</h2>" if with_table
        else f"<h3>How the ranking works ({_esc(framework)})</h3>"
    ]
    if not with_table:
        if framework_reason:
            out.append(_p(framework_reason))
        out.append(_ul([
            "<strong>MUST</strong> — a stated blocker: something is stopping "
            "an account today. <em>Marked <strong>MUST?</strong> when only "
            "one source document backs it — real, worth confirming.</em>",
            "<strong>SHOULD / COULD</strong> — a stated preference: something "
            "an account asked for.",
            "<strong>Reach</strong> — how many of your accounts the theme "
            "touches. Counted, not estimated.",
            "Graded by how many <strong>independent source documents</strong> "
            "back each one, not by raw claim count — several restatements "
            "of one complaint from one document are one voice.",
        ]))
        return "".join(out)

    largest = max(
        [r.reach for r in rows if isinstance(r.reach, (int, float))] or [0]
    )
    body = "".join(
        "<tr>"
        f"<td>{_esc_clipped(r.label, MAX_PARAM_NAME_CHARS)}</td>"
        f"<td>{_esc(r.bucket)}</td>"
        f"<td>{_esc(r.bucket_basis)}</td>"
        f"<td>{'Not measured' if r.reach is None else f'{r.reach:g} {_esc(r.reach_unit)}<br>{_bar_cell(r.reach, largest)}'}</td>"
        f"<td>{r.doc_count}</td>"
        "</tr>"
        for r in rows
    )
    out.append(
        '<table class="rank"><thead><tr>'
        "<th>Theme</th><th>Bucket</th><th>Why</th><th>Reach</th>"
        "<th>Source documents</th>"
        "</tr></thead><tbody>" + body + "</tbody></table>"
    )

    unranked = sum(1 for r in rows if r.bucket == "unranked")
    if unranked:
        out.append(_p(
            f"{unranked} of these state neither a blocker nor a preference — "
            f"they describe the world, so MoSCoW does not bucket them."
        ))
    if len(findings) > len(rows):
        out.append(_p(
            f"The other {len(findings) - len(rows)} are listed below, not "
            f"bucketed out here — a table this long stops being one."
        ))
    return "".join(out)


def _funnel_section(considered: int, kept: int) -> str:
    """How many themes were found, and how many bear on the goal.

    THE FIRST THING A FILTERED LIST OWES ITS READER. The reference memo
    opens "Twelve initiatives can move revenue. Two are high confidence. One is
    the recommendation." — the funnel is stated before anything is shown, so
    the reader knows the list below is a selection rather than the whole
    picture. A filtered list that does not say it was filtered is the more
    confident-looking of the two, and the less honest.

    Silent when nothing was set aside: a funnel with one step is not a funnel,
    and a line saying "329 of 329" is noise.
    """
    if kept >= considered or considered <= 0:
        return ""
    aside = considered - kept
    return "".join([
        "<h3>What bears on this goal</h3>",
        _p(
            f"<strong>We found {considered:,} themes. {kept:,} of them bear "
            f"on this goal.</strong> "
            + (
                "The other one is listed with the reason we set it aside"
                if aside == 1 else
                f"The other {aside:,} are listed with the reason we set each "
                f"aside"
            )
            + " — set aside is not gone, and one that does not answer this "
              "goal may be the answer to a different one."
        ),
    ])


def _relevance_coverage_section(relevance_judged: dict) -> str:
    """The disclosure half of the relevance-gate performance fix.
    `relevance.py` promises "the renderer says how many were not evaluated" —
    the relevance gate has a hard budget
    (`MAX_JUDGED`, and a wall-clock deadline that can stop it earlier still),
    and neither this document nor the panel ever said so. That is a silent cap
    on top of the funnel `_funnel_section` already discloses, and it needs its
    own sentence: `_funnel_section` is silent whenever nothing was SET ASIDE,
    which says nothing about whether everything was even LOOKED AT.

    Separate from `_funnel_section` rather than folded into it: this fires
    whenever the gate stopped short, even on a run where every judged finding
    came back `true` and the appendix below is empty — a reader still needs to
    know the "found" count above includes themes the gate never got to.
    """
    judged = relevance_judged.get("judged")
    considered = relevance_judged.get("considered")
    if (
        not isinstance(judged, int) or not isinstance(considered, int)
        or considered <= 0 or judged >= considered
    ):
        return ""
    remaining = considered - judged
    return _p(
        f"Of the {considered} themes found, this run evaluated {judged} for "
        f"relevance to your goal before its time or cost budget ran out. The "
        f"other {remaining} are still counted as found and are kept in the "
        f"list — unjudged, not irrelevant."
    )


def _worth(finding: dict) -> str:
    """What a set-aside theme is worth, in the memo's own vocabulary.

    §06's REVENUE THIS CYCLE column never prints a misleading zero: it prints
    `Unsized`, `Not attributable`, `Unquantified`, `Direction unknown`. This
    corpus needs exactly two of those words, and which one applies is a fact
    about the finding rather than a judgement about it.
    """
    value = finding.get("impact_value")
    if value is None:
        # I3: not measured is not nothing, and the whole reason this column can
        # exist without money in the corpus.
        return "Unsized"
    currency = (finding.get("currency") or "accounts").strip()
    return f"{value:g} {currency}"


def _set_aside_section(pairs: list) -> str:
    """The themes that did not bear on the goal, and why — the memo's appendix.

    NOT A DELETION. Every one of these was found, corroborated and ranked
    exactly like the findings above; what changed is that it does not answer
    the question that was asked. Printing the reason beside each is what makes
    the filter arguable: a reader who disagrees can see precisely what was
    judged and say so.
    """
    if not pairs:
        return ""
    out = [
        f"<h3>Considered and set aside for this goal ({len(pairs)})</h3>",
        _p(
            "We found and ranked these exactly like the findings above. They "
            "are here because they do not bear on the goal as you defined it, "
            "not because the evidence behind them was weak."
        ),
    ]
    # CAPPED FOR THE SAME REASON THE TAIL ABOVE IS. A 95-row table of what was
    # NOT the answer was named, specifically, as one of the things that made
    # the document unreadable. The count in the heading stays the TRUE total,
    # so shortening the table cannot hide how much was set aside.
    shown = pairs[:MAX_SET_ASIDE_ROWS]
    # ── THE MEMO'S FOUR COLUMNS. ───────────────────────────────────────
    #
    # §06 is a TABLE: INITIATIVE | WHAT IT IS | REVENUE THIS CYCLE | WHY NOT
    # PRIORITISED, and its third column is the most instructive thing in the
    # document — `~$0`, `Not attributable`, `Unquantified`, `Direction
    # unknown`, `Unsized`. That is I3's discipline expressed as a vocabulary,
    # and it is why this column can exist at all on a corpus with no money in
    # it: the honest answer to "what is this worth" is usually a word.
    #
    # A BULLET LIST CANNOT CARRY FOUR COLUMNS. It carried two — the label and
    # the reason — so what the theme actually SAID and what it was worth were
    # dropped, which are the two a reader needs to disagree with the verdict.
    rows = "".join(
        "<tr>"
        f"<td><strong>{_esc_clipped((f.get('label') or '').strip() or _statement_text(f), MAX_PARAM_NAME_CHARS)}</strong></td>"
        f"<td>{_esc_clipped((f.get('example') or '').strip() or _statement_text(f), MAX_PARAM_BASIS_CHARS)}</td>"
        f"<td>{_esc(_worth(f))}</td>"
        f"<td>{_esc_clipped(reason, MAX_PARAM_BASIS_CHARS)}</td>"
        "</tr>"
        for f, reason in shown
    )
    out.append(
        '<table class="aside"><thead><tr>'
        "<th>Theme</th><th>What it is</th><th>Worth this cycle</th>"
        "<th>Why it was set aside</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )
    if len(pairs) > len(shown):
        # NO SILENT CAPS. A list that stops without saying so reads as the
        # whole set.
        out.append(_p(
            f"and {len(pairs) - len(shown)} more set aside, all on the run."
        ))
    return "".join(out)


def _placement_note_section(findings: list[dict]) -> str:
    """Why the first finding is first — METHOD, so it reads with the method.

    THIS USED TO BE "THE SHORT VERSION", AT THE TOP. It opened with the top
    finding's own stored statement set as a deck, then explained what its
    position does and does not mean. The statement is now the write-up's own
    heading two sections above, so the deck was the same sentence twice; what
    remains is the part that was never said anywhere else, which is what the
    ordering is a claim about.

    EVERY BRANCH SURVIVES THE MOVE, and getting them right is not cosmetic:
    `_rank` keys on (conflict, claim-type bucket, reach, confidence), so
    "it is the largest" is true on exactly one of them. See the branch
    comments below, each of which records a wrong sentence that reached a
    rendered report.

    THE UNSIZED COUNT IS NO LONGER NAMED HERE. `_findings_section` states it
    unconditionally now, two sections above and beside the findings it
    qualifies; repeating the number in the appendix would be the duplication
    this pass exists to remove. The CAVEAT that a missing size is not a small
    one still travels with every branch that needs it.
    """
    if not findings:
        return ""
    out = ["<h3>Why the first finding is first</h3>"]
    top = findings[0]
    band = (top.get("confidence_band") or "").strip()
    claims = len(_as_list(top.get("claim_ids")))

    # "LARGEST" IS A CLAIM, AND IT HAS TO BE EARNED.
    #
    # `_rank` (pipeline.py) keys on THREE terms, and the first version of this
    # fix only knew about two of them:
    #
    #     (0 if conflict else 1, -(value if value is not None else -1),
    #      -confidence)
    #
    # An authoritative CONFLICT is placed first regardless of size — the
    # dominant term — and the size term is constant when nothing could be
    # sized, leaving a strict confidence sort. So there are three different
    # true sentences here, and exactly one of them is "it is the largest".
    #
    # Getting this wrong is not cosmetic: the first attempt gated on the TOP
    # finding's own value while asserting something about ALL of them, so a
    # conflict-led run said "nothing here could be sized" with 412 accounts
    # rendered on row two, and an unsized-elsewhere run called a 3-account
    # finding "the largest" above a 900-account one. Both reproduced from
    # rendered HTML in review.
    #
    # Same rule as I3 one level up: I3 stops a missing SIZE rendering as zero,
    # this stops a missing ORDERING rendering as a ranking.
    anything_sized = any(f.get("impact_value") is not None for f in findings)
    unsized = sum(1 for f in findings if f.get("impact_value") is None)
    top_is_conflict = (top.get("adjudication") or "") == "conflict"
    lead = (
        f", at {_esc(band)} confidence" if band else ""
    ) + (
        f", resting on {claims} claim{'' if claims == 1 else 's'}"
        if claims else ""
    )

    if top_is_conflict:
        # Placed first BY RULE, so size never entered into it either way.
        #
        # NOT "first regardless of size": with two or more conflicts `_rank`
        # orders them among THEMSELVES by size, so the one that surfaces here
        # is the largest conflict and size did decide which. What is true in
        # every case is the weaker, exact claim — a conflict outranks
        # everything that is not one.
        tail = (
            "It is placed first because two sources that may both speak "
            "contradict each other" + lead
            + ". That placement is a rule, not a measurement — a disagreement "
              "is placed above every finding that is not one, so read it as "
              "the disagreement most worth resolving rather than as the "
              "biggest thing here."
        )
    elif top.get("impact_value") is not None and not unsized:
        tail = (
            f"It is the largest thing this reading found: {_esc(_reach(top))}"
            + lead
            + ". Largest by how much of your book it touches — not by how much "
              "it would move the metric, which this reading cannot compute."
        )
    elif top.get("impact_value") is not None:
        # SIZED, BUT NOT AGAINST EVERYTHING. Unsized findings sort last, so
        # this row is the largest of those that HAVE a size — which is not the
        # same sentence as "the largest thing this reading found", and the
        # difference is the whole of I3. An unsized finding is not a small one;
        # it is one whose size is unknown, and an unknown can be bigger.
        tail = (
            "It is the largest of the ones we could size: "
            f"{_esc(_reach(top))}" + lead
            + ". Others could not be sized at all, and a missing size is not "
              "a small one — so this is the largest known size, not "
              "necessarily the largest thing here."
        )
    elif anything_sized:
        # Unsized itself, but sized findings exist below it — so the order is a
        # real ordering and this row simply has no size of its own.
        tail = (
            "It is listed first" + lead
            + ". It could not be sized, though others below it could — a "
              "missing size is not a small one, so do not read its position as "
              "a measurement of it."
        )
    else:
        # Nothing anywhere could be sized, so the size term is constant — but
        # the sort is NOT "strictly confidence-descending", which is what this
        # comment used to say and what the sentence below used to assert.
        # `_rank`'s key is (conflict, claim-type bucket, reach, confidence):
        # with reach constant, what orders the list is the BUCKET, and
        # confidence only breaks ties inside one. Describing it as ordered by
        # confidence told a reader the order says how SURE each finding is,
        # when what it mostly says is what KIND of claim each one is — the
        # opposite emphasis, on the sentence that introduces the whole list.
        tail = (
            "It is listed first" + lead
            + ". Nothing in this reading could be sized, so what orders these "
              "is the kind of claim behind each one — what blocks an account "
              "above what an account only asks for — with how sure we are "
              "breaking ties inside a kind."
        )
    out.append(_p(tail))
    return "".join(out)


#: A single statement's rendered ceiling. THIS is what makes the block budget a
#: bound rather than a measurement.
#:
#: `cluster.label_for` caps the embedding path at 90 chars — but that is the
#: FALLBACK path. The primary one takes `kg_entity.canonical_label` verbatim
#: (`kg_themes.py`), a bare text column with no truncation anywhere downstream,
#: so a statement is unbounded in code even though the largest observed is 126.
#: Truncating here rather than at ingest because this is a RENDERING budget:
#: the graph is entitled to a long label, the document is not entitled to
#: unlimited space for it.
MAX_STATEMENT_CHARS = 400

#: THE ONE FIELD ALLOWED TO BE AN ARGUMENT RATHER THAN A STATEMENT: the
#: synthesized recommendation's `because`, and only that.
#:
#: There is exactly ONE of these per report — `_synthesized_recommendation_
#: section` renders the single top-line recommendation — so raising its
#: ceiling costs the block budget one paragraph, not one per finding. And it
#: is the paragraph the whole document exists to deliver: on a real run the
#: model wrote 1,508 characters setting out TWO interlocking blockers, and
#: `MAX_STATEMENT_CHARS` cut it inside the first, taking with it the second —
#: which is to say the report deleted the reason its own recommendation
#: exists, and did so silently.
#:
#: 1,600 rather than "unbounded": this is still a stored, model-authored
#: string with no upstream cap, and the shed ladder is a fallback for a
#: document that is too big, not a licence to have no ceiling at all. Every
#: OTHER model-authored field stays at `MAX_STATEMENT_CHARS`, including the
#: per-finding `because`, of which there can be one per full block.
MAX_ARGUMENT_CHARS = 1_600

#: THE CAVEAT THAT TRAVELS WITH EVERY KILL SIGNAL. A kill signal here is
#: derived from a corpus of what people said — there is no metric series
#: behind it and nothing watches it — so the line is a belief a reader can go
#: and disprove, never a measured trigger. It renders inline, in the same
#: paragraph as the signal itself, so it cannot be skimmed past the way a
#: footnote can. The live panel
#: (`web/app/components/shared/GoalAnalysisReport.tsx`) carries the same
#: sentence; the two renderers share no code, so they are kept in step by
#: hand.
KILL_SIGNAL_CAVEAT = (
    "This is a falsifiable belief, not a measured threshold — this analysis "
    "reads what people said, not a metric series, so nothing is watching for "
    "this on your behalf. Someone has to go and look."
)


#: An inline claim-id reference in model-authored prose: `[<uuid>]`, usually
#: several in a row. The deep pass is asked to cite, and it cites INLINE as
#: well as in the `changes[]` structure the citation gate reads — so a
#: sentence reaches the page as "…on procurement grounds alone
#: [16e40304-1113-5253-b624-f300317b5fdd][189ac9b0-0aec-52b0-8069-a16f542c19bc].
#: Second, …". Nothing stripped them; the only reason a reader had not seen
#: one is that truncation happened to cut before they appeared, which is not a
#: guarantee of anything.
#:
#: MATCHED ON THE UUID SHAPE, NOT ON BRACKETS. Stripping every `[...]` would
#: eat legitimate prose — a bracketed aside, a `[sic]`, a quoted source that
#: uses brackets — so this only removes a bracket group whose entire contents
#: is a claim id. Leading whitespace goes with it, so removing a mid-sentence
#: citation does not leave the space that preceded it stranded before a
#: full stop.
#:
#: THE CITATIONS ARE NOT LOST. Every accepted `change` renders its claim id's
#: own assertion text beside it (`_SOURCE_LEAD_IN`), which is the provenance a
#: reader can actually use. The inline brackets are leakage from the model's
#: scratchpad, not a second, better citation.
_CLAIM_REF = re.compile(
    r"\s*\[[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\]"
)

#: THE PUNCTUATION THE MODEL WRAPPED AROUND THE IDS, which removing the ids
#: leaves behind. `_CLAIM_REF` takes the bracket group and the space before
#: it and nothing else, so prose written as `…before any purchase can proceed
#: ([id], [id])` rendered `…before any purchase can proceed (,)` — three
#: characters of the model's scratchpad punctuation, sitting in a sentence a
#: client reads. Seen live: `(,)`, `()` after a single citation, and a space
#: stranded before a full stop.
#:
#: MATCHED ON EMPTINESS, NOT ON POSITION. A group is only removed when what
#: is left inside it is separators and whitespace — never when a word
#: survives — so `(a 10-day trial [id])` keeps its parentheses and its
#: content, and only a parenthesis that was holding nothing BUT citations
#: goes. That is the difference between cleaning up after the stripper and
#: deleting an author's aside that happened to sit next to a citation.
_EMPTY_REF_GROUP = re.compile(
    r"\s*\(\s*[,;:&/·–—-]*\s*\)"
    r"|\s*\[\s*[,;:&/·–—-]*\s*\]"
)

#: A separator left with nothing on one side of it. `A [id], B [id]` is fine
#: — both ids go and the comma still joins two things — but a citation that
#: was itself a list item leaves `A, , B`, `(, B)` or `(A ,)`.
_DOUBLED_SEP = re.compile(r"(?:\s*,\s*){2,}")
_OPENING_SEP = re.compile(r"([(\[])\s*[,;]\s*")
_CLOSING_SEP = re.compile(r"\s*[,;]\s*([)\]])")

#: Whitespace stranded before punctuation that never takes a space before it.
#: Excludes the ellipsis, which `cluster.example_for` appends deliberately.
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?)\]])")
_RUN_OF_SPACES = re.compile(r"[ \t]{2,}")


def strip_claim_refs(text: str) -> str:
    """Model-authored prose with its inline `[claim-id]` references removed,
    and with the punctuation that was wrapping them removed too.

    Applied at the render boundary rather than in `recommend.py`, for the same
    reason the lint runs there: the stored recommendation stays exactly what
    the model returned, and every renderer strips independently, so neither
    can be the one that forgot.

    TWO PASSES, DELIBERATELY. Matching the ids together with their wrapper in
    one expression would need every arrangement the model might write —
    `([id])`, `([id], [id])`, `[[id]; [id]]`, `([id] and [id])` — and the one
    arrangement not enumerated is the one that ships a `(,)`. Removing the
    ids first and then removing what is provably empty afterwards has no such
    list to keep complete.
    """
    out = _CLAIM_REF.sub("", text or "")
    out = _EMPTY_REF_GROUP.sub("", out)
    out = _DOUBLED_SEP.sub(", ", out)
    out = _OPENING_SEP.sub(r"\1", out)
    out = _CLOSING_SEP.sub(r"\1", out)
    out = _SPACE_BEFORE_PUNCT.sub(r"\1", out)
    return _RUN_OF_SPACES.sub(" ", out).strip()


#: Sentence-final punctuation. A string already ending in one of these does
#: not get another appended.
_TERMINALS = ".!?…"


def _stop(text: str) -> str:
    """`text` with exactly one closing full stop.

    Several sentences here are built as "lead-in {value}." where `value` is
    free text that MAY already be a complete sentence. `cannot_answer`'s
    `because` is one — for the framework gap it carries
    `framework_choice.reason`, which ends in its own full stop — so the page
    rendered "…what it only asks for..". Appending unconditionally is the bug;
    never appending leaves the branches whose value has no terminator hanging.
    """
    t = (text or "").rstrip()
    if not t:
        return t
    return t if t[-1] in _TERMINALS else t + "."


def _upper_first(text: str) -> str:
    """`text` with its first letter capitalised, and nothing else touched.

    NOT `.capitalize()`, which lowercases the remainder and would turn "RICE
    cannot size this" into "Rice cannot size this". Several of these strings
    are written as CLAUSES because they also render mid-sentence elsewhere;
    where one follows a bold full stop it has to start a sentence, and
    "How many got a full recommendation. you named a target of…" is the
    result of leaving it alone.
    """
    t = (text or "").lstrip()
    return t[:1].upper() + t[1:] if t else t


def _clip(text: str, limit: int) -> str:
    """`text`, bounded, cut on a word boundary, with claim-id refs stripped.

    STRIPPED BEFORE CLIPPING, so a citation the reader will never see does not
    spend forty characters of a four-hundred-character budget.
    """
    t = " ".join(strip_claim_refs(text or "").split())
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


def _esc_clipped(value: Any, limit: int) -> str:
    """`limit` characters of text, escaped — clipped on the RAW string.

    CLIPPING ESCAPED OUTPUT IS THE BUG THIS REPLACES. The previous version
    clipped raw, escaped, and then — because escaping expands (`M&A` ->
    `M&amp;A`) — cut a second time to hold the ESCAPED length under `limit`.
    That second cut had none of the first one's care: it landed mid-word and
    it discarded the ellipsis `_clip` had already added, so the flagship
    paragraph of a real report ended "…architecturally tied to one vend" and
    ran straight into the next heading with no space and no mark that anything
    had been removed. A reader cannot tell that from a sentence the model
    simply ended badly.

    So the bound is on the raw string only, and the escaped result may exceed
    `limit` — by up to 6x in a pathological input of pure quote characters.
    That is deliberate and consistent with the rest of this module: see
    `_SHED_LADDER`, which already records that a truthful static bound "is not
    worth having" and that the size guarantee here is EMPIRICAL — render,
    measure, shed. A budget defended by mangling the one sentence the report
    exists to deliver is not a budget worth defending.
    """
    return _esc(_clip(value if isinstance(value, str) else str(value or ""), limit))


#: NOTHING IN A GOAL ANALYSIS IS A QUOTATION, AND NOTHING MAY LOOK LIKE ONE.
#: `graph.extractor` validates a verbatim quote against the transcript, uses
#: it to gate the write, and then discards it by design — no raw source text
#: is ever stored. So every `content`, every theme label derived from one and
#: every `example` is a PARAPHRASE, and several are additionally cut at a
#: causal connective and ellipsised (`cluster.example_for`). Four render
#: sites used to set that text in curly quotes, which told a reader that a
#: named company said those words in that order. None did.
#:
#: The evidence still has to READ as evidence, so the visual separation is
#: kept — a blockquote, an italic provenance line — and only the false claim
#: to be verbatim is removed, replaced by a lead-in that says what the text
#: actually is. These constants exist so the wording cannot drift between
#: the four places it appears.
#:
#: How a summarised example is ATTRIBUTED, under the text rather than in
#: front of it.
#:
#: THE HONESTY IS KEPT AND MOVED OUT OF THE WAY. As a lead-in this sat at the
#: head of the blockquote, so a reader met a clause about our extraction
#: pipeline before meeting the evidence — one of the specific things that made
#: the document read as an audit trail. It is not dropped, because what
#: follows genuinely is a paraphrase and presenting it as a quotation was a
#: correctness bug. It renders as a quiet attribution line beneath the quote
#: (`em.src`, the same treatment provenance already gets), and the section
#: states the same fact once at its head — visual treatment plus one stated
#: caveat, instead of a clause inside every line.
_EXAMPLE_LEAD_IN = "Summarised from one source — not a quotation."

#: The same lead-in mid-sentence, where the statement builder puts it.
#: MUST STAY EQUAL TO `pipeline.EXAMPLE_LEAD_IN` — `_findings_heading` cuts a
#: statement here, so a heading and a card would show the same words twice if
#: the two drifted. Not imported, because `report` renders stored dicts and
#: has never needed the pipeline module to do it.
_STATEMENT_EXAMPLE_LEAD_IN = "— summarising one source:"

#: How the claim a recommendation was drawn from is introduced, beside it.
#: "from" rather than any verb of speech, for the same reason: the text after
#: it is the claim's stored assertion, which is the extractor's summary of
#: the source and not the source.
#:
#: NO LEADING EM DASH. `em.src` is `display: block` — the line already sits
#: under what it qualifies, in a smaller, quieter face, so the dash was
#: punctuation joining it to a sentence it is not part of.
_SOURCE_LEAD_IN = "Summarised from the source:"

#: The theme lead-in `pipeline._statement_parts` writes, and the quoted shape
#: it used to write. Both are matched below.
_THEME_LEAD_IN = "a reported theme:"

#: THE OLD QUOTED SHAPE, AS STORED. Rows written before the statement builder
#: stopped using quotation marks carry
#: `N claims across M accounts concern “Topic” — for example, “Example”.`
#: Those rows are not rewritten in the database — see `_restate_statement` —
#: so this is how the renderer recognises them.
_LEGACY_THEME = re.compile(r"\b(concerns?) “([^“”]*)”")
_LEGACY_EXAMPLE = re.compile(
    r"\s*— for example,\s*“([^“”]*)”\.?\s*$"
)


def _restate_statement(statement: str) -> str:
    """A stored statement with any quotation marks around reported text
    replaced by an attribution.

    WHY THE RENDERER AND NOT A BACKFILL. The extractor validates a verbatim
    quote against the transcript, uses it to gate the write and then discards
    it by design, so every stored `content` — and therefore every theme label
    and every example built from one — is a PARAPHRASE. Presenting a
    paraphrase in curly quotes tells a reader that a named company said those
    words; nobody did. The builder no longer writes that shape
    (`pipeline._statement_parts`), but hundreds of already-stored rows do, and
    a run rendered from them would keep making the claim. Rewriting the rows
    would edit the record of what a past run decided, which is a worse trade
    than rewriting the presentation of it.

    STRUCTURAL, NOT HEURISTIC. The old shape was generated by this codebase,
    not by a model, so both halves of it are matched exactly where the builder
    put them — after "concern"/"concerns" for the theme, and as the trailing
    "— for example, …" clause. A quotation anywhere else in a statement is not
    this engine's and is left alone.
    """
    text = (statement or "").strip()
    if not text:
        return text
    text = _LEGACY_THEME.sub(rf"\1 {_THEME_LEAD_IN} \2", text, count=1)
    m = _LEGACY_EXAMPLE.search(text)
    if m:
        example = m.group(1).strip()
        head = text[: m.start()].rstrip().rstrip(".")
        text = f"{head} {_STATEMENT_EXAMPLE_LEAD_IN} {example}"
        if not text.endswith((".", "!", "?", "…")):
            text += "."
    return text


def _statement_text(finding: dict) -> str:
    """A finding's statement, bounded, cut on a word boundary."""
    return _clip(
        _restate_statement(finding.get("statement") or ""), MAX_STATEMENT_CHARS
    )


def _esc_statement(finding: dict) -> str:
    """The statement, escaped, with the BOUND ON THE ESCAPED length."""
    return _esc_clipped(
        _restate_statement(finding.get("statement") or ""), MAX_STATEMENT_CHARS
    )



def _assumption_key(finding: dict) -> tuple:
    """The assumed parameters of one finding, as a comparable value."""
    return tuple(sorted(
        ((a.get("name") or "").strip(), (a.get("basis") or "").strip())
        for a in _as_list(finding.get("assumed_params"))
        if isinstance(a, dict)
    ))


def _shared_assumptions(findings: list[dict]) -> tuple[tuple, int]:
    """The assumption every finding that MAKES one makes, and how many do.

    I8 requires an assumed parameter be disclosed where the number is read.
    It does not require it be disclosed 279 times. On a corpus with no revenue
    data connected every finding carries the identical line —

        value_per_account: no revenue data connected; accounts weighted equally

    — so a real report repeated that sentence on all 279 findings. That is not
    disclosure, it is the noise the reader has to look past to find the
    disclosures that ARE per-finding, and it is a large part of what "lots of
    irrelevant information" meant.

    Same rule as `_shared` above — a single distinct value across MORE THAN ONE
    finding is a statement about the corpus, and the moment two findings assume
    different things they both go back on their own rows.

    FINDINGS WITH NO ASSUMPTION ARE NOT COUNTED AGAINST THE MATCH, and that is
    the whole correction. The first version asked whether EVERY finding carried
    the identical set, which sounded right and never fired on a real run: a live
    report had 326 findings of which 30 were sized and carried
    `value_per_account`, and 296 were unsized and carried nothing at all. An
    unsized finding has no size to qualify, so it has no assumption — that is
    not disagreement, and treating it as disagreement left the line repeated 30
    times on the page it was written to de-duplicate.

    The COUNT comes back with the key because the hoisted sentence has to say
    how many findings it speaks for. "Every finding below" is a false sentence
    when 296 of them assume nothing.
    """
    with_any = [f for f in findings if _assumption_key(f)]
    if len(with_any) < 2:
        return (), 0
    keys = {_assumption_key(f) for f in with_any}
    if len(keys) != 1:
        return (), 0
    return keys.pop(), len(with_any)


def _finding_money_estimate(reach: float, account_value: Any) -> Optional[str]:
    """A per-finding money clause, in the memo's TOP-LINE wording verbatim.

    `_stat_strip`'s "Reach × your estimate" cell and `_decision_section`'s
    money clause already established the exact convention for this kind of
    number — labelled as the reader's own estimate every time it appears,
    never bare. This reuses that phrasing rather than writing a third one, so
    a reader never meets two different tones for the same kind of number in
    one document.

    `None` when `account_value` is absent, zero, or not numeric — the same
    guard shape `_stat_strip`/`_decision_section` use — so a caller can skip
    cleanly rather than repeat the guard.
    """
    if not isinstance(account_value, (int, float)) or account_value <= 0:
        return None
    value = float(account_value)
    return (
        f"about {reach * value:,.0f} on your own figure of {value:,.0f} per "
        f"account, which is an estimate you gave rather than something "
        f"measured"
    )


#: How many source nodes the convergence figure draws. Beyond this the
#: remainder is COUNTED in the sentence beneath it, the way every other cap in
#: this file states its own overflow — a diagram that silently stops at four
#: would understate corroboration, which is the one direction this figure must
#: never err in.
MAX_CONVERGENCE_NODES = 4

#: The figure only exists above this many distinct source types.
#:
#: A CONVERGENCE DIAGRAM WITH ONE NODE IS NOT ONE. It draws a single box with
#: a single line into an outcome and reads as triangulation, which is a claim
#: about independent agreement that a single-source finding has not earned —
#: worse than showing nothing, because the picture is more persuasive than the
#: sentence it replaces. On a corpus that is overwhelmingly one source type
#: this figure simply never appears, and that is the correct behaviour rather
#: than a gap to fill.
MIN_CONVERGENCE_SOURCES = 2

#: SVG text does not wrap, so every string in the figure is cut to fit its box
#: rather than trusted to.
#: Sized against the boxes they sit in at the figure's own scale: a 200-unit
#: node box holds about 26 characters of the 12px sans, and a 282-unit outcome
#: box about 32 of the 13px mono. Erring short, because an overflowing label
#: in SVG does not clip — it runs out over whatever is beside it.
MAX_NODE_LABEL_CHARS = 26
MAX_OUTCOME_LINE_CHARS = 32


def _svg_lines(text: str, limit: int, max_lines: int) -> list[str]:
    """`text` broken on word boundaries into at most `max_lines` of `limit`.

    SVG has no line box: a `<text>` runs straight out of its rect and over
    whatever is beside it. Everything drawn here is therefore wrapped by this
    function or clipped by `_clip`, never left to the renderer.
    """
    words = (text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
        if len(lines) == max_lines:
            break
        current = word
    if current and len(lines) < max_lines:
        lines.append(current)
    if not lines:
        return []
    # The cut is stated, not silent — the same rule `_clip` follows.
    consumed = len(" ".join(lines))
    if consumed < len(" ".join(words)):
        lines[-1] = _clip(lines[-1] + " …", limit + 2)
    return lines


def _convergence_figure(finding: dict) -> str:
    """Which source types independently carry this finding, drawn and stated.

    THE COMPONENT IS NOT INVENTED HERE. It follows the convergence diagram in
    `backend/skills/evidence-brief/references/component-reference.html` —
    source boxes on the left, curved paths converging, one outcome box on the
    right, a caption — using the same token names the evidence brief's
    stylesheet defines (`--hair`, `--grid`, `--opp`, `--opp-soft`) and the same
    `.blabel` / `.vlabel` text classes.

    EVERY SLOT IS SOMETHING THE ENGINE ALREADY COMPUTED. The nodes are the
    distinct `source_type`s behind this finding's claims and how many claims
    each contributed; the outcome is the finding's own recommended action, or
    its own label when it has no recommendation; the caption counts how many
    of the converging types are authoritative. No extraction, no model, no
    narration (I2).

    THERE IS NO FORECAST LINE. The reference component ends its outcome box
    with a supporting line, and the example filling it reads "→ 3-5 Tier 1
    design-partner closes" — a projection of an outcome. This corpus cannot
    produce one and the engine is built not to try, so the line under the
    outcome box carries what the finding actually measured: its reach, or its
    claim count when nothing could be sized. An unsized finding says so rather
    than borrowing a number.

    BOTH PATHS KEEP THE INFORMATION. `custom_artifact_html.py` drops `svg`
    WITH ITS CHILDREN, so on the saved-document path the drawing disappears
    entirely. The sentence beneath it is therefore not a caption for the
    picture — it is the same fact in words, and it is what that path keeps.
    One string, written once, doing both jobs.
    """
    types = _as_dict(finding.get("source_types"))
    counted = sorted(
        ((str(k), int(v)) for k, v in types.items() if isinstance(v, (int, float))),
        key=lambda kv: (-kv[1], kv[0]),
    )
    if len(counted) < MIN_CONVERGENCE_SOURCES:
        # NOT SILENCE. A single-source finding has a composition too, and it
        # is the more important one to state: a reader who sees no figure
        # should be told the reason is that there was nothing to converge.
        if counted:
            name, n = counted[0]
            return _p(
                f"<em>Rests on {n} claim{'' if n == 1 else 's'} from one "
                f"source type ({_human_source(name)}) — nothing here is "
                f"independently corroborated.</em>"
            )
        return ""

    shown = counted[:MAX_CONVERGENCE_NODES]
    beyond = len(counted) - len(shown)
    authoritative = finding.get("authoritative_source_types")
    authoritative = (
        int(authoritative) if isinstance(authoritative, (int, float)) else None
    )

    # THE OUTCOME IS WHAT THIS FINDING ASKS FOR, or what it is. Never a
    # synthesis of the two, and never a sentence written for the box.
    outcome = (
        (_as_dict(finding.get("deep_recommendation")).get("action") or "").strip()
        or (_as_dict(finding.get("recommendation")).get("action") or "").strip()
        or (finding.get("label") or "").strip()
        or _statement_text(finding)
    )

    # ── Geometry. Every drawn shape carries an explicit fill, and the viewBox
    #    is sized to the outermost label rather than to the boxes, so nothing
    #    is clipped at the edge when the sheet scales the figure down.
    node_h, gap = 46, 30
    pitch = node_h + gap
    top = 14
    height = top * 2 + len(shown) * pitch - gap
    mid = height / 2
    box_x, box_w = 8, 200
    out_x, out_w = 430, 282
    out_h = 76
    out_y = mid - out_h / 2

    nodes: list[str] = []
    paths: list[str] = []
    for i, (name, n) in enumerate(shown):
        y = top + i * pitch
        cy = y + node_h / 2
        label = _esc(_clip(_human_source(name).strip() or "source", MAX_NODE_LABEL_CHARS))
        nodes.append(
            f'<rect x="{box_x}" y="{y:g}" width="{box_w}" height="{node_h}" '
            f'rx="3" fill="#ffffff" stroke="var(--hair)"/>'
            f'<text x="{box_x + box_w / 2:g}" y="{cy - 3:g}" text-anchor="middle" '
            f'class="blabel" fill="var(--ink)">{label}</text>'
            f'<text x="{box_x + box_w / 2:g}" y="{cy + 13:g}" text-anchor="middle" '
            f'class="blabel" fill="var(--sub)">{n} claim{"" if n == 1 else "s"}</text>'
        )
        # A straight line when the node is already level with the outcome,
        # a curve otherwise — the reference's own two cases.
        if abs(cy - mid) < 0.5:
            paths.append(f'<path d="M{box_x + box_w} {cy:g} L{out_x} {mid:g}" fill="none"/>')
        else:
            paths.append(
                f'<path d="M{box_x + box_w} {cy:g} '
                f'C {box_x + box_w + 90} {cy:g}, {out_x - 110} {mid:g}, '
                f'{out_x} {mid:g}" fill="none"/>'
            )

    out_lines = _svg_lines(outcome, MAX_OUTCOME_LINE_CHARS, 2)
    line_y = out_y + (26 if len(out_lines) > 1 else 34)
    outcome_text = "".join(
        f'<text x="{out_x + out_w / 2:g}" y="{line_y + i * 17:g}" '
        f'text-anchor="middle" class="vlabel" fill="var(--opp)">'
        f"{_esc(line)}</text>"
        for i, line in enumerate(out_lines)
    )
    # WHAT THIS MEASURED — NOT WHAT IT WOULD ACHIEVE. See the docstring.
    if finding.get("impact_value") is not None:
        support = _reach(finding)
    else:
        claims = len(_as_list(finding.get("claim_ids")))
        support = (
            f"{claims} claim{'' if claims == 1 else 's'}; size not measured"
            if claims else "size not measured"
        )

    svg = (
        f'<svg viewBox="0 0 720 {height:g}" role="img" '
        f'aria-label="Diagram: {len(shown)} independent source types '
        f'converging on one finding">'
        f'<g>{"".join(nodes)}</g>'
        f'<g stroke="var(--grid)" stroke-width="2" fill="none">{"".join(paths)}</g>'
        f'<rect x="{out_x}" y="{out_y:g}" width="{out_w}" height="{out_h}" rx="3" '
        f'fill="var(--opp-soft)" stroke="var(--opp)"/>'
        f"{outcome_text}"
        f'<text x="{out_x + out_w / 2:g}" y="{out_y + out_h - 14:g}" '
        f'text-anchor="middle" class="blabel" fill="var(--sub)">'
        f"{_esc(support)}</text>"
        "</svg>"
    )

    # THE SENTENCE THE SANITIZED PATH KEEPS. Says the same thing the picture
    # does, in the same order, and states the count without characterising it.
    named = ", ".join(
        f"{_human_source(name)} ({n} claim{'' if n == 1 else 's'})"
        for name, n in shown
    )
    caption = (
        f"{len(counted)} source types independently carry this: {named}"
        + (f", and {beyond} more" if beyond else "")
        + "."
    )
    if authoritative is not None:
        caption += (
            f" {authoritative} of them "
            f"{'is a source' if authoritative == 1 else 'are sources'} the "
            f"registry treats as able to speak to this."
        )
    return f'<figure>{svg}<p class="convergence">{caption}</p></figure>'


def _finding_block(
    finding: dict, rank: int, *,
    shared_weakest: bool = False, shared_cap: bool = False,
    shared_assumptions: bool = False,
    option: int = 0,
    option_total: int = 0,
    data_gaps: Sequence[str] = (),
    one_topic: bool = False,
    one_topic_note: str = "",
    account_value: Any = None,
    defer_comparison: bool = True,
    defer_gaps: bool = False,
    show_call_note: bool = True,
) -> str:
    """One finding, written out so it can be read on its own.

    THE ORDER IS THE READER'S, NOT THE ENGINE'S. Asked what a write-up should
    contain he described a sequence — the problem it addresses, who asked,
    what it unlocks, what they said, what to change, what would kill it — and
    every one of those already existed in this document, scattered: the
    problem was a document-level section describing only the top finding, what
    it unlocks was two paragraphs in an evidence column, what would kill it
    was the last line of an argument column. This is the same material in that
    order, under headings that say which question is being answered.

    IT USED TO BE TWO COLUMNS AND IS NOW ONE. The columns split the material
    into "the argument" and "what it rests on", which cuts straight across the
    sequence above — the problem and what they said sat on one side, what to
    change and what would kill it on the other, and a reader following the
    order had to zig-zag. A sequence cannot be read in parallel.

    NO ACCOUNT IS NAMED, AND THAT IS NOT AN OMISSION. He asked for the
    accounts by name; a stored finding carries how MANY accounts a theme
    touches and never which — `pipeline` counts `accounts_named` and keeps the
    count alone. Naming them would mean inventing them, so the "who" section
    states the reach and names the SOURCE DOCUMENTS, which is what is actually
    on the record. The limit itself is stated once, at the head of the
    section, rather than on every card.
    """
    # THE THEME IS THE HEADING. It used to be the whole stored sentence — "30
    # claims across 11 accounts concern a reported theme: Sales Pipeline —
    # summarising one source: …" — so the one word a reader scans for sat
    # mid-clause, behind two numbers.
    #
    # FALLS BACK TO THE SENTENCE when there is no label, which is every run
    # stored before this shipped and every fixture that predates it.
    label = (finding.get("label") or "").strip()
    head = (
        _esc_clipped(label, MAX_STATEMENT_CHARS) if label
        else _esc_statement(finding)
    )
    out = [f'<h3 class="finding">{rank}. {head}</h3>']

    deep = _as_dict(finding.get("deep_recommendation"))
    rec = _as_dict(finding.get("recommendation"))
    deep_action = (deep.get("action") or "").strip()
    deep_because = (deep.get("because") or "").strip()
    action = (rec.get("action") or "").strip()
    because = (rec.get("because") or "").strip()
    has_deep = bool(deep_action and deep_because)

    # ── WHAT TO BUILD, RESTATED FROM THE SCREEN AT THE TOP. ────────────────
    #
    # The header is `data_gaps.option_header`'s decision so the wording cannot
    # drift, and it no longer contains the word "recommended": exactly one
    # thing in this document is the recommendation and it is the first screen.
    if has_deep:
        header = option_header(option, option_total, one_topic)
        out.append(
            f'<p class="action"><strong>{header}</strong> '
            f'{_esc_clipped(deep_action, MAX_STATEMENT_CHARS)}</p>'
        )
    elif action and because:
        out.append(
            f'<p class="action"><strong>Suggested.</strong> '
            f'{_esc_clipped(action, MAX_STATEMENT_CHARS)}</p>'
        )

    # ── 1. THE PROBLEM IT ADDRESSES. ───────────────────────────────────────
    #
    # Assembled, never authored (I2): the theme's own label, the kind of claim
    # behind it, its reach, and the finding's own `because` where one was
    # written. This is the paragraph that used to be a document-level section
    # sitting above everything and describing only the top finding.
    out.append("<h4>The problem</h4>")
    problem = _claim_sentence(finding)
    if problem:
        out.append(problem)
    reason = deep_because if has_deep else (because if action else "")
    if reason:
        out.append(_p(_esc_clipped(reason, MAX_STATEMENT_CHARS)))
    if not problem and not reason:
        out.append(_p(
            "We have no statement of the problem beyond the theme above — "
            "nothing we read framed it as a difficulty, only as a subject."
        ))
    if not has_deep and action and because and finding.get("deep_attempted"):
        # THE SHORTFALL, CONNECTED TO THE FINDING IT ACTUALLY DROPPED.
        # `deep_attempted` is only set on a finding that was IN the top N but
        # whose evidence did not clear the citation gate — never on one that
        # was simply ranked past N.
        out.append(_p(
            "<em>This was in line for a full write-up and did not get one "
            "this run — see “How many got a full write-up” under how this "
            "was produced. The suggestion above is the plain version, not a "
            "downgrade of a deeper one you are missing.</em>"
        ))

    # ── 2. WHO. ────────────────────────────────────────────────────────────
    out.append("<h4>Who this comes from</h4>")
    out.append(f'<p class="chips">{_option_chips(finding, full=True)}</p>')
    surfaced = [s for s in _as_list(finding.get("surfaced_by")) if s]
    if surfaced:
        # WHERE IT CAME FROM, beside the claim it supports — the difference
        # between an argument and an assertion. BOUNDED HERE, not only at
        # write time: `pipeline.MAX_NAMED_SOURCES` caps what new runs store,
        # but a document name is tenant text of any length and rows already on
        # disk predate every cap.
        shown = [_esc_clipped(x, MAX_SOURCE_NAME_CHARS)
                 for x in surfaced[:MAX_RENDERED_SOURCES]]
        extra = len(surfaced) - len(shown)
        out.append(_p(
            "<strong>Source documents</strong> " + " · ".join(shown)
            + (f" (+{extra} more)" if extra > 0 else "")
        ))
        # THE FLOOR, SAID IN WORDS — AND SAID ONCE PER DOCUMENT. A call
        # provider is extracted one pass per call, so a collapsed entry
        # carries how many CALLS it stands for, but anything ingested before
        # that changed was batched several calls to a document, so the number
        # can only be a lower bound. `show_call_note` is set by
        # `_findings_section` for the first block it applies to.
        if show_call_note and has_call_count(surfaced):
            out.append(_p(f"<em>{_esc(CALL_COUNT_FLOOR_NOTE)}</em>"))

    confidence = _as_dict(finding.get("confidence"))
    # The weakest leg is the ACTIONABLE half of a confidence score: it says
    # what to go and find out, which a band on its own never does. SUPPRESSED
    # WHEN IT IS THE SAME SENTENCE ON EVERY ROW — one fact about the corpus
    # printed 32 times reads as 32 separate judgements.
    if confidence.get("weakest_leg_reason") and not shared_weakest:
        out.append(_p(
            f"<strong>Weakest link.</strong> "
            f"{_esc(confidence['weakest_leg_reason'])}"
        ))
    if confidence.get("cap_reason") and not shared_cap:
        out.append(_p(_esc(confidence["cap_reason"])))
    # THE COMPOSITION OF THE EVIDENCE. Suppressed to a single sentence when
    # there is nothing to converge — see `_convergence_figure`.
    out.append(_convergence_figure(finding))

    # ── 3. WHAT IT UNLOCKS. ────────────────────────────────────────────────
    #
    # Empty on most findings, and it says so rather than being dropped — see
    # `_unlocks_block`.
    out.append("<h4>What it unlocks</h4>")
    out.append(_unlocks_block(finding, account_value))
    # I8: every assumed parameter is disclosed WHERE THE NUMBER IS READ, and
    # the number it qualifies is the one directly above. Bounded because
    # `name` and `basis` are tenant strings with no cap upstream, and a single
    # block once reached 41,745 characters here. Hoisted to the top of the
    # section when every finding says the same thing (`_shared_assumptions`).
    assumed = (
        [] if shared_assumptions
        else [a for a in _as_list(finding.get("assumed_params"))
              if isinstance(a, dict)]
    )
    if assumed:
        shown_params = assumed[:MAX_ASSUMED_PARAMS]
        out.append(_ul(
            f"<strong>{_esc_clipped(a.get('name'), MAX_PARAM_NAME_CHARS)}"
            f"</strong>: {_esc_clipped(a.get('basis'), MAX_PARAM_BASIS_CHARS)}"
            for a in shown_params
        ))
        if len(assumed) > len(shown_params):
            out.append(_p(
                f"and {len(assumed) - len(shown_params)} further assumed "
                f"parameters"
            ))

    # ── 4. WHAT THEY SAID. ─────────────────────────────────────────────────
    #
    # STILL A BLOCKQUOTE, AND STILL NOT IN QUOTATION MARKS. `graph.extractor`
    # validates a verbatim quote against the transcript, uses it to gate the
    # write and then discards it by design, so `example` is an extractor
    # paraphrase that `example_for` has additionally cut and may have
    # ellipsised. The indent sets it apart as evidence; the attribution line
    # under it says what it actually is. That line sits BELOW the text rather
    # than in front of it — the disclosure is kept and moved out of the way,
    # and the section states the same thing once at its head.
    example = (finding.get("example") or "").strip()
    if label and example:
        out.append("<h4>What they said</h4>")
        out.append(
            f"<blockquote>{_esc_clipped(example, MAX_STATEMENT_CHARS)}"
            f'<em class="src">{_EXAMPLE_LEAD_IN}</em></blockquote>'
        )

    # ── 5. WHAT TO CHANGE. ─────────────────────────────────────────────────
    if has_deep:
        changes = [
            c for c in _as_list(deep.get("changes"))
            if isinstance(c, dict) and (c.get("text") or "").strip()
        ]
        if changes:
            out.append("<h4>What to change</h4>")
            out.append(_ul(
                f"{_esc_clipped(c.get('text'), MAX_STATEMENT_CHARS)} "
                f'<em class="src">{_SOURCE_LEAD_IN} '
                f"{_esc_clipped(c.get('cited_claim'), MAX_PARAM_BASIS_CHARS)}"
                f"</em>"
                for c in changes[:MAX_DEEP_CHANGES]
            ))
        open_qs = [
            q for q in _as_list(deep.get("open_questions"))
            if isinstance(q, str) and q.strip()
        ]
        # SUPPRESSED ON THE FINDING THAT CARRIES THE GAPS LIST, because these
        # same questions are the middle of it (`data_gaps.data_gaps_for`).
        if open_qs and not data_gaps:
            out.append("<h4>Still open</h4>")
            out.append(_ul(
                _esc_clipped(q, MAX_STATEMENT_CHARS)
                for q in open_qs[:MAX_DEEP_OPEN_QUESTIONS]
            ))

        # ── 6. WHAT WOULD KILL IT. ─────────────────────────────────────────
        out.append("<h4>What would kill it</h4>")
        falsify = (deep.get("what_would_falsify") or "").strip()
        if falsify:
            # THE KILL SIGNAL, NAMED AS ONE — and carrying its own caveat in
            # the same breath. This corpus is what people said; it has no
            # metric series in it, so this can only be a belief someone can go
            # and falsify, never a threshold that trips on its own.
            out.append(_p(
                f"{_esc_clipped(falsify, MAX_STATEMENT_CHARS)} "
                f"<em>{KILL_SIGNAL_CAVEAT}</em>"
            ))
        else:
            out.append(_p(
                "We did not find anything that would tell you this is wrong. "
                "Read that as a gap in the evidence, not as a clean bill of "
                "health."
            ))
        # WHY THIS ONE OVER THE NEXT. It reads on the first screen now, above
        # both write-ups, because holding two options in your head to reach
        # the comparison two screens later is what made a reader think there
        # was no comparison at all. Still rendered here when a caller has not
        # deferred it.
        comparison = (deep.get("comparison") or "").strip()
        if comparison and not defer_comparison:
            out.append(_p(
                f"<strong>Why this over the next.</strong> "
                f"{_esc_clipped(comparison, MAX_STATEMENT_CHARS)}"
            ))
        if one_topic_note and not defer_comparison:
            out.append(_p(
                f"<strong>Why these are not two options.</strong> "
                f"{_esc(one_topic_note)}"
            ))
        # ── WHAT WE DO NOT KNOW ABOUT THE THING WE JUST RECOMMENDED. ───────
        #
        # Assembled deterministically from fields the engine already produced
        # (`data_gaps.data_gaps_for`) — no model call, nothing scored (I2).
        # GAPS, NOT ACTIONS. `defer_gaps` lifts them out to
        # `_before_you_spend_section`, where they qualify the recommendation
        # the whole document is making rather than one card.
        if data_gaps and not defer_gaps:
            out.append(
                f"<h4>{_esc(DATA_GAPS_HEADING)}</h4>"
            )
            out.append(_p(
                "Gaps in what is known about this one, not work to schedule."
            ))
            out.append(_ul(
                _esc_clipped(g, MAX_STATEMENT_CHARS) for g in data_gaps
            ))
    elif not (action and because):
        out.append(_p(
            "We wrote no recommendation for this one. What is above is what "
            "the evidence carries on its own."
        ))
    return "".join(x for x in out if x)


#: How many findings get a full block in the document.
#:
#: NOT a display preference — a hard constraint made visible. `custom_artifacts`
#: refuses a body over `MAX_BODY_CHARS` (400,000), and a real run rendered
#: 831 findings into **421,696 characters**: over the limit, so the document
#: could not be created at all and the route died on an unhandled
#: `BodyTooLarge`. The browser saw a dropped connection.
#:
#: 150 full blocks is roughly 80,000 characters on that same run — comfortably
#: inside the limit with the other sections, and far more than anyone reads.
#: The remainder is NOT dropped: it is listed one line each, and the count is
#: stated, because a silently shortened report is the failure this feature
#: exists to avoid.
MAX_FULL_FINDING_BLOCKS = 150

#: How many findings get a FULL write-up. EDITORIAL, not a size guard — that
#: is what `MAX_FULL_FINDING_BLOCKS` and the shed ladder are for.
#:
#: A run on a real corpus produced 549 findings that bore on the goal, and the
#: document rendered 150 of them in full: 162,000 characters, inside every size
#: budget and read by nobody. The report is a decision memo, so a full block is
#: reserved for the themes the ranking actually put first — the same ten the
#: RICE table shows, so the two sections cannot disagree about what mattered.
#:
#: The rest are NOT dropped: they are listed one line each in rank order and
#: anything past that is counted, exactly as before.
#: SUPERSEDED BY `MAX_WRITTEN_UP_FINDINGS`, which is now what the assembler
#: passes. Kept as a name rather than deleted because it is the RICE table's
#: row cap under a second name, and something may still read it for that.
MAX_DETAILED_FINDINGS = MAX_RICE_ROWS

#: HOW MANY FINDINGS GET A FULL WRITE-UP, AND HOW MANY OF THE REST GET A LINE.
#:
#: THESE TWO COME FROM THE READER, NOT FROM A LIMIT. Asked what he wanted this
#: document to be, he described it himself: "finding number one could be, hey,
#: maybe build XYZ … and then we go to item number two … and then the bottom
#: will be other things that we considered — these are 20 other things that you
#: could also build." Two write-ups, then twenty lines.
#:
#: They are deliberately NOT derived from anything technical. `MAX_RICE_ROWS`,
#: `MAX_FULL_FINDING_BLOCKS`, `MAX_OVERFLOW_ROWS` and `_SHED_LADDER` remain the
#: size guards, they are unchanged, and they all sit far above these — so a
#: later change to a size budget cannot silently move an editorial decision,
#: and a reader asking "why two?" gets an answer that is about reading rather
#: than about bytes.
#:
#: NOTHING IS DROPPED. Everything past the twenty is counted in a sentence, and
#: everything past that is still on the run.
MAX_WRITTEN_UP_FINDINGS = 2
MAX_OTHER_CONSIDERED_ROWS = 20

#: The set-aside appendix, capped the same way and for the same reason: at 95
#: rows, a table of what was NOT the answer was one of the things named as
#: making the document unreadable. The heading still carries the true total.
MAX_SET_ASIDE_ROWS = MAX_OTHER_CONSIDERED_ROWS

#: An assumed parameter, as rendered. I8 wants it disclosed, not quoted whole.
MAX_ASSUMED_PARAMS = 8
MAX_PARAM_NAME_CHARS = 120
MAX_PARAM_BASIS_CHARS = 300

#: `changes[]` / `open_questions[]` rows a deep recommendation renders.
#: Mirrors `recommend.MAX_CHANGES_PER_DEEP` / `MAX_OPEN_QUESTIONS_PER_DEEP` —
#: not imported, because `report.py` renders STORED rows and must bound them
#: even for a row written before either constant existed or changed value.
MAX_DEEP_CHANGES = 5
MAX_DEEP_OPEN_QUESTIONS = 5

#: A source document's name, as rendered. Tenant text, so it is bounded.
MAX_SOURCE_NAME_CHARS = 120

#: How many source names a block prints. `pipeline.MAX_NAMED_SOURCES` bounds
#: what new runs WRITE; this bounds what any row, however old, RENDERS.
MAX_RENDERED_SOURCES = 5

#: The overflow list's rows. Each is one clipped statement, and the count of
#: anything beyond is still stated, so nothing becomes invisible.
#:
#: 1,000 rather than 400 because 400 SILENTLY DEGRADED A REAL RUN: the
#: 831-finding report listed all 681 of its tail before this PR and only 400
#: after, which is a regression dressed as a safety fix. At 1,000 every run
#: that exists lists its tail in full, and the ladder still bounds the rest.
MAX_OVERFLOW_ROWS = 1_000

#: The ledger and the limits section — neither is reachable by the ladder.
MAX_LEDGER_ROWS = 300
MAX_LEDGER_LABEL_CHARS = 200
MAX_LEDGER_REASON_CHARS = 400
MAX_GAPS = 40
MAX_GAP_CHARS = 400

_BODY_LIMIT = 400_000  # mirrors custom_artifacts.MAX_BODY_CHARS

#: WHY THERE IS NO IMPORT-TIME ASSERTION HERE ANY MORE.
#:
#: The previous version multiplied constants together and claimed the result
#: was "derived, not measured". It was neither: it compared constants to other
#: constants and never looked at a rendered character, so it passed while real
#: data broke the budget it certified. Two things defeated it — `surfaced_by`
#: held an unclipped tenant filename, and the overflow list grew with the run.
#:
#: A truthful static bound is also not worth having. Escaping expands text up
#: to 6x, so a worst case honest enough to assert would force the full-block cap
#: from 150 down to roughly 95 — degrading every real report to insure against
#: a document of pure quote characters that no tenant has.
#:
#: So the guarantee is empirical instead: render, MEASURE, and shed detail until
#: the document fits. Real reports never leave the first rung; pathological ones
#: shrink themselves and say so. `_body_or_413` stays as the backstop for the
#: case where even the last rung is too big.
#: Overflow rows go first and GRADUALLY, then full blocks. A ladder that
#: halves both at once turns a run that missed rung 1 by a hundred characters
#: into a report with a tenth of its tail.
_SHED_LADDER = (
    (MAX_FULL_FINDING_BLOCKS, MAX_OVERFLOW_ROWS),
    (MAX_FULL_FINDING_BLOCKS, 600),
    (MAX_FULL_FINDING_BLOCKS, 400),
    (MAX_FULL_FINDING_BLOCKS, 200),
    (100, 150),
    (50, 75),
    (20, 30),
    (10, 10),
)


def _list_pricing(findings: list[dict]) -> Optional[tuple[float, float, int]]:
    """Corpus-wide list pricing: the two ends of the range, and how many
    findings carry one. `None` when no finding does.

    HOISTED TO THE CORPUS, AND THAT IS THE FIX FOR A REAL RENDERING BUG. This
    sentence used to live inside a finding's own write-up — and only the top
    handful of findings get one, while list pricing sits on whichever
    findings the pricing conversations happened to cluster into. On a live
    run twelve findings carried correctly-shaped pricing units and the line
    rendered for none of them, because not one of the twelve was in the top
    ten.

    It also belongs here on the merits. A rate card is not a property of one
    theme; it is what the product costs, and it turns up wherever pricing was
    discussed.

    ONLY WHAT CAN BE AGGREGATED WITHOUT DOUBLE COUNTING. The two ends are a
    min of mins and a max of maxes, which is exact. Per-finding distinct-price
    and account counts are NOT summed here — the same price quoted in two
    findings would be counted twice — so the only count reported is one this
    function can be sure of: how many findings carry pricing at all.

    THE ARITHMETIC IS SHARED, not reimplemented here: `aggregate_price_range`
    (`app.crucible.recommend`) is the one place the min-of-mins/max-of-maxes
    rule is coded, so this document and the live panel's own list-pricing
    line (`recommend.quoted_list_pricing_basis`) can never independently
    drift on the numbers, only ever read them from the same place. This
    function's own job is just adapting the dict shape a stored finding row
    carries into the `(min, max)` pairs that rule takes.
    """
    from app.crucible.recommend import aggregate_price_range

    pairs: list[tuple[float, float]] = []
    for f in findings:
        units = _as_dict(_as_dict(f.get("impact")).get("native_units"))
        lo = units.get("commercial_list_price_min")
        hi = units.get("commercial_list_price_max")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
            pairs.append((float(lo), float(hi)))
    return aggregate_price_range(pairs)


def _findings_heading(findings: list[dict]) -> str:
    """The findings-section heading: a CLAIM, not a label.

    The reference memo opens its equivalent section with "We tell customers
    their export succeeded roughly 72,000 times, and it did not" — an
    assertion about the corpus, not a description of the section
    ("What the evidence says"). This corpus's assertion already exists: the
    top-ranked finding's own `statement` is exactly that shape ("N claims
    across M accounts concern X"), written for the finding's own body text
    (`_esc_statement`, used throughout `_finding_block`) and reused here
    rather than generated a second way — one wording convention for what a
    finding says, not two.

    `findings` arrives already rank-ordered (see `render_report_html`'s own
    docstring on why re-sorting anywhere downstream would be wrong), so
    `findings[0]` is the strongest claim in the set without this function
    doing any ranking of its own.

    STILL A SECTION HEADING, NOT JUST THE TOP FINDING STANDING ALONE. A claim
    with nothing after it reads as if it were the only finding, which is false
    on every run with more than one — so a heading for more than one finding
    names how many more sit under it. Exactly one finding needs no such
    qualifier: the claim already describes the whole section.

    CUT BEFORE THE FINDING'S OWN EXAMPLE, not after it. `pipeline.py`'s
    statement-builder embeds a supporting example at
    `_STATEMENT_EXAMPLE_LEAD_IN` precisely when a finding has no `label` —
    and a labelless finding's own `<h3>` card (`_finding_block`) falls back
    to that SAME statement for ITS heading. Reusing the whole sentence here
    would put the identical words in two headings back to back, about the
    same theme — the exact duplication `Finding.label`'s own docstring exists
    to avoid ("a terrible thing to SCAN"). Cutting at the same clause
    boundary leaves the claim here and the example where it already is: in
    the card below, or in its blockquote.

    THROUGH `_restate_statement`, so a row stored in the old quoted shape is
    cut at the same place a new one is.
    """
    statement = _restate_statement(findings[0].get("statement") or "")
    core = statement.split(_STATEMENT_EXAMPLE_LEAD_IN, 1)[0].strip().rstrip(",")
    claim = _esc_clipped(core or statement, MAX_STATEMENT_CHARS)
    if len(findings) > 1:
        return f"{claim} — the strongest of {len(findings):,} findings below"
    return claim


def _ordering_note(findings: list[dict]) -> str:
    """What the order actually is. METHOD, so it reads with the method.

    `_rank`'s key is (conflict, claim-type bucket, reach, confidence). Each
    clause is stated only when the term it names did work on this run: on a
    corpus of nothing but blockers the bucket term ordered nothing, and
    claiming it did would be an overstatement in the other direction.
    """
    if not findings:
        return ""
    anything_sized = any(f.get("impact_value") is not None for f in findings)
    buckets = {
        type_bucket([str(t) for t in _as_list(f.get("claim_types"))])
        for f in findings
    }
    bucket_clause = (
        " What blocks an account is placed above what an account only asks "
        "for, whatever their sizes."
        if len(buckets) > 1 else ""
    )
    conflict_clause = (
        " An authoritative disagreement is placed above "
        + ("both" if bucket_clause else "all of it")
        + ": two sources that may both speak contradicting each other is "
        "worth more than either alone."
    )
    if anything_sized:
        return _p(
            "Ranked by reach — how many accounts each theme touches."
            + bucket_clause + conflict_clause
        )
    # Nothing could be sized, so the reach term is constant and the BUCKET is
    # what orders the list. `_rank`'s last term is a confidence SCORE, which
    # is real and never rendered — the reader sees bands — so when every band
    # is the same, the gap between neighbours in one group rests on something
    # this document does not print, and that is owed a sentence.
    bands = {(f.get("confidence_band") or "").strip() for f in findings}
    one_band = len(bands) == 1 and len(findings) > 1
    return _p(
        "Not ranked by reach: nothing here could be sized."
        + bucket_clause + conflict_clause
        + (
            " Within a kind, findings are ordered by a confidence score this "
            "report does not print, and every finding here carries the same "
            "band — so read the gap between two neighbours in one group as "
            "narrow."
            if one_band else ""
        )
    )


def _findings_section(
    findings: list[dict],
    # THE EDITORIAL CAP IS THE DEFAULT, so a caller that forgets to pass one
    # gets the memo rather than a dump.
    full_cap: int = MAX_WRITTEN_UP_FINDINGS,
    plan: Optional[dict] = None,
) -> str:
    """The findings that get a full write-up, and the facts hoisted above them.

    The tail — everything ranked below these — is `_other_considered_section`,
    which renders AFTER the ranking table rather than immediately under the
    write-ups. That split is the reader's own running order: finding one,
    finding two, why number one, the table, then the other things considered.
    """
    if not findings:
        return ""
    # THE SAME NUMBER THE STAT STRIP AND DECISION BOX ALREADY MULTIPLY BY,
    # read once here rather than re-derived per finding.
    account_value = _as_dict(plan).get("account_value")
    out = ["<h2>Each one, in full</h2>"]

    # ── THE TWO CAVEATS THIS SECTION OWES, STATED ONCE AT ITS HEAD. ───────
    #
    # Both used to travel as a clause inside individual lines — "Summarising
    # one source:" opening every blockquote, and the naming limit buried in
    # the appendix while the write-ups above it read as though they named
    # accounts. Neither is dropped. Said once, at the top, they are out of the
    # way of the prose and still unmissable, which is the whole trade.
    out.append(_p(
        "Two things to know before you read these. We can tell you how many "
        "accounts sit behind a finding, never which ones — this reading "
        "counts accounts and does not keep their names. And anything set "
        "apart below is a summary of what one source said rather than a "
        "quotation: the raw text is checked, used, and never stored."
    ))

    # THE UNSIZED COUNT, STATED UNCONDITIONALLY. It used to be suppressed
    # depending on how much of the same disclosure the old opening section had
    # already made two paragraphs above; that section is now the appendix's
    # placement note, below this one and no longer naming the count, so the
    # coordination it required is gone and this is the only place the number
    # lives.
    unsized = sum(1 for f in findings if f.get("impact_value") is None)
    if unsized:
        how_many = "One" if unsized == 1 else f"{unsized:,}"
        out.append(_p(
            f"{how_many} of these we could not size at all. An unsized theme "
            f"sorts last without being small: its size is unknown, not zero."
        ))

    # ONE FACT ABOUT THE CORPUS, OR MANY ABOUT THE FINDINGS? Detected, never
    # assumed: the moment a run produces two different weakest links they both
    # go back on their own rows. Only a single distinct value across MORE THAN
    # ONE finding is a corpus-wide statement.
    def _shared(key: str) -> str:
        if len(findings) < 2:
            return ""
        vals = {
            (_as_dict(f.get("confidence")).get(key) or "").strip()
            for f in findings
        }
        return vals.pop() if len(vals) == 1 else ""
    shared_weakest = _shared("weakest_leg_reason")
    shared_cap = _shared("cap_reason")

    if shared_weakest:
        out.append(_p(
            "<strong>Every finding here has the same weakest link</strong>, "
            "so we state it once rather than on each: "
            + _esc(shared_weakest)
            # A CLAUSE, NOT A NEW SENTENCE. `cap_reason` arrives uncapitalised
            # ("capped at medium: …"), so a full stop before it rendered
            # "…the diagnosis are not. capped at medium".
            + (_stop(f"; {_esc(shared_cap)}") if shared_cap else ".")
        ))
    elif shared_cap:
        out.append(_p(
            "<strong>Every finding here is capped the same way</strong>, so "
            "we state it once rather than on each: "
            + _stop(_esc(shared_cap))
        ))

    shared_assumptions, shared_count = _shared_assumptions(findings)
    if shared_assumptions:
        many = len(shared_assumptions) > 1
        # SAYS HOW MANY IT SPEAKS FOR. "Every finding" is false when only the
        # sized ones carry an assumption, and a hoisted sentence that
        # overstates its own scope is worse than the repetition it replaced.
        subject = (
            "Every finding rests on the same assumption"
            if shared_count == len(findings)
            else f"{shared_count} findings rest on the same assumption"
        )
        out.append(_p(
            f"<strong>{subject}{'s' if many else ''}</strong>, which we state "
            "once rather than on each:"
        ))
        out.append(_ul(
            f"<strong>{_esc_clipped(name, MAX_PARAM_NAME_CHARS)}</strong>"
            f": {_esc_clipped(basis, MAX_PARAM_BASIS_CHARS)}"
            for name, basis in shared_assumptions[:MAX_ASSUMED_PARAMS]
        ))

    # LIST PRICING: A RANGE, IN ITS OWN PARAGRAPH, WITH NO TOTAL.
    #
    # THE READER MUST NOT BE ABLE TO ADD THIS TO A COMMITTED FIGURE. One is a
    # sum of money people agreed to; the other is a rate card quoted to
    # whoever asked, whose total is meaningless — a $30,000 tier quoted
    # sixteen times is not $480,000. A range and a sum are structurally
    # non-additive, this is its own paragraph rather than a clause beside a
    # committed figure, and each says which KIND of money it is. No total is
    # printed here, and none should be added later.
    pricing = _list_pricing(findings)
    if pricing is not None:
        low, high, carrying = pricing
        span = (
            f"${low:,.0f}" if low == high else f"${low:,.0f}–${high:,.0f}"
        )
        where = (
            "one finding" if carrying == 1 else f"{carrying} findings"
        )
        out.append(_p(
            f"<strong>List pricing was quoted in {where}.</strong> {span}. "
            f"That is what was quoted, not what was agreed — the same price "
            f"offered to several accounts is one rate card, so we never add "
            f"these together or add them to any figure above."
        ))

    full = findings[:full_cap]
    # THE ALTERNATIVES, NUMBERED, AND THE GAPS UNDER THE ONE BEING
    # RECOMMENDED. Both computed over `full` — the findings that actually get
    # a write-up — and both deterministic reads over what the engine already
    # produced (`data_gaps`): no model call, no grouping, nothing chosen (I2).
    options = option_numbers(full)
    gaps_index, gaps = data_gaps_for(full)
    # ONE TOPIC NAMED TWICE IS NOT TWO OPTIONS. When the engine's own
    # `same_topic` says the top two write-ups are the same subject, the Option
    # labels come off and the comparison explains the absence instead of
    # comparing against a sibling that is not an alternative.
    one_topic = options_are_one_topic(full)
    # THE COMPARISON ALWAYS READS ON THE FIRST SCREEN, above both write-ups
    # — `_answer_section` renders it, with a fallback built from the ranking's
    # own terms when the deep pass wrote none. It used to sit two screens
    # below the first option, which is what made a reader conclude the
    # comparison was missing.
    defer_comparison = True
    # ONE CALL-COUNT FLOOR NOTE PER DOCUMENT. It is a fact about how the
    # corpus was ingested, not about any one finding, and it used to print
    # under every block that showed a call count.
    call_note_spent = False
    blocks: list[str] = []
    for i, f in enumerate(full):
        surfaced = [x for x in _as_list(f.get("surfaced_by")) if x]
        show_call_note = not call_note_spent and has_call_count(surfaced)
        if show_call_note:
            call_note_spent = True
        blocks.append(_finding_block(
            f, i + 1,
            shared_weakest=bool(shared_weakest), shared_cap=bool(shared_cap),
            shared_assumptions=bool(shared_assumptions),
            option=options[i], option_total=(max(options) if options else 0),
            data_gaps=gaps if i == gaps_index else (),
            one_topic=one_topic,
            one_topic_note=(
                ONE_TOPIC_NOTE if one_topic and i == gaps_index else ""
            ),
            account_value=account_value,
            defer_comparison=defer_comparison,
            defer_gaps=True,
            show_call_note=show_call_note,
        ))
    out.extend(blocks)
    return "".join(out)


def _rank_reason(first: dict, second: dict) -> str:
    """Why the first write-up outranks the second, when the deep pass wrote no
    comparison of its own.

    READ OFF `_rank`'s OWN KEY — (conflict, claim-type bucket, reach,
    confidence) — in that order, and it stops at the first term that actually
    separated the two. Nothing is scored or decided here (I2): the order was
    frozen upstream and this says which term produced it.

    THE LAST BRANCH IS THE HONEST ONE. When no term visible in this document
    separates them, the remaining term is a confidence SCORE the report does
    not print, and the sentence says so rather than inventing a reason that
    reads better.
    """
    a_conflict = (first.get("adjudication") or "") == "conflict"
    b_conflict = (second.get("adjudication") or "") == "conflict"
    if a_conflict and not b_conflict:
        return (
            "Two sources that may both speak contradict each other in the "
            "first one, and a disagreement is placed above everything that is "
            "not one — it is the thing most worth resolving, not the biggest."
        )
    a_bucket = type_bucket([str(t) for t in _as_list(first.get("claim_types"))])
    b_bucket = type_bucket([str(t) for t in _as_list(second.get("claim_types"))])
    if a_bucket != b_bucket and a_bucket == TYPE_BUCKET_BLOCKER:
        return (
            "The first is something accounts are blocked by; the second is "
            "something they asked for. We put a blocker above a request, "
            "whatever their sizes."
        )
    if a_bucket != b_bucket and a_bucket == TYPE_BUCKET_PREFERENCE:
        return (
            "Accounts asked for the first one. The second describes the world "
            "without blocking anyone or asking for anything."
        )
    a_val, b_val = first.get("impact_value"), second.get("impact_value")
    if isinstance(a_val, (int, float)) and b_val is None:
        return (
            f"We could size the first at {_esc(_reach(first))} and could not "
            f"size the second at all. That is an unknown rather than a small "
            f"number, so read this as an ordering we can defend, not as a gap "
            f"between them."
        )
    if (
        isinstance(a_val, (int, float)) and isinstance(b_val, (int, float))
        and a_val != b_val
    ):
        return (
            f"It reaches {_esc(_reach(first))} against "
            f"{_esc(_reach(second))}."
        )
    a_band = (first.get("confidence_band") or "").strip()
    b_band = (second.get("confidence_band") or "").strip()
    if a_band and b_band and a_band != b_band:
        return (
            f"They reach about the same, and the first rests on {_esc(a_band)} "
            f"confidence against {_esc(b_band)}."
        )
    return (
        "Nothing this document prints separates them. The ranking split them "
        "on a confidence score it does not show, so treat the gap between "
        "these two as narrow."
    )


def _answer_section(
    kept: list[dict], full_cap: int, one_topic: bool,
) -> str:
    """The screen that answers the question, above everything else.

    "No one is really going to read all of this, people are going to skim."
    So the first thing under the title is what to build, numbered, with the
    facts under each as a strip rather than a paragraph, and the reason for
    the order immediately beneath. Everything that used to sit here — what was
    asked, what the definition was, the problem, the short version, the
    decision box, the synthesis — is below it or inside the write-up it
    belongs to.

    THE COMPARISON LIVES HERE, and that is the specific fix. It used to render
    two screens after the first option, so reaching it meant holding both
    options in your head; the reader's conclusion was that the memo did not
    compare them at all. It always did.

    AND IT ALWAYS RENDERS. The engine's own `comparison` is a model-authored
    sentence that a run can simply not have, and a screen whose last line
    disappears on some runs is a screen whose shape a reader cannot learn. The
    fallback is `_rank_reason`, built from the ranking's own terms.

    NOTHING NEW IS ASSERTED. Every option is a deep write-up the run already
    produced, in the run's own frozen rank order (I10); the numbering is
    `data_gaps.option_numbers`, the same function the write-ups below use, so
    the screen and the cards cannot disagree about which is first.
    """
    out = ["<h2>What we recommend</h2>"]

    if not kept:
        out.append(_p(
            "Nothing survived verification, so we have nothing to recommend. "
            "What was considered is listed below with the reason it was "
            "dropped — that list, not this silence, is the result of this run."
        ))
        return "".join(out)

    written = kept[:full_cap]
    options = option_numbers(written)
    numbered = [(n, f) for n, f in zip(options, written) if n]
    if not numbered:
        out.append(_p(
            "Nothing in this evidence produced a build we can stand behind, "
            "so there is nothing to recommend. What we did find is ranked "
            "below, and it is worth reading before you conclude there is "
            "nothing here."
        ))
        return "".join(out)

    # THE GOAL IS THE TITLE, ONE LINE ABOVE THIS. Naming it again here put the
    # same words on the screen twice, which is what a deck is least able to
    # afford.
    n = len(numbered)
    if n == 1:
        out.append(
            '<p class="deck">One thing here is worth building.</p>'
        )
    else:
        out.append(
            f'<p class="deck">We think {_count_word(n, capital=False)} things '
            f'here are worth building, and one of them first.</p>'
        )

    rows = "".join(
        "<tr>"
        f"<td>{num}.</td>"
        f"<td><strong>"
        f"{_esc_clipped(_as_dict(f.get('deep_recommendation')).get('action'), MAX_STATEMENT_CHARS)}"
        f"</strong><br><span>{_option_chips(f)}</span></td>"
        "</tr>"
        for num, f in numbered
    )
    out.append(f'<table class="opts"><tbody>{rows}</tbody></table>')

    if n > 1:
        comparison = ""
        for _, f in numbered:
            candidate = (
                _as_dict(f.get("deep_recommendation")).get("comparison") or ""
            ).strip()
            if candidate:
                comparison = candidate
                break
        reason = (
            _esc_clipped(comparison, MAX_STATEMENT_CHARS) if comparison
            else _rank_reason(numbered[0][1], numbered[1][1])
        )
        out.append(_p(f"<strong>Do number one first.</strong> {reason}"))
        if one_topic:
            out.append(_p(
                f"<strong>These are not two options.</strong> "
                f"{_esc(ONE_TOPIC_NOTE)}"
            ))
    return "".join(out)


def _before_you_spend_section(written: list[dict]) -> str:
    """What is not known about the thing being recommended.

    The same list `_finding_block` used to print inside the recommended card,
    lifted out now that the memo runs two write-ups instead of ten: it
    qualifies the recommendation the document is making, so it reads after the
    write-ups and before the ranking rather than as a footnote on one card.

    GAPS, NOT ACTIONS — the heading says "before you spend" rather than "next
    steps" precisely so it cannot be read as work competing with the
    recommendation above it. Corpus-level gaps (`plan.cannot_answer`) are
    excluded on purpose and render once, at the end. Assembled
    deterministically from what the engine already produced: no model call,
    nothing scored (I2).
    """
    _, gaps = data_gaps_for(written)
    if not gaps:
        return ""
    return "".join([
        "<h2>What we do not know about number one</h2>",
        _p(
            "These are gaps in what we know about the one we would start "
            "with, not work for anyone to schedule. We would want them closed "
            "before you spend against it."
        ),
        _ul(_esc_clipped(g, MAX_STATEMENT_CHARS) for g in gaps),
    ])


def _why_not_chosen(finding: dict) -> str:
    """Why a theme is in the tail rather than in the two above.

    THE REASON ALREADY EXISTS IN THE DATA — it is `_rank`'s own key, read back
    out: what kind of claim is behind it, how far it reaches, and how much the
    evidence earned. Nothing is judged here and no model is asked (I2); the
    ordering was frozen upstream, and this states the terms it was frozen on.

    IT USED TO BE A BARE LABEL. Twenty names in rank order tell a reader
    nothing they can argue with — the reader's own description of what this
    list should do is "we consider these other ten projects, but then we
    didn't choose them because maybe the revenue is large, but then the
    confidence is low." That sentence is two facts and a contrast, and both
    facts were already on the finding.
    """
    bits: list[str] = []
    if finding.get("impact_value") is None:
        bits.append("we could not size it — unknown, not small")
    else:
        bits.append(_esc(_reach(finding)))
    band = (finding.get("confidence_band") or "").strip()
    if band:
        bits.append(f"{_esc(band)} confidence")
    bucket = type_bucket([str(t) for t in _as_list(finding.get("claim_types"))])
    if bucket == TYPE_BUCKET_BLOCKER:
        bits.append("stated as blocking accounts")
    elif bucket == TYPE_BUCKET_PREFERENCE:
        bits.append("asked for rather than blocking")
    else:
        bits.append("describes rather than blocks or asks")
    if (finding.get("adjudication") or "") == "conflict":
        bits.append("<strong>sources disagree</strong>")
    return _stop("; ".join(bits))


def _other_considered_section(
    findings: list[dict],
    full_cap: int = MAX_WRITTEN_UP_FINDINGS,
    overflow_cap: int = MAX_OTHER_CONSIDERED_ROWS,
) -> str:
    """"And then the bottom will be other things that we considered — these
    are 20 other things that you could also build."

    A TABLE, BECAUSE A LIST OF NAMES IS NOT AN ANSWER. This was one clipped
    label per line in rank order, which says what was considered and nothing
    about why it lost. Each row now carries what the theme actually is, what
    it is worth in the same vocabulary the set-aside appendix uses (`_worth`,
    so an unsized theme reads "Unsized" and never "0"), and the terms it
    ranked below the two above on (`_why_not_chosen`).

    Still in the run's own rank order, and everything past the cap is still
    COUNTED. The count is the whole reason this section can be short: a list
    that stops without saying so reads as the complete set, which is the
    silent degradation this file exists to prevent.
    """
    rest = findings[full_cap:]
    if not rest:
        return ""
    listed = rest[:overflow_cap]
    out = [f"<h2>The other {len(rest):,} we did not choose</h2>"]
    out.append(_p(
        "Every one of these was found, corroborated and ranked exactly like "
        "the two above. The last column is the one that matters: it is what "
        "put each of them below, in the ranking's own terms."
    ))
    rows = "".join(
        "<tr>"
        f"<td>{offset}</td>"
        f"<td><strong>"
        f"{_esc_clipped((f.get('label') or '').strip() or _statement_text(f), MAX_PARAM_NAME_CHARS)}"
        f"</strong></td>"
        f"<td>{_esc_clipped((f.get('example') or '').strip() or _statement_text(f), MAX_PARAM_BASIS_CHARS)}</td>"
        f"<td>{_esc(_worth(f))}</td>"
        f"<td>{_why_not_chosen(f)}</td>"
        "</tr>"
        for offset, f in enumerate(listed, start=full_cap + 1)
    )
    out.append(
        '<table class="aside"><thead><tr>'
        "<th>#</th><th>Theme</th><th>What it is</th><th>Worth</th>"
        "<th>Why it is not one of the two</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )
    beyond = len(rest) - len(listed)
    if beyond > 0:
        out.append(_p(
            f"and {beyond:,} more, all of them on the run."
        ))
    return "".join(out)


def _hypotheses_section(plan: dict) -> str:
    hypotheses = [h for h in _as_list(plan.get("hypotheses")) if h]
    if not hypotheses:
        return ""
    return "".join([
        "<h2>What you already believed</h2>",
        # Bounded here too. The API now caps each string, but a plan stored
        # before that cap existed is still on disk, and the document budget
        # cannot depend on when a row was written.
        _ul(_esc_clipped(h, MAX_STATEMENT_CHARS) for h in hypotheses),
        # NOT A VERDICT. The engine does not test a stated hypothesis against
        # the claims, and listing these beside the findings without saying so
        # would let a reader infer that silence meant "not supported" — a
        # conclusion nothing produced.
        _p(
            "This reading did not test these. Nothing above was matched "
            "against what you wrote here, so their absence from the findings "
            "is not evidence against them."
        ),
    ])


def _ledger_section(ledger: list[dict]) -> str:
    if not ledger:
        return ""
    # BOUNDED. `label` traces to `pipeline._label()` -> `claim.subject` ->
    # `kg_entity.canonical_label`, the same untruncated tenant string that had
    # to be clipped for `statement` — and the shed ladder cannot rescue this
    # section, because it sheds findings only. 102 rows at 4,000-char labels
    # rendered 828,071 characters, over the limit at every rung.
    shown = ledger[:MAX_LEDGER_ROWS]
    # ALWAYS EXPANDED HERE, unlike the panel, which folds a long ledger behind
    # a `<details>`. `<details>` is not on the artifact allowlist and would be
    # unwrapped into a permanently-open list anyway.
    #
    # GROUPED BY REASON, because that is the shape of the answer. A real run
    # rejected 102 candidates for FIVE distinct reasons — 49 with no
    # authoritative source, 47 backed by a single claim, 4 from one account —
    # and the flat list printed each reason beside each label, so the same
    # sentence appeared 49 times and the reader could not see that half the
    # ledger died one way and half another without counting by hand.
    #
    # An earlier version of this hoisted a reason only when ALL of them
    # matched, which is the degenerate case of exactly this and fired on
    # almost no real run. Grouping subsumes it: one group is the same thing as
    # "they all died for one reason", said in the same words.
    # BOOKKEEPING IS NOT A CANDIDATE. Two of these rows stand for everything
    # the list could NOT hold — the "N further candidates" overflow summary and
    # the one for signals with no usable embedding. Counted as rejections they
    # made a run that considered 1,576 candidates report "Considered and ruled
    # out (102)", directly under a promise that everything considered was
    # listed; grouped as reasons they turned a one-cause ledger into three.
    from app.crucible.pipeline import AGGREGATE_STAGES
    aggregates = [r for r in shown
                  if (r.get("stopped_at_stage") or "") in AGGREGATE_STAGES]
    shown = [r for r in shown
             if (r.get("stopped_at_stage") or "") not in AGGREGATE_STAGES]

    groups: dict[str, list[dict]] = {}
    for r in shown:
        groups.setdefault((r.get("reason") or "").strip(), []).append(r)
    # Biggest cause first, ties broken on the reason text so a re-run of the
    # same data renders the same document (the whole engine is deterministic;
    # a section that reordered itself would undermine that everywhere else).
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    out = [f"<h2>Considered and ruled out ({len(shown)})</h2>"]
    out.append(_p(
        "A ranking whose rejections are invisible is a ranking you have to "
        "take on faith. Each of these was a candidate and each one died for a "
        "stated reason"
        + (
            f", grouped below by that reason — {len(ordered)} of them across "
            f"{len(shown)} candidates."
            if len(ordered) > 1 else
            ", and every one of them died for the same one."
        )
    ))
    for reason, rows in ordered:
        head = (
            f"<strong>{len(rows)}</strong> "
            + ("died" if len(rows) != 1 else "died")
            + (
                f" because {_esc_clipped(reason, MAX_LEDGER_REASON_CHARS)}"
                if reason else " with no reason recorded"
            )
        )
        out.append(_p(head))
        out.append(_ul(
            f"<strong>{_esc_clipped(r.get('label'), MAX_LEDGER_LABEL_CHARS)}"
            f"</strong>"
            + (
                f" <em>(stopped at "
                f"{_esc_clipped(r.get('stopped_at_stage'), 60)})</em>"
                if r.get("stopped_at_stage") else ""
            )
            for r in rows
        ))
    # THE BOOKKEEPING, SAID AS BOOKKEEPING. These rows carry numbers the reader
    # needs — how many candidates the list could not hold, how many signals
    # could not be grouped at all — and burying them among the candidates is
    # what made the count wrong. Stated after the list, as their own facts.
    for r in aggregates:
        out.append(_p(
            f"<strong>{_esc_clipped(r.get('label'), MAX_LEDGER_LABEL_CHARS)}"
            f"</strong> \u2014 "
            f"{_esc_clipped(r.get('reason'), MAX_LEDGER_REASON_CHARS)}"
        ))
    remainder = len(ledger) - len(shown) - len(aggregates)
    if remainder > 0:
        out.append(_p(
            f"{remainder} further rejections are on the run and are not "
            f"listed here."
        ))
    return "".join(out)


def _limits_section(plan: dict, *, relevance_gate_ran: bool = False) -> str:
    out = ["<h2>What this cannot tell you</h2>"]
    out.append(_p(
        "This reading is qualitative. It sizes a theme by reach — how many "
        "accounts it touches — and produces no point estimate, effort figure "
        "or significance test, because nothing it read carries the numbers "
        "those need."
    ))
    # WHICH FINDINGS APPEAR WAS NOT ALWAYS DECIDED BY THE GOAL, and the
    # sentence below is what said so — CORRECTLY, until a relevance gate
    # shipped. Claim SELECTION still never sees the goal (`_load_signals` reads
    # the whole connected corpus and `build_findings` runs with no goal
    # argument), but the list a reader is SHOWN is a different question, and
    # `app.crucible.relevance.judge_relevance` now answers it: it is handed
    # this run's goal and definition and used to move findings that do not
    # bear on either into the appendix below. A run that ran the gate must not
    # print the sentence that denies it — that is exactly the falsehood this
    # branch exists to close. `relevance_gate_ran` is true only
    # when `judge_relevance` completed without raising (`routes/crucible.py`);
    # a run that predates the gate, or whose gate call failed and kept
    # everything, gets the original, still-true sentence.
    if relevance_gate_ran:
        out.append(_p(
            "<strong>These findings were filtered for relevance to your "
            "goal.</strong> A model checked every theme against your goal "
            "and definition and kept what could plausibly bear on it; what "
            "did not is listed separately below, with the reason. Being in "
            "the evidence you approved AND surviving that check is still not "
            "a claim about how much a theme matters — judge that yourself."
        ))
    else:
        out.append(_p(
            "<strong>These findings were not selected for your goal.</strong> "
            "Nothing here was filtered or ranked by relevance to your "
            "definition — a theme appears because it is in the evidence you "
            "approved, not because it bears on what you asked about. Its "
            "presence is not a claim that it matters to this goal; judge "
            "that yourself."
        ))
    gaps = [g for g in _as_list(plan.get("cannot_answer")) if isinstance(g, dict)]
    if gaps:
        # Built from the run PLAN's own gaps, so what the user was warned about
        # BEFORE the run is what they are reminded of after it.
        # Bounded for the same reason as the ledger: uncapped in count and in
        # all three fields, and out of the ladder's reach. 500 gaps rendered
        # 800,349 characters.
        for gap in gaps[:MAX_GAPS]:
            out.append(_p(
                f"<strong>{_esc_clipped(gap.get('question'), MAX_GAP_CHARS)}"
                f"</strong>"
            ))
            out.append(_p(_stop(
                f"Not answerable here, because "
                f"{_esc_clipped(gap.get('because'), MAX_GAP_CHARS)}"
            )))
            out.append(_p(
                f"<em>To close it</em> "
                f"{_esc_clipped(gap.get('remedy'), MAX_GAP_CHARS)}"
            ))
        if len(gaps) > MAX_GAPS:
            out.append(_p(
                f"{len(gaps) - MAX_GAPS} further gaps are recorded on the run "
                f"and are not listed here."
            ))
    else:
        out.append(_p(
            "This run recorded no list of its own gaps, which does not mean it "
            "had none — only that it predates the step that states them."
        ))
    return "".join(out)


def _further_findings_sentence(beyond: int) -> str:
    """The overflow disclosure, agreeing with itself in the singular.

    A HELPER RATHER THAN AN INLINE f-STRING because the singular branch is
    otherwise only reachable by landing exactly one finding past a size
    budget — a boundary too fragile to hold in a test, which is why this
    sentence's sibling ("None of the 1 met the citation bar") reached a live
    report before anyone saw it.
    """
    if beyond == 1:
        return (
            "A further finding is on the run and is not listed here, because "
            "this document has a size limit. It was not dropped from the "
            "analysis."
        )
    return (
        f"A further {beyond} findings are on the run and are not listed "
        f"here, because this document has a size limit. They were not "
        f"dropped from the analysis."
    )


def _provenance_section(
    run: dict,
    plan: dict,
    considered: list[dict],
    kept: list[dict],
    set_aside: list,
    *,
    relevance_gate_ran: bool,
    relevance_judged: dict,
    recommendation_basis: str,
    written: int = 0,
) -> str:
    """Everything the memo rests on, AFTER the memo.

    WHY THIS SECTION EXISTS AT ALL, given it is five sections that used to be
    the first five. Reading the current output, the customer put the line
    exactly here: the content started at "the short version", and the 3,658
    characters above it — what this was asked to establish, what was read, the
    source breakdown, what was missing, how it was ranked — were, in his
    words, "all of this information should not be in the final report".

    MOVED, NEVER DELETED, and that distinction is the whole design. Several of
    these lines are the disclosures this feature is built on: that a run was
    filtered by a definition, that a third of the evidence was undated, that
    a ranking term could not be filled. A memo whose provenance has been
    deleted is not shorter, it is unfalsifiable. So it all still renders, in
    one plainly-labelled place, where a reader who wants to check the memo
    finds it together instead of having to read it first.

    PER-FINDING DISCLOSURES DO NOT MOVE. An unsized value, an assumption
    behind one specific finding, the weakest link on one theme — those stay
    attached to the finding they qualify, because they are facts about that
    finding rather than about the corpus, and detaching them is how a caveat
    stops being read.
    """
    out = [
        '<h2 class="appendix">How this was produced</h2>',
        _p(
            "What the memo above rests on: what you asked us, what we read, "
            "what was missing from it, and how the ranking works. None of it "
            "is needed to act on the recommendation, and all of it is needed "
            "to argue with it."
        ),
        _ask_section(run, plan),
        _what_was_read_section(run, plan),
        _funnel_chart(plan, considered, kept, written),
        _funnel_section(len(considered), len(kept)),
        _relevance_coverage_section(relevance_judged),
        _definition_method_note(run),
        _framework_section(kept, plan, with_table=False),
        _ordering_note(kept),
        _placement_note_section(kept),
        _recommendation_basis_section(recommendation_basis),
        _set_aside_section(set_aside),
        # SAID ONCE, AND SAID PLAINLY, because the memo above is read as
        # though it named accounts and it does not. A finding carries how MANY
        # accounts a theme touches; which ones is never stored (`pipeline`
        # keeps `len(accounts_named)` and drops the names). Stating the limit
        # here is the alternative to a write-up that implies a customer list
        # it cannot produce.
        _p(
            "We count accounts, we never name them: this reading records how "
            "many accounts a theme touches and not which ones. Where a name "
            "appears in this document it is a source document."
        ),
    ]
    return "".join(p for p in out if p)


def render_report_html(
    run: dict,
    findings: Optional[list[dict]] = None,
    ledger: Optional[list[dict]] = None,
    plan: Optional[dict] = None,
) -> str:
    """The run as a document.

    `run` is the `crucible_runs` row; `findings` and `ledger` are what
    `db.crucible_runs.load_findings` returns, IN THAT ORDER — insertion order
    is the rank, and it is not recoverable from any column (an authoritative
    conflict is ranked first regardless of size). Re-sorting here would
    silently contradict the `tier` each row already carries.

    `plan` defaults to the run's own stored plan, which is where it always
    lives; the parameter exists so a caller that has already parsed
    `prioritisation` does not parse it twice.

    Deterministic: same row in, same bytes out. That is what lets
    `body_fingerprint` mean "has a human touched this" rather than "has
    anything at all changed".
    """
    findings = list(findings or [])
    ledger = list(ledger or [])
    # ── THE THEME, THE QUOTE AND THE RECOMMENDATION, MERGED IN ONCE. ────────
    #
    # These three live in the run's own JSON rather than in columns on
    # `crucible_findings`, because adding columns means a migration against the
    # shared Supabase — a production change, and not one to make unasked.
    #
    # POSITIONAL, and safe to be: this function's own contract says the
    # findings arrive in rank order and that the order is not recoverable from
    # any column, so the list the route wrote and the list read back are the
    # same sequence. Merging here rather than threading a second argument
    # through four functions means every renderer downstream reads one dict and
    # cannot disagree with another about what a finding said.
    # ── THE GOAL-RELEVANCE GATE, APPLIED AT RENDER. ────────────────────────
    #
    # `set_aside_by_rank[i]` is the reason finding `i` does not bear on the
    # goal, or None. Splitting HERE rather than at write time keeps every
    # finding in the row set: a verdict that was wrong is recoverable, and a
    # reader who wants the whole list still has one.
    #
    # POSITIONAL, and guarded by length like the extras below — a mismatch
    # means the two lists are not the same sequence, and setting aside the
    # wrong finding is far worse than setting none aside.
    _aside_reasons = _as_list(_as_dict(run.get("prioritisation")).get("set_aside_by_rank"))
    if len(_aside_reasons) != len(findings):
        _aside_reasons = [None] * len(findings)

    extra = _as_list(_as_dict(run.get("prioritisation")).get("findings_extra_by_rank"))
    if extra and len(extra) == len(findings):
        findings = [
            {**f, **{k: v for k, v in _as_dict(x).items() if v}}
            for f, x in zip(findings, extra)
        ]
    ledger = list(ledger)
    if plan is None:
        plan = _as_dict(_as_dict(run.get("prioritisation")).get("plan"))
    plan = _as_dict(plan)

    # Split once, after the extras are merged, so both halves carry their
    # themes, quotes and recommendations.
    kept = [f for f, r in zip(findings, _aside_reasons) if not r]
    set_aside = [(f, r) for f, r in zip(findings, _aside_reasons) if r]

    goal = (run.get("goal_text") or "").strip()
    prioritisation = _as_dict(run.get("prioritisation"))
    recommendation_basis = str(prioritisation.get("recommendation_basis") or "")
    # Whether `judge_relevance` actually ran on this run — see
    # `_definition_section` and `_limits_section` for what turns on it.
    relevance_gate_ran = bool(prioritisation.get("relevance_gate_ran"))
    relevance_judged_info = _as_dict(prioritisation.get("relevance_judged"))

    # THE ARGUMENT BEHIND THE ANSWER. Read straight off the run's own JSON —
    # computed once, upstream, by `recommend.build_synthesized_recommendation`
    # — and rendered by `_assemble` below immediately UNDER the screen that
    # states the answer, never as a second answer of its own: that call binds
    # its `action` to rank one's action verbatim, so printing it again is the
    # same sentence twice. See `_why_this_section`.
    synthesized_recommendation = _as_dict(
        prioritisation.get("synthesized_recommendation")
    )

    def _assemble(full_cap: int, overflow_cap: int) -> str:
        # ── THE MEMO'S RUNNING ORDER, IN THE READER'S OWN WORDS. ────────
        #
        #   "finding number one could be, hey, maybe build XYZ … and then we
        #    go to item number two … but I think you should do number one
        #    because it's the most important one … so this is the RICE
        #    prioritization, with the table. And then the bottom will be other
        #    things that we considered."
        #
        # THE ANSWER IS THE FIRST SCREEN, and everything else is arranged
        # around it: the argument for it, who has to decide, the numbers, the
        # write-ups in full, what is not known about the first one, the
        # ranking, and the tail of what was not chosen. Everything that
        # describes HOW the run worked — including what was asked and what the
        # definition was — is below all of that, in `_provenance_section`.
        #
        # WHAT MOVED, AND WHY. "No one is really going to read all of this,
        # people are going to skim." Six sections used to sit between the
        # title and the first option — the ask, the definition, the problem,
        # the short version, the decision box and a synthesis headed "the
        # recommendation" two screens above options headed "recommended". A
        # reader reached the thing they came for on the third screen and met
        # the word "recommended" three times before getting there.
        #
        # NOTHING HERE CHANGES WHAT THE ENGINE COMPUTED. The same findings, in
        # the same frozen rank order (I10), with the same values; this list is
        # the order they are read in.
        written = kept[:full_cap]
        one_topic = options_are_one_topic(written)
        parts = [
            f"<h1>{_esc_clipped(goal, MAX_STATEMENT_CHARS) or 'Goal analysis'}</h1>",
            _answer_section(kept, full_cap, one_topic),
            _why_this_section(synthesized_recommendation, written),
            _decision_section(plan, kept),
            _stat_strip(plan, findings, kept),
            _findings_section(kept, full_cap, plan),
            _before_you_spend_section(written),
            _framework_section(kept, plan),
            _other_considered_section(kept, full_cap, overflow_cap),
            _hypotheses_section(plan),
            _provenance_section(
                run, plan, findings, kept, set_aside,
                relevance_gate_ran=relevance_gate_ran,
                relevance_judged=relevance_judged_info,
                recommendation_basis=recommendation_basis,
                written=len(written),
            ),
            _ledger_section(ledger),
            _limits_section(plan, relevance_gate_ran=relevance_gate_ran),
        ]
        return "".join(p for p in parts if p)

    # MEASURE, don't assert. See `_SHED_LADDER`. Deterministic: the same row
    # yields the same rung and therefore the same bytes, which is what
    # `body_fingerprint` depends on.
    html = ""
    for full_cap, overflow_cap in _SHED_LADDER:
        # `min`, NOT a smaller ladder. The rungs below rung 0 raise the full
        # cap back up (100, 50, 20) to shed overflow rows first, so passing the
        # rung straight through would let a document that missed the size limit
        # come back with TEN TIMES the write-ups it started with.
        html = _assemble(
            min(MAX_WRITTEN_UP_FINDINGS, full_cap),
            min(MAX_OTHER_CONSIDERED_ROWS, overflow_cap),
        )
        if len(html) <= _BODY_LIMIT:
            return html
    return html


#: The canonical stylesheet, read off disk once. Beside this module rather
#: than under `skills/` — see the file's own header for why a deterministic
#: pipeline should not be vendored as a prompt-layer skill just to own a CSS
#: file.
_CSS_PATH = Path(__file__).with_name("assets") / "goal-analysis.css"


@lru_cache(maxsize=1)
def _document_css() -> str:
    return _CSS_PATH.read_text(encoding="utf-8")


def render_report_document(
    run: dict,
    findings: Optional[list[dict]] = None,
    ledger: Optional[list[dict]] = None,
    plan: Optional[dict] = None,
) -> str:
    """The same report, in the other envelope: a self-contained HTML document.

    ONE CONTENT GENERATOR, TWO ENVELOPES, and the split is the whole point.
    `render_report_html` produces the BODY — every sentence, every number,
    every ordering decision — and it is called here rather than reimplemented,
    so the two can never disagree about what the report says. This function
    adds a `<!doctype>`, a sheet and a wrapper. Nothing else.

    WHY TWO ENVELOPES EXIST AT ALL. The body's first home is
    `custom_artifacts.body_html`, which is sanitized on every write to an
    allowlist that strips `class`, drops `<style>` with its children, and
    keeps almost no CSS — because that document is rendered INLINE in a
    contenteditable surface, where the app's own chrome is one DOM away. The
    body is written to that constraint and stays written to it.

    But that constraint is a property of one destination, not of the report.
    The PRD, the evidence brief and every chat report render as a whole
    document inside a sandboxed iframe (`web/app/components/shared/
    HtmlReportView.tsx`, `srcDoc` + `sandbox="allow-same-origin"` and no
    `allow-scripts`), where a stylesheet is safe because nothing can execute
    and nothing can reach the app around it. This envelope is that path, and
    it is why the panel no longer rebuilds the document in React: it renders
    the same bytes the server already knows how to produce.

    THE EMPTY `<style>` MARKER IS DELIBERATE. `inject_canonical_css` replaces
    the first `<style>` element, so the sheet is spliced in by the server
    exactly as it is for the other two documents, and re-running this on its
    own output is idempotent.
    """
    body = render_report_html(run, findings, ledger, plan)
    title = _esc(report_title(run))
    doc = (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{title}</title>"
        "<style></style>"
        "</head><body>"
        f'<div class="frame"><div class="page">{body}</div></div>'
        "</body></html>"
    )
    return inject_canonical_css(doc, _document_css())


def report_title(run: dict) -> str:
    """What the document is called in the shared library.

    Truncated to fit `custom_artifacts.title`'s 300 characters by the storage
    layer; trimmed here too so the cut lands on a word rather than mid-goal.
    """
    goal = " ".join((run.get("goal_text") or "").split())
    if not goal:
        return TITLE_PREFIX
    if len(goal) > 200:
        goal = goal[:200].rsplit(" ", 1)[0] + "…"
    return f"{TITLE_PREFIX}: {goal}"
