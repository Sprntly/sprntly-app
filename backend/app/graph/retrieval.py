"""KG retrieval for the Ask/chat surface (#18 — chat answers from the brain).

Pure, tenant-scoped retrieval over the knowledge graph. No LLM calls: this
layer embeds the question, finds the most relevant themes via the pgvector
kNN primitive (`facade.find_candidates`), gathers the signals wired to those
themes plus recent non-stale signals, and folds in the §20 session context
(active hypotheses / recent decisions / measured outcomes). It returns a
structured, ranked, deduped context bundle capped to a token budget that the
Ask runner renders into a "LIVE CONTEXT FROM CONNECTED SOURCES" prompt section.

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

        vecs = embed_texts([question], enterprise_id=enterprise_id,
                           purpose="kg_retrieval")
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
    #
    #    Gather the inbound signal edges for every matched theme FIRST, batch
    #    the signal fetch into ONE query (kills the per-edge N+1), then walk the
    #    themes again applying the per-theme cap exactly as before. The pre-walk
    #    only reads `edges_to` (one query per theme — same as before) and records
    #    each theme's ordered signal source_ids; no per-signal round-trips here.
    by_id: dict[str, tuple[float, dict]] = {}
    themes_out: list[dict] = []
    theme_edge_ids: list[tuple[Any, float, list[str]]] = []
    needed_ids: list[str] = []
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
        edge_ids = [e.source_id for e in edges if e.source_kind == "signal"]
        theme_edge_ids.append((theme, boost, edge_ids))
        needed_ids.extend(edge_ids)

    # ONE batched fetch for every theme-edge signal across all matched themes.
    signals_by_id = facade.get_signals(enterprise_id, needed_ids)

    for theme, boost, edge_ids in theme_edge_ids:
        kept = 0
        for sid in edge_ids:
            sig = signals_by_id.get(sid)
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


# ── insight → hypothesis → SUPPORTS-signals evidence trail ────────────────
#
# The synthesis agent writes, per chosen brief insight, a `hypothesis` Entity
# whose `properties.theme_id` copies the insight's `theme_id`, with an
# ADDRESSES edge to the theme and SUPPORTS edges (signal → hypothesis) from the
# evidence that backed it. This is the SAME "evidence trail" that grounds the
# brief insight; resolving it here lets evidence AND PRD ground on identical KG
# primitives instead of re-deriving from the corpus.

# How many theme-convergence signals to fold in alongside the SUPPORTS trail,
# capturing breadth the hypothesis edges may not all carry.
_TRAIL_THEME_SIGNALS = 8


def _trail_signal_payload(signal: Signal, *, edge_type: str) -> dict:
    """Flatten a Signal into the trail's signal shape — content + source_type +
    provenance + confidence the PRD/evidence cite, tagged with the edge that
    surfaced it (SUPPORTS = direct backing, theme = convergence breadth)."""
    return {
        "signal_id": signal.id,
        "content": signal.content,
        "kind": signal.kind,
        "source_type": signal.source_type,
        "provenance": signal.provenance or {},
        "confidence": round(signal.confidence, 3),
        "edge": edge_type,
    }


def resolve_insight_hypothesis(
    facade: GraphFacade,
    enterprise_id: str,
    theme_id: Optional[str],
    insight_title: Optional[str],
) -> Optional[Any]:
    """The SINGLE resolver from a brief insight to its hypothesis Entity.

    Both the PRD trail (this module) and the Evidence page (`evidence_kg`) call
    THIS function, so they always ground on the SAME hypothesis for a given
    insight — previously the two had separate resolvers that diverged on the
    missing-`theme_id` path and could drift further apart.

    Resolution order (the synthesis agent writes one hypothesis Entity per
    insight, copying the insight's `theme_id` into `properties.theme_id` and the
    insight title into `canonical_label`):
      1. Primary key: `properties.theme_id == theme_id`. Among matches, prefer
         the one whose `canonical_label` matches the insight title, then the most
         recently written (a theme can recur across weekly briefs).
      2. No `theme_id` on the insight: fall back to a `canonical_label` match on
         the insight title (the safer behavior — a title-keyed hypothesis is the
         right grounding when the theme link is absent). With neither a theme_id
         nor a title, there is nothing to match → None (empty trail), never a
         blind corpus guess.
    Returns None when nothing matches; the caller degrades to a theme-only trail
    or to the corpus. Best-effort: a failed KG read returns None, never raises."""
    try:
        hyps = facade.query_entities(enterprise_id, type="hypothesis")
    except Exception as exc:  # noqa: BLE001 — trail read must not hard-fail
        logger.info("evidence trail: query hypotheses failed (%s)", exc)
        return None
    if not hyps:
        return None
    matches: list[Any] = []
    if theme_id:
        matches = [h for h in hyps if (h.properties or {}).get("theme_id") == theme_id]
        if matches and insight_title:
            titled = [h for h in matches if h.canonical_label == insight_title[:200]]
            if titled:
                matches = titled
    # No theme_id (or no theme match): title fallback so Evidence and PRD agree.
    if not matches and insight_title:
        matches = [h for h in hyps if h.canonical_label == insight_title[:200]]
    if not matches:
        return None
    matches.sort(key=lambda h: h.transaction_at, reverse=True)
    return matches[0]


# Back-compat alias for the module-internal call site (and any importers that
# referenced the old private name); the shared resolver above is canonical.
_resolve_hypothesis = resolve_insight_hypothesis


def _theme_id_for_insight(insight: dict) -> Optional[str]:
    return insight.get("theme_id") if isinstance(insight, dict) else None


def insight_evidence_trail(
    facade: GraphFacade,
    enterprise_id: str,
    brief: dict,
    insight_index: int,
    *,
    insight: dict | None = None,
) -> dict[str, Any]:
    """Resolve the KG evidence trail behind one brief insight.

    Walks insight → theme_id → hypothesis (the synthesis-written Entity) →
    SUPPORTS signals, and folds in the theme's convergence signals for breadth.
    Each signal carries content/source_type/provenance/confidence so the
    consumer (evidence OR PRD) can cite the actual data-source signals.

    `insight` overrides brief.insights[insight_index] when supplied — used by the
    ideation PRD path, where the theme is NOT in the brief payload but carries the
    same shape ({theme_id, title, ...}). When omitted, the insight is read from
    the brief at insight_index (the brief-insight path). The walk depends only on
    the insight's theme_id/title, so both paths resolve the identical trail.

    Returns a dict:
      {
        "insight":      <the insight dict>,
        "theme_id":     <str | None>,
        "hypothesis":   {entity_id, label, properties} | None,
        "signals":      [ {signal_id, content, kind, source_type, provenance,
                           confidence, edge}, ... ],   # SUPPORTS first, deduped
        "kg_refs":      [ ...signal + hypothesis + theme ids... ],
        "empty":        <bool>,   # True when no KG backing was found
      }

    Pure, tenant-scoped, best-effort: every read is isolated, and an unbacked
    insight (no theme_id, no hypothesis, no signals) yields empty=True so the
    caller can fall back to the corpus. The helper name may collide with the
    parallel evidence branch at merge — that is intentional; both consumers
    want exactly this shape.
    """
    if insight is None:
        insights = (brief.get("insights") or []) if isinstance(brief, dict) else []
        insight = (
            insights[insight_index]
            if 0 <= insight_index < len(insights)
            else {}
        )
    theme_id = _theme_id_for_insight(insight)
    insight_title = insight.get("title") if isinstance(insight, dict) else None

    by_id: dict[str, dict] = {}  # dedupe; SUPPORTS edges win over theme edges

    # Collect the SUPPORTS + theme inbound signal edges FIRST, then batch the
    # signal fetch into ONE query (kills the per-edge N+1). The `edges_to` reads
    # are unchanged (one per target); only the per-signal lookups are batched.
    hyp = resolve_insight_hypothesis(facade, enterprise_id, theme_id, insight_title)
    hyp_out: Optional[dict] = None
    support_ids: list[str] = []
    if hyp is not None:
        hyp_out = {
            "entity_id": hyp.id,
            "label": hyp.canonical_label,
            "properties": hyp.properties or {},
        }
        try:
            edges = facade.edges_to(enterprise_id, hyp.id, type="SUPPORTS")
        except Exception as exc:  # noqa: BLE001
            logger.info("evidence trail: edges_to(hyp=%s) failed (%s)", hyp.id, exc)
            edges = []
        support_ids = [e.source_id for e in edges if e.source_kind == "signal"]

    theme_edge_ids: list[str] = []
    if theme_id:
        try:
            theme_edges = facade.edges_to(enterprise_id, theme_id)
        except Exception as exc:  # noqa: BLE001
            logger.info("evidence trail: edges_to(theme=%s) failed (%s)", theme_id, exc)
            theme_edges = []
        theme_edge_ids = [e.source_id for e in theme_edges if e.source_kind == "signal"]

    # ONE batched fetch for both edge sets.
    signals_by_id = facade.get_signals(enterprise_id, support_ids + theme_edge_ids)

    # 1) SUPPORTS signals — the direct backing the synthesis agent wired to the
    #    hypothesis. These are the strongest evidence for the insight.
    for sid in support_ids:
        sig = signals_by_id.get(sid)
        if sig is None or (sig.properties or {}).get("superseded_by"):
            continue
        by_id[sig.id] = _trail_signal_payload(sig, edge_type="SUPPORTS")

    # 2) Theme convergence signals — every non-stale signal wired to the theme,
    #    for breadth the SUPPORTS set may not fully carry. Doesn't overwrite a
    #    signal already tagged SUPPORTS.
    kept = 0
    for sid in theme_edge_ids:
        sig = signals_by_id.get(sid)
        if sig is None or (sig.properties or {}).get("superseded_by"):
            continue
        if sig.id not in by_id:
            by_id[sig.id] = _trail_signal_payload(sig, edge_type="theme")
        kept += 1
        if kept >= _TRAIL_THEME_SIGNALS:
            break

    # SUPPORTS-edged signals first (direct backing), theme-convergence after.
    signals_out = sorted(
        by_id.values(), key=lambda s: 0 if s["edge"] == "SUPPORTS" else 1
    )

    kg_refs: list[str] = [s["signal_id"] for s in signals_out]
    if hyp_out:
        kg_refs.append(hyp_out["entity_id"])
    if theme_id:
        kg_refs.append(theme_id)

    empty = not signals_out and hyp_out is None

    return {
        "insight": insight,
        "theme_id": theme_id,
        "hypothesis": hyp_out,
        "signals": signals_out,
        "kg_refs": kg_refs,
        "empty": empty,
    }


def render_evidence_trail_section(trail: dict[str, Any]) -> str:
    """Render an evidence trail into a markdown block the PRD/evidence prompt
    grounds on, under a "KNOWLEDGE GRAPH EVIDENCE" header. Empty trail → "".
    Each signal cites its source_type + provenance so the grounding rules can
    point a reader at the same data."""
    if not trail or trail.get("empty"):
        return ""

    lines: list[str] = ["# KNOWLEDGE GRAPH EVIDENCE"]
    lines.append(
        "The data-source signals backing this insight, drawn from the "
        "knowledge graph (the same evidence trail behind the brief). Ground "
        "every claim, number, and acceptance criterion in these signals and "
        "cite the source_type (and provenance where present); never invent."
    )

    hyp = trail.get("hypothesis")
    if hyp:
        props = hyp.get("properties") or {}
        claim = props.get("claim")
        lines.append("\n## Hypothesis (the insight's claim)")
        lines.append(f"- {hyp['label']}")
        if claim:
            lines.append(f"  - claim: {claim}")

    signals = trail.get("signals") or []
    if signals:
        lines.append("\n## Backing signals")
        for s in signals:
            prov = s.get("provenance") or {}
            src = prov.get("source") or prov.get("doc") or prov.get("connector")
            prov_txt = f" · provenance: {src}" if src else ""
            tag = "SUPPORTS" if s.get("edge") == "SUPPORTS" else "theme"
            lines.append(
                f"- [{s['source_type']}/{s['kind']} · {tag}]{prov_txt}: {s['content']}"
            )

    return "\n".join(lines)


def render_context_section(bundle: dict[str, Any]) -> str:
    """Render a retrieval bundle into the markdown block injected into the Ask
    prompt under a "LIVE CONTEXT FROM CONNECTED SOURCES" header. Empty bundle →
    "" (the caller then runs source-material-only). Provenance + source_type
    travel with each signal so the grounding rules can cite them.

    The header is deliberately plain ("connected sources", not "knowledge
    graph") so the model never echoes Sprntly's internal vocabulary into a
    user-facing answer — see VOICE_GUARD in app/prompts.py."""
    if not bundle or bundle.get("empty"):
        return ""

    lines: list[str] = ["# LIVE CONTEXT FROM CONNECTED SOURCES"]
    lines.append(
        "Live signals from your connected sources and prior agent findings. "
        "Treat these as first-class evidence alongside your source material. Cite "
        "the source_type (and provenance where present); never invent."
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
