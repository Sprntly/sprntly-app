"""Monthly intelligence report → KG ingest — the scheduled reports as knowledge.

The three monthly reports (`app.monthly_reports`) used to end their run by
PUSHING: a Slack block and an email saying "your report is ready". That put the
finding in front of whoever happened to read the channel that morning and
nowhere else — the report's contents stayed locked inside one artifact row, so
Ask, PRD evidence and ideation could not see a word of it. Someone asking "did
any competitor raise money recently?" got nothing, a month after we had paid for
a web sweep that answered exactly that.

This module replaces the push with an ingest. Each finished report is extracted
into the same knowledge graph every other source writes to, so its facts are
retrievable by anyone who asks a question they answer — `ask_runner` already
calls `graph.retrieval.retrieve_context` generically, so nothing needed teaching
about reports. The report artifact itself is still saved to the `reports`
library exactly as before; this is additive to that, and it is what makes the
library row reachable by question rather than only by browsing.

Four properties, the same four that matter for any ingest source:

1. NEVER EVIDENCE — enforced on BOTH axes, like the roadmap leg.
   An intelligence report is a model's synthesis of facts scraped off the public
   web. It says things about competitors, funding rounds and app-store reviews,
   and the extractor would happily type those as `revenue` or `customer_voice`
   evidence on merit. A tenant with no connected sources and no uploads must not
   have the brief-sufficiency gate opened by a report Sprntly generated about
   the outside world on its behalf.

   * ``origin="web_research"`` — in `graph.types.NON_EVIDENCE_ORIGINS`, so
     `compute_evidence_eligible` returns False whatever source_type survives.
     The value is not a convenience: it is literally what these facts are, and
     `app.company_research` already stamps it for the same reason.
   * ``force_source_type="pm_manual"`` — outside `CONNECTED_SOURCE_TYPES`, so
     the gate's PRIMARY (source_type-counting) path cannot see them either.
     `pm_manual` over `agent_inferred` for its 60-day staleness window
     (graph/types.SOURCE_STALE_WINDOW_DAYS): retrieval's `_recency_factor` is a
     half-life on that window, and on `agent_inferred`'s 14 days a report would
     decay to ~0.23 by the time it is three weeks old — invisible for the back
     half of every month it is the current report. 60 days keeps it ranked for
     its whole life and no longer.

   What downstream code filters and attributes on is
   ``provenance["channel"] == "monthly_report"``.

2. REPLACE semantics, PER REPORT. There is one live report per skill per
   company — the latest month wins (owner's call: the graph should reflect the
   current picture, not accumulate a year of market events). So when September's
   competitive report is ingested, every still-live signal from August's
   competitive report is expired.

   Expiry is scoped by ``provenance["report_skill"]``, and that scoping is the
   whole safety property: the three reports run back-to-back in one scheduler
   tick, and a channel-only sweep would have the market report expire the
   competitive report's signals seconds after they were written. Same shape as
   the roadmap leg's workspace scoping, for the same reason.

   Expiry is skipped when the extraction produced ZERO signals — a report we
   failed to read is not evidence that last month's findings were withdrawn.
   Unlike the roadmap leg there is no "changed underneath us" re-check to do:
   the text is passed in by the caller from a `reports` row that has already
   been written and is never edited.

3. A content-hash ledger (kg_source rows, source_type ``monthly_report``) makes
   a re-ingest free. Two months' reports never collide in practice, so this is
   not really deduping months — it is the guard for the same report being
   ingested twice (a manual `run_and_deliver` re-run, a retry), which would
   otherwise pay for a second extraction and, worse, expire nothing while
   double-wiring the root. Written only AFTER a successful extraction, so a
   failed run retries on the next touch.

4. Company-root wiring. Every signal the report asserts gets an INFORMS edge to
   the tenant's single ``company`` root entity, so report content doesn't float
   disconnected from its tenant — identical to the roadmap leg, including
   leaving the shared extractor's theme entities unwired.

Called from exactly one place: `monthly_reports.run_and_deliver`, after the
report row is saved, error-isolated there. On-demand reports someone asks for in
chat are deliberately NOT ingested (owner's call) — a mid-month re-run would
churn the graph, and the scheduled run is the one with a cadence behind it.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from typing import Optional

from app.graph.extractor import _NS, extract_document
from app.graph.facade import GraphFacade
from app.graph.types import Relationship, Source

logger = logging.getLogger(__name__)

#: kg_source.source_type used for the per-report ingest ledger.
LEDGER_SOURCE_TYPE = "monthly_report"

#: Provenance channel every report-derived signal carries. This — NOT an
#: origin, and not the source_type — is what downstream filters on.
REPORT_CHANNEL = "monthly_report"

#: Chars per extraction call, and the ceiling on calls per report. Parity with
#: the roadmap and uploads legs so a chunk always fits one extraction batch. A
#: report runs 10-20k chars, so the cap is headroom, not a trim.
_CHUNK_CHARS = 4000
_MAX_CHUNKS = 25

#: Source type FORCED onto every report signal — not a default, a pin. See
#: module docstring (1) for why both this and the origin are needed.
_FORCE_SOURCE_TYPE = "pm_manual"
_ORIGIN = "web_research"

#: What the extractor is looking at. Intelligence reports read very differently
#: from customer evidence: they are dated, sourced statements about the OUTSIDE
#: world (rivals, the category, public reviews), and the model must not mistake
#: a competitor's quoted metric for one of ours.
_SOURCE_HINT = (
    "a generated market/competitive/public-feedback intelligence report — "
    "dated, sourced findings about the OUTSIDE world (competitors, the "
    "category, public reviews), not this company's own customer evidence"
)


def _chunks(text: str) -> list[str]:
    """Split on the chunk budget. Returns [] for whitespace-only text."""
    body = (text or "").strip()
    if not body:
        return []
    return [
        body[i:i + _CHUNK_CHARS] for i in range(0, len(body), _CHUNK_CHARS)
    ][:_MAX_CHUNKS]


def content_sha(company_id: str, report_skill: str, text: str) -> str:
    """Ledger key for one report. Scoped by company AND report skill so two
    reports that somehow synthesise identical text each get their own row."""
    return hashlib.sha256(
        f"{company_id}|{report_skill}|{text}".encode()
    ).hexdigest()


def _ledger_id(company_id: str, sha: str) -> str:
    """Deterministic kg_source id — upsert-safe, so a retry racing the original
    run can't duplicate the row."""
    return str(uuid.uuid5(_NS, f"monthly-report|{company_id}|{sha}"))


