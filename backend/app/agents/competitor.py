"""Competitor analysis agent.

Identifies competitors from existing corpus context, scrapes their
public web presence, and produces a comparative analysis written
to competitor_analysis.md in the corpus.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app import db
from app.agents.scraper import fetch_page, search_ddg, scrape_multiple
from app.config import settings
from app.llm import call_json, call_md

logger = logging.getLogger(__name__)

COMPETITOR_ID_SYSTEM = """\
You are a competitive intelligence analyst. Given context about a company,
identify their top 3-5 direct competitors. Return JSON only.
"""

COMPETITOR_ID_SCHEMA = {
    "type": "object",
    "properties": {
        "competitors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "website": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["name", "website", "reason"],
            },
        },
    },
    "required": ["competitors"],
}

COMPETITOR_ANALYSIS_SYSTEM = """\
You are a competitive intelligence analyst. Given scraped data about a company
and its competitors, produce a structured markdown competitive analysis:

1. **Competitive Landscape Overview** — Market category, key players, market dynamics
2. **Feature Comparison Matrix** — Table comparing key capabilities across competitors
3. **Pricing Analysis** — Pricing models and tiers where publicly available
4. **Positioning & Messaging** — How each competitor positions vs the company
5. **Strengths & Weaknesses** — Per competitor SWOT-style assessment
6. **Competitive Threats** — Where competitors are gaining ground
7. **Opportunities** — Gaps competitors leave open
8. **Strategic Recommendations** — 3-5 actionable recommendations

