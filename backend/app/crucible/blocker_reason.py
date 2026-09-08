"""WHY a deal is blocked — the classifier that breaks apart the checklist's
single `deal blockers` theme.

WHY THIS EXISTS. `app.graph.extractor._CHECKLIST_CATEGORIES` stamps every
objection/blocker a checklist pass finds with the same constant theme label,
regardless of subject: a security review, an absent budget, a wrong
stakeholder, an incumbent tool and a hiring-timing mismatch all land under one
name. On a real corpus that one label held several hundred signals from
several hundred distinct documents — a channel tag wearing a topic's clothes,
and because it is exempt from ordinary corroboration it is also the first
answer to every goal, whatever the goal is.

EMBEDDING CLUSTERING WAS TRIED AND MEASURED DEAD. Two claims saying "no
budget until Q4" about different customers, in different sentence shapes,
sit at roughly 0.6 cosine similarity — well under any threshold that does not
also over-merge unrelated claims. The subjects a reader wants here are
CATEGORIES over these claims, not NEIGHBOURHOODS of them; a classifier buckets
them, a similarity threshold never will.

THE SHAPE IS COPIED FROM `app.crucible.figure_class`, DELIBERATELY, not
reinvented: a closed vocabulary, a schema, a chunked classify pass that skips
already-classified rows, a persist step, a deterministic read-back. See that
module's own docstring for the reasoning behind each piece; only what differs
is called out here.

WHAT IT DOES NOT DECIDE. The model returns a REASON from a closed vocabulary
and nothing else — never an account, never a size, never whether a blocker is
worth acting on. What happens with a reason afterwards — becoming its own
cluster, or being folded back into the constant checklist theme — is decided
by deterministic code in `app.crucible.pipeline`, from the reason alone.

WHERE IT RUNS. Same reasoning as `figure_class`: `pipeline.py` contains no LLM
call anywhere, so this runs BEFORE it, in `execute_run`, beside
`classify_figures`. Claims go in, the same claims come back carrying
`blocker_reason`, and the pipeline reads that field as an ordinary
deterministic input.

DEGRADE, NEVER FAIL. A claim the classifier never reaches, or one in a chunk
whose call failed, keeps `blocker_reason=None` and `pipeline._cluster` groups
it exactly as it does today — on its checklist theme. A run must never die,
and a claim must never go missing, because this classifier did.
"""
from __future__ import annotations

import functools
import logging
import sys
from dataclasses import replace
from typing import Optional, Sequence

from app.crucible.types import Claim

logger = logging.getLogger(__name__)

#: The closed vocabulary, as (key, model-facing description, report display
#: label) triples — CLOSED, AND SMALL, for the same reason `figure_class`'s
#: is: a model choosing between a handful of known reasons is a reliable job
#: for a fast model; a model writing free-text prose is not, and free text is
#: exactly how a closed vocabulary quietly becomes a second constant label.
#:
#: DERIVED FROM THE CLAIMS, not invented. An indicative keyword sweep over a
#: real corpus of ~390 `deal blockers` signals (understating, and buckets
#: overlap — not itself a classification) found budget/no-funding, legal or
#: security or compliance review, stakeholder authority, timing/priority,
#: product gap or missing integration, and incumbent/competitive pressure as
#: the recurring shapes; `other` is the honest remainder.
#:
#: `other` IS A VALID DRAW AND IS DELIBERATELY NOT ITS OWN FINDING. See
#: `CLUSTERABLE_REASONS` below — building a second catch-all bucket under a
#: new name would recreate the exact opaque mega-theme this module exists to
#: break apart. A claim classified `other` is left on its checklist theme,
#: the identical treatment an unclassified claim gets.
BLOCKER_REASONS: tuple[tuple[str, str, str], ...] = (
    ("budget",
     "No budget approved for this deal, budget frozen or reallocated "
     "elsewhere, or the funding/approval needed to buy has not been "
     "secured yet.",
     "budget & funding not approved"),
    ("legal_security_compliance",
     "Blocked on a legal, security, privacy, or compliance review — an "
     "NDA, an MSA, a security questionnaire, SOC2, data residency, or a "
     "procurement policy that has not cleared.",
     "legal, security & compliance review"),
    ("stakeholder_authority",
     "The person engaged cannot approve the purchase alone, the actual "
     "decision-maker has not been reached, or the buying committee has "
     "not aligned internally.",
     "stakeholder or decision-maker not aligned"),
    ("timing_priority",
     "Blocked by timing rather than substance: a competing initiative has "
     "priority right now, the fiscal calendar or renewal date does not "
     "line up, or the account asked to revisit later.",
     "timing & competing priorities"),
    ("product_gap",
     "A feature, integration, or capability the account needs is missing "
     "from the product as it stands today.",
     "product gap or missing integration"),
    ("competitive",
     "An incumbent tool, a named competitor, or an existing internal "
     "workaround is preferred over switching.",
     "incumbent or competitor preferred"),
    ("other",
     "A real blocker that does not confidently fit any category above.",
     "other blocker"),
)