def _already_ingested(facade: GraphFacade, company_id: str, sha: str) -> bool:
    """True when this exact report text already has a ledger row."""
    return any(
        (s.config or {}).get("content_sha") == sha
        for s in facade.list_sources(company_id, source_type=LEDGER_SOURCE_TYPE)
    )


def is_ingested(
    company_id: str, *, report_skill: str, text: str,
    ledger_rows: list | None = None, facade: Optional[GraphFacade] = None,
) -> bool:
    """Has this exact report text already reached the KG?

    The public half of the ledger check, for `monthly_reports.pending_ingests`
    — which asks the question the other way round ("was this saved report ever
    ingested?") to find runs whose document landed in the library but whose
    extraction failed.

    ``ledger_rows`` lets a caller checking several specs for one company pass a
    single `list_sources` result in rather than re-reading it per spec.
    """
    sha = content_sha(company_id, report_skill, (text or "").strip())
    if ledger_rows is None:
        facade = facade or GraphFacade()
        return _already_ingested(facade, company_id, sha)
    return any((s.config or {}).get("content_sha") == sha for s in ledger_rows)


def ledger_rows(company_id: str, facade: Optional[GraphFacade] = None) -> list:
    """This company's ingest-ledger rows — one read, reusable across specs."""
    facade = facade or GraphFacade()
    return facade.list_sources(company_id, source_type=LEDGER_SOURCE_TYPE)


def _live_report_signal_ids(
    facade: GraphFacade, company_id: str, report_skill: str
) -> list[str]:
    """Ids of this REPORT's still-active signals.

    Skill-exact by design: the three reports ingest back-to-back inside one
    scheduler tick, so a channel-only match would have each report expire its
    siblings' just-written signals. See module docstring (2).
    """
    out: list[str] = []
    for sig in facade.active_signals(company_id):
        prov = sig.provenance or {}
        if prov.get("channel") != REPORT_CHANNEL:
            continue
        if prov.get("report_skill") != report_skill:
            continue
        out.append(sig.id)
    return out


def _company_informed_signal_ids(
    facade: GraphFacade, company_id: str, company_entity_id: str
) -> set[str]:
    """Ids of signals already carrying an INFORMS edge to the tenant's
    `company` root. Extraction is content-keyed, so a finding repeated verbatim
    in a later month re-derives the same signal id and comes back as a
    duplicate — without this check its root edge would be written again."""
    return {
        e.source_id
        for e in facade.edges_to(company_id, company_entity_id, type="INFORMS")
        if e.source_kind == "signal"
    }