Rules:
- Only use information from the provided scraped data
- Mark data availability per competitor (FULL / PARTIAL / LIMITED)
- Include source URLs
- If a competitor's data is unavailable, note it rather than guessing
- Use markdown tables for the feature matrix
- Keep the report under 4000 words
"""


def _read_company_context(dataset: str) -> tuple[str, str]:
    """Return (company_name, corpus_excerpt) for competitor identification."""
    corpus_dir = settings.data_path / dataset
    company_name = dataset.replace("_", " ").replace("-", " ").title()
    corpus_text = ""

    # Read onboarding context
    for name in ("onboarding_context.md", "_context.md"):
        path = corpus_dir / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            corpus_text += text + "\n\n"
            for line in text.splitlines():
                if line.startswith("# "):
                    company_name = line.lstrip("# ").strip()
                    break
            break

    # Read first few corpus files for additional context
    if corpus_dir.exists():
        for p in sorted(corpus_dir.glob("*.md"))[:5]:
            if not p.name.startswith("_") and p.name != "competitor_analysis.md":
                corpus_text += p.read_text(encoding="utf-8")[:2000] + "\n\n"

    return company_name, corpus_text[:8000]


def _get_configured_competitors(dataset: str) -> list[dict[str, str]] | None:
    """Check if competitors are manually configured in input sources."""
    try:
        sources = db.list_input_sources(dataset)
        for src in sources:
            if src.get("source_type") == "competitor_agent":
                config = src.get("config") or {}
                competitors = config.get("competitors")
                if isinstance(competitors, list) and competitors:
                    return competitors
    except Exception:
        pass
    return None


async def _identify_competitors(
    company_name: str, corpus_text: str,
) -> list[dict[str, str]]:
    """Use Claude to identify competitors from corpus context."""
    user = (
        f"Company: {company_name}\n\n"
        f"Company Context (from their knowledge base):\n{corpus_text}\n\n"
        f"Identify the top 3-5 direct competitors for {company_name}."
    )
    try:
        result = call_json(
            system=COMPETITOR_ID_SYSTEM,
            user=user,
            schema=COMPETITOR_ID_SCHEMA,
            max_tokens=2000,
        )
        return result.get("competitors", [])
    except Exception as exc:
        logger.warning("Competitor identification failed: %s", exc)
        return []


async def run_competitor_agent(dataset: str) -> dict[str, Any]:
    """Run the competitor analysis agent for a dataset.

    Returns a result dict with status and competitor count.
    """
    corpus_dir = settings.data_path / dataset
    corpus_dir.mkdir(parents=True, exist_ok=True)

    company_name, corpus_text = _read_company_context(dataset)

    # 1. Get competitors (manual config or LLM-identified)
    competitors = _get_configured_competitors(dataset)
    if not competitors:
        competitors = await _identify_competitors(company_name, corpus_text)

    if not competitors:
        logger.warning("No competitors identified for %s", dataset)
        return {"status": "no_competitors", "dataset": dataset}

    logger.info(
        "Analyzing %d competitors for %s: %s",
        len(competitors), dataset,
        ", ".join(c.get("name", "?") for c in competitors),
    )

    # 2. Scrape competitor data
    scraped_parts: list[str] = []
    scraped_parts.append(f"## Company: {company_name}\n")
    scraped_parts.append(f"Context:\n{corpus_text[:3000]}\n\n")

    for comp in competitors[:5]:
        comp_name = comp.get("name", "Unknown")
        comp_url = comp.get("website", "")
        scraped_parts.append(f"\n## Competitor: {comp_name}\n")
        scraped_parts.append(f"Reason: {comp.get('reason', 'Direct competitor')}\n")

        # Scrape their website
        if comp_url:
            text = await fetch_page(comp_url, max_chars=8000)
            if text:
                scraped_parts.append(f"\n### Homepage ({comp_url}):\n{text[:5000]}\n")

            # Try pricing page
            for suffix in ("/pricing", "/plans", "/price"):
                pricing_url = comp_url.rstrip("/") + suffix
                pricing_text = await fetch_page(pricing_url, max_chars=5000)
                if pricing_text and len(pricing_text) > 200:
                    scraped_parts.append(
                        f"\n### Pricing ({pricing_url}):\n{pricing_text[:3000]}\n"
                    )
                    break

        # Search for recent news about competitor
        try:
            news = await search_ddg(f"{comp_name} company news 2026", max_results=3)
            if news:
                scraped_parts.append(f"\n### Recent News for {comp_name}:\n")
                for r in news:
                    scraped_parts.append(
                        f"- **{r['title']}** ({r['url']})\n  {r['snippet']}\n"
                    )
        except Exception:
            pass

    # 3. Analyze with Claude
    scraped_text = "\n".join(scraped_parts)
    user_prompt = (
        f"Company being analyzed: {company_name}\n\n"
        f"Scraped Competitor Data:\n\n{scraped_text}\n\n"
        f"Produce a comprehensive competitive analysis for {company_name} "
        f"against the identified competitors."
    )

    try:
        report = call_md(
            system=COMPETITOR_ANALYSIS_SYSTEM,
            user=user_prompt,
            max_tokens=10000,
        )
    except Exception as exc:
        logger.error("Competitor analysis LLM call failed: %s", exc)
        return {"status": "llm_failed", "dataset": dataset, "error": str(exc)}

    # 4. Write to corpus
    header = (
        f"# Competitive Analysis — {company_name}\n\n"
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"**Competitors analyzed:** "
        f"{', '.join(c.get('name', '?') for c in competitors[:5])}\n"
        f"**Sources:** Public websites, pricing pages, DuckDuckGo news\n\n"
        f"---\n\n"
    )
    full_md = header + report

    out_path = corpus_dir / "competitor_analysis.md"
    out_path.write_text(full_md, encoding="utf-8")
    logger.info("Wrote competitor_analysis.md for %s (%d chars)", dataset, len(full_md))

    # 5. Auto-enable input source
    try:
        db.upsert_input_source(
            dataset, "competitor_agent", enabled=True,
            config={
                "last_run_at": db.utc_now(),
                "competitors": competitors[:5],
            },
        )
    except Exception:
        logger.warning("Failed to enable competitor_agent input source", exc_info=True)

    return {
        "status": "completed",
        "dataset": dataset,
        "competitors_analyzed": len(competitors[:5]),
        "chars": len(full_md),
    }
