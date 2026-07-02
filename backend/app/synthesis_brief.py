"""KG-synthesis brief engine — the read path the UI already speaks.

This is the engine behind the weekly brief. It bridges the UI's dataset-slug
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

from app.corpus import load_corpus
from app.db.briefs import get_current_brief
from app.db.companies import company_id_for_slug, slug_for_company_id
from app.graph.extractor import _NS, extract_document
from app.graph.facade import GraphFacade
from app.graph.types import Source
from app.synthesis.agent import EmptyKnowledgeGraphError, run_synthesis

logger = logging.getLogger(__name__)

# Bounds so a seed can never hang the request / scheduler cycle. The KG is
# idempotent (content-keyed signal ids), so a capped first pass that misses a
# few docs is corrected on the next run; the point is to never block.
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

    Raises ValueError if the identifier resolves to no company.
    """
    if _looks_like_uuid(company_id_or_slug):
        slug = slug_for_company_id(company_id_or_slug)
        if slug is None:
            raise ValueError(f"No company for id {company_id_or_slug!r}")
        return company_id_or_slug, slug
    company_id = company_id_for_slug(company_id_or_slug)
    if company_id is None:
        raise ValueError(f"No company for slug {company_id_or_slug!r}")
    return company_id, company_id_or_slug


def _kg_is_empty(facade: GraphFacade, company_id: str) -> bool:
    """True when the company's KG has no active (non-stale) signals — i.e.
    convergence would find nothing to rank, so we must seed first."""
    return not facade.active_signals(company_id)


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
    """
    totals = {"signals": 0, "themes": 0, "skipped": 0, "docs": 0, "unchanged": 0}
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

    extracted = 0
    for doc in corpus.docs:
        sha = hashlib.sha256(f"{company_id}|{doc.text}".encode()).hexdigest()
        if sha in existing:
            totals["unchanged"] += 1
            continue
        # Cap NEW extractions per run; keep cheaply skipping unchanged docs.
        if extracted >= MAX_SEED_DOCS:
            continue
        try:
            r = extract_document(
                facade, company_id, doc_name=doc.name, text=doc.text,
                origin="upload",
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
        except Exception:  # noqa: BLE001 — error-isolation per doc
            logger.exception("seed: corpus extraction failed for doc %s", doc.name)
    return totals


def _seed_from_connectors(facade: GraphFacade, company_id: str) -> dict:
    """Best-effort pull of any connected providers into the KG.

    Bounded (MAX_SEED_CONNECTORS) and fully isolated: a missing puller, a bad
    token, or a provider outage is logged and skipped — it never aborts the
    seed. Providers without a kg_ingest puller (figma/slack/drive) are no-ops.
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


def seed_incremental(facade: GraphFacade, company_id: str, slug: str) -> dict:
    """Populate the KG before synthesis, incrementally.

    The corpus seed ALWAYS runs (extracting only docs not already ingested),
    so a doc uploaded after the first brief reaches the graph. Connectors are
    pulled ONLY on a first-ever (empty) KG — they have their own ongoing sync
    path, so we don't re-pull them on every brief regen.

    Returns {"corpus": <totals>, "connectors": <totals>|None, "was_empty": bool}.
    """
    was_empty = _kg_is_empty(facade, company_id)
    if was_empty:
        logger.info("KG empty for company=%s (slug=%s) — first-time seed "
                    "(corpus + connectors) before synthesis", company_id, slug)
    corpus = _seed_from_corpus(facade, company_id, slug)
    connectors = _seed_from_connectors(facade, company_id) if was_empty else None
    return {"corpus": corpus, "connectors": connectors, "was_empty": was_empty}


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
            generate_brief_for(slug)
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


def generate_brief_for(company_id_or_slug: str) -> dict:
    """Generate + persist the KG-driven weekly brief for one company.

    Resolves slug↔company_id, incrementally seeds the KG (always picking up
    newly-uploaded corpus docs), then runs synthesis (which save_brief()s into
    the `briefs` table the UI reads). Returns the brief payload. Raises
    ValueError if the identifier is unknown or if the KG is still empty after
    seeding (run_synthesis raises on no themes).

    Refresh-gating: if a current brief already exists AND no new signal has
    entered the KG since it was generated, synthesis is skipped and the
    existing brief is returned unchanged (an unchanged company keeps its brief
    instead of regenerating an identical one). Seeding still runs first — it is
    what CREATES the new signals we then detect — so a newly-uploaded doc adds
    fresh signals, `has_signals_since` becomes True, and we synthesize. The
    check is timestamp-based, so it also catches signals written by other paths
    (DS agent, connector sync) since the last brief. The first-ever brief
    (no prior) always synthesizes, preserving EmptyKnowledgeGraphError on an
    empty KG.
    """
    company_id, slug = resolve_company(company_id_or_slug)
    facade = GraphFacade()

    # Capture the current brief (if any) + its timestamp BEFORE seeding, so the
    # comparison point is the moment the existing brief was generated.
    prior = get_current_brief(slug)
    prior_ts = prior.get("generated_at") if prior else None

    seed_incremental(facade, company_id, slug)

    # Skip the expensive synthesis when nothing new has entered the KG since the
    # current brief was generated.
    if prior is not None and prior_ts and not facade.has_signals_since(
        company_id, prior_ts
    ):
        logger.info(
            "KG unchanged since brief %s (generated_at=%s) for company=%s "
            "(slug=%s) — skipping synthesis, returning existing brief",
            prior.get("id"), prior_ts, company_id, slug,
        )
        # Flag that this brief came from cache (synthesis skipped ⇒ NOT delivered
        # this run). The weekly scheduler tick uses this to deliver the brief on
        # schedule without double-sending a brief run_synthesis just delivered.
        prior["_from_cache"] = True
        return prior

    return run_synthesis(facade, company_id, dataset_slug=slug)
