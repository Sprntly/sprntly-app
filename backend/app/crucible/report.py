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
        # WHAT THIS SENTENCE ACTUALLY GOVERNS. The previous text — "everything
        # below is measured against that sentence and nothing else" — is the
        # exact claim the limits section now denies: claim selection never sees
        # the definition (`build_findings` takes a `goal_accounts` filter that
        # production does not pass). Leaving it here would have put the
        # falsehood three sections ABOVE its own correction, in the more
        # prominent position, which is worse than never having written the
        # disclosure.
        out.append(_p(
            "This is the sentence the run was given to work from, and it is "
            "recorded here so a decision can be defended against it. It did "
            "not decide which findings appear below — nothing here was "
            "filtered or ranked by it. If it is not what you meant, say so "
            "before you rely on any of this."
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



def _finding_block(
    finding: dict, rank: int, *,
    shared_weakest: bool = False, shared_cap: bool = False,
) -> str:
    out = [f"<h3>{rank}. {_esc_statement(finding)}</h3>"]

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

#: An assumed parameter, as rendered. I8 wants it disclosed, not quoted whole.
MAX_ASSUMED_PARAMS = 8
MAX_PARAM_NAME_CHARS = 120
MAX_PARAM_BASIS_CHARS = 300

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


def _findings_section(
    findings: list[dict],
    full_cap: int = MAX_FULL_FINDING_BLOCKS,
    overflow_cap: int = MAX_OVERFLOW_ROWS,
) -> str:
    if not findings:
        return ""
    out = [f"<h2>What the evidence says ({len(findings)})</h2>"]
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
    if anything_sized:
        out.append(_p(
            "Ranked by reach — how many accounts each theme touches"
            + (
                f", and {'one' if unsized == 1 else str(unsized)} of them could "
                f"not be sized at all. An unsized theme sorts last without "
                f"being small: its size is unknown, not zero"
                if unsized else ""
            )
            + ". An authoritative disagreement is placed above everything that "
              "is not one, because two sources that may both speak "
              "contradicting each other is worth more than either of them "
              "alone."
        ))
    else:
        out.append(_p(
            "Not ranked by reach: nothing here could be sized, so these are "
            "ordered by confidence. An authoritative disagreement is still "
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

    full = findings[:full_cap]
    rest = findings[full_cap:]
    out.extend(
        _finding_block(
            f, i + 1,
            shared_weakest=bool(shared_weakest), shared_cap=bool(shared_cap),
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
            f"rather than in full, because the document has a size limit"
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
                f"A further {beyond} findings are on the run and are not "
                f"listed here, because this document has a size limit. They "
                f"were not dropped from the analysis."
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
    groups: dict[str, list[dict]] = {}
    for r in shown:
        groups.setdefault((r.get("reason") or "").strip(), []).append(r)
    # Biggest cause first, ties broken on the reason text so a re-run of the
    # same data renders the same document (the whole engine is deterministic;
    # a section that reordered itself would undermine that everywhere else).
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))

    out = [f"<h2>Considered and ruled out ({len(ledger)})</h2>"]
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
    if len(ledger) > len(shown):
        out.append(_p(
            f"{len(ledger) - len(shown)} further rejections are on the run and "
            f"are not listed here."
        ))
    return "".join(out)


def _limits_section(plan: dict) -> str:
    out = ["<h2>What this cannot tell you</h2>"]
    out.append(_p(
        "This reading is qualitative. It sizes a theme by reach — how many "
        "accounts it touches — and it does not produce a point estimate, an "
        "effort figure, a prioritisation score or a significance test, because "
        "nothing it read carries the numbers those need. Where you expected "
        "one of those, this is why it is absent."
    ))
    # WHICH FINDINGS APPEAR IS NOT DECIDED BY THE GOAL, and a reader cannot
    # tell that from the output — which is the problem. The definition gate
    # establishes what the goal means with some care, and then claim selection
    # never sees it: `_load_signals` reads the whole connected corpus and
    # `build_findings` is called with no goal argument at all. So a run about
    # enterprise churn returns export reliability and receipt-scanning accuracy
    # alongside anything that does bear on churn, with nothing marking which is
    # which.
    #
    # Stated rather than quietly left for the reader to notice, because the
    # alternative is a document that LOOKS like it answered the question it was
    # asked. This is the same rule as I3 and as the headline's superlative: do
    # not present something the run did not establish. The filter itself is
    # real work and is not pretended at here.
    # SAYS ONLY THE PART THAT IS TRUE. The first draft added "every theme in
    # the sources you approved is listed", which the same document contradicts
    # a section earlier — findings are capped, and anecdote / ungroupable /
    # refuted candidates never reach this list at all. The relevance claim is
    # solid on its own (`build_findings` has a `goal_accounts` parameter that
    # production never passes); the completeness claim was unearned, and
    # bundling them would have made the true half easy to dismiss.
    out.append(_p(
        "<strong>These findings were not selected for your goal.</strong> "
        "Nothing here was filtered or ranked by relevance to your definition — "
        "a theme appears because it is in the evidence you approved, not "
        "because it bears on what you asked about. Its presence is not a claim "
        "that it matters to this goal; judge that yourself."
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
    def _assemble(full_cap: int, overflow_cap: int) -> str:
        parts = [
            f"<h1>{_esc_clipped(goal, MAX_STATEMENT_CHARS) or 'Goal analysis'}</h1>",
            _definition_section(run, plan),
            _what_was_read_section(run, plan),
            _headline_section(findings),
            _findings_section(findings, full_cap, overflow_cap),
            _hypotheses_section(plan),
            _ledger_section(ledger),
            _limits_section(plan),
        ]
        return "".join(p for p in parts if p)

    # MEASURE, don't assert. See `_SHED_LADDER`. Deterministic: the same row
    # yields the same rung and therefore the same bytes, which is what
    # `body_fingerprint` depends on.
    html = ""
    for full_cap, overflow_cap in _SHED_LADDER:
        html = _assemble(full_cap, overflow_cap)
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
