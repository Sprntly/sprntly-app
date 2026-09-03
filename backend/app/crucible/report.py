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
from html import escape
from typing import Any, Iterable, Optional

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


def _human_source(source_type: str) -> str:
    """`project_mgmt` reads as "project mgmt", not as a column name.

    An excluded source is only a KEY by the time the report runs — its label
    went with the plan entry the run dropped. Softened rather than looked up,
    for the reason the panel gives: a second copy of the backend's source prose
    would drift from the first.
    """
    return _esc((source_type or "").replace("_", " "))


def _definition_section(run: dict, plan: dict) -> str:
    definition = (
        (plan.get("definition_text") or "").strip()
        or (_as_dict(run.get("prioritisation")).get("proposed_definition") or "").strip()
    )
    # DID A RELEVANCE GATE ACTUALLY RUN ON THIS RUN? Written by the route the
    # moment `judge_relevance` completes without raising — never guessed from
    # whether anything ended up set aside, because a gate that ran and kept
    # everything is still a gate that ran, and reads this sentence's "it did
    # not decide which findings appear below" as false the moment it exists.
    gate_ran = bool(_as_dict(run.get("prioritisation")).get("relevance_gate_ran"))
    out = ["<h2>What this was asked to establish</h2>"]
    if definition:
        out.append(_p("You confirmed this goal means, in your own words:"))
        out.append(f"<blockquote>{_esc(definition)}</blockquote>")
        # WHAT THIS SENTENCE ACTUALLY GOVERNS. The previous text — "everything
        # below is measured against that sentence and nothing else" — was the
        # exact claim the limits section used to deny: claim SELECTION never
        # sees the definition (`build_findings` takes a `goal_accounts` filter
        # that production does not pass). That is still true of selection. It
        # stopped being true of the LIST below the moment a relevance gate
        # shipped: `judge_relevance` is handed this exact sentence and used to
        # decide which findings stay in view (`app.crucible.relevance`), so a
        # run that ran the gate must not deny having filtered by it.
        if gate_ran:
            out.append(_p(
                "This is the sentence the run was given to work from, and it "
                "is recorded here so a decision can be defended against it. "
                "It shaped which findings appear below: each was checked "
                "against it for whether it bears on this goal, and any that "
                "did not are listed separately, with the reason, further "
                "down. If it is not what you meant, say so before you rely "
                "on any of this."
            ))
        else:
            out.append(_p(
                "This is the sentence the run was given to work from, and it "
                "is recorded here so a decision can be defended against it. "
                "It did not decide which findings appear below — nothing "
                "here was filtered or ranked by it. If it is not what you "
                "meant, say so before you rely on any of this."
            ))
    else:
        # STATED, NOT SKIPPED. A report with no recorded definition is a report
        # whose subject is unknown, and omitting the section would make that
        # look like the ordinary case.
        out.append(_p(
            "No confirmed definition was recorded for this run, so what the "
            "goal means is not on the record. Read everything below as being "
            "about the goal as typed, nothing narrower."
        ))
    return "".join(out)


