"""What we do not know about the finding we just recommended.

ASSEMBLED, NOT GENERATED. Every line here is something the engine already
computed and then dropped on the floor: a reach it could not measure, the
open questions the deep pass wrote and the citation gate already cleared,
and the `?` on a thin `MUST`. No model is called, nothing is scored, nothing
is chosen — this reads existing fields and formats them (I2).

SCOPED TO THE RECOMMENDED FINDING, NOT THE CORPUS. A gaps list covering
everything the run could not answer is a different document: it belongs
beside the plan, and it is already there (`report.py`'s
`_plan_gaps_section`, from `plan.cannot_answer`). This one answers a much
narrower question — "you are about to spend on THIS; what would you want to
know first?" — and a reader can only act on it because it is narrow.

WHY `plan.cannot_answer` IS DELIBERATELY EXCLUDED. Those gaps are
corpus-level ("nothing connected carries numbers — connect analytics") and
therefore IDENTICAL on every finding. Repeating them here would be
duplication that also carries a false promise: that answering them is a
precondition of this particular decision. It is not. They are a statement
about the corpus, rendered once, where they belong.

NOT ACTIONS. NOT NEXT STEPS. These are gaps in what is KNOWN, and the
heading says so. Rendered as work items they would compete with the
recommendation directly above them, and a reader would reasonably read
"close these" as "do these first" — which would make the recommendation
conditional on work nobody committed to.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.crucible.kg_themes import content_tokens, same_topic
from app.crucible.moscow import (
    TYPE_BUCKET_BLOCKER, bucket_for, document_count, type_bucket,
)

#: The heading both renderers print above the list. One string, so the panel
#: and the exported document cannot word it differently.
DATA_GAPS_HEADING = "Close these before you spend."

#: How many gaps to print. Past this the list stops being a checklist and
#: starts being a second findings section.
MAX_DATA_GAPS = 6


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, (list, tuple)) else []


def _deep(finding: Mapping) -> Mapping:
    d = finding.get("deep_recommendation")
    return d if isinstance(d, Mapping) else {}


def _has_deep(finding: Mapping) -> bool:
    d = _deep(finding)
    return bool((d.get("action") or "").strip()
                and (d.get("because") or "").strip())


def recommended_index(findings: Sequence[Mapping]) -> int:
    """Which finding the report is actually recommending, or `-1`.

    THE SAME BINDING `recommend.build_synthesized_recommendation` USES, read
    off the rendered list rather than recomputed: `findings` arrives in the
    run's own frozen rank order (I10), and the single recommendation is
    bound to the first of them that KEPT a deep write-up — `ranked[0]` there
    is this index here. Not "rank 1": rank 1 may have had its deep pass
    dropped at the citation gate, and in that case the memo is recommending
    something else and this must follow it rather than lead it.
    """
    for i, f in enumerate(findings):
        if _has_deep(f):
            return i
    return -1


def _bucket_of(finding: Mapping) -> str:
    claim_types = [str(t) for t in _as_list(finding.get("claim_types"))]
    surfaced_by = [str(s) for s in _as_list(finding.get("surfaced_by"))]
    bucket, _ = bucket_for(claim_types, document_count(surfaced_by))
    return bucket


def thin_flag_discriminates(findings: Sequence[Mapping]) -> bool:
    """Whether `MUST?` on one finding actually SAYS anything on this run.

    It says something only when some blocker on the run is NOT thin. On a
    corpus where every blocker is single-document — which is the normal
    shape for a small tenant, and was the shape of EVERY call tenant before
    `pipeline._sources_of` started counting calls — `MUST?` is a property of
    the corpus rather than of the finding, and printing it as a gap tells a
    reader to go and confirm something no finding here could have avoided.
    Suppressed there; kept where it discriminates.
    """
    blockers = [
        f for f in findings
        if type_bucket([str(t) for t in _as_list(f.get("claim_types"))])
        == TYPE_BUCKET_BLOCKER
    ]
    if not blockers:
        return False
    return any(_bucket_of(f) == "MUST" for f in blockers)


def data_gaps_for(
    findings: Sequence[Mapping],
) -> tuple[int, tuple[str, ...]]:
    """`(index of the recommended finding, its gaps)` — `(-1, ())` when the
    run recommended nothing, and `(i, ())` when it recommended something with
    no gaps left to name. Deterministic; same inputs, same list, every time.

    ORDER IS THE WHOLE SAFETY PROPERTY HERE, not a matter of taste.

    Two of these gaps are ENGINE-DERIVED — facts about the evidence that the
    pipeline computed and that nothing else on the page states: that the
    recommended finding could not be sized, and that the blocker it rests on
    is backed by a single document. The rest are the MODEL's open questions,
    which are prose, and of which there can be any number.

    Sorted the obvious way — measurement, then questions, then caveat — a
    real run put one unsized gap and five model questions in front of the
    corroboration caveat, `[:MAX_DATA_GAPS]` cut at exactly six, and the
    document recommended a blocker resting on ONE source document under a
    heading reading "close these before you spend" that never mentioned it.
    The two facts a reader most needs were the two the cap could reach.

    So the engine-derived gaps go FIRST and are never counted against the
    cap; only the model's questions are truncated. Truncating prose is a
    presentation decision. Truncating "this rests on one document" is a
    disclosure failure wearing a presentation decision's clothes.
    """
    i = recommended_index(findings)
    if i < 0:
        return -1, ()
    f = findings[i]
    # NEVER TRUNCATED — see the docstring. Facts about the evidence.
    engine_gaps: list[str] = []
    # Truncated to whatever the cap leaves. The model's prose.
    model_gaps: list[str] = []

    if f.get("impact_value") is None:
        unit = (str(f.get("currency") or "accounts")).strip() or "accounts"
        noun = "accounts" if unit == "accounts" else unit.replace("_", " ")
        engine_gaps.append(
            f"Which {noun} is this about? Nothing connected put a number on "
            f"how far this reaches, so its size here is unknown — which is "
            f"not the same as small."
        )

    if _bucket_of(f) == "MUST?" and thin_flag_discriminates(findings):
        engine_gaps.append(
            "One source document backs this blocker, where other blockers on "
            "this run are backed by more. Confirm it against a second source "
            "before you commit to it."
        )

    for q in _as_list(_deep(f).get("open_questions")):
        q = (q or "").strip() if isinstance(q, str) else ""
        if q:
            model_gaps.append(q)

    # THE CAP APPLIES TO THE MODEL'S QUESTIONS ONLY, and `max(0, ...)` rather
    # than a bare subtraction so that a run with more engine gaps than the cap
    # drops every question rather than slicing with a negative bound — which
    # silently keeps the LAST few instead of none.
    room = max(0, MAX_DATA_GAPS - len(engine_gaps))
    return i, tuple(engine_gaps + model_gaps[:room])


def _label_of(finding: Mapping) -> str:
    return (str(finding.get("label") or "").strip()
            or str(finding.get("statement") or "").strip())


def options_are_one_topic(findings: Sequence[Mapping]) -> bool:
    """Whether the top two write-ups are the SAME topic named twice.

    A real run rendered Option 1 "enterprise compliance / citation chains"
    and Option 2 "court-admissible citation chains" — and the engine's own
    predicate says those are one topic:

        same_topic(content_tokens("enterprise compliance / citation chains"),
                   content_tokens("court-admissible citation chains")) is True

    They reached the reader as separate options only because the clique
    builder is greedy first-fit, so which group a label lands in depends on
    the order candidates were offered in. Presenting them as a CHOICE is the
    worst available outcome: the reader is asked to pick between two names
    for one thing, under a "why this over the next" sentence that has to
    admit it has no reason, because there genuinely is none.

    ANSWERED IN THE RENDERER, NOT BY CHANGING THE MERGE. Widening the merge
    to catch this pair would change which findings exist, which recommendation
    binds to rank 1, and every count on the page. This changes only whether
    two write-ups are PRESENTED as alternatives. `same_topic` and
    `content_tokens` are imported from `kg_themes`, never reimplemented — a
    second copy of the predicate would be free to disagree with the one that
    formed the groups, and then the page would offer a choice the engine had
    already refused to make, or refuse one it had made.
    """
    deep = [f for f in findings if _has_deep(f)]
    if len(deep) < 2:
        return False
    return same_topic(
        content_tokens(_label_of(deep[0])), content_tokens(_label_of(deep[1])),
    )


#: What the report says INSTEAD of a second option, when the corpus produced
#: two names for one topic. Said plainly: an absent alternative that is not
#: explained reads as a rendering bug, and a reader who saw two options in a
#: previous run needs to know why this one has one.
ONE_TOPIC_NOTE = (
    "Only one recommendation is offered here. The next-ranked write-up names "
    "the same topic as this one rather than a different approach to it, so "
    "presenting the two as alternatives would be a choice the evidence does "
    "not actually offer."
)


def option_numbers(findings: Sequence[Mapping]) -> tuple[int, ...]:
    """`Option N` for each finding, positionally — `0` for one that is not an
    option at all.

    RETURNS ALL ZEROS WHEN THE TOP TWO ARE ONE TOPIC (`options_are_one_topic`)
    — the write-ups still render, they simply stop being labelled as a choice
    between alternatives, and `ONE_TOPIC_NOTE` says why. Numbering is a claim
    that the numbered things differ; making it silently when they do not is
    what produced "Option 1: enterprise compliance / citation chains" beside
    "Option 2: court-admissible citation chains".

    A LABELLING CHANGE, AND ONLY THAT. The options ARE the deep write-ups the
    run already produced, numbered in the run's own frozen rank order (I10);
    nothing is grouped, merged, scored or chosen here, and no model is asked
    anything (I2). Option 1 is whichever finding the recommendation is bound
    to — the same one `recommended_index` returns, by construction, since both
    walk the rank order looking for the first kept deep write-up.

    WHY NUMBER THEM AT ALL. A column of identically-headed "Recommended — the
    full write-up" cards reads as a findings list a reader is expected to
    work through. The same cards headed Option 1 / Option 2 read as a choice
    with a stated preference between them, which is what the ranking actually
    computed — and the `_compare` sentence under Option 1 is the reason for
    the preference, already written and already shown.
    """
    if options_are_one_topic(findings):
        return tuple(0 for _ in findings)
    out: list[int] = []
    n = 0
    for f in findings:
        if _has_deep(f):
            n += 1
            out.append(n)
        else:
            out.append(0)
    return tuple(out)
