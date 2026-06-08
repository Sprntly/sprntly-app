"""KG-synthesis brief engine — the read path the UI already speaks.

This is the production engine behind the weekly brief when
``settings.brief_engine == "synthesis"`` (the default). It bridges the UI's
dataset-slug world to the knowledge-graph world:

    slug → company_id  (companies.slug is the tenant key + the dataset slug)
    seed-if-empty      (no KG signals yet → extract the corpus + best-effort
                        connector pulls so convergence has something to rank)
    run_synthesis(...) (convergence → ranked insights → save_brief into the
                        SAME `briefs` table the UI's /current,/status,/{id}
                        endpoints read)

The legacy corpus→single-Claude-call path (app.brief_runner) stays available
behind the flag; this module never calls it.

Seeding is resilient + bounded: corpus docs and connector pulls are capped, and
every extraction is error-isolated so one bad doc/connector can't abort the
seed. Seeding only runs when the KG has no active signals for the company — a
populated graph skips straight to synthesis.
"""
from __future__ import annotations

import logging

from app.corpus import load_corpus
from app.db.companies import company_id_for_slug, slug_for_company_id
from app.graph.extractor import extract_document
from app.graph.facade import GraphFacade
from app.synthesis.agent import run_synthesis

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
    """Extract up to MAX_SEED_DOCS of the company's corpus into the KG.

    Error-isolated per doc (mirrors /v1/synthesis/seed): one bad doc logs +
    is skipped, the rest proceed. Missing corpus is not fatal — a company
    might be connector-only.
    """
    totals = {"signals": 0, "themes": 0, "skipped": 0, "docs": 0}
    try:
        corpus = load_corpus(slug)
    except (FileNotFoundError, RuntimeError) as e:
        logger.info("seed: no corpus for %s (%s) — skipping corpus extraction",
                    slug, e)
        return totals
    for doc in corpus.docs[:MAX_SEED_DOCS]:
        try:
            r = extract_document(
                facade, company_id, doc_name=doc.name, text=doc.text
            )
            for k in ("signals", "themes", "skipped"):
                totals[k] += r[k]
            totals["docs"] += 1
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


def seed_if_empty(facade: GraphFacade, company_id: str, slug: str) -> dict | None:
    """Populate the KG from the corpus + connectors, but only when it has no
    active signals yet. Returns the seed summary, or None if the KG was already
    populated (no seeding done)."""
    if not _kg_is_empty(facade, company_id):
        return None
    logger.info("KG empty for company=%s (slug=%s) — seeding before synthesis",
                company_id, slug)
    corpus = _seed_from_corpus(facade, company_id, slug)
    connectors = _seed_from_connectors(facade, company_id)
    return {"corpus": corpus, "connectors": connectors}


def generate_brief_for(company_id_or_slug: str) -> dict:
    """Generate + persist the KG-driven weekly brief for one company.

    Resolves slug↔company_id, seeds the KG if empty, then runs synthesis
    (which save_brief()s into the `briefs` table the UI reads). Returns the
    brief payload. Raises ValueError if the identifier is unknown or if the KG
    is still empty after seeding (run_synthesis raises on no themes).
    """
    company_id, slug = resolve_company(company_id_or_slug)
    facade = GraphFacade()
    seed_if_empty(facade, company_id, slug)
    return run_synthesis(facade, company_id, dataset_slug=slug)