def ingest_report(
    company_id: str,
    *,
    report_skill: str,
    report_label: str,
    period: str,
    text: str,
    report_id: int | None = None,
    facade: Optional[GraphFacade] = None,
) -> dict:
    """Extract one finished monthly report into the KG (replace semantics).

    ``text`` is the report's markdown body — the same string saved to the
    `reports` row. ``report_id`` rides on every signal's provenance so an answer
    grounded on a report can point back at the artifact it came from.

    Returns a status dict:
      * ``{"status": "no_text"}`` — nothing extractable. No ledger row.
      * ``{"status": "unchanged", "content_sha": …}`` — already ingested; zero
        model calls.
      * ``{"status": "ingested", signals, themes, duplicates, chunks, expired,
        content_sha}`` — ``expired`` counts last month's signals retired here.

    Raises on extraction failure WITHOUT writing the ledger row, so the next
    run retries. The single call site isolates.
    """
    facade = facade or GraphFacade()
    parts = _chunks(text)
    if not parts:
        logger.info(
            "report-ingest: no extractable text for company=%s report=%s",
            company_id, report_skill,
        )
        return {"status": "no_text"}

    sha = content_sha(company_id, report_skill, (text or "").strip())
    if _already_ingested(facade, company_id, sha):
        return {"status": "unchanged", "content_sha": sha}

    # Snapshot what the PREVIOUS month left live BEFORE extracting, so the diff
    # below is against last month's report, not against our own fresh writes.
    previous_ids = set(_live_report_signal_ids(facade, company_id, report_skill))

    provenance_extra: dict[str, object] = {
        "channel": REPORT_CHANNEL,
        "report_skill": report_skill,
        "report_label": report_label,
        "report_period": period,
        "report_id": report_id,
        # OVERRIDES the extractor's `source: "extractor"`, and that override is
        # the whole point. `graph.retrieval.render_context_section` builds its
        # provenance line as
        # `prov.get("source") or prov.get("doc") or prov.get("connector")`, so
        # "extractor" won and every report finding reached the model as
        # "[pm_manual/competitor_move] · provenance: extractor: ...". The five
        # keys above are precise and none of them is read there — the model saw
        # a machine-typed note with no source, not a finding from a paid sweep.
        #
        # Observed, not theorised: asked for a competitor review in chat, the
        # answer disclaimed its own evidence — "I'm answering from your synced
        # sources only" — cited every finding as "[Source: pm_manual]", and
        # recommended running the very review those findings came from.
        #
        # Safe to override: `provenance_extra` is merged last by the extractor,
        # and the only readers of `provenance["source"]` in the app are that
        # render call and its sibling. Nothing branches on the literal
        # "extractor".
        "source": f"{report_label} · {period}",
    }

    totals = {"signals": 0, "themes": 0, "duplicates": 0}
    keep: set[str] = set()
    for i, part in enumerate(parts):
        doc_name = (
            f"{report_label} · {period}" if len(parts) == 1
            else f"{report_label} · {period} (part {i + 1}/{len(parts)})"
        )
        r = extract_document(
            facade, company_id,
            doc_name=doc_name, text=part,
            agent="ingest:monthly-report",
            source_hint=_SOURCE_HINT,
            # Both pins, both load-bearing — see module docstring (1).
            origin=_ORIGIN,
            force_source_type=_FORCE_SOURCE_TYPE,
            provenance_extra=provenance_extra,
        )
        totals["signals"] += r["signals"]
        totals["themes"] += r["themes"]
        totals["duplicates"] += r["skipped"]
        keep.update(r.get("signal_ids") or [])

    # ── Company-root wiring ──────────────────────────────────────────────────
    # Same primitive and dedup as the roadmap leg: every signal this report
    # asserts gets an INFORMS edge to the tenant's `company` root, and `keep`
    # includes duplicate-skipped ids (a finding repeated from last month), so
    # the write is diffed against the edges already there rather than blind.
    company_wired = 0
    if keep:
        company_entity_id = facade.ensure_company_entity(company_id)
        already_wired = _company_informed_signal_ids(
            facade, company_id, company_entity_id)
        for sig_id in sorted(keep - already_wired):
            facade.write_relationship(company_id, Relationship(
                enterprise_id=company_id, type="INFORMS",
                source_kind="signal", source_id=sig_id,
                target_kind="entity", target_id=company_entity_id,
                provenance={"agent": "ingest:monthly-report"},
            ))
            company_wired += 1

    # ── Replace semantics, with the one safety gate that applies ─────────────
    # Retire what last month's report asserted and this month's doesn't. A
    # zero-signal extraction means we failed to READ this month's report, which
    # is not evidence last month's findings were withdrawn — never wipe on
    # empty. (The roadmap leg's second gate, re-reading the source to catch a
    # concurrent replacement, has no analogue here: `text` comes from a saved
    # `reports` row that is never edited.)
    dropped = sorted(previous_ids - keep)
    expired = 0
    if not dropped:
        pass
    elif not keep:
        logger.warning(
            "report-ingest: extraction produced ZERO signals for company=%s "
            "report=%s (%s) — skipping expiry, keeping %d previous signal(s)",
            company_id, report_skill, period, len(dropped),
        )
    else:
        expired = facade.expire_signals(company_id, dropped)

    # Ledger LAST: a report is only "done" once its signals are in the graph.
    facade.create_source(company_id, Source(
        id=_ledger_id(company_id, sha),
        enterprise_id=company_id,
        source_type=LEDGER_SOURCE_TYPE,
        label=f"{report_label} · {period}"[:200],
        config={
            "content_sha": sha,
            "report_skill": report_skill,
            "report_label": report_label,
            "report_period": period,
            "report_id": report_id,
            "chunks": len(parts),
        },
    ))
    logger.info(
        "report-ingest done: company=%s report=%s (%s) chunks=%s signals=%s "
        "duplicates=%s expired=%s company_wired=%s",
        company_id, report_skill, period, len(parts),
        totals["signals"], totals["duplicates"], expired, company_wired,
    )
    return {
        "status": "ingested",
        "signals": totals["signals"],
        "themes": totals["themes"],
        "duplicates": totals["duplicates"],
        "chunks": len(parts),
        "expired": expired,
        "content_sha": sha,
    }