BLOCKER_REASON_KEYS: tuple[str, ...] = tuple(r[0] for r in BLOCKER_REASONS)

#: `key -> the report-facing label `pipeline._label` should show for it.
BLOCKER_REASON_LABELS: dict[str, str] = {r[0]: r[2] for r in BLOCKER_REASONS}

#: Reasons specific enough to become their own cluster/finding. `other` is
#: excluded on purpose — see the vocabulary comment above. A claim whose
#: stored reason is `other` is treated the same as an unclassified claim by
#: `pipeline._cluster`: it falls back to `subject`/`type`.
CLUSTERABLE_REASONS: frozenset[str] = frozenset(
    k for k in BLOCKER_REASON_KEYS if k != "other"
)


@functools.lru_cache(maxsize=1)
def constant_checklist_theme_labels() -> frozenset[str]:
    """The theme labels `app.graph.extractor._CHECKLIST_CATEGORIES` mints,
    lowercased — the ONLY subjects a classified reason may override.

    WHY THIS EXISTS AND WHY IT IS SCOPED THIS NARROWLY. `pipeline._cluster`
    keys on `subject_cluster_id` before anything else, and by the time
    `build_findings` runs, `subject_cluster_id` is set on every claim the
    graph could theme (`kg_themes.assign_themes`) or cluster by embedding
    (`cluster.assign_clusters`) — `execute_run` runs both, unconditionally,
    before `build_findings`. So `reason` is unreachable UNLESS something
    overrides `subject_cluster_id` outright for the claims it should apply
    to. It must not do that for every claim with a reason: 38 `constraint`
    claims measured on a real tenant are `business_context`-sourced and
    already sit on real, specific graph themes ("Budget & procurement",
    8 accounts; "FedRAMP / compliance", 2 accounts; …) — letting a reason
    override those pulls them OUT of a good, specific theme and INTO a
    generic reason bucket, destroying findings that were never the defect.
    Scoping the override to claims whose `subject` IS one of the twelve
    constant checklist labels is what keeps those 38 alone: only a claim
    still carrying the checklist pass's own constant label (never rewritten
    to anything more specific) is a claim the constant-label defect actually
    describes.

    A LOCAL HELPER, NOT AN IMPORT of a shared constant: `extractor.py`
    exposes no public checklist-label list on `main` — reading it straight
    off `_CHECKLIST_CATEGORIES`'s own theme-label column (index 3),
    filtered to the categories that actually mint a signal (index 6;
    'stakeholders' does not — see that module's own comment), guarantees
    this can never drift from what the extractor actually writes. Imported
    lazily, inside the function, the same convention `_classify_chunk` uses
    for `app.graph.gateway`/`app.llm` — `app.graph.extractor` pulls in the
    embeddings/facade/gateway chain, and this module (like `figure_class`)
    keeps that out of its own top-level import graph. Cached because the
    checklist's own category table is a fixed constant for the life of the
    process, not something worth re-deriving on every `_cluster` call.
    """
    from app.graph.extractor import _CHECKLIST_CATEGORIES

    return frozenset(
        row[3].strip().lower() for row in _CHECKLIST_CATEGORIES if row[6]
    )


CLASSIFY_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["classifications"],
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["idx", "blocker_reason"],
                "properties": {
                    "idx": {
                        "type": "integer",
                        "description": (
                            "The number of the item being classified, as "
                            "shown in the list."
                        ),
                    },
                    "blocker_reason": {
                        "type": "string",
                        "enum": list(BLOCKER_REASON_KEYS),
                        "description": (
                            "Why this blocker exists. Exactly one of the "
                            "listed values."
                        ),
                    },
                },
            },
        },
    },
}


