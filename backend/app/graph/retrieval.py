"""KG retrieval for the Ask/chat surface (#18 — chat answers from the brain).

Pure, tenant-scoped retrieval over the knowledge graph. No LLM calls: this
layer embeds the question, finds the most relevant themes via the pgvector
kNN primitive (`facade.find_candidates`), gathers the signals wired to those
themes plus recent non-stale signals, and folds in the §20 session context
(active hypotheses / recent decisions / measured outcomes). It returns a
structured, ranked, deduped context bundle capped to a token budget that the
Ask runner renders into a "KNOWLEDGE GRAPH CONTEXT" prompt section.

Why this exists: connector data + agent findings live in the KG
(kg_signal / kg_entity) and previously surfaced only through the weekly
brief. The Ask surface answered from the legacy per-dataset markdown corpus
alone, so "ask about my HubSpot pipeline" had nothing to retrieve. This
bridges chat → KG.

Resilience: every read is best-effort. An empty KG (or a fake backend with
no pgvector, where `find_candidates` returns []) yields an empty bundle and
the caller falls back to corpus-only — the pre-#18 behaviour.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.graph.facade import GraphFacade
from app.graph.types import SOURCE_STALE_WINDOW_DAYS, Signal

logger = logging.getLogger(__name__)

# Rough chars-per-token for the budget cap. We size by serialized content
# length rather than calling a tokenizer (no new dependency, and the cap is
# a soft guardrail, not an exact accounting). ~4 chars/token is the usual
# English approximation.
_CHARS_PER_TOKEN = 4

# Default token budget for the whole bundle's signal/theme/decision text.
# Sits well under the Ask call's max_tokens so the corpus prefix keeps its
# headroom.
DEFAULT_TOKEN_BUDGET = 2200

# How many candidate themes to pull from the kNN primitive, and how many
# signals to keep per theme before the global budget cap.
_DEFAULT_THEME_K = 12
_SIGNALS_PER_THEME = 6
# Recent non-stale signals to fold in regardless of theme match (covers
# fresh connector data not yet wired to a resolved theme).
_RECENT_SIGNALS = 8


def _recency_factor(signal: Signal, now: datetime) -> float:
    """Half-life decay using the per-source_type staleness window (#1).
    Never-expiring source types (outcome_measured) don't decay. Mirrors the
    convergence scorer so retrieval ranking agrees with brief ranking."""
    window = SOURCE_STALE_WINDOW_DAYS.get(signal.source_type)
    if not window:
        return 1.0
    age_days = max(0.0, (now - signal.valid_at).total_seconds() / 86400)
    return 0.5 ** (age_days / window)


def _signal_rank(signal: Signal, now: datetime, theme_boost: float) -> float:
    """Composite rank for a signal: evidence weight × recency, plus a boost
    when the signal is wired to a question-relevant theme. Higher = surface
    first."""
    base = signal.confidence * signal.weight * _recency_factor(signal, now)
    return base + theme_boost


def _signal_payload(signal: Signal, *, theme_label: Optional[str], rank: float) -> dict:
    """Flatten a Signal into the bundle's signal shape — content + provenance
    the LLM cites, no embedding vector."""
    return {
        "signal_id": signal.id,
        "content": signal.content,
        "kind": signal.kind,
        "source_type": signal.source_type,
        "provenance": signal.provenance or {},
        "theme": theme_label,
        "confidence": round(signal.confidence, 3),
        "rank": round(rank, 4),
    }


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def retrieve_context(
    facade: GraphFacade,
    enterprise_id: str,
    question: str,
    *,
    k: int = _DEFAULT_THEME_K,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> dict[str, Any]:
    """Retrieve a ranked, deduped KG context bundle for a chat question.

    Steps:
      1. Embed the question (best-effort; if embeddings are unavailable we
         skip the kNN theme match and fall back to recent signals only).
      2. `find_candidates(type="theme")` → the question-relevant themes.
      3. For each candidate theme, gather its inbound signal edges
         (`edges_to`, source_kind == "signal"), boosted by the theme's
         similarity score.
      4. Fold in recent non-stale `active_signals` (fresh connector data not
         yet wired to a resolved theme), without a theme boost.
      5. Dedupe by signal id, rank by (evidence weight × recency + theme
         boost), and cap the serialized content to `token_budget`.
      6. Attach `load_session_context` (hypotheses / decisions / outcomes).

    Returns a dict:
      {
        "signals":   [ {signal_id, content, kind, source_type, provenance,
                        theme, confidence, rank}, ... ],   # ranked, capped
        "themes":    [ {entity_id, label, score}, ... ],   # matched themes
        "decisions": [ {entity_id, label, properties}, ... ],  # recent
        "hypotheses":[ {entity_id, label, properties}, ... ],
        "outcomes":  [ {entity_id, label, properties}, ... ],
        "kg_refs":   [ ...signal+entity ids used... ],     # for the decision log
        "token_estimate": <int>,
        "empty": <bool>,
      }

    Pure retrieval: tenant-scoped (everything reads through `enterprise_id`),
    no writes, no LLM. Never raises on a partial-KG read — degrades to an
    emptier bundle and logs.
    """
    now = datetime.now(timezone.utc)

    # 1) Embed the question. Embeddings can be unconfigured (no OPENAI key) or
    #    the call can fail; either way we still return recent signals.
    qvec: Optional[list[float]] = None
    try:
        from app.graph.embeddings import embed_texts

        vecs = embed_texts([question])
        qvec = vecs[0] if vecs else None
    except Exception as exc:  # noqa: BLE001 — retrieval must not hard-fail Ask
        logger.info("Ask KG retrieval: embedding unavailable (%s); recent-only", exc)

    # 2) kNN theme match (returns [] on the fake/no-pgvector backend).
    matched_themes: list[tuple[Any, float]] = []
    if qvec is not None:
        try:
            matched_themes = facade.find_candidates(enterprise_id, "theme", qvec, k=k)
        except Exception as exc:  # noqa: BLE001
            logger.info("Ask KG retrieval: find_candidates failed (%s)", exc)
            matched_themes = []

    # 3) Per-theme inbound signals, boosted by theme similarity.
    #    by_id dedupes; we keep the highest rank seen for any signal.
    by_id: dict[str, tuple[float, dict]] = {}
    themes_out: list[dict] = []
    for theme, score in matched_themes:
        themes_out.append(
            {"entity_id": theme.id, "label": theme.canonical_label, "score": round(float(score), 4)}
        )
        # Normalize the similarity score (cosine ~0..1) into a modest boost so
        # theme-matched evidence floats above generic recent signals without
        # swamping the evidence-weight term.
        boost = max(0.0, float(score)) * 0.5
        try:
            edges = facade.edges_to(enterprise_id, theme.id)
        except Exception as exc:  # noqa: BLE001
            logger.info("Ask KG retrieval: edges_to(%s) failed (%s)", theme.id, exc)
            continue
        kept = 0
        for edge in edges:
            if edge.source_kind != "signal":
                continue
            sig = facade.get_signal(enterprise_id, edge.source_id)
            if sig is None or (sig.properties or {}).get("superseded_by"):
                continue
            rank = _signal_rank(sig, now, boost)
            payload = _signal_payload(sig, theme_label=theme.canonical_label, rank=rank)
            prev = by_id.get(sig.id)
            if prev is None or rank > prev[0]:
                by_id[sig.id] = (rank, payload)
            kept += 1
            if kept >= _SIGNALS_PER_THEME:
                break

    # 4) Recent non-stale signals (no theme boost). Covers fresh connector
    #    data the synthesis pass hasn't wired to a theme yet.
    try:
        recent = facade.active_signals(enterprise_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("Ask KG retrieval: active_signals failed (%s)", exc)
        recent = []
    recent.sort(key=lambda s: s.transaction_at, reverse=True)
    for sig in recent[:_RECENT_SIGNALS]:
        if (sig.properties or {}).get("superseded_by"):
            continue
        if sig.id in by_id:
            continue
        rank = _signal_rank(sig, now, 0.0)
        by_id[sig.id] = (rank, _signal_payload(sig, theme_label=None, rank=rank))

    # 5) Rank globally + apply the token budget cap.
    ranked = sorted(by_id.values(), key=lambda t: -t[0])
    signals_out: list[dict] = []
    used_tokens = 0
    for _, payload in ranked:
        cost = _approx_tokens(payload["content"])
        if signals_out and used_tokens + cost > token_budget:
            break
        signals_out.append(payload)
        used_tokens += cost

    # 6) Session context — the §2 ledger spine (hypotheses/decisions/outcomes).
    decisions_out: list[dict] = []
    hypotheses_out: list[dict] = []
    outcomes_out: list[dict] = []
    try:
        ctx = facade.load_session_context(enterprise_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("Ask KG retrieval: load_session_context failed (%s)", exc)
        ctx = {}

    def _entities(rows: list) -> list[dict]:
        return [
            {"entity_id": e.id, "label": e.canonical_label, "properties": e.properties or {}}
            for e in rows
        ]

    decisions_out = _entities(ctx.get("recent_decisions") or [])
    hypotheses_out = _entities(ctx.get("active_hypotheses") or [])
    outcomes_out = _entities(ctx.get("recent_outcomes") or [])

    # kg_refs: every node id that fed the answer — signals surfaced + themes
    # matched + ledger entities. Drives the decision log's kg_refs column.
    kg_refs: list[str] = [s["signal_id"] for s in signals_out]
    kg_refs += [t["entity_id"] for t in themes_out]
    kg_refs += [e["entity_id"] for e in decisions_out + hypotheses_out + outcomes_out]

    empty = not (signals_out or themes_out or decisions_out or hypotheses_out or outcomes_out)

    return {
        "signals": signals_out,
        "themes": themes_out,
        "decisions": decisions_out,
        "hypotheses": hypotheses_out,
        "outcomes": outcomes_out,
        "kg_refs": kg_refs,
        "token_estimate": used_tokens,
        "empty": empty,
    }


def render_context_section(bundle: dict[str, Any]) -> str:
    """Render a retrieval bundle into the markdown block injected into the Ask
    prompt under a "KNOWLEDGE GRAPH CONTEXT" header. Empty bundle → "" (the
    caller then runs corpus-only). Provenance + source_type travel with each
    signal so the grounding rules can cite them."""
    if not bundle or bundle.get("empty"):
        return ""

    lines: list[str] = ["# KNOWLEDGE GRAPH CONTEXT"]
    lines.append(
        "Live signals + entities from connected sources and prior agent findings. "
        "Treat these as first-class evidence alongside the corpus. Cite the "
        "source_type (and provenance where present); never invent."
    )

    themes = bundle.get("themes") or []
    if themes:
        lines.append("\n## Relevant themes")
        for t in themes:
            lines.append(f"- {t['label']} (relevance {t['score']})")

    signals = bundle.get("signals") or []
    if signals:
        lines.append("\n## Signals")
        for s in signals:
            theme = f" · theme: {s['theme']}" if s.get("theme") else ""
            prov = s.get("provenance") or {}
            src = prov.get("source") or prov.get("doc") or prov.get("connector")
            prov_txt = f" · provenance: {src}" if src else ""
            lines.append(
                f"- [{s['source_type']}/{s['kind']}]{theme}{prov_txt}: {s['content']}"
            )

    hyps = bundle.get("hypotheses") or []
    if hyps:
        lines.append("\n## Open hypotheses")
        for h in hyps:
            lines.append(f"- {h['label']}")

    decisions = bundle.get("decisions") or []
    if decisions:
        lines.append("\n## Recent decisions")
        for d in decisions:
            lines.append(f"- {d['label']}")

    outcomes = bundle.get("outcomes") or []
    if outcomes:
        lines.append("\n## Measured outcomes")
        for o in outcomes:
            lines.append(f"- {o['label']}")

    return "\n".join(lines)