def _what_was_read_section(run: dict, plan: dict) -> str:
    out = ["<h2>What was read</h2>"]
    if plan:
        sources = [s for s in _as_list(plan.get("sources")) if isinstance(s, dict)]
        total = plan.get("total_signals") or 0
        out.append(_p(
            f"{total:,} signal{'' if total == 1 else 's'} across {len(sources)} "
            f"source{'' if len(sources) == 1 else 's'}. Each one can witness "
            f"some things and not others, which is why they are listed "
            f"separately rather than totalled."
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
        out.append("<h3>What was missing from it</h3>")
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
        cells.append(("Flagged with any suggestion", f"{len(recommended):,}"))
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
    return f"<table><tbody><tr>{body}</tr></tbody></table>"


def _decision_section(plan: dict, findings: list[dict]) -> str:
    """The memo's decision box: who signs off, by when, and what is at stake.

    ONLY WHAT WAS ANSWERED. Apurva opened the gate to questions the run cannot
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
    out = ["<h2>The decision</h2>"]
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
            f"The findings that bear on this goal touch <strong>{reach:g} "
            f"accounts</strong>{money}. That is what the evidence counts, not "
            f"a forecast of what changes if you act."
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
    return _p(
        f"<strong>How many got a full recommendation.</strong> "
        f"{_esc_clipped(basis, MAX_STATEMENT_CHARS)}"
    )


def _framework_section(findings: list[dict], plan: dict) -> str:
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
        return _moscow_section(findings, label, reason)
    return _rice_section(findings, label, reason)


def _rice_section(
    findings: list[dict], framework: str, framework_reason: str = "",
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

    out = [f"<h2>How this was ranked ({_esc(framework)})</h2>"]
    if framework_reason:
        out.append(_p(framework_reason))
    # WHAT EACH TERM MEANS HERE, because RICE's letters carry assumptions this
    # corpus cannot all satisfy and a reader who assumes the standard ones will
    # misread the table.
    out.append(_ul([
        "<strong>Reach</strong> — how many of your accounts the theme touches. "
        "Counted, not estimated.",
        "<strong>Impact</strong> — how directly it bears on the metric, read "
        "from the kind of claim behind it: something blocked outranks something "
        "asked for, which outranks something described. "
        "<em>That ordering is ours, not your data's.</em>",
        "<strong>Confidence</strong> — the band the evidence earned, lowered "
        "once for each input this table could not fill.",
        f"<strong>Effort</strong> — <em>{EFFORT_ABSENT}</em>. Nothing in your "
        "connected sources carries a person-month, and inventing one would put "
        "a number in front of you that no evidence supports.",
    ]))

    body = "".join(
        "<tr>"
        f"<td>{_esc_clipped(r.label, MAX_PARAM_NAME_CHARS)}</td>"
        f"<td>{'—' if r.reach is None else f'{r.reach:g} {_esc(r.reach_unit)}'}</td>"
        f"<td>{r.impact:g}</td>"
        f"<td>{_esc(r.confidence_band)}</td>"
        f"<td>{_esc(EFFORT_ABSENT)}</td>"
        f"<td>{'—' if r.score is None else f'{r.score:.1f}'}</td>"
        f"<td>{r.inputs_present} of {len(RICE_INPUTS)}</td>"
        "</tr>"
        for r in rows
    )
    out.append(
        "<table><thead><tr>"
        "<th>Theme</th><th>Reach</th><th>Impact</th><th>Confidence</th>"
        "<th>Effort</th><th>Score</th><th>Inputs</th>"
        "</tr></thead><tbody>" + body + "</tbody></table>"
    )

    # EVERY ROW IS SCORED ON THE SAME MISSING TERM, so say what that costs
    # rather than leaving the reader to wonder what the score would have been.
    # KEYED ON EFFORT ALONE. `scored_without_effort` also requires a reach, so
    # a single unsized row made this false and swallowed the sentence for a
    # table where nobody had supplied effort at all.
    if all(not r.effort for r in rows):
        out.append(_p(
            "No effort estimate was supplied for any of these, so the score is "
            "reach × impact × confidence. That is not a gap in the ranking: an "
            "effort applied equally to every row divides them all by the same "
            "number and cannot change their order. It would change the order "
            "only once the estimates differ from each other."
        ))
    # NO SILENT CAPS. A table that stops at ten without saying so reads as the
    # whole ranking, and the rule this file applies everywhere else is that a
    # bound is stated where it bites.
    if len(findings) > len(rows):
        out.append(_p(
            f"The {len(findings) - len(rows)} findings below these are ranked "
            f"in the list that follows, but not scored out here — a table this "
            f"long stops being one."
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

    out = [f"<h2>How this was ranked ({_esc(framework)})</h2>"]
    if framework_reason:
        out.append(_p(framework_reason))
    out.append(_ul([
        "<strong>MUST</strong> — a stated blocker: something is stopping an "
        "account today. <em>Marked <strong>MUST?</strong> when only one "
        "source document backs it — real, worth confirming.</em>",
        "<strong>SHOULD / COULD</strong> — a stated preference: something an "
        "account asked for.",
        "<strong>Reach</strong> — how many of your accounts the theme "
        "touches. Counted, not estimated.",
        "Graded by how many <strong>independent source documents</strong> "
        "back each one, not by raw claim count — several restatements of one "
        "complaint from one document are one voice, not several.",
    ]))

    body = "".join(
        "<tr>"
        f"<td>{_esc_clipped(r.label, MAX_PARAM_NAME_CHARS)}</td>"
        f"<td>{_esc(r.bucket)}</td>"
        f"<td>{_esc(r.bucket_basis)}</td>"
        f"<td>{'—' if r.reach is None else f'{r.reach:g} {_esc(r.reach_unit)}'}</td>"
        f"<td>{r.doc_count}</td>"
        "</tr>"
        for r in rows
    )
    out.append(
        "<table><thead><tr>"
        "<th>Theme</th><th>Bucket</th><th>Why</th><th>Reach</th>"
        "<th>Source documents</th>"
        "</tr></thead><tbody>" + body + "</tbody></table>"
    )

    unranked = sum(1 for r in rows if r.bucket == "unranked")
    if unranked:
        out.append(_p(
            f"{unranked} of these neither state a blocker nor a preference "
            f"— they describe the world rather than asking for or blocking "
            f"something, so MoSCoW does not bucket them. Listed below in "
            f"rank order; not scored out here."
        ))
    if len(findings) > len(rows):
        out.append(_p(
            f"The {len(findings) - len(rows)} findings below these are "
            f"ranked in the list that follows, but not bucketed out here — a "
            f"table this long stops being one."
        ))
    return "".join(out)


def _funnel_section(considered: int, kept: int) -> str:
    """How many themes were found, and how many bear on the goal.

    THE FIRST THING A FILTERED LIST OWES ITS READER. Apurva's reference memo
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
        "<h2>What bears on this goal</h2>",
        _p(
            f"<strong>{considered} themes were found. {kept} bear on this "
            f"goal.</strong> The other {aside} are listed at the end with the "
            f"reason each was set aside — they are not gone, and a theme set "
            f"aside for this goal may be the answer to a different one."
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
        f"other {remaining} were never judged and are kept in the list above "
        f"— unjudged, not irrelevant."
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
        f"<h2>Considered and set aside for this goal ({len(pairs)})</h2>",
        _p(
            "Each of these was found and ranked like the findings above. They "
            "are here because they do not bear on the goal as you defined it, "
            "not because the evidence was weak."
        ),
    ]
    shown = pairs[:MAX_OVERFLOW_ROWS]
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
        "<table><thead><tr>"
        "<th>Theme</th><th>What it is</th><th>Worth this cycle</th>"
        "<th>Why it was set aside</th>"
        "</tr></thead><tbody>" + rows + "</tbody></table>"
    )
    if len(pairs) > len(shown):
        # NO SILENT CAPS. A list that stops without saying so reads as the
        # whole set.
        out.append(_p(
            f"and {len(pairs) - len(shown)} further themes set aside, not "
            f"listed here because the document has a size limit"
        ))
    return "".join(out)


def _headline_unsized_coverage(findings: list[dict]) -> str:
    """How much of the unsized disclosure the headline has already made.

    THE HEADLINE RUNS IMMEDIATELY ABOVE THE FINDINGS LEDE, and both were
    written to disclose the same two facts — HOW MANY findings have no size,
    and that a missing size is not a small one. In a real report that read:

        …257 of these could not be sized at all, and a missing size is not a
        small one — so this is the largest known size, not necessarily the
        largest thing here.

        Ranked by reach — how many accounts each theme touches, and 257 of them
        could not be sized at all. An unsized theme sorts last without being
        small: its size is unknown, not zero.

    Two paragraphs, three lines apart, making the same point twice. Feedback:
    "poorly formatted (not human readable), lots of irrelevant information".

    THREE STATES, NOT TWO, and the third is the reason this is not a boolean.
    The headline's branches do not all say the same amount:

      "full"    — a SIZED top row. It names the count AND the caveat, so the
                  lede has nothing left to add.
      "caveat"  — an UNSIZED top row with sized rows below it. It says "a
                  missing size is not a small one" and NEVER NAMES THE COUNT.
                  A boolean here suppressed the whole lede clause and silently
                  dropped "257 of them could not be sized" from the document —
                  de-duplication quietly turning into data loss, which is the
                  one thing this file exists to prevent.
      "none"    — nothing anywhere could be sized. The headline says something
                  else entirely and the lede is the only place the fact lives.

    Both callers read THIS function rather than re-deriving the branch, because
    the pair that drifted last time drifted precisely by each computing its own
    version of the same fact.
    """
    if not findings:
        return "none"
    if not any(f.get("impact_value") is None for f in findings):
        return "none"
    if findings[0].get("impact_value") is not None:
        return "full"
    if any(f.get("impact_value") is not None for f in findings):
        return "caveat"
    return "none"


def _headline_section(findings: list[dict]) -> str:
    out = ["<h2>The short version</h2>"]
    if not findings:
        out.append(_p(
            "Nothing survived verification. What was considered is listed "
            "below with the reason it was dropped — that list, not this "
            "silence, is the result of this run. Where more was considered "
            "than the list can hold, the remainder is counted with it rather "
            "than folded in as though it were one more candidate."
        ))
        return "".join(out)

    top = findings[0]
    out.append(_p(f"<strong>{_esc_statement(top)}</strong>"))
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
            "It is the largest of the ones that could be sized: "
            f"{_esc(_reach(top))}" + lead
            + ". " + ("One of these" if unsized == 1 else f"{unsized} of these")
            + " could not be sized at all, and a missing size is not a small "
              "one — so this is the largest known size, not necessarily the "
              "largest thing here."
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
        # Nothing anywhere could be sized. The size term is then constant and
        # the sort is strictly confidence-descending — which is a real order,
        # just not the one the heading implies. Saying "arbitrary" here was
        # itself false.
        tail = (
            "It is listed first" + lead
            + ". Nothing in this reading could be sized, so these are ordered "
              "by confidence rather than by size — the order says how sure "
              "each one is, not how big."
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


def _clip(text: str, limit: int) -> str:
    """`text`, bounded, cut on a word boundary."""
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[:limit].rsplit(" ", 1)[0] + "…"


def _esc_clipped(value: Any, limit: int) -> str:
    """Escaped text whose ESCAPED length is <= `limit`.

    `_clip` then `_esc` bounds the wrong string: escaping expands, and the
    worst case is 6x (`"` -> `&quot;`), so a 400-char clip can still emit 2,400
    characters. Every size claim in this module is about rendered bytes, so the
    bound has to be applied to the rendered form.

    Cutting escaped text can land inside an entity and emit `&am`, so a trailing
    partial entity is dropped. An entity is at most 6 characters, hence the
    window.
    """
    out = _esc(_clip(value if isinstance(value, str) else str(value or ""), limit))
    if len(out) <= limit:
        return out
    out = out[:limit]
    amp = out.rfind("&")
    if amp != -1 and ";" not in out[amp:] and len(out) - amp <= 6:
        out = out[:amp]
    return out


def _statement_text(finding: dict) -> str:
    """A finding's statement, bounded, cut on a word boundary."""
    return _clip(finding.get("statement") or "", MAX_STATEMENT_CHARS)


def _esc_statement(finding: dict) -> str:
    """The statement, escaped, with the BOUND ON THE ESCAPED length."""
    return _esc_clipped(finding.get("statement") or "", MAX_STATEMENT_CHARS)



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


def _finding_block(
    finding: dict, rank: int, *,
    shared_weakest: bool = False, shared_cap: bool = False,
    shared_assumptions: bool = False,
    account_value: Any = None,
) -> str:
    # THE THEME IS THE HEADING. It used to be the whole sentence — "30 claims
    # across 11 accounts concern “Sales Pipeline” — for example, “…”" — so the
    # one word a reader scans for sat mid-clause, in quotes, behind two numbers
    # that the chips on the next line repeat verbatim. Heading, chips, quote:
    # each fact once, in the place it is looked for.
    #
    # FALLS BACK TO THE SENTENCE when there is no label, which is every run
    # stored before this shipped and every fixture that predates it. A card with
    # an empty heading would be a worse regression than the run-on it replaced.
    label = (finding.get("label") or "").strip()
    head = (
        _esc_clipped(label, MAX_STATEMENT_CHARS) if label
        else _esc_statement(finding)
    )
    out = [f"<h3>{rank}. {head}</h3>"]

    # ── WHAT TO DO, FIRST. ─────────────────────────────────────────────────
    #
    # Apurva, on a real report: "this is only the issues, no suggestion on how
    # to solve or what's the exact recommendation from it". So the suggestion
    # leads the card and its justification sits directly under it — a reader
    # who stops after two lines has the actionable half.
    #
    # ABSENT IS NORMAL, not an error. Only the top findings get one, and any
    # suggestion that quoted a figure, promised an outcome or failed the lint
    # was dropped rather than repaired. The card then reads exactly as it did
    # before, which is a document that says nothing it cannot stand behind.
    # THE DEEP PASS TAKES PRECEDENCE. A finding in the deep set also has a
    # flat `recommendation` (the same `relevant` findings feed both LLM
    # calls), and showing both would put two suggestions on one finding —
    # rendered once, as the deeper of the two.
    deep = _as_dict(finding.get("deep_recommendation"))
    rec = _as_dict(finding.get("recommendation"))
    deep_action = (deep.get("action") or "").strip()
    deep_because = (deep.get("because") or "").strip()
    action = (rec.get("action") or "").strip()
    because = (rec.get("because") or "").strip()
    if deep_action and deep_because:
        # A DIFFERENT HEADER FROM THE FLAT PASS BELOW, deliberately. Both used
        # to say the identical "Recommended." — the only visible discriminator
        # was whether a "What to change" list happened to follow, which a
        # reader has no reason to go looking for. This is the deeper of the
        # two passes — the full write-up, not a one-liner — and it says so.
        out.append(_p(
            f"<strong>Recommended — the full write-up.</strong> "
            f"{_esc_clipped(deep_action, MAX_STATEMENT_CHARS)}"
        ))
        out.append(_p(
            f"<em>Why.</em> {_esc_clipped(deep_because, MAX_STATEMENT_CHARS)}"
        ))
        changes = [
            c for c in _as_list(deep.get("changes"))
            if isinstance(c, dict) and (c.get("text") or "").strip()
        ]
        if changes:
            out.append("<p><strong>What to change.</strong></p>")
            out.append(_ul(
                f"{_esc_clipped(c.get('text'), MAX_STATEMENT_CHARS)} "
                f"<em>— from: “"
                f"{_esc_clipped(c.get('cited_claim'), MAX_PARAM_BASIS_CHARS)}"
                f"”</em>"
                for c in changes[:MAX_DEEP_CHANGES]
            ))
        open_qs = [
            q for q in _as_list(deep.get("open_questions"))
            if isinstance(q, str) and q.strip()
        ]
        if open_qs:
            out.append("<p><strong>Still open.</strong></p>")
            out.append(_ul(
                _esc_clipped(q, MAX_STATEMENT_CHARS)
                for q in open_qs[:MAX_DEEP_OPEN_QUESTIONS]
            ))
        falsify = (deep.get("what_would_falsify") or "").strip()
        if falsify:
            out.append(_p(
                f"<em>Would change this if.</em> "
                f"{_esc_clipped(falsify, MAX_STATEMENT_CHARS)}"
            ))
        comparison = (deep.get("comparison") or "").strip()
        if comparison:
            out.append(_p(
                f"<strong>Why this over the next.</strong> "
                f"{_esc_clipped(comparison, MAX_STATEMENT_CHARS)}"
            ))
    elif action and because:
        out.append(_p(
            f"<strong>Recommended.</strong> "
            f"{_esc_clipped(action, MAX_STATEMENT_CHARS)}"
        ))
        out.append(_p(
            f"<em>Why.</em> {_esc_clipped(because, MAX_STATEMENT_CHARS)}"
        ))
        # THE SHORTFALL, CONNECTED TO THE FINDING IT ACTUALLY DROPPED — not
        # left as a bare fact in "How many got a full recommendation" while
        # this card sits below it looking like an unexplained absence.
        # `deep_attempted` is only set (`routes/crucible.py`'s
        # `_run_enrichment`) on a finding that was IN the top N a count named
        # or defaulted to, but whose evidence did not clear the citation gate
        # (or a deep pass that failed outright) — never on a finding that was
        # simply ranked past N, which never had a full write-up coming. The
        # specific reason lives once, in `_recommendation_basis_section`
        # above; this points there rather than restating it, so the two can
        # never drift apart.
        if finding.get("deep_attempted"):
            out.append(_p(
                "<em>This finding was one of the ones in line for a full "
                "write-up. It did not get one this run — see “How many got "
                "a full recommendation” above for why — so the "
                "recommendation above is the plain version, not a "
                "downgrade of a deeper one you are missing.</em>"
            ))

    meta = [_esc(_reach(finding))]
    band = (finding.get("confidence_band") or "").strip()
    if band:
        meta.append(f"{_esc(band)} confidence")
    if (finding.get("adjudication") or "") == "conflict":
        # Never softened into a footnote. Two sources that may both speak
        # disagreeing is the single most decision-relevant thing a run can
        # find, which is also why `_rank` puts it first regardless of size.
        meta.append("<strong>sources disagree</strong>")
    claims = len(_as_list(finding.get("claim_ids")))
    if claims:
        meta.append(f"{claims} claim{'' if claims == 1 else 's'}")
    out.append(_p(" · ".join(meta)))

    # NAMED EVIDENCE, NEVER A PROJECTION. A grounded dollar figure — a
    # number a customer actually stated, in a call OR in any other
    # connected source's text — is sized differently from `_reach` above:
    # it is a SUM of real, quoted amounts across the accounts that named
    # one, not the finding's own scored `Impact.value` (which stays
    # reach-based/unsized exactly as before this evidence existed).
    # Rendered as its own line, in its own words, so a reader cannot
    # mistake "customers named $X" for "this is worth $X" — the
    # distinction the evidence exists to preserve.
    #
    # NEVER NAMES A CHANNEL. `native_units` carries the SUM and the account
    # count, not which connector(s) the contributing claims came from —
    # that provenance is not threaded this far, and guessing it here (e.g.
    # always saying "on calls") would assert something this function
    # cannot actually know. A grounded figure is captured identically from
    # a call, a Slack thread, an email or any other connected text (see
    # `app.graph.extractor`'s open-extraction path); the sentence stays
    # true regardless of which one it was by never naming one. Naming the
    # actual source(s) would need that provenance carried through
    # `pipeline`/`routes.crucible` into `native_units` first — a real
    # follow-up, not something to guess at render time.
    commercial = _as_dict(_as_dict(finding.get("impact")).get("native_units"))
    commercial_usd = commercial.get("commercial_committed_usd")
    if isinstance(commercial_usd, (int, float)):
        accounts_n = commercial.get("commercial_grounded_accounts")
        accounts_txt = (
            f" across {int(accounts_n)} named account{'' if accounts_n == 1 else 's'}"
            if isinstance(accounts_n, (int, float)) and accounts_n
            else ""
        )
        # PROVENANCE IS PART OF THE CLAIM, NOT A FOOTNOTE. A figure recovered
        # from a written summary is not the same evidence as one captured
        # against a verified verbatim quote: the summary was itself written
        # under a grounding gate, so the number came from real text, but it
        # was copied once more than a quoted figure was and could have been
        # copied wrong. "Customers named $X" is only true of the quoted kind.
        #
        # Proportionate, and specific about WHICH risk. The exposure is
        # transcription error, not invention, so a blanket "this may be
        # unreliable" would overstate it — and saying nothing at all would
        # let a derived figure wear a quoted figure's credibility, which is
        # the promise this line was making and not keeping.
        derived_usd = commercial.get("commercial_committed_usd_derived")
        derived_usd = (
            float(derived_usd) if isinstance(derived_usd, (int, float)) else 0.0
        )
        if derived_usd >= commercial_usd:
            out.append(_p(
                f"<strong>Figures stated in the source material.</strong> "
                f"${commercial_usd:,.0f}{accounts_txt} — a sum of figures "
                f"stated, not a projection. These were read back from written "
                f"summaries rather than matched to a verified quote, so each "
                f"is only as accurate as the summary it came from."
            ))
        elif derived_usd:
            out.append(_p(
                f"<strong>Customers named this evidence.</strong> Customers "
                f"named ${commercial_usd:,.0f}{accounts_txt} — a sum of "
                f"figures actually quoted, not a projection. "
                f"${derived_usd:,.0f} of that was read back from written "
                f"summaries rather than matched to a verified quote."
            ))
        else:
            out.append(_p(
                f"<strong>Customers named this evidence.</strong> Customers "
                f"named ${commercial_usd:,.0f}{accounts_txt} — a sum of "
                f"figures actually quoted, not a projection."
            ))

    # ONE CLAIM, IN ITS SOURCE'S OWN WORDS, set as a quote.
    #
    # Only when the heading is the label: with the sentence as the heading the
    # quote is already inside it, and repeating it would be the duplication this
    # whole pass is removing. `example` is empty whenever the statement fell
    # back to its plain form, so this is silent exactly when there is nothing to
    # show.
    example = (finding.get("example") or "").strip()
    if label and example:
        out.append(
            f"<blockquote>\u201c{_esc_clipped(example, MAX_STATEMENT_CHARS)}"
            f"\u201d</blockquote>"
        )

    confidence = _as_dict(finding.get("confidence"))
    # The weakest leg is the ACTIONABLE half of a confidence score: it says
    # what to go and find out, which a band on its own never does.
    # SUPPRESSED WHEN IT IS THE SAME SENTENCE ON EVERY ROW. A corpus with no
    # outcome evidence anywhere gives every finding an identical weakest link
    # and an identical cap. Printing it on all 32 reads as 32 separate
    # judgements about 32 different themes when it is ONE fact about the
    # corpus — so the section states it once and the rows carry what actually
    # differs between them. Repetition is not thoroughness: a reader skims an
    # identical sentence after the third row and stops seeing it, which is how
    # a genuine per-finding difference would go unnoticed later.
    if confidence.get("weakest_leg_reason") and not shared_weakest:
        out.append(_p(
            f"<strong>Weakest link.</strong> {_esc(confidence['weakest_leg_reason'])}"
        ))
    if confidence.get("cap_reason") and not shared_cap:
        out.append(_p(_esc(confidence["cap_reason"])))

    # WHERE IT CAME FROM, beside the claim it supports. Without this a reader
    # cannot check a single finding against anything, which is the difference
    # between an argument and an assertion.
    # BOUNDED HERE, not only at write time. `pipeline.MAX_NAMED_SOURCES` caps
    # what new runs store, but a document name is tenant text of any length and
    # rows already on disk predate every cap. This was the one string in the
    # block that nothing truncated: a 255-char name pushed a block to 2,179
    # against a declared ceiling of 2,000, and the import-time assertion that
    # was supposed to catch that compared constants to each other and passed.
    surfaced = [s for s in _as_list(finding.get("surfaced_by")) if s]
    if surfaced:
        shown = [_esc_clipped(s, MAX_SOURCE_NAME_CHARS)
                 for s in surfaced[:MAX_RENDERED_SOURCES]]
        extra = len(surfaced) - len(shown)
        tail = f" (+{extra} more)" if extra > 0 else ""
        out.append(_p(
            "<strong>Source documents</strong> " + " · ".join(shown) + tail
        ))

    # I8: every assumed parameter is disclosed WHERE THE NUMBER IS READ, not in
    # a methodology page nobody opens.
    # Bounded for the same reason as `surfaced_by`: `name` and `basis` are
    # tenant strings with no cap anywhere upstream. Measuring found a single
    # block reaching 41,745 characters, almost all of it here — I8 requires the
    # assumption be DISCLOSED, not reproduced at any length.
    assumed = [a for a in _as_list(finding.get("assumed_params")) if isinstance(a, dict)]
    # Hoisted to the top of the section when every finding says the same thing;
    # see `_shared_assumptions`. Suppressed HERE rather than emptied upstream so
    # the finding row itself is untouched and the two renderers cannot disagree
    # about what a finding assumed.
    if shared_assumptions:
        assumed = []
    if assumed:
        shown = assumed[:MAX_ASSUMED_PARAMS]
        out.append(_ul(
            f"<strong>{_esc_clipped(a.get('name'), MAX_PARAM_NAME_CHARS)}</strong>"
            f": {_esc_clipped(a.get('basis'), MAX_PARAM_BASIS_CHARS)}"
            for a in shown
        ))
        if len(assumed) > len(shown):
            out.append(_p(
                f"and {len(assumed) - len(shown)} further assumed parameters"
            ))

    # SIZED ON THE READER'S OWN ESTIMATE, DISPLAY-ONLY — NEVER SCORING.
    # `Impact.value`/ranking read the frozen reach exactly as before; this is
    # commentary alongside an unchanged order, not a new sizing input. David's
    # own words: "Reach is the unit, not points against revenue."
    #
    # LAST IN THE CARD, AND ITS OWN PARAGRAPH — deliberately far from the
    # grounded committed-money paragraph above, which is a SUM of figures a
    # customer actually stated. That number is a fact; this one is the
    # reach this finding already shows, multiplied by a figure the reader
    # typed at the plan gate. Putting them at opposite ends of the same card,
    # each in its own sentence with its own framing, is the same
    # non-additivity discipline `_findings_section`'s list-pricing paragraph
    # already follows for the corpus-wide figures: two numbers that must
    # never be added are never adjacent enough to invite it.
    #
    # SKIPPED ENTIRELY FOR AN UNSIZED FINDING (I3): there is no reach to
    # multiply, and no size should be invented for a theme this run could not
    # measure. `_finding_money_estimate` returns `None` on its own guard
    # (no `account_value`, or one that is zero/non-numeric) — this is the
    # ADDITIONAL guard I3 needs, on the finding's own reach.
    if finding.get("impact_value") is not None:
        reach_n = float(finding.get("impact_value") or 0)
        estimate = _finding_money_estimate(reach_n, account_value)
        if estimate:
            out.append(_p(f"<strong>On your own estimate.</strong> {estimate}."))
    return "".join(out)


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
MAX_DETAILED_FINDINGS = MAX_RICE_ROWS

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

    Apurva's own example memo opens this section with "We tell customers
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

    CUT BEFORE THE FINDING'S OWN QUOTE, not after it. `pipeline.py`'s
    statement-builder embeds a supporting quote at "— for example,
    “…”" precisely when a finding has no `label` — and a labelless
    finding's own `<h3>` card (`_finding_block`) falls back to that SAME
    statement for ITS heading. Reusing the whole sentence here would put the
    identical quoted words in two headings back to back, about the same
    theme — the exact duplication `Finding.label`'s own docstring exists to
    avoid ("a terrible thing to SCAN"). Cutting at the same clause boundary
    leaves the claim here and the quote where it already is: in the card
    below, or in its blockquote.
    """
    statement = (findings[0].get("statement") or "").strip()
    core = statement.split("— for example,", 1)[0].strip()
    claim = _esc_clipped(core or statement, MAX_STATEMENT_CHARS)
    if len(findings) > 1:
        return f"{claim} — the strongest of {len(findings):,} findings below"
    return claim


def _findings_section(
    findings: list[dict],
    # The EDITORIAL cap is the default, so a future caller that forgets to pass
    # one gets a memo rather than the 150-block dump this replaced.
    full_cap: int = MAX_DETAILED_FINDINGS,
    overflow_cap: int = MAX_OVERFLOW_ROWS,
    plan: Optional[dict] = None,
) -> str:
    if not findings:
        return ""
    # THE SAME NUMBER THE STAT STRIP AND DECISION BOX ALREADY MULTIPLY BY,
    # read once here rather than re-derived per finding — see
    # `_finding_money_estimate`, which does the actual guarding (absent,
    # zero, non-numeric all fall through to no line at all).
    account_value = _as_dict(plan).get("account_value")
    out = [f"<h2>{_findings_heading(findings)}</h2>"]
    # THE HEADING HAS TO AGREE WITH THE HEADLINE. Fixing only the summary left
    # one document reading "not ordered by size at all" and, two lines later,
    # "Ranked by reach" — a fix that stopped at its own boundary.
    #
    # Computed ONCE and read by both the lede and the overflow paragraph below,
    # because those two are the pair that drifted: the overflow line called the
    # remainder "ranked lower by reach" while the lede three paragraphs up had
    # just said nothing here had a reach at all.
    anything_sized = any(f.get("impact_value") is not None for f in findings)
    unsized = sum(1 for f in findings if f.get("impact_value") is None)
    # ONE FACT ABOUT THE CORPUS, OR MANY ABOUT THE FINDINGS? Detected, never
    # assumed: the moment a run produces two different weakest links they both
    # go back on their own rows, where they belong. Only a single distinct
    # value across MORE THAN ONE finding is a corpus-wide statement.
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
    # SAID ONCE, BY WHICHEVER SECTION GETS THERE FIRST — but only the part the
    # headline actually said; see `_headline_unsized_coverage`. When it named
    # the caveat without the count, the count is still this paragraph's to
    # make.
    covered = _headline_unsized_coverage(findings)
    if unsized and covered == "full":
        unsized_clause = ""
    elif unsized and covered == "caveat":
        unsized_clause = (
            f", and {'one' if unsized == 1 else str(unsized)} of them could "
            f"not be sized at all"
        )
    elif unsized:
        unsized_clause = (
            f", and {'one' if unsized == 1 else str(unsized)} of them could "
            f"not be sized at all. An unsized theme sorts last without "
            f"being small: its size is unknown, not zero"
        )
    else:
        unsized_clause = ""
    if anything_sized:
        out.append(_p(
            "Ranked by reach — how many accounts each theme touches"
            + unsized_clause
            + ". An authoritative disagreement is placed above everything that "
              "is not one, because two sources that may both speak "
              "contradicting each other is worth more than either of them "
              "alone."
        ))
    else:
        # AND SAY WHETHER THAT ORDER CARRIES ANYTHING. `_rank`'s last term is a
        # confidence SCORE, which is real and is never rendered — the reader
        # sees bands. On a corpus with no outcome evidence anywhere every band
        # comes out the same, so "ordered by confidence" describes an ordering
        # they cannot check against a single thing on the page, and a list that
        # LOOKS ranked gets read as ranked. Position is the most persuasive
        # thing in a document; claiming it means something it does not is the
        # same defect as the headline calling an unsized row the largest.
        bands = {(f.get("confidence_band") or "").strip() for f in findings}
        one_band = len(bands) == 1 and len(findings) > 1
        out.append(_p(
            "Not ranked by reach: nothing here could be sized, so these are "
            "ordered by confidence."
            + (
                " Every finding here carries the same confidence band, so that "
                "order rests on a score this report does not show you — read "
                "the position as a place in a list, not as a verdict on which "
                "matters more."
                if one_band else ""
            )
            + " An authoritative disagreement is still "
              "placed above everything that is not one, because two sources that "
              "may both speak contradicting each other is worth more than either "
              "of them alone."
        ))

    if shared_weakest:
        out.append(_p(
            "<strong>Every finding below has the same weakest link</strong>, so "
            "it is stated here once rather than repeated on each of them: "
            + _esc(shared_weakest)
            # A CLAUSE, NOT A NEW SENTENCE. `cap_reason` arrives uncapitalised
            # ("capped at medium: …"), so a full stop before it rendered
            # "…the diagnosis are not. capped at medium".
            + (f"; {_esc(shared_cap)}." if shared_cap else ".")
        ))
    elif shared_cap:
        out.append(_p(
            "<strong>Every finding below is capped the same way</strong>, so it "
            "is stated here once rather than on each of them: "
            + _esc(shared_cap) + "."
        ))

    shared_assumptions, shared_count = _shared_assumptions(findings)
    if shared_assumptions:
        many = len(shared_assumptions) > 1
        # SAYS HOW MANY IT SPEAKS FOR. "Every finding below" is false when only
        # the sized ones carry an assumption, and a hoisted sentence that
        # overstates its own scope is worse than the repetition it replaced.
        subject = (
            "Every finding below rests on the same assumption"
            if shared_count == len(findings)
            else f"{shared_count} of the findings below rest on the same "
                 f"assumption"
        )
        out.append(_p(
            f"<strong>{subject}{'s' if many else ''}</strong>, so "
            + ("they are" if many else "it is")
            + " stated here once rather than repeated on each of them:"
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
    # sixteen times is not $480,000. Three things keep them apart, all
    # deliberate:
    #
    #   * a RANGE and a SUM are structurally non-additive, so the arithmetic
    #     a reader might attempt has no obvious form;
    #   * this is its own paragraph in its own place, never a clause beside a
    #     committed figure — two numbers in one sentence is an invitation to
    #     add them;
    #   * each says which KIND of money it is in its own words, rather than
    #     leaving a reader to infer it from the number.
    #
    # No total is printed here, and none should be added later.
    pricing = _list_pricing(findings)
    if pricing is not None:
        low, high, carrying = pricing
        span = (
            f"${low:,.0f}" if low == high else f"${low:,.0f}–${high:,.0f}"
        )
        # SAYS HOW MANY IT SPEAKS FOR, the same way the assumptions hoist
        # above does — a hoisted sentence that overstates its own scope is
        # the failure this whole pass has been correcting.
        where = (
            "one finding below"
            if carrying == 1 else
            f"{carrying} of the findings below"
        )
        out.append(_p(
            f"<strong>List pricing was quoted in {where}.</strong> {span}. "
            f"This is what was quoted, not what was agreed — the same price "
            f"offered to several accounts is one rate card, so these are "
            f"never added together or added to any figure above."
        ))

    full = findings[:full_cap]
    rest = findings[full_cap:]
    out.extend(
        _finding_block(
            f, i + 1,
            shared_weakest=bool(shared_weakest), shared_cap=bool(shared_cap),
            shared_assumptions=bool(shared_assumptions),
            account_value=account_value,
        )
        for i, f in enumerate(full)
    )

    if rest:
        # SAID PLAINLY, where the reader is. A document that stopped at 150
        # without a word would read as "these are all the findings", which is
        # exactly the quiet degradation the coverage notes exist to prevent.
        listed = rest[:overflow_cap]
        # WRITTEN FROM `listed`, NOT `rest`. Keyed off `rest` this paragraph
        # promised "the remaining 681 are listed below … nothing has been
        # dropped" and was then followed by 400 rows and a sentence conceding
        # 281 were missing. The document contradicted itself in two adjacent
        # paragraphs, on the very run cited as evidence that it was fine.
        out.append(_p(
            f"The next {len(listed)} findings are listed below in rank order "
            f"rather than in full — a full write-up is reserved for the "
            f"{len(full)} the ranking put first"
            + (
                " and they rank lower by reach" if anything_sized
                else " — they rank lower by confidence, not by size, which "
                     "nothing here had"
            )
            + ". Every one of them is still on the run itself."
        ))
        rows = []
        for offset, f in enumerate(listed, start=len(full) + 1):
            statement = _esc_statement(f)
            rows.append(f"<li>{offset}. {statement}</li>")
        out.append("<ul>" + "".join(rows) + "</ul>")
        # The list itself grows with the run — at 831 findings it alone spent
        # ~95,000 characters against a 90,000 budget. Bounded, with the
        # remainder COUNTED rather than dropped in silence.
        beyond = len(rest) - len(listed)
        if beyond > 0:
            out.append(_p(
                _further_findings_sentence(beyond)
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
            "This reading did not test these. It reports what it found, and "
            "nothing above was matched against what you wrote here — so their "
            "absence from the findings is not evidence against them."
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
        "accounts it touches — and it does not produce a point estimate, an "
        "effort figure, a prioritisation score or a significance test, because "
        "nothing it read carries the numbers those need. Where you expected "
        "one of those, this is why it is absent."
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
            out.append(_p(
                f"Not answerable here, because "
                f"{_esc_clipped(gap.get('because'), MAX_GAP_CHARS)}."
            ))
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

    def _assemble(full_cap: int, overflow_cap: int) -> str:
        parts = [
            f"<h1>{_esc_clipped(goal, MAX_STATEMENT_CHARS) or 'Goal analysis'}</h1>",
            _definition_section(run, plan),
            _what_was_read_section(run, plan),
            _stat_strip(plan, findings, kept),
            _decision_section(plan, kept),
            _funnel_section(len(findings), len(kept)),
            _relevance_coverage_section(relevance_judged_info),
            _framework_section(kept, plan),
            _headline_section(kept),
            _recommendation_basis_section(recommendation_basis),
            _findings_section(kept, full_cap, overflow_cap, plan),
            _set_aside_section(set_aside),
            _hypotheses_section(plan),
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
        html = _assemble(min(MAX_DETAILED_FINDINGS, full_cap), overflow_cap)
        if len(html) <= _BODY_LIMIT:
            return html
    return html


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
