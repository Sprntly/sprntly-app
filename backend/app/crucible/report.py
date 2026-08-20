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
    out = ["<h2>What this was asked to establish</h2>"]
    if definition:
        out.append(_p("You confirmed this goal means, in your own words:"))
        out.append(f"<blockquote>{_esc(definition)}</blockquote>")
        out.append(_p(
            "Everything below is measured against that sentence and nothing "
            "else. If it is not what you meant, the ranking will be wrong in a "
            "way no amount of evidence can correct."
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


def _headline_section(findings: list[dict]) -> str:
    out = ["<h2>The short version</h2>"]
    if not findings:
        out.append(_p(
            "Nothing survived verification. Everything that was considered is "
            "listed below with the reason it was dropped — that list, not this "
            "silence, is the result of this run."
        ))
        return "".join(out)

    top = findings[0]
    out.append(_p(f"<strong>{_esc(top.get('statement'))}</strong>"))
    band = (top.get("confidence_band") or "").strip()
    claims = len(_as_list(top.get("claim_ids")))
    tail = (
        f"It is the largest thing this reading found: {_esc(_reach(top))}"
        + (f", at {_esc(band)} confidence" if band else "")
        + (f", resting on {claims} claim{'' if claims == 1 else 's'}" if claims else "")
        + ". Largest by how much of your book it touches — not by how much it "
          "would move the metric, which this reading cannot compute."
    )
    out.append(_p(tail))
    return "".join(out)


def _finding_block(finding: dict, rank: int) -> str:
    out = [f"<h3>{rank}. {_esc(finding.get('statement'))}</h3>"]

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

    confidence = _as_dict(finding.get("confidence"))
    # The weakest leg is the ACTIONABLE half of a confidence score: it says
    # what to go and find out, which a band on its own never does.
    if confidence.get("weakest_leg_reason"):
        out.append(_p(
            f"<strong>Weakest link.</strong> {_esc(confidence['weakest_leg_reason'])}"
        ))
    if confidence.get("cap_reason"):
        out.append(_p(_esc(confidence["cap_reason"])))

    # WHERE IT CAME FROM, beside the claim it supports. Without this a reader
    # cannot check a single finding against anything, which is the difference
    # between an argument and an assertion.
    surfaced = [s for s in _as_list(finding.get("surfaced_by")) if s]
    if surfaced:
        out.append(_p(
            "<strong>Source documents</strong> "
            + " · ".join(_esc(s) for s in surfaced)
        ))

    # I8: every assumed parameter is disclosed WHERE THE NUMBER IS READ, not in
    # a methodology page nobody opens.
    assumed = [a for a in _as_list(finding.get("assumed_params")) if isinstance(a, dict)]
    if assumed:
        out.append(_ul(
            f"<strong>{_esc(a.get('name'))}</strong>: {_esc(a.get('basis'))}"
            for a in assumed
        ))
    return "".join(out)


def _findings_section(findings: list[dict]) -> str:
    if not findings:
        return ""
    out = [f"<h2>What the evidence says ({len(findings)})</h2>"]
    out.append(_p(
        "Ranked by reach — how many accounts each theme touches. An "
        "authoritative disagreement is placed first regardless of size, "
        "because two sources that may both speak contradicting each other is "
        "worth more than either of them alone."
    ))
    out.extend(_finding_block(f, i + 1) for i, f in enumerate(findings))
    return "".join(out)


def _hypotheses_section(plan: dict) -> str:
    hypotheses = [h for h in _as_list(plan.get("hypotheses")) if h]
    if not hypotheses:
        return ""
    return "".join([
        "<h2>What you already believed</h2>",
        _ul(_esc(h) for h in hypotheses),
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
    # ALWAYS EXPANDED HERE, unlike the panel. The panel folds a long ledger
    # behind a `<details>` so it cannot push the limits section off the screen;
    # `<details>` is not on the artifact allowlist and would be unwrapped into
    # a permanently-open list anyway, so rather than pretend, this states the
    # count and prints the list. In a document a reader scrolls, that is the
    # honest shape.
    return "".join([
        f"<h2>Considered and ruled out ({len(ledger)})</h2>",
        _p(
            "A ranking whose rejections are invisible is a ranking you have to "
            "take on faith. Each of these was a candidate and each one died "
            "for a stated reason."
        ),
        _ul(
            f"<strong>{_esc(r.get('label'))}</strong> — {_esc(r.get('reason'))}"
            + (
                f" <em>(stopped at {_esc(r.get('stopped_at_stage'))})</em>"
                if r.get("stopped_at_stage") else ""
            )
            for r in ledger
        ),
    ])


def _limits_section(plan: dict) -> str:
    out = ["<h2>What this cannot tell you</h2>"]
    out.append(_p(
        "This reading is qualitative. It sizes a theme by reach — how many "
        "accounts it touches — and it does not produce a point estimate, an "
        "effort figure, a prioritisation score or a significance test, because "
        "nothing it read carries the numbers those need. Where you expected "
        "one of those, this is why it is absent."
    ))
    gaps = [g for g in _as_list(plan.get("cannot_answer")) if isinstance(g, dict)]
    if gaps:
        # Built from the run PLAN's own gaps, so what the user was warned about
        # BEFORE the run is what they are reminded of after it.
        for gap in gaps:
            out.append(_p(f"<strong>{_esc(gap.get('question'))}</strong>"))
            out.append(_p(f"Not answerable here, because {_esc(gap.get('because'))}."))
            out.append(_p(f"<em>To close it</em> {_esc(gap.get('remedy'))}"))
    else:
        out.append(_p(
            "This run recorded no list of its own gaps, which does not mean it "
            "had none — only that it predates the step that states them."
        ))
    return "".join(out)


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
    if plan is None:
        plan = _as_dict(_as_dict(run.get("prioritisation")).get("plan"))
    plan = _as_dict(plan)

    goal = (run.get("goal_text") or "").strip()
    parts = [
        f"<h1>{_esc(goal) or 'Goal analysis'}</h1>",
        _definition_section(run, plan),
        _what_was_read_section(run, plan),
        _headline_section(findings),
        _findings_section(findings),
        _hypotheses_section(plan),
        _ledger_section(ledger),
        _limits_section(plan),
    ]
    return "".join(p for p in parts if p)


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
