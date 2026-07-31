"""Extraction evals — sampled, async structural checks of extracted Signals
against the expected shape their producing skill declares.

Scope (deliberately narrow): checks a SAMPLE of already-written Signals for
required fields, a sane confidence range, and non-malformed content, plus
(when the producing skill is one of the vendored connector-extraction skills
in ``backend/skills/*-extraction/``) that ``source_type``/``kind`` are legal
members of the vocabulary that skill's own
``references/expected-signal-shape.md`` declares. Every check here is a
deterministic property of a persisted row — no LLM judge call, so there is
no router-tier model to pick (see ``app.graph.triage`` for where one IS
needed elsewhere in this pipeline).

``SKILL_EXPECTED_VOCAB`` below is hand-maintained from each skill's
``expected-signal-shape.md`` table rather than parsed from the markdown at
runtime — the same call ``test_kg_extraction_skills.py`` already made for
the identical structural check ("Kept here (not parsed from the markdown) so
a doc/code drift shows up as a clear assertion failure on either side, not a
silently-passing regex match against free-form prose"). Keep it in sync by
hand when a skill's contract changes.

Never called from a live ingestion or request path — see
``app.scheduler``'s opt-in ``extraction_eval`` job, the only caller in this
codebase, and ``run_scheduled_eval_cycle``'s docstring for the "why async /
why sampled" rationale.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from app.graph.facade import GraphFacade
from app.graph.types import Signal

logger = logging.getLogger(__name__)

# skill_id -> {source_type: {kind, kind, ...}} — the source_type x kind
# vocabulary each vendored connector-extraction skill declares in its own
# references/expected-signal-shape.md contract. Mirrors
# app.kg_ingest.runner.PROVIDER_SKILLS's skill ids (hubspot/jira/clickup —
# the only skills with a shape contract today).
SKILL_EXPECTED_VOCAB: dict[str, dict[str, frozenset[str]]] = {
    "hubspot-extraction": {
        "revenue": frozenset({"deal_blocker", "finding"}),
        "customer_voice": frozenset(
            {"bug", "feature_request", "incident", "sentiment", "finding"}
        ),
    },
    "jira-extraction": {
        "project_mgmt": frozenset({"bug", "feature_request", "finding"}),
    },
    "clickup-extraction": {
        "project_mgmt": frozenset({"bug", "feature_request", "finding"}),
    },
}

# How many of an enterprise+skill's most recent signals a single eval pass
# samples — bounded on purpose (this is a spot-check, not an audit of every
# row ever written).
DEFAULT_SAMPLE_SIZE = 25

_MIN_CONFIDENCE = 0.0
_MAX_CONFIDENCE = 1.0
_MIN_CONTENT_CHARS = 3
# A raw source-record header leaking straight into `content` (extraction
# handed back the input framing instead of a distilled statement) — mirrors
# RawRecord.render()'s `[provider/kind id=... ]` bracket header.
_RAW_RECORD_HEADER_RE = re.compile(r"^\[\w[\w./-]*\s+id=")


@dataclass
class EvalFinding:
    """One structural mismatch between a Signal and its producing skill's
    declared contract — carries everything needed to route the finding back
    to the responsible skill and the exact offending signal: which skill,
    which enterprise, which signal id, which field, what was wrong."""
    enterprise_id: str
    skill_id: str
    signal_id: str
    field: str
    detail: str
    actual: object = None


@dataclass
class SkillEvalResult:
    """One eval pass over one (enterprise, skill_id) sample."""
    enterprise_id: str
    skill_id: str
    sampled: int
    findings: list[EvalFinding] = field(default_factory=list)

    @property
    def flagged(self) -> int:
        """Distinct signals with at least one finding (a single signal can
        trip more than one check)."""
        return len({f.signal_id for f in self.findings})


def check_signal(signal: Signal, skill_id: str) -> list[EvalFinding]:
    """Pure structural check of one Signal against the fixed extraction
    schema every skill fills (kind/content/source_type/properties/
    confidence — see app.graph.extractor._EXTRACT_SCHEMA) plus, when
    ``skill_id`` has a declared contract in SKILL_EXPECTED_VOCAB, that
    contract's source_type x kind vocabulary. No side effects, no I/O — the
    caller (run_skill_eval) owns logging."""
    findings: list[EvalFinding] = []

    def flag(field_name: str, detail: str, actual: object = None) -> None:
        findings.append(EvalFinding(
            enterprise_id=signal.enterprise_id, skill_id=skill_id,
            signal_id=signal.id, field=field_name, detail=detail, actual=actual,
        ))

    # ---- required fields present ----
    if not signal.kind or not str(signal.kind).strip():
        flag("kind", "kind is missing/empty", signal.kind)
    if not signal.content or not str(signal.content).strip():
        flag("content", "content is missing/empty", signal.content)
    if not signal.source_type or not str(signal.source_type).strip():
        flag("source_type", "source_type is missing/empty", signal.source_type)
    if not isinstance(signal.properties, dict):
        flag("properties", "properties is not an object",
             type(signal.properties).__name__)

    # ---- confidence in a sane range ----
    try:
        conf = float(signal.confidence)
    except (TypeError, ValueError):
        flag("confidence", "confidence is not numeric", signal.confidence)
    else:
        if not (_MIN_CONFIDENCE <= conf <= _MAX_CONFIDENCE):
            flag("confidence", f"confidence {conf} outside [0.0, 1.0]", conf)

    # ---- no malformed content ----
    if signal.content and isinstance(signal.content, str):
        stripped = signal.content.strip()
        if len(stripped) < _MIN_CONTENT_CHARS:
            flag("content", "content is suspiciously short", stripped)
        elif _RAW_RECORD_HEADER_RE.match(stripped):
            flag(
                "content",
                "content looks like a raw source-record header, not a "
                "distilled statement",
                stripped[:120],
            )

    # ---- skill-declared source_type / kind vocabulary ----
    vocab = SKILL_EXPECTED_VOCAB.get(skill_id)
    if vocab is not None and signal.source_type and signal.kind:
        allowed_kinds = vocab.get(signal.source_type)
        if allowed_kinds is None:
            flag(
                "source_type",
                f"source_type {signal.source_type!r} is not in {skill_id}'s "
                f"declared contract (expected one of {sorted(vocab)})",
                signal.source_type,
            )
        elif signal.kind not in allowed_kinds:
            flag(
                "kind",
                f"kind {signal.kind!r} is not valid for source_type "
                f"{signal.source_type!r} under {skill_id}'s declared "
                f"contract (expected one of {sorted(allowed_kinds)})",
                signal.kind,
            )

    return findings


def run_skill_eval(
    facade: GraphFacade,
    enterprise_id: str,
    skill_id: str,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
) -> SkillEvalResult:
    """Sample the most recent signals ``skill_id`` produced for one
    enterprise and check each against its declared contract. Read-only,
    bounded by ``sample_size`` — safe to call often, never on a hot path.

    Every finding is logged (structured, enough detail to route back to the
    responsible skill) — findings are never silently dropped, only
    aggregated for the caller."""
    signals = facade.recent_signals_by_skill(
        enterprise_id, skill_id, limit=sample_size
    )
    result = SkillEvalResult(
        enterprise_id=enterprise_id, skill_id=skill_id, sampled=len(signals)
    )
    for sig in signals:
        result.findings.extend(check_signal(sig, skill_id))

    for finding in result.findings:
        logger.warning(
            "extraction-eval finding: skill=%s enterprise=%s signal=%s "
            "field=%s detail=%s actual=%r",
            finding.skill_id, finding.enterprise_id, finding.signal_id,
            finding.field, finding.detail, finding.actual,
        )
    if result.findings:
        logger.warning(
            "extraction-eval: skill=%s enterprise=%s sampled=%s flagged=%s/%s",
            skill_id, enterprise_id, result.sampled, result.flagged,
            result.sampled,
        )
    else:
        logger.info(
            "extraction-eval: skill=%s enterprise=%s sampled=%s — all clean",
            skill_id, enterprise_id, result.sampled,
        )
    return result


def run_scheduled_eval_cycle(*, sample_size: int = DEFAULT_SAMPLE_SIZE) -> dict:
    """Sweep every company x vendored extraction skill, sampling recent
    output and logging any structural mismatch.

    Intended to be invoked ONLY from a scheduled job (see app.scheduler's
    opt-in ``extraction_eval`` job) — never from a live request or
    ingestion path, and never per-signal: each (company, skill) pair reads
    a bounded, recent sample, so a large tenant base stays cheap and this
    can never add latency to a sync or an agent response. Per-(company,
    skill) isolated: one raise is logged and skipped so the rest of the
    cycle still runs, mirroring app.scheduler's other per-company sweeps."""
    from app.db.companies import list_companies
    from app.kg_ingest.runner import PROVIDER_SKILLS

    try:
        companies = list_companies() or []
    except Exception:  # noqa: BLE001 — a listing failure must not crash the job
        logger.exception("extraction-eval: failed to list companies")
        return {"companies": 0, "skills": 0, "sampled": 0, "findings": 0}

    skill_ids = sorted(set(PROVIDER_SKILLS.values()))
    facade = GraphFacade()
    totals = {
        "companies": len(companies), "skills": len(skill_ids),
        "sampled": 0, "findings": 0,
    }
    for company in companies:
        company_id = company.get("id")
        if not company_id:
            continue
        for skill_id in skill_ids:
            try:
                result = run_skill_eval(
                    facade, company_id, skill_id, sample_size=sample_size
                )
            except Exception:  # noqa: BLE001 — per-(company, skill) isolation
                logger.exception(
                    "extraction-eval: run failed for company=%s skill=%s",
                    company_id, skill_id,
                )
                continue
            totals["sampled"] += result.sampled
            totals["findings"] += len(result.findings)
    return totals
