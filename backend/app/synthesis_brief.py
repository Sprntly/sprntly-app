"""KG-synthesis brief engine — the read path the UI already speaks.

This is the engine behind the Top Insights brief. It bridges the UI's dataset-slug
world to the knowledge-graph world:

    slug → company_id  (companies.slug is the tenant key + the dataset slug)
    seed-incremental   (always extract corpus docs not yet ingested — tracked
                        by a per-doc content hash in `kg_source` — so a doc
                        uploaded *after* the first brief still flows into the
                        graph; on a first-ever (empty) KG also do best-effort
                        connector pulls so convergence has something to rank)
    run_synthesis(...) (convergence → ranked insights → save_brief into the
                        SAME `briefs` table the UI's /current,/status,/{id}
                        endpoints read)

The legacy corpus→single-Claude-call path (app.brief_runner) stays available
behind the flag; this module never calls it.

Seeding is resilient + bounded: corpus docs and connector pulls are capped, and
every extraction is error-isolated so one bad doc/connector can't abort the
seed. The corpus seed is INCREMENTAL — only docs whose content hash isn't
already recorded as a `corpus_doc` source get (re-)extracted, so newly-uploaded
docs always reach the brief while unchanged ones are skipped cheaply. Connector
pulls run only on a first-ever (empty) KG; they have their own ongoing sync path
(pipeline stage 1 + auto-sync-on-connect), so we don't re-pull them every regen.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app import datasets
from app.brief_gate import (
    NO_DATA_SOURCE_MESSAGE,
    NoBriefDataSourceError,
    has_brief_data_source,
)
from app.connectors.catalog import EVIDENCE_UPLOAD_CATEGORIES
from app.corpus import load_corpus
from app.ingest import is_unparsed_stub
from app.db.briefs import get_current_brief
from app.db.companies import company_id_for_slug, slug_for_company_id
from app.graph.extractor import _NS, extract_document
from app.graph.config_layers import config_get
from app.graph.facade import GraphFacade, _parse_iso
from app.graph.types import Source
from app.synthesis.agent import EmptyKnowledgeGraphError, run_synthesis

logger = logging.getLogger(__name__)

# Bounds so a seed can never hang the request / scheduler cycle. The KG is
# idempotent (content-keyed signal ids), so a capped first pass that misses a
# few docs is corrected on the next run; the point is to never block.
# Floor on how often the Top Insights brief is RECOMPOSED, in hours.
#
# The refresh gate below skips synthesis only when ZERO new signals have landed
# since the current brief. Seeding runs first and connector syncs write signals
# continuously, so in practice almost every call cleared that bar: production
# ran `compose_top_insights` 520 times in 30 days across 11 companies — 1-3 per
# company per active hour — for a brief the product delivers WEEKLY. Each run is
# an opus composition over a 32.5k-token method block.
#
# So the gate needs a second condition: new signals AND enough time since the
# last composition. Six hours keeps a same-day feel (a doc uploaded this morning
# is reflected by the afternoon) while cutting recompositions by roughly 4x.
# It is a floor on the SCHEDULED/incidental path only — an explicit
# "Regenerate" always passes `force=True` and recomposes now.
#
# Per-company overridable via config `brief.min_recompose_hours`; 0 disables the
# floor and restores the old any-new-signal behaviour.
MIN_RECOMPOSE_HOURS = 6

MAX_SEED_DOCS = 25          # corpus docs extracted in one seed pass
MAX_SEED_CONNECTORS = 6     # connector pulls attempted in one seed pass


def _looks_like_uuid(value: str) -> bool:
    """company ids are uuids; slugs match ^[a-z0-9][a-z0-9_-]{1,62}$ (with
    hyphens, but never the 8-4-4-4-12 uuid shape). Cheap disambiguation so
    callers can pass either a slug or a company_id."""
    parts = value.split("-")
    return len(parts) == 5 and [len(p) for p in parts] == [8, 4, 4, 4, 12]


def resolve_company(company_id_or_slug: str) -> tuple[str, str]:
    """Return (company_id, slug) from either a company id or a dataset slug.

    The slug may be a bare company slug (the default workspace's dataset) OR a
    non-default workspace's dataset slug (``'<company>--<workspace>'``), which
    lives in the `datasets` table, not `companies`. We try the companies table
    first, then fall back to the workspace/dataset binding so every workspace's
    brief resolves to its parent company. Both keep the passed-in slug as the
    returned dataset slug (the KG is company-scoped; the corpus/brief are
    slug-scoped).

    Raises ValueError if the identifier resolves to no company.
    """
    if _looks_like_uuid(company_id_or_slug):
        slug = slug_for_company_id(company_id_or_slug)
        if slug is None:
            raise ValueError(f"No company for id {company_id_or_slug!r}")
        return company_id_or_slug, slug
    company_id = company_id_for_slug(company_id_or_slug)
    if company_id is not None:
        return company_id, company_id_or_slug
    # Not a bare company slug — try a non-default workspace's dataset slug,
    # which binds to its parent company via the datasets → workspaces tables.
    from app.db.workspaces import workspace_for_dataset_slug

    binding = workspace_for_dataset_slug(company_id_or_slug)
    if binding and binding.get("company_id"):
        return binding["company_id"], company_id_or_slug
    raise ValueError(f"No company for slug {company_id_or_slug!r}")


def _kg_is_empty(facade: GraphFacade, company_id: str) -> bool:
    """True when the company's KG has no active (non-stale) signals — i.e.
    convergence would find nothing to rank, so we must seed first."""
    return not facade.active_signals(company_id)


def mark_corpus_doc_ingested(
    facade: GraphFacade, company_id: str, doc_name: str, text: str
) -> str:
    """Record a corpus doc in the corpus_doc ledger WITHOUT extracting it.

    Used by connector→corpus syncs (Google Drive) whose content reaches the KG
    through their own connector-origin extraction path: marking the ledger here
    keeps _seed_from_corpus from re-extracting the same bytes with
    origin="upload". Same sha + source-id scheme as _seed_from_corpus, so
    either side recording a doc makes the other skip it. Returns the sha."""
    sha = hashlib.sha256(f"{company_id}|{text}".encode()).hexdigest()
    facade.create_source(company_id, Source(
        id=str(uuid.uuid5(_NS, f"corpus-doc|{company_id}|{sha}")),
        enterprise_id=company_id,
        source_type="corpus_doc",
        label=doc_name[:200],
        config={"content_sha": sha, "doc": doc_name, "via": "google_drive"},
    ))
    return sha


def _min_recompose_hours(company_id: str) -> float:
    """The company's recompose floor in hours (see MIN_RECOMPOSE_HOURS).

    Config-resolved so a company that genuinely wants a fresher brief can be
    tuned without a deploy. A non-numeric or negative value falls back to the
    default rather than disabling the floor by accident — 0 disables it, but
    only when it is actually written as 0.
    """
    raw = config_get("brief.min_recompose_hours", company_id,
                     default=MIN_RECOMPOSE_HOURS)
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return float(MIN_RECOMPOSE_HOURS)
    return hours if hours >= 0 else float(MIN_RECOMPOSE_HOURS)


def _within_recompose_floor(company_id: str, prior_ts: Any) -> bool:
    """True when the current brief is too young to recompose.

    Fails OPEN — an unparseable or future-dated timestamp returns False, so the
    floor can never wedge a company into never regenerating. The failure mode we
    are willing to accept here is one extra composition; the one we are not is a
    brief frozen forever.
    """
    hours = _min_recompose_hours(company_id)
    if hours <= 0:
        return False
    try:
        generated_at = _parse_iso(prior_ts)
    except Exception:  # noqa: BLE001 — see the fail-open note above
        # Deliberately broad. `generated_at` comes off a DB row, and any shape
        # this cannot parse (an int, a dict, a format change) must degrade to
        # "compose it" rather than propagate. A narrower catch missed a plain
        # int and raised AttributeError out of the gate.
        logger.warning(
            "brief recompose floor: unparseable generated_at %r for company=%s "
            "— composing rather than skipping", prior_ts, company_id,
        )
        return False
    if generated_at is None:
        return False
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - generated_at
    return timedelta() <= age < timedelta(hours=hours)


def _seed_from_corpus(facade: GraphFacade, company_id: str, slug: str) -> dict:
    """Incrementally extract the company's corpus into the KG.

    Only docs whose content hash isn't already recorded as a `corpus_doc`
    `kg_source` row get extracted, so a doc uploaded *after* the first brief
    still flows in, while unchanged docs are skipped cheaply (and don't count
    toward the MAX_SEED_DOCS new-doc cap). Extraction itself is idempotent
    (content-keyed signal ids), so a re-extract of edited text self-dedups.

    Error-isolated per doc (mirrors /v1/synthesis/seed): one bad doc logs +
    is skipped, the rest proceed; the source row is recorded ONLY after a
    successful extract, so a failed doc retries on the next run. Missing corpus
    is not fatal — a company might be connector-only.

    TWO BOUNDS on that retry-forever property, both learned the hard way:

      * `MAX_SEED_DOCS` counts ATTEMPTS, not successes. It used to count only
        successful extracts, so the cap was unreachable in the one case it most
        needed to hold — when every doc fails, nothing increments, and the whole
        corpus is re-attempted on every run.
      * A provider LIMIT error (out of credit, over quota) aborts the pass
        immediately rather than being isolated per doc. Per-doc isolation is
        right for a bad document and exactly wrong for a dead account: the next
        doc will fail identically, so isolating it just means failing N times
        instead of once. A company whose key ran dry drove ~200k such calls over
        nine days at roughly one per second, each one taking a slot in the
        process-wide LLM concurrency gate that every interactive request queues
        behind. The tokens were free; the throughput was not.
    """
    # Lazy import — matches the lazy-import style this module already uses
    # for connector-path imports (see _seed_from_connectors below), so no
    # module-load cycle is created between synthesis_brief and connectors.
    from app.connectors.slack_sync import SLACK_CORPUS_DOC_STEM
    from app.llm_errors import PROVIDER_LIMIT, classify_provider_error

    totals = {"signals": 0, "themes": 0, "skipped": 0, "docs": 0, "unchanged": 0,
              "unreadable": 0, "kg_excluded": 0, "attempted": 0, "aborted": 0}
    try:
        corpus = load_corpus(slug)
    except (FileNotFoundError, RuntimeError) as e:
        logger.info("seed: no corpus for %s (%s) — skipping corpus extraction",
                    slug, e)
        return totals

    # Load the already-ingested content hashes once (the per-doc ledger).
    existing = {
        s.config.get("content_sha")
        for s in facade.list_sources(company_id, source_type="corpus_doc")
        if s.config
    }

    # Connector-category attribution: a doc uploaded into an evidence-bearing
    # connector category (voice/analytics/revenue/crm/monitoring) is that kind
    # of connector data, not plain documentation — extract it with the
    # category's source hint, a deterministic default source_type, and
    # origin="connector" (channel="upload" keeps the brief gate's upload-only
    # relaxation intact — see convergence.is_upload_only). Uncategorized docs
    # and non-evidence categories keep the plain origin="upload" path.
    doc_categories = datasets.md_file_categories(slug)

    extracted = 0
    attempted = 0
    for doc in corpus.docs:
        # A placeholder for a file we couldn't read is not content: extracting
        # it spends an LLM call on the words "its content is not included in
        # analysis yet", and recording it would mark the file permanently
        # ingested so a future parser never gets to retry it. Skip WITHOUT
        # recording, so it re-enters the moment we can read its type.
        if is_unparsed_stub(doc.text):
            totals["unreadable"] += 1
            continue
        # Slack reaches the KG per-channel via kg_ingest.slack_extract, which
        # carries channel-level provenance this wholesale doc cannot. The file
        # itself stays — the corpus loader still feeds it to briefs and Ask.
        if doc.name == SLACK_CORPUS_DOC_STEM:
            totals["kg_excluded"] += 1
            continue
        sha = hashlib.sha256(f"{company_id}|{doc.text}".encode()).hexdigest()
        if sha in existing:
            totals["unchanged"] += 1
            continue
        # Cap ATTEMPTED extractions per run; keep cheaply skipping unchanged
        # docs. Counting attempts rather than successes is the point: a corpus
        # where every extract fails must still stop at the cap.
        if attempted >= MAX_SEED_DOCS:
            continue
        attempted += 1
        totals["attempted"] = attempted
        category = doc_categories.get(f"{doc.name}.md", "")
        evidence = EVIDENCE_UPLOAD_CATEGORIES.get(category)
        try:
            if evidence:
                source_type, hint = evidence
                r = extract_document(
                    facade, company_id, doc_name=doc.name, text=doc.text,
                    origin="connector", source_hint=hint,
                    source_type_default=source_type,
                    provenance_extra={"channel": "upload", "category": category},
                    # Haiku relevance + category triage ahead of every corpus
                    # doc. This is separate from `category` above
                    # (the user-picked upload category / evidence bucket) —
                    # triage's own classification lands as
                    # provenance["triage_category"].
                    triage=True,
                )
            else:
                r = extract_document(
                    facade, company_id, doc_name=doc.name, text=doc.text,
                    origin="upload",
                    triage=True,
                )
            for k in ("signals", "themes", "skipped"):
                totals[k] += r[k]
            totals["docs"] += 1
            extracted += 1
            # Record the doc as ingested ONLY after a successful extract.
            facade.create_source(company_id, Source(
                id=str(uuid.uuid5(_NS, f"corpus-doc|{company_id}|{sha}")),
                enterprise_id=company_id,
                source_type="corpus_doc",
                label=doc.name[:200],
                config={"content_sha": sha, "doc": doc.name},
            ))
            existing.add(sha)
        except Exception as exc:  # noqa: BLE001 — error-isolation per doc
            # A dead account is not a per-doc problem. Isolating it would make
            # every remaining doc fail the same way, so stop the pass instead
            # and let the caller surface the reason. See the docstring.
            if classify_provider_error(exc) == PROVIDER_LIMIT:
                totals["aborted"] = 1
                logger.warning(
                    "seed: aborting corpus extraction for company=%s after a "
                    "provider limit error on doc %s (%d attempted, %d extracted) "
                    "— the remaining docs would fail identically",
                    company_id, doc.name, attempted, extracted,
                )
                raise
            logger.exception("seed: corpus extraction failed for doc %s", doc.name)
    return totals


def _seed_from_connectors(facade: GraphFacade, company_id: str) -> dict:
    """Best-effort pull of any connected providers into the KG.

    Bounded (MAX_SEED_CONNECTORS) and fully isolated: a missing puller, a bad
    token, or a provider outage is logged and skipped — it never aborts the
    seed. Google Drive is special-cased (connection-config sync, not a token
    puller); providers without any KG path (figma/slack) are no-ops.
    """
    totals = {"providers": 0, "signals": 0}
    try:
        import json

        from app import db
        from app.connectors.tokens import decrypt_token_json
        from app.kg_ingest.runner import PULLERS, sync_provider, token_for

        connections = db.list_connections(company_id)
    except Exception:  # noqa: BLE001 — connectors are optional infrastructure
        logger.exception("seed: could not enumerate connectors for %s", company_id)
        return totals

    for row in connections[:MAX_SEED_CONNECTORS]:
        provider = row.get("provider")
        if provider == "google_drive":
            # Drive has no token puller — its docs come from the connection's
            # picked-file config. Run its sync inline (kg_inline) so the
            # extracted signals land before this first synthesis reads the KG.
            try:
                from app.connectors.google_drive_sync import sync_google_drive

                r = sync_google_drive(company_id=company_id, kg_inline=True)
                totals["providers"] += 1
                totals["signals"] += r.kg_signals
            except Exception:  # noqa: BLE001 — error-isolation per connector
                logger.exception("seed: google_drive pull failed for %s",
                                 company_id)
            continue
        if provider not in PULLERS:
            continue
        try:
            token_json = json.loads(
                decrypt_token_json(row["token_json_encrypted"])
            )
            token = token_for(provider, token_json)
            r = sync_provider(facade, company_id, provider, token=token)
            totals["providers"] += 1
            totals["signals"] += r.get("signals", 0)
        except Exception:  # noqa: BLE001 — error-isolation per connector
            logger.exception("seed: connector pull failed for %s/%s",
                             company_id, provider)
    return totals


def _workspace_id_for_slug(company_id: str, slug: str) -> str | None:
    """Which workspace's roadmap does this brief slug refer to?

    Additional workspaces own a '{company_slug}--{workspace_slug}' dataset, so
    the dataset→workspace binding answers directly. A bare company slug is the
    DEFAULT workspace's dataset, whose binding predates workspace scoping for
    older tenants — fall back to the company's default workspace. None means we
    couldn't resolve one (legacy/unbound dataset): ingest_roadmap then reads the
    company's no-workspace roadmap row, which is the same row the synthesis
    agent's company-keyed load_roadmap_doc reads.
    """
    from app.db.workspaces import default_workspace_for_company, workspace_for_dataset_slug

    try:
        binding = workspace_for_dataset_slug(slug)
        if binding and binding.get("workspace_id"):
            return str(binding["workspace_id"])
        ws = default_workspace_for_company(company_id)
        return str(ws["id"]) if ws else None
    except Exception:  # noqa: BLE001 — best-effort resolution, never fatal
        logger.exception("seed: could not resolve workspace for slug %s", slug)
        return None


def _seed_from_roadmap(facade: GraphFacade, company_id: str, slug: str) -> dict:
    """Grandfather + retry leg: make sure the workspace's uploaded roadmap is in
    the KG before synthesis reads it.

    The roadmap upload endpoint already kicks this off (auto_sync.
    kickoff_roadmap_ingest), so on the happy path this is a ledger no-op costing
    one kg_source read. It exists for the two paths the kickoff can't cover:
    every roadmap uploaded BEFORE roadmap→KG ingest shipped (backfilled on the
    next brief, no migration needed), and any kickoff that failed or lost its
    thread. Error-isolated — a roadmap problem must never block a brief.

    Concurrency: ingest_roadmap takes the per-company roadmap lock itself (the
    same object auto_sync's kickoff uses), so this leg cannot interleave with an
    in-flight upload ingest and expire the current roadmap's signals.
    """
    from app.kg_ingest.roadmap import ingest_roadmap

    try:
        return ingest_roadmap(
            company_id, _workspace_id_for_slug(company_id, slug), facade=facade
        )
    except Exception:  # noqa: BLE001 — error-isolation, mirrors the corpus leg
        logger.exception("seed: roadmap ingest failed for %s (slug=%s)",
                         company_id, slug)
        return {"status": "error"}


def seed_incremental(facade: GraphFacade, company_id: str, slug: str) -> dict:
    """Populate the KG before synthesis, incrementally.

    The corpus seed ALWAYS runs (extracting only docs not already ingested),
    so a doc uploaded after the first brief reaches the graph. The roadmap leg
    ALSO always runs, ledger-deduped to a no-op when the current roadmap version
    is already in the graph. Connectors are pulled ONLY on a first-ever (empty)
    KG — they have their own ongoing sync path, so we don't re-pull them on every
    brief regen.

    Returns {"corpus": <totals>, "roadmap": <status>, "connectors":
    <totals>|None, "was_empty": bool}.
    """
    was_empty = _kg_is_empty(facade, company_id)
    if was_empty:
        logger.info("KG empty for company=%s (slug=%s) — first-time seed "
                    "(corpus + connectors) before synthesis", company_id, slug)
    corpus = _seed_from_corpus(facade, company_id, slug)
    roadmap = _seed_from_roadmap(facade, company_id, slug)
    connectors = _seed_from_connectors(facade, company_id) if was_empty else None
    return {"corpus": corpus, "roadmap": roadmap, "connectors": connectors,
            "was_empty": was_empty}


def generate_all_synthesis_briefs() -> None:
    """Generate a synthesis brief for every company, warming drill-downs.

    The startup brief-generation pass. Mirrors the scheduler's per-company
    synthesis cycle: error-isolated per company so one bad slug/empty-KG/LLM
    hiccup is logged and skipped without aborting the rest, and the whole pass
    never blocks or breaks startup.
    """
    from app.brief_runner import warm_synthesis_drilldowns
    from app.db.companies import list_companies

    try:
        companies = list_companies()
    except Exception:  # noqa: BLE001 — startup must never block on this
        logger.exception("synthesis startup: failed to list companies")
        return

    for company in companies:
        slug = company.get("slug") or company.get("id")
        if not slug:
            continue
        try:
            # Startup/background pass: nobody is waiting, so take the
            # half-price batch path.
            generate_brief_for(slug, batch=True)
            warm_synthesis_drilldowns(slug)
        except EmptyKnowledgeGraphError:
            # Benign: this company simply has no themes/signals yet (nothing
            # ingested). Not a failure — log at INFO so the startup pass isn't
            # full of false errors.
            logger.info("synthesis startup: skipping %s — KG has no themes "
                        "with signals yet", slug)
        except Exception:  # noqa: BLE001 — per-company isolation
            logger.exception("synthesis startup: brief generation failed for %s",
                             slug)


def generate_brief_for(
    company_id_or_slug: str, *, deliver: bool = True, force: bool = False,
    # Passed straight to run_synthesis. OFF by default because two user-facing
    # routes reach this function (`routes/brief.py`, and `routes/synthesis.py`
    # via run_synthesis); only the scheduler's background passes opt in.
    batch: bool = False,
) -> dict:
    """Generate + persist the KG-driven Top Insights brief for one company.

    ``deliver=False`` suppresses the on-generation Slack/email push (see
    run_synthesis) — for callers that deliver on their own schedule (the weekly
    scheduler) or send their own short notification (the regenerate paths).

    Resolves slug↔company_id, incrementally seeds the KG (always picking up
    newly-uploaded corpus docs), then runs synthesis (which save_brief()s into
    the `briefs` table the UI reads). Returns the brief payload. Raises
    ValueError if the identifier is unknown or if the KG is still empty after
    seeding (run_synthesis raises on no themes).

    Data-source gate: after seeding (so non-evidence connectors like Jira still
    reach the KG for PRDs/chat), generation is refused with
    NoBriefDataSourceError unless the company has an evidence-bearing source
    (brief_gate.has_brief_data_source — same rule as the 409ing endpoints).
    This closes the scheduler/startup/pipeline paths, which are not endpoint-
    gated: a company whose only connections are pm/code/design/comms/docs must
    never get a Top Insights brief, even though those connectors DO seed the
    KG. The check runs before the cache-return too, so an evidence-less company
    yields nothing from this path rather than re-serving a stale brief.

    Refresh-gating, in two parts. Synthesis is skipped and the existing brief
    returned unchanged unless BOTH hold:

      1. a new signal has entered the KG since the current brief was generated
         (an unchanged company keeps its brief rather than regenerating an
         identical one), and
      2. the current brief is at least `MIN_RECOMPOSE_HOURS` old.

    Condition 1 alone was the whole gate, and it almost never fired: seeding
    runs first (it is what CREATES the signals we then detect) and connector
    syncs write signals continuously, so a weekly brief was being recomposed on
    opus several times an hour. Condition 2 is what makes the gate mean
    something — see MIN_RECOMPOSE_HOURS.

    Both checks are timestamp-based, so they also catch signals written by other
    paths (DS agent, connector sync) since the last brief. The first-ever brief
    (no prior) always synthesizes, preserving EmptyKnowledgeGraphError on an
    empty KG.

    `force=True` bypasses condition 2 only — an explicit user "Regenerate"
    recomposes now even if the current brief is minutes old. It does NOT bypass
    condition 1: with nothing new in the graph there is nothing to recompose
    into, and the result would be identical output at full cost.
    """
    company_id, slug = resolve_company(company_id_or_slug)
    facade = GraphFacade()

    # Capture the current brief (if any) + its timestamp BEFORE seeding, so the
    # comparison point is the moment the existing brief was generated.
    prior = get_current_brief(slug)
    prior_ts = prior.get("generated_at") if prior else None

    seed_incremental(facade, company_id, slug)

    # Evidence gate (see docstring): seeding above already ran, so Jira/GitHub/…
    # signals are in the KG — but without an evidence source there is no brief.
    if not has_brief_data_source(company_id, slug):
        logger.info(
            "no evidence-bearing data source for company=%s (slug=%s) — "
            "refusing brief generation (KG seeding still ran)",
            company_id, slug,
        )
        raise NoBriefDataSourceError(NO_DATA_SOURCE_MESSAGE)

    # Skip the expensive synthesis when nothing new has entered the KG since the
    # current brief was generated, OR when the current brief is younger than the
    # recompose floor. Both return the existing brief untouched.
    skip_reason = None
    if prior is not None and prior_ts:
        if not facade.has_signals_since(company_id, prior_ts):
            skip_reason = "KG unchanged"
        elif not force and _within_recompose_floor(company_id, prior_ts):
            skip_reason = (
                f"brief younger than the {_min_recompose_hours(company_id)}h "
                f"recompose floor"
            )
    if skip_reason:
        logger.info(
            "%s since brief %s (generated_at=%s) for company=%s "
            "(slug=%s) — skipping synthesis, returning existing brief",
            skip_reason, prior.get("id"), prior_ts, company_id, slug,
        )
        # Flag that this brief came from cache (synthesis skipped ⇒ NOT delivered
        # this run). The weekly scheduler tick uses this to deliver the brief on
        # schedule without double-sending a brief run_synthesis just delivered.
        prior["_from_cache"] = True
        return prior

    return run_synthesis(facade, company_id, dataset_slug=slug, deliver=deliver,
                         batch=batch)