def _prompt_vocabulary() -> str:
    """The vocabulary section of `_SYSTEM`, built from `BLOCKER_REASONS` so
    the prompt and the schema can never drift apart — the description a
    reader can test IS the description the model is shown."""
    lines = []
    for key, description, _label in BLOCKER_REASONS:
        lines.append(f"{key} — {description}")
    return "\n\n".join(lines)


_SYSTEM = f"""You are told a short paraphrase of an objection, risk, or \
blocker raised about a deal — something a customer or a rep said on a call, \
in a message, or in a document. For each one, answer ONE question: WHY is \
this deal blocked?

You are NOT judging how serious the blocker is, how likely the deal is to \
close, or which blocker matters most. You are not ranking anything. Choose \
one reason per item.

{_prompt_vocabulary()}

Two rules that matter more than getting a close call right:

Prefer `other` when genuinely unsure. A claim labelled `other` still counts \
as a blocker on its existing theme; a claim wrongly forced into a specific \
reason category muddies that category's pattern with something that does \
not belong in it.

Judge WHY the deal is blocked, not what else the sentence mentions. "We like \
the product but legal needs to review the DPA before signing" is \
legal_security_compliance, even though it also expresses enthusiasm.

Reply with one classification per item shown, using the item's own number as \
`idx`."""

#: Same sizing logic as `figure_class.CHUNK` — see its own comment.
CHUNK = 40

#: Same as `figure_class.MAX_TEXT_CHARS` — a blocker's paraphrase is a single
#: clause, not a document.
MAX_TEXT_CHARS = 400

#: The `kg_signal.properties` key a stored reason lives under, beside
#: `figure_class.PROPERTY_KEY`.
PROPERTY_KEY = "blocker_reason"


def _offline() -> bool:
    """True when no model should be called. See `figure_class._offline` —
    same convention, same reason: a test that wants the real path can
    monkeypatch this."""
    return "pytest" in sys.modules


def _candidates(claims: Sequence[Claim]) -> list[Claim]:
    """The claims worth spending a call on: `constraint`-typed claims with no
    stored reason yet.

    SCOPED TO `constraint`, not to every claim. `KIND_TO_CLAIM_TYPE` maps
    both the checklist's `deal_blocker` kind and `business_context`'s own
    `constraint` kind to claim type `constraint` — this is exactly, and
    only, the population the checklist's `deal blockers` mega-theme is built
    from (`app.crucible.claims.KIND_TO_CLAIM_TYPE`). Every other checklist
    category (product gaps, legal, timeline, …) maps to a different claim
    type and is never a candidate here, which is what keeps this change from
    touching any of the other eleven checklist themes.

    ALREADY-CLASSIFIED ROWS ARE NEVER RE-SENT — see `classify_blocker_reasons`.
    """
    return [
        c for c in claims
        if c.type == "constraint" and c.blocker_reason is None
    ]


def _input(candidates: Sequence[Claim]) -> str:
    """Numbered 1..N — the number IS `idx`, the only handle the model gets
    back to a claim. The claim id never leaves this function."""
    lines = ["BLOCKERS:"]
    for i, c in enumerate(candidates, start=1):
        text = (c.assertion or "").strip()[:MAX_TEXT_CHARS]
        lines.append(f"{i}. {text}")
    return "\n".join(lines)


def _classify_chunk(
    *, enterprise_id: str, candidates: Sequence[Claim],
) -> dict[str, str]:
    from app.graph.gateway import llm_call
    from app.llm import FAST_MODEL

    result = llm_call(
        enterprise_id=enterprise_id,
        agent="crucible",
        purpose="classify_blocker_reason",
        prompt_version="crucible-blocker-reason-v1",
        # HIGH-VOLUME, closed-set, one-enum-per-item output — same shape and
        # same model tier as `figure_class`. A bucket choice, not analysis.
        model=FAST_MODEL,
        system=_SYSTEM,
        input=_input(candidates),
        json_schema=CLASSIFY_SCHEMA,
        max_tokens=4000,
    )
    out = result.output
    if not isinstance(out, dict):
        return {}
    by_idx = {i: c for i, c in enumerate(candidates, start=1)}
    classified: dict[str, str] = {}
    for item in out.get("classifications") or []:
        if not isinstance(item, dict):
            continue
        claim = by_idx.get(item.get("idx"))
        if claim is None:
            continue
        reason = item.get("blocker_reason")
        # THE CLOSED VOCABULARY IS ENFORCED HERE, not trusted from the
        # schema — same posture as `figure_class._classify_chunk`.
        if reason in BLOCKER_REASON_KEYS:
            classified[claim.id] = reason
    return classified


