"""Chat "analyze my data" command → deterministic DS-engine run.

qa_agent intercepts data-analysis questions (skill_router.is_data_analysis_request)
and hands them here — the same pattern as call_digest. We gather the company's
uploaded tabular exports (dataset raw/ CSVs, plus .xlsx sheets converted on the
fly), copy them into a throwaway workdir, run the vendored v5.8 engine
(app.ds.engine) over it, and render the four-channel report as a markdown Ask
answer. Every number in the reply was computed and replication-gated by the
deterministic battery — no LLM is called anywhere on this path.

The temp-workdir copy matters: the engine mutates workspace state (`.sprntly/`
fingerprints for drift detection), and the dataset raw/ dir is user-visible via
the file-listing routes, so runs must never write next to the uploads. State is
discarded per run — the registry/feedback/drift workstreams stay inert until a
persistent workspace is wired up.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL_TAGS = {
    "_skill": "ds-agent",
    "_skill_action": "Analyze data",
    "_skill_source": "ds-engine",
}

# Mirrors routes/datasets.py MAX_UPLOAD_BYTES — anything larger never landed via
# the upload route, so a bigger file in raw/ is unexpected; skip, don't choke.
_MAX_FILE_BYTES = 20 * 1024 * 1024

# Reporting caps for the chat answer (the engine's own selection caps already
# bound each channel; these keep the chat message readable, not the scan).
_MAX_LEADS_SHOWN = 8
_MAX_NULLS_SHOWN = 6


def _payload(answer: str, key_points: list[str] | None = None, *, confidence: float) -> dict:
    return {
        "answer": answer,
        "key_points": key_points or [],
        "citations": [],
        "confidence": confidence,
        "unanswered": "",
        **_SKILL_TAGS,
    }


def _stage_workspace(raw_dir: Path, workdir: Path) -> list[str]:
    """Copy analyzable tabular files into the throwaway workdir.

    CSVs are copied as-is; each sheet of an .xlsx becomes its own CSV (the
    engine only globs *.csv). Returns the staged filenames; unconvertible or
    oversized files are skipped silently — partial coverage beats no answer,
    and the engine's representation manifest reports exactly what it saw.
    """
    staged: list[str] = []
    for src in sorted(raw_dir.iterdir()):
        if not src.is_file() or src.stat().st_size > _MAX_FILE_BYTES:
            continue
        suffix = src.suffix.lower()
        if suffix == ".csv":
            shutil.copy(src, workdir / src.name)
            staged.append(src.name)
        elif suffix in (".xlsx", ".xls"):
            try:
                import pandas as pd

                sheets = pd.read_excel(src, sheet_name=None)
            except Exception:  # noqa: BLE001 — bad workbook ≠ failed analysis
                logger.warning("DS chat: could not read workbook %s", src.name, exc_info=True)
                continue
            for sheet_name, df in sheets.items():
                if df.empty:
                    continue
                out = workdir / f"{src.stem}_{sheet_name}.csv".replace(" ", "_")
                df.to_csv(out, index=False)
                staged.append(out.name)
    return staged


def _fmt_findings(findings: list[dict]) -> list[str]:
    lines = []
    for i, f in enumerate(findings, 1):
        lines.append(f"{i}. **{f['claim']}**")
        if f.get("cohort_code"):
            lines.append(f"   - cohort: `{f['cohort_code']}`")
        if f.get("replication"):
            lines.append(f"   - replication: {f['replication']}")
    return lines


def _render_report(result: dict, staged: list[str]) -> tuple[str, list[str], float]:
    """Four-channel engine output → (markdown answer, key_points, confidence)."""
    findings = result.get("findings", [])
    leads = result.get("leads", [])
    nulls = result.get("null_results", [])
    alerts = result.get("alerts", [])
    coverage = result.get("coverage_notes", [])

    # Nothing was analyzable at any grain (e.g. a tiny hand-made summary sheet
    # with formatted strings): don't claim "no effect survived the gates" —
    # nothing ran. Explain what the run could not see and what exports work.
    if not findings and not leads and not nulls and not alerts:
        msg = [
            "I ran the analysis battery over your uploaded "
            f"file{'s' if len(staged) != 1 else ''}, but none had the structure "
            "the statistical scans need — typically a users/accounts table (one "
            "row per user with attributes), raw event exports, or experiment "
            "exposure logs from your analytics tool (Mixpanel, Amplitude, "
            "PostHog, GA4, Statsig…). Aggregated summary sheets are too coarse "
            "to mine for verified effects."
        ]
        if coverage:
            msg.append("\n### What this run could not see")
            msg.extend(f"- {c}" for c in coverage)
        msg.append(
            "\nUpload a raw export under **Sources** and ask me again — I'll run "
            "the full battery and report only replicated, statistically-gated "
            "findings."
        )
        return "\n".join(msg), [], 0.2

    parts: list[str] = []
    tool = result.get("tool", "unknown")
    tool_note = f" (detected format: {tool})" if tool != "unknown" else ""
    parts.append(
        f"I ran the full deterministic analysis battery over your {len(staged)} "
        f"uploaded data file{'s' if len(staged) != 1 else ''}{tool_note}. Every "
        "finding below was statistically gated and replicated in an independent "
        "half of the data — these are measured facts, not model impressions."
    )

    if findings:
        parts.append(f"\n### Measured findings ({len(findings)})")
        parts.extend(_fmt_findings(findings))
    else:
        parts.append(
            "\n### Measured findings\nNo effect survived the statistical gates "
            "(replication + false-discovery control). That itself is informative — "
            "see the cleared hypotheses below."
        )

    if leads:
        parts.append(f"\n### Directional leads ({len(leads)}) — real signal, not yet enough data to confirm")
        parts.extend(f"- {l['claim']}" for l in leads[:_MAX_LEADS_SHOWN])
        if len(leads) > _MAX_LEADS_SHOWN:
            parts.append(f"- …and {len(leads) - _MAX_LEADS_SHOWN} more")

    if alerts:
        parts.append(f"\n### Operational alerts ({len(alerts)})")
        parts.extend(f"- ⚠️ {a}" for a in alerts)

    if nulls:
        parts.append(f"\n### Hypotheses tested and cleared ({len(nulls)})")
        parts.extend(f"- {n['claim']}" for n in nulls[:_MAX_NULLS_SHOWN])
        if len(nulls) > _MAX_NULLS_SHOWN:
            parts.append(f"- …and {len(nulls) - _MAX_NULLS_SHOWN} more")

    if coverage:
        parts.append("\n### What this analysis could not see")
        parts.extend(f"- {c}" for c in coverage)

    key_points = [f["claim"] for f in findings[:5]]
    if not key_points and leads:
        key_points = [l["claim"] for l in leads[:3]]
    confidence = 0.9 if findings else (0.6 if leads else 0.4)
    return "\n".join(parts), key_points, confidence


def answer(*, enterprise_id: str, question: str, history: list[dict] | None = None) -> dict:
    """Run the DS engine over the company's uploaded data; Ask-shaped payload."""
    from app.datasets import raw_path
    from app.db.companies import slug_for_company_id

    slug = slug_for_company_id(enterprise_id)
    raw_dir = raw_path(slug) if slug else None
    if not raw_dir or not raw_dir.is_dir():
        return _payload(
            "I can run a full data-science analysis for you, but I don't see any "
            "uploaded data yet. Upload your product-analytics exports (CSV or "
            "Excel — Mixpanel, Amplitude, PostHog, GA4, Statsig and other tool "
            "exports are auto-detected) under **Sources**, then ask me again.",
            confidence=0.0,
        )

    workdir = Path(tempfile.mkdtemp(prefix="sprntly-ds-"))
    try:
        staged = _stage_workspace(raw_dir, workdir)
        if not staged:
            return _payload(
                "I can run a full data-science analysis for you, but none of your "
                "uploaded files are tabular data I can analyze (I need CSV or "
                "Excel exports — e.g. a users/accounts table, event exports, or "
                "experiment exposures). Upload one under **Sources** and ask me "
                "again.",
                confidence=0.0,
            )
        from app.ds.engine import run as run_engine

        result = run_engine(str(workdir))
        markdown, key_points, confidence = _render_report(result, staged)
        _log_run(enterprise_id, result, staged)
        return _payload(markdown, key_points, confidence=confidence)
    except Exception:  # noqa: BLE001 — a broken export must not 500 the chat
        logger.exception("DS chat analysis failed for %s", enterprise_id)
        return _payload(
            "I hit an error while analyzing your uploaded data — one of the files "
            "may be malformed. Please re-export it and upload again; if it keeps "
            "failing, tell me which file and I'll flag it for the team.",
            confidence=0.0,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _log_run(enterprise_id: str, result: dict, staged: list[str]) -> None:
    """Best-effort decision-log row — this path makes no LLM call, so the
    gateway never logs it; mirror qa_agent._log_qa here instead."""
    try:
        from app.graph.decision_log import log_agent_decision

        log_agent_decision(
            enterprise_id=enterprise_id,
            agent="ds",
            decision_type="chat_data_analysis",
            factors={
                "files": len(staged),
                "tool": result.get("tool"),
                "findings": len(result.get("findings", [])),
                "leads": len(result.get("leads", [])),
                "nulls": len(result.get("null_results", [])),
                "alerts": len(result.get("alerts", [])),
            },
            model=None,
            prompt_version="ds-chat-analysis-v1",
        )
    except Exception:  # noqa: BLE001
        logger.exception("DS chat decision-log write failed")
