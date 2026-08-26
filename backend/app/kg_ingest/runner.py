"""Ingestion runner — RawRecords → extraction batches → KG (§1b pipeline).

Generic across providers: a puller yields RawRecords; the runner batches them
(by char budget) and routes each batch through the extractor. A provider with
a dedicated method in ``PROVIDER_SKILLS`` (currently HubSpot, Jira, ClickUp —
the connectors whose record shapes carry classification signal the generic
prompt can't see, e.g. Jira's native issue type) is routed to its skill;
every other provider falls back to the fully generic extraction path
unchanged. Signal idempotency is content-keyed (uuid5), so re-syncs and
shifting batches can't duplicate. Error-isolated per batch — one bad batch
never kills the sync.

COST GATE: pullers re-fetch everything on every sync, and the uuid5 dedup
only fires at the signal WRITE — after the LLM call was paid for. The runner
therefore keeps a per-record content-hash ledger (db.kg_ingest_ledger) and
extracts ONLY records not seen before; hashes are recorded per batch that
extracted successfully, so a failed batch is retried on the next sync. The
ledger is advisory and fails open — any ledger error degrades to extracting
everything, never to skipping unextracted data.

ONE BOUND on that retry-forever property: a provider LIMIT error (out of
credits, over quota) ABORTS the run instead of being isolated per batch.
Per-batch isolation is right for one bad record and exactly wrong for a dead
account — every remaining batch fails identically, and since none of them
reach the ledger, the next sync re-attempts the ENTIRE corpus. The failure
compounds rather than decaying. See the guard in `sync_provider`'s batch loop
for the measured cost of not having it.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from dataclasses import replace
from typing import Callable, Iterable

from app.db.kg_ingest_ledger import record_hashes, seen_hashes
from app.graph.extractor import (
    extract_document,
    run_checklist_pass,
    summarize_call_transcript,
)
from app.graph.facade import GraphFacade
from app.kg_ingest.pullers import (
    asana,
    clickup,
    confluence,
    fireflies,
    github,
    google_meet,
    hubspot,
    jira,
    sprinklr,
    superset,
    uploads,
    zoom,
)
from app.kg_ingest.types import RawRecord

logger = logging.getLogger(__name__)

_BATCH_CHAR_BUDGET = 6000

# provider → (puller fn, token_json key, source-type hint for the extractor)
PULLERS: dict[str, tuple[Callable[[str], Iterable[RawRecord]], str, str]] = {
    "clickup":   (clickup.pull,   "access_token", "project_mgmt (work items; classify bug/feature/fix)"),
    "asana":     (asana.pull,     "access_token", "project_mgmt (tasks: no native type or priority — classify bug/feature/fix from title+notes; status is the SECTION the task sits in within its project, completed is the done boolean; custom fields carry the team's own taxonomy where set)"),
    "jira":      (jira.pull,      "access_token", "project_mgmt (issues: bugs/stories/tasks/epics with native type + status + priority)"),
    "hubspot":   (hubspot.pull,   "access_token", "revenue + support + customer_voice (deals: blockers/feature gaps; tickets: support pain/churn risk; notes/emails: voice-of-customer; owners: attribution; line items: revenue detail)"),
    "fireflies": (fireflies.pull, "api_key",      "customer_voice / communication (meeting transcripts)"),
    # Like uploads and confluence, the "credential" is the company id — a Zoom
    # pull needs the picked hosts and the incremental cursor off the connection
    # row, and token_for can only hand a puller one field.
    "zoom":      (zoom.pull,      "company_id",   "customer_voice / communication (Zoom cloud-recorded meeting transcripts, speaker-attributed: what customers, prospects and the team actually SAID on a call — treat a quoted line as first-party evidence of that person's view, not as a decision. A record whose text states no transcript was available is a recording we could not read, NOT an empty meeting)"),
    # Like zoom/uploads/confluence, the "credential" is the company id — a Meet
    # pull needs the connected account's identity off the connection row, and
    # token_for can only hand a puller one field.
    "google_meet": (google_meet.pull, "company_id", "customer_voice / communication (Google Meet transcripts, speaker-attributed: what customers, prospects and the team actually SAID on a call — treat a quoted line as first-party evidence of that person's view, not as a decision. COVERAGE IS ONE PERSON'S CALENDAR: Google only exposes meetings the connected account ORGANIZED, and only for 30 days, so absence of a meeting is never evidence it did not happen. A record whose text states no transcript was available is a call we could not read, NOT an empty meeting)"),
    "github":    (github.pull,    "access_token", "engineering activity (PRs + commit messages; distilled ship signals — classify feature/fix/refactor, surface what's being built)"),
    "sprinklr":  (sprinklr.pull,  "access_token", "customer_voice (CX cases: support pain/churn risk; inbound social messages/mentions: public voice-of-customer + market sentiment)"),
    "superset":  (superset.pull,  "superset_credential", "analytics (BI metadata: dashboards/charts/datasets/saved queries — the company's metrics vocabulary, what is measured and how it's organized)"),
    # No third party behind this one: the "credential" is the owning company id
    # and the puller reads the documents the user uploaded themselves. Each
    # record carries the user's own source name + description (see the puller).
    "uploads":   (uploads.pull,  "company_id",          "user-uploaded business documents (research, strategy, support exports, decks, spreadsheets — the user named and described this corpus; treat the source_name/source_description properties as authoritative context for what the text is)"),
    # Like uploads, the "credential" is the company id — a Confluence pull
    # needs the site id and the selected spaces off the connection row, and
    # token_for can only hand a puller one field.
    "confluence": (confluence.pull, "company_id",       "internal_documentation (Confluence wiki pages + blog posts from the spaces this workspace selected: product specs, PRDs, architecture and decision records, runbooks, meeting and retro notes, team handbooks — the company's WRITTEN CONTEXT. Treat these as statements of intent, plans and internal process, NOT as customer evidence: a page asserting a problem is its author's claim about it, not measured proof. The space_key and title properties carry which team/area owns the page)"),
}

# provider → vendored extraction skill id (backend/skills/<id>/), for the
# highest-value connectors that have a purpose-built extraction method beyond
# the generic prompt above. A provider with no entry here (fireflies, github,
# sprinklr, superset, uploads, and every non-PULLERS connector) falls back to
# the fully generic extractor unchanged — see extract_document's skill_id
# docstring. Extend this mapping as more connectors get a dedicated skill.
PROVIDER_SKILLS: dict[str, str] = {
    "hubspot": "hubspot-extraction",
    "jira": "jira-extraction",
    "clickup": "clickup-extraction",
}

#: Providers whose records are DOCUMENTS rather than connector telemetry.
#:
#: Their signals still carry origin="connector" (they did arrive over a
#: connector) but also channel="upload", which is what the brief's evidence
#: gate reads to keep an upload-only tenant on the relaxed single-source path
#: (synthesis.convergence: `origin == "connector" and channel == "upload"`
#: counts as upload evidence). Google Drive reaches the same conclusion by a
#: different route — kg_ingest.drive_extract stamps origin="upload" outright,
#: for the reason spelled out in its module docstring.
#:
#: Getting this wrong is a SILENT regression in the wrong direction: a plain
#: connector origin would make briefs STRICTER for a tenant the moment they
#: connect their wiki.
_DOCUMENT_PROVIDERS: frozenset[str] = frozenset({"uploads", "confluence"})

#: Providers whose records are individual CALLS. These are extracted ONE CALL
#: PER DOCUMENT rather than char-budget-batched, so every signal can carry its
#: source call's ``(provider, external_id)`` — the FK
#: (``call_index.resolve_call_id`` → ``kg_signal.source_id``) that links a
#: distilled signal back to the exact call it came from. Char-budget batching
#: mixes several calls' text into one extraction and flattens that provenance
#: away (it also lets one call's facts contaminate another's). Kept in step with
#: ``call_index.CALL_PROVIDERS`` / ``call_digest._CALL_PROVIDERS``. Every OTHER
#: provider keeps batching unchanged — see ``_extraction_units``.
_CALL_PROVIDERS: frozenset[str] = frozenset({"fireflies", "zoom", "google_meet"})

#: Env var honored by `_call_provider_reextraction_allowed` — a comma-separated
#: tenant (enterprise_id) allowlist gating the call-provider pipeline
#: (transcript-read + directed-checklist pass) rollout. See that function's
#: docstring for the off-by-default-safe contract.
REEXTRACT_ALLOWLIST_ENV = "KG_CALL_REEXTRACT_ALLOWLIST"


def _call_provider_reextraction_allowed(enterprise_id: str) -> bool:
    """Gated-rollout guard for `_CALL_PROVIDERS` (fireflies/zoom/google_meet).

    Reads a comma-separated tenant allowlist from `REEXTRACT_ALLOWLIST_ENV`
    (`KG_CALL_REEXTRACT_ALLOWLIST`):

      * Unset / empty (the default): no restriction. Every tenant's
        call-provider sync proceeds exactly as this code is written — the env
        var has zero effect until someone sets it. Deploying this change with
        the var absent behaves identically to not having this guard at all.
      * Set: ONLY the listed enterprise_ids proceed. Every other tenant's
        call-provider sync for this tick is SKIPPED, not errored — the
        all-tenant scheduler simply retries it on its next pass, so a
        narrowed allowlist pauses rather than fails those tenants.

    Exists to let the FIRST re-extraction sweep after full-transcript-read +
    the directed-checklist pass ships be scoped to one tenant, then widened,
    rather than firing uncontrolled across every enterprise the moment the
    ledger busts (see the module docstring's COST GATE section for why a
    content change busts the ledger).
    """
    raw = os.environ.get(REEXTRACT_ALLOWLIST_ENV, "").strip()
    if not raw:
        return True
    allowed = {e.strip() for e in raw.split(",") if e.strip()}
    return enterprise_id in allowed


def _condensed_and_full_text(
    provider: str, rec: RawRecord, enterprise_id: str
) -> tuple[str, str]:
    """Return ``(main_pass_text, checklist_text)`` for one call-shaped record
    — Config B (2026-08-26): the directed-checklist pass is the SOLE
    full-transcript reader; the main open-extraction pass runs on a cheap
    CONDENSED input instead. Reading the full transcript twice (once per
    pass) was the main cost driver (~$0.20/call); this halves it
    (measured ~$0.13/call Fireflies, ~$0.16/call Zoom/Meet) with recall
    preserved, since nothing that used to reach the main pass's transcript
    read is lost — it now reaches the checklist pass instead.

    Fireflies condenses at the PULLER level: ``rec.text`` is already its
    free digest (no LLM call needed — see ``fireflies._record_from``), and
    ``rec.checklist_text`` carries the digest + full transcript combination
    the checklist pass reads.

    Zoom/Meet have no native digest, so ``rec.text`` IS the full transcript,
    unchanged from the puller (``rec.checklist_text`` is unset for them — the
    puller-level split only exists where it's free). A cheap
    ``claude-haiku-4-5`` call condenses it for the main pass HERE; the
    checklist pass then reads ``rec.text`` — the ORIGINAL, un-summarized
    transcript — unmodified. Chosen over head-truncation, which would
    degrade the main pass to opening-minutes-only.

    Both branches wrap the chosen text through ``RawRecord.render()`` (a
    fresh copy via ``dataclasses.replace`` — never mutates ``rec``) so both
    passes keep the usual header/title/data context, not just bare text.
    """
    checklist_source = rec.checklist_text if rec.checklist_text is not None else rec.text
    checklist_render = replace(rec, text=checklist_source).render()

    if provider == "fireflies":
        # Already condensed at the puller — no LLM call needed here.
        return rec.render(), checklist_render

    # zoom / google_meet: rec.text is the full transcript; derive a cheap
    # Haiku summary for the main pass. Best-effort: a summarization failure
    # must not fail the sync — degrade to the full transcript rather than an
    # empty main pass (the checklist pass alone was never meant to be the
    # ONLY signal source for a call).
    try:
        condensed = summarize_call_transcript(enterprise_id, rec.text)
    except Exception:  # noqa: BLE001 — best-effort condensation only
        logger.warning(
            "kg-ingest: %s call summarization failed for %s/%s — falling "
            "back to the full transcript for the main pass",
            provider, enterprise_id, rec.external_id, exc_info=True,
        )
        condensed = rec.text
    return replace(rec, text=condensed).render(), checklist_render


def _extraction_units(
    provider: str, fresh: list[RawRecord], enterprise_id: str
) -> Iterable[tuple[str, str, list[RawRecord], tuple[str, str] | None, str | None]]:
    """Yield ``(doc_name, text, records, source_ref, checklist_text)`` — one
    per extraction pass.

    Call providers (``_CALL_PROVIDERS``) get ONE pass per call, with a
    ``source_ref`` = the call's ``(provider, external_id)`` so the extractor can
    stamp ``kg_signal.source_id``, PLUS Config B's condensed/full-transcript
    split (see ``_condensed_and_full_text``): ``text`` is the CHEAP main-pass
    input and ``checklist_text`` is the FULL transcript the directed-checklist
    pass reads. Every other provider keeps the existing char-budget batching,
    with no ``source_ref`` (``source_id`` stays NULL, unchanged) and
    ``checklist_text`` left ``None`` — the checklist pass never runs on them
    (see ``sync_provider``).

    The ``<provider>-sync-batch-<n>`` doc_name shape is DELIBERATELY preserved
    for both — ``call_digest`` identifies a call-provider signal by matching
    that shape against ``provenance["doc"]`` (``call_digest._SYNC_BATCH_DOC``,
    the double-counting filter), so changing it here would silently break that
    filter. The per-call linkage rides ``source_id`` and
    ``provenance["provider"]/["external_id"]`` instead, not the doc name.
    """
    if provider in _CALL_PROVIDERS:
        for i, rec in enumerate(fresh):
            main_text, checklist_text = _condensed_and_full_text(
                provider, rec, enterprise_id
            )
            yield (f"{provider}-sync-batch-{i}", main_text, [rec],
                   (rec.provider, rec.external_id), checklist_text)
    else:
        for i, batch in enumerate(_batches(fresh)):
            yield (f"{provider}-sync-batch-{i}",
                   "\n\n".join(r.render() for r in batch), batch, None, None)


def _batches(records: list[RawRecord]) -> Iterable[list[RawRecord]]:
    batch: list[RawRecord] = []
    size = 0
    for r in records:
        rendered = len(r.render())
        if batch and size + rendered > _BATCH_CHAR_BUDGET:
            yield batch
            batch, size = [], 0
        batch.append(r)
        size += rendered
    if batch:
        yield batch


def token_for(provider: str, token_json: dict) -> str:
    """Pull the right credential field out of the decrypted token payload."""
    key = PULLERS[provider][1]
    value = token_json.get(key) or ""
    if not value:
        raise ValueError(f"connection for {provider!r} has no {key!r}")
    return value


def sync_provider(
    facade: GraphFacade,
    enterprise_id: str,
    provider: str,
    *,
    token: str,
    records: list[RawRecord] | None = None,
) -> dict:
    """Pull + extract one provider into the KG. Returns counts + errors."""
    if provider not in PULLERS:
        raise ValueError(f"No puller for provider {provider!r}")
    puller, _, hint = PULLERS[provider]

    # Gated rollout (call providers only) — see
    # `_call_provider_reextraction_allowed`. Checked before pulling anything:
    # a non-allowlisted tenant's sync for this provider is a no-op for this
    # tick, retried by the scheduler once the allowlist widens.
    if provider in _CALL_PROVIDERS and not _call_provider_reextraction_allowed(
            enterprise_id):
        logger.info(
            "kg-ingest: skipping %s sync for %s — not on the %s allowlist "
            "(gated rollout; widen the allowlist to include this tenant)",
            provider, enterprise_id, REEXTRACT_ALLOWLIST_ENV,
        )
        return {"records": 0, "deduped": 0, "batches": 0, "signals": 0,
                "themes": 0, "skipped": 0, "errors": [], "gated": True}

    if records is None:
        # A puller that declares `enterprise_id` gets the tenant id so it can
        # scope the pull to tenant-owned connector config — github reads its
        # App installation's granted repo list instead of trusting the user
        # token's org-wide visibility. Signature-gated so every other puller
        # keeps its plain token-only contract.
        kwargs: dict = {}
        if "enterprise_id" in inspect.signature(puller).parameters:
            kwargs["enterprise_id"] = enterprise_id
        records = list(puller(token, **kwargs))
        # Name every pull as it lands: which connector, for whom, how much.
        # This plus the fresh/dedup line below make "did provider X's data
        # reach the KG, and if not where did it stop" answerable from logs.
        logger.info(
            "kg-ingest: PULLED %s for %s — %d raw records",
            provider, enterprise_id, len(records),
        )

    # Ledger gate: drop records whose exact rendering was already extracted
    # for this enterprise, BEFORE any model call. A changed record renders
    # differently → new hash → extracted again. Fail-open by construction:
    # seen_hashes returns {} on any error, so the sync degrades to extracting
    # everything (pre-ledger behavior) rather than skipping data.
    hashes = {id(r): _content_hash(r.render()) for r in records}
    seen = seen_hashes(enterprise_id, list(set(hashes.values())))
    fresh = [r for r in records if hashes[id(r)] not in seen]

    totals = {"records": len(records), "deduped": len(records) - len(fresh),
              "batches": 0, "signals": 0, "themes": 0, "skipped": 0}
    # 0 fresh means the extraction below is a no-op by design (everything
    # already in the ledger), not a connector failure.
    logger.info(
        "kg-ingest: %s for %s — %d fresh of %d (dedup skipped %d)",
        provider, enterprise_id, len(fresh), len(records),
        totals["deduped"],
    )
    # Lazy — keeps app.llm_errors off this module's import path, matching the
    # lazy-import style synthesis_brief uses for the same classifier.
    from app.llm_errors import PROVIDER_LIMIT, classify_provider_error, user_message

    errors: list[str] = []
    for i, (doc_name, text, unit_records, source_ref, checklist_text) in enumerate(
            _extraction_units(provider, fresh, enterprise_id)):
        try:
            r = extract_document(
                facade, enterprise_id,
                doc_name=doc_name,
                text=text,
                agent=f"ingest:{provider}",
                source_hint=hint,
                origin="connector",
                # Document-class providers (the user's own uploads, and the
                # company wiki) are the same evidentiary class as manual
                # uploads, so they keep the brief gate's upload-only
                # relaxation — convergence counts channel="upload" as upload
                # evidence, mirroring #868's Drive rationale. See
                # _DOCUMENT_PROVIDERS.
                provenance_extra=(
                    {"channel": "upload"} if provider in _DOCUMENT_PROVIDERS else None
                ),
                skill_id=PROVIDER_SKILLS.get(provider),
                # Call-shaped providers extract one call per document and pass
                # that call's (provider, external_id) so the extractor can
                # stamp kg_signal.source_id; every other provider passes None
                # (batched, no per-call link) — see _extraction_units.
                source_ref=source_ref,
                # Haiku relevance + category triage ahead of every extraction
                # unit — the core connector-sync ingestion path.
                triage=True,
            )
            totals["batches"] += 1
            for k in ("signals", "themes", "skipped"):
                totals[k] += r[k]

            # Directed-checklist second pass (call providers only) — a
            # SEPARATE, directed LLM call asking explicitly whether each
            # high-value fact category was discussed, lifting recall on
            # those classes past what open extraction alone catches on a
            # long call. Config B: this pass reads the FULL transcript
            # (`checklist_text`) while the main pass above ran on the cheap
            # condensed `text` — see `_condensed_and_full_text`. Fully
            # isolated: a checklist failure must never block the main
            # extraction's ledger progress or force the whole unit to
            # retry — it only costs this cycle's recall boost for this one
            # call.
            if provider in _CALL_PROVIDERS:
                try:
                    c = run_checklist_pass(
                        facade, enterprise_id,
                        doc_name=doc_name, text=checklist_text,
                        agent=f"ingest:{provider}",
                        origin="connector", source_ref=source_ref,
                    )
                    for k in ("signals", "themes", "skipped"):
                        totals[k] += c[k]
                except Exception:  # noqa: BLE001 — additive pass, never blocking
                    logger.warning(
                        "kg-ingest: checklist pass failed for %s/%s doc=%s",
                        provider, enterprise_id, doc_name, exc_info=True,
                    )

            # Only a unit that made it through extraction is recorded — a
            # failed unit keeps its hashes out of the ledger and is simply
            # re-extracted on the next sync.
            record_hashes(
                enterprise_id, provider, [hashes[id(rec)] for rec in unit_records]
            )
        except Exception as e:  # noqa: BLE001 — error-isolation per batch
            # A dead account is not a per-batch problem. Isolating it makes
            # every REMAINING batch fail identically, and because a failed
            # batch deliberately keeps its hashes out of the ledger (see the
            # module docstring), the whole corpus is re-attempted on the very
            # next sync — so the failure does not decay, it repeats in full
            # every tick. `app.synthesis_brief._seed_from_corpus` already
            # learned this on the corpus path and aborts there for the same
            # reason; this is the connector path, which was still isolating.
            #
            # Measured before this guard: one company whose BYOK key ran out
            # of credits drove ~57k failed calls a day across
            # extract_document + ingest_triage (98.6% of ALL extract_document
            # traffic), climbing from ~8k/day as its corpus grew. The tokens
            # were free — a 400 bills nothing — but each attempt still took a
            # slot in the process-wide LLM concurrency gate (cap 6) that every
            # interactive request queues behind.
            #
            # Breaking rather than raising keeps the counts and the ledger
            # progress from the batches that DID succeed.
            #
            # `errors` is what `auto_sync._run_sync` stamps into
            # last_sync_error, and `routes/connectors.py` serves that straight
            # to the connector UI -- so this string lands on a CUSTOMER's
            # screen. It is therefore the fixed sentence from `llm_errors`,
            # never `str(e)`: a provider error body is untrusted output that
            # carries request ids, org names, key prefixes and billing detail.
            # The raw text goes to the log (warning below) and stops there.
            if classify_provider_error(e) == PROVIDER_LIMIT:
                logger.warning(
                    "kg-ingest: aborting %s sync for %s after a provider limit "
                    "error on batch %d — the remaining batches would fail "
                    "identically (%d batches extracted first): %s",
                    provider, enterprise_id, i, totals["batches"], e,
                )
                errors.append(user_message(PROVIDER_LIMIT))
                break
            logger.exception("extraction failed: %s batch %d", provider, i)
            errors.append(f"batch {i}: {e}")

    # Call-shaped providers only: now that this provider's fresh calls have been
    # extracted (their action-item signals carry provenance.external_id), stamp
    # each action item's owner NAME with an owner_person_id where the owner is a
    # recognizable participant. Wholly best-effort — attribution metadata must
    # never fail or slow a sync — and idempotent (a signal already carrying
    # owner_person_id is skipped).
    if provider in _CALL_PROVIDERS and fresh:
        _resolve_call_owners(facade, enterprise_id, provider, fresh)
    return {**totals, "errors": errors}


def _record_participants(rec: RawRecord) -> list[str]:
    """Participant strings for owner matching, from one call RawRecord. Fireflies
    carries attendee EMAILS; Google Meet carries display NAMES plus one
    `organizer_email`; Zoom carries only `host_email`. All are folded into one
    list — the matcher handles email vs bare-name per entry."""
    props = rec.properties or {}
    out = [str(p) for p in (props.get("participants") or []) if p]
    for key in ("organizer_email", "host_email"):
        value = props.get(key)
        if value:
            out.append(str(value))
    return out


def _resolve_call_owners(
    facade: GraphFacade, enterprise_id: str, provider: str,
    fresh: list[RawRecord],
) -> None:
    """Stamp owner_person_id onto this provider's fresh calls' action items.
    Fully isolated: any failure degrades to unattributed owners (the raw name is
    still on the signal), never a failed sync."""
    try:
        from app.call_index import _own_domains
        from app.kg_ingest import directory

        own = _own_domains(
            enterprise_id,
            [{"participants": _record_participants(r)} for r in fresh],
        )
        # One person-index load shared across the batch — find-or-create then
        # costs no per-call query for a participant we have already seen.
        index = directory._load_person_index(facade, enterprise_id)
        for rec in fresh:
            try:
                directory.resolve_owners_for_call(
                    facade, enterprise_id, provider, rec.external_id,
                    _record_participants(rec), own, person_index=index,
                )
            except Exception:  # noqa: BLE001 — one call must not stop the rest
                logger.warning(
                    "owner-resolution failed for %s/%s",
                    provider, rec.external_id, exc_info=True,
                )
    except Exception:  # noqa: BLE001 — attribution is never load-bearing
        logger.warning(
            "owner-resolution pass failed for %s/%s",
            provider, enterprise_id, exc_info=True,
        )


def _content_hash(rendered: str) -> str:
    """Stable ledger key for one RawRecord rendering."""
    return hashlib.sha256(rendered.encode("utf-8", "replace")).hexdigest()