def classify_blocker_reasons(
    claims: Sequence[Claim], *, enterprise_id: str,
) -> dict[str, str]:
    """`claim id -> blocker reason` for every UNCLASSIFIED `constraint` claim.

    CLASSIFY ONCE. TAKE THE FIRST SAMPLE AND KEEP IT — identical discipline
    to `figure_class.classify_figures`: a model call is a draw, not a
    lookup, and re-drawing on every run would make which findings exist a
    function of how many times the analysis was run rather than of the
    evidence. A reason is drawn ONCE per row, stored beside it
    (`persist_reasons`), and later runs read it back rather than re-drawing.

    NEVER RE-ROLL A STORED REASON TO CHECK IT.

    A claim the model did not answer for, or one in a chunk whose call
    failed, is simply absent from the result — `pipeline._cluster` falls
    back to the claim's `subject`/`type`, exactly today's behaviour, so a
    model outage degrades the split rather than losing the claim.
    """
    candidates = _candidates(claims)
    if not candidates or _offline():
        return {}

    classified: dict[str, str] = {}
    for start in range(0, len(candidates), CHUNK):
        chunk = candidates[start:start + CHUNK]
        try:
            classified.update(
                _classify_chunk(enterprise_id=enterprise_id, candidates=chunk)
            )
        except Exception:  # noqa: BLE001 — one bad chunk is not a failed run
            logger.exception(
                "crucible: blocker reason classification chunk failed "
                "(offset=%s)", start,
            )

    if classified:
        counts: dict[str, int] = {}
        for value in classified.values():
            counts[value] = counts.get(value, 0) + 1
        # Identifiers and counts only — never the paraphrase, never an
        # account name.
        logger.info(
            "crucible_blocker_reasons candidates=%s classified=%s by_reason=%s",
            len(candidates), len(classified), dict(sorted(counts.items())),
        )
    return classified


def apply_reasons(
    claims: Sequence[Claim], classified: dict[str, str],
) -> list[Claim]:
    """Attach each reason to its claim, leaving everything else untouched.

    Returns new `Claim` objects rather than mutating: they are frozen.
    """
    return [
        replace(c, blocker_reason=classified[c.id]) if c.id in classified
        else c
        for c in claims
    ]


def persist_reasons(
    classified: dict[str, str], *, company_id: str,
) -> int:
    """Write each reason beside its own signal's other properties. Returns
    how many rows were written.

    TENANT-SCOPED ON EVERY WRITE — the update names `enterprise_id` as well
    as `id`, same posture as `figure_class.persist_classes`, because this
    runs against a shared database.

    ONLY THE ONE KEY MOVES. Existing `properties` are read and rewritten
    with `blocker_reason` set; nothing else in the dict is touched.
    """
    if not classified or not company_id:
        return 0
    from app.db.client import require_client

    client = require_client()
    written = 0
    for signal_id, reason in classified.items():
        if reason not in BLOCKER_REASON_KEYS:
            continue
        rows = (
            client.table("kg_signal").select("id, properties")
            .eq("enterprise_id", company_id).eq("id", signal_id)
            .execute().data or []
        )
        if not rows:
            continue
        props = dict(rows[0].get("properties") or {})
        props[PROPERTY_KEY] = reason
        (
            client.table("kg_signal").update({"properties": props})
            .eq("enterprise_id", company_id).eq("id", signal_id)
            .execute()
        )
        written += 1
    logger.info(
        "crucible_blocker_reasons_persisted company_id=%s written=%s",
        company_id, written,
    )
    return written


def estimate_cost(claims: Sequence[Claim]) -> dict[str, int]:
    """Token volumes for classifying this corpus, so a run can be costed
    before it is paid for. Counts only — see `figure_class.estimate_cost`
    for why no price is multiplied in here.
    """
    candidates = _candidates(claims)
    calls = (len(candidates) + CHUNK - 1) // CHUNK if candidates else 0
    system_tokens = len(_SYSTEM) // 4
    body_chars = sum(
        len((c.assertion or "")[:MAX_TEXT_CHARS]) + 8 for c in candidates
    )
    return {
        "candidates": len(candidates),
        "calls": calls,
        "estimated_input_tokens": calls * system_tokens + body_chars // 4,
        # One `{"idx": N, "blocker_reason": "..."}` per item, ~15 tokens.
        "estimated_output_tokens": len(candidates) * 15,
    }
