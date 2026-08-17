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

from app.graph.embeddings import EMBEDDING_DIM
from app.graph.facade import GraphFacade
from app.graph.types import SOURCE_STALE_WINDOW_DAYS, Signal, signal_is_retired

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
#: How many rows to FETCH to fill those 8. The staleness filter runs in Python
#: after the fetch, so this needs headroom over `_RECENT_SIGNALS` — but not the
#: facade's 1000-row default, which shipped a thousand signal rows per ask to
#: read eight. See the call site for why 250 is the safe width.
_RECENT_SIGNALS_FETCH = 250

# ── The voice-of-customer preset ────────────────────────────────────────────
#
# "When someone asks for feedback, show ALL of it." The defaults above are
# sized for the general Ask, where the KG bundle is one ingredient beside a
# document corpus and every token it takes is a token the rest of the prompt
# loses. A feedback question is the opposite shape: the stored signal IS the
# answer, and a cap on it is a cap on how much of their own customers' voice
# the user is allowed to see.
#
# Under the defaults that cap was severe and, worse, INVISIBLE. At most
# `_DEFAULT_THEME_K * _SIGNALS_PER_THEME + _RECENT_SIGNALS` = 80 signals were
# ever candidates, and the token budget then cut that to ~2,200 tokens with a
# bare `break` — no count of what it dropped, nothing downstream that could say
# "there was more". A tenant with a year of support tickets and Slack feedback
# got an answer built from a couple of dozen signals and no indication that was
# a sample. That is the bug this preset fixes; `signals_dropped` (returned by
# `retrieve_context`) is the other half, so the cut is never silent again.
#
# THE NUMBERS ARE DERIVED, NOT GUESSED. `call_digest` reserves
# `_KG_CHAR_BUDGET` for the rendered bundle and trims to it on a line boundary
# WITH a marker the coverage line reports. Making the token budget larger than
# that char budget can hold is deliberate: it moves the binding constraint off
# the silent cut and onto the honest one. The pool is then sized so the budget
# can actually be filled — a budget nothing reaches is the same starvation with
# a bigger number on it.
#
# Cost is bounded by the same arithmetic: the answer model has a 1M-token
# window, and calls + Slack + a fully-spent KG budget still land around a fifth
# of it. The fan-out is bounded too — themes and their edges resolve through
# ONE chunked `edges_to_many`, and the signals behind them through ONE chunked
# `get_signals` (chunked *because* of this preset; see that method).
VOC_TOKEN_BUDGET = 120_000
VOC_THEME_K = 40
VOC_SIGNALS_PER_THEME = 100
VOC_RECENT_SIGNALS = 400

#: The preset as one `retrieve_context(**VOC_SCALE)` kwargs bundle. Exported as
#: a unit rather than four constants because the knobs only work together:
#: raising the budget while leaving the pool at 80 candidates, or the pool while
#: leaving the budget at 2,200 tokens, both look like a fix and change nothing.
#: One name means a caller cannot half-apply it, and the two VoC paths cannot
#: drift apart in what "all of it" means.
VOC_SCALE: dict[str, int] = {
    "k": VOC_THEME_K,
    "token_budget": VOC_TOKEN_BUDGET,
    "signals_per_theme": VOC_SIGNALS_PER_THEME,
    "recent_signals": VOC_RECENT_SIGNALS,
}

#: Cosine similarity below which a theme match is indistinguishable from
#: noise. `kg_find_candidates` is a pure kNN (ORDER BY <=> ... LIMIT k) — it
#: returns the NEAREST themes, never the RELEVANT ones, so on a KG with >= k
#: themes every question matched all k. text-embedding-3-small places
#: unrelated English text pairs near 0.0-0.1; a genuine topical match sits
#: well above 0.15. This is a noise gate, not a tuned relevance knob.
_MIN_THEME_SCORE = 0.15


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


# ── §2 ledger spine (hypothesis/decision/outcome) causal-chain resolution ──
#
# `decision_chain.py` writes the closed-loop edges (hypothesis --PROMOTED_TO-->
# decision --RESULTED_IN--> artifact --REALIZES--> outcome --VALIDATES-->
# hypothesis) whenever a PRD is generated/finalized or an idea reaches
# status='done'. `load_session_context` fetches the ledger entities themselves
# but not these edges, so a rendered decision/hypothesis/outcome used to be a
# bare label with no indication it's actually connected to the rest of the
# chain. The helpers below walk the SAME edge primitives `retrieve_context`
# already uses elsewhere (`facade.edges_to`/`edges_from`) to resolve that
# chain into plain labels `render_context_section` can weave into the text.


def _resolve_label(
    facade: GraphFacade, enterprise_id: str, entity_id: Optional[str],
    label_cache: dict[str, Optional[str]],
) -> Optional[str]:
    """Best-effort entity_id -> canonical_label lookup, memoized per-call so a
    hypothesis/decision/outcome referenced by multiple edges only costs one
    round-trip. Never raises; a failed or missing lookup just yields None so
    the caller renders the entity without that piece of the chain."""
    if not entity_id:
        return None
    if entity_id in label_cache:
        return label_cache[entity_id]
    label: Optional[str] = None
    try:
        ent = facade.get_entity(enterprise_id, entity_id)
        label = ent.canonical_label if ent else None
    except Exception as exc:  # noqa: BLE001 — ledger enrichment is best-effort
        logger.info("Ask KG retrieval: get_entity(%s) failed (%s)", entity_id, exc)
    label_cache[entity_id] = label
    return label


def _ledger_entities(
    facade: GraphFacade, enterprise_id: str, rows: list, *, kind: str,
    label_cache: dict[str, Optional[str]],
) -> list[dict]:
    """Flatten `hypothesis`/`decision`/`outcome` Entity rows into the bundle
    shape, carrying `properties` (already on the entity, previously never
    rendered) plus a best-effort `related` dict resolving the decision-chain
    edge(s) that touch this entity, so the render step can make the causal
    link explicit instead of three disconnected lists:
      - decision:   PROMOTED_TO-in  -> promoted_from_hypothesis
                    RESULTED_IN-out -> resulted_in_artifact
      - hypothesis: VALIDATES-in    -> validated_by_outcome
      - outcome:    VALIDATES-out   -> validates_hypothesis
    A failed edge read for one entity never breaks the others or raises;
    `related` is simply omitted (the caller falls back to label+properties).

    The edge reads and the label lookups are both BATCHED across `rows`. Walked
    per entity this was the ask's largest N+1 after the theme walk — 21 typed
    edge queries plus 11 single-row label fetches on a normal ledger — and it
    collapses to at most two edge queries and one label query per kind. Both
    batches fall back to the original per-entity path on failure, so the
    degradation profile above still holds exactly."""
    out: list[dict] = []
    if not rows:
        return out

    ids = [e.id for e in rows]

    def _group(edges: list, attr: str) -> dict[str, list]:
        grouped: dict[str, list] = {}
        for edge in edges:
            grouped.setdefault(getattr(edge, attr), []).append(edge)
        return grouped

    # One query per direction this kind actually walks, instead of one per
    # entity per direction. `batched` going False is what re-arms the original
    # per-entity reads below.
    inbound: dict[str, list] = {}
    outbound: dict[str, list] = {}
    batched = True
    try:
        if kind == "decision":
            inbound = _group(
                facade.edges_to_many(enterprise_id, ids, type="PROMOTED_TO"), "target_id"
            )
            outbound = _group(
                facade.edges_from_many(enterprise_id, ids, type="RESULTED_IN"), "source_id"
            )
        elif kind == "hypothesis":
            inbound = _group(
                facade.edges_to_many(enterprise_id, ids, type="VALIDATES"), "target_id"
            )
        elif kind == "outcome":
            outbound = _group(
                facade.edges_from_many(enterprise_id, ids, type="VALIDATES"), "source_id"
            )
    except Exception as exc:  # noqa: BLE001 — ledger enrichment is best-effort
        logger.info(
            "Ask KG retrieval: batched ledger edges failed kind=%s (%s); "
            "falling back to per-entity reads", kind, exc,
        )
        inbound, outbound, batched = {}, {}, False

    # Warm the shared label cache in ONE fetch for every entity these edges
    # point at. `_resolve_label` reads that cache first, so a warm entry costs
    # it no round trip — and ids an earlier kind already resolved cost nothing
    # here either, since the cache is shared across all three calls.
    if batched:
        referenced: list[str] = []
        for e in rows:
            referenced.extend(
                edge.source_id for edge in inbound.get(e.id, [])
                if edge.source_kind == "entity"
            )
            referenced.extend(
                edge.target_id for edge in outbound.get(e.id, [])
                if edge.target_kind == "entity"
            )
        missing = [i for i in dict.fromkeys(referenced) if i not in label_cache]
        if missing:
            try:
                fetched = facade.get_entities(enterprise_id, missing)
                for eid in missing:
                    ent = fetched.get(eid)
                    # Negative caching preserved: an id that did not resolve is
                    # cached as None so `_resolve_label` never retries it.
                    label_cache[eid] = ent.canonical_label if ent else None
            except Exception as exc:  # noqa: BLE001
                logger.info(
                    "Ask KG retrieval: batched label fetch failed kind=%s (%s); "
                    "falling back to per-id lookups", kind, exc,
                )

    for e in rows:
        entry: dict[str, Any] = {
            "entity_id": e.id, "label": e.canonical_label, "properties": e.properties or {},
        }
        related: dict[str, str] = {}
        try:
            if kind == "decision":
                promoted = (
                    inbound.get(e.id, []) if batched
                    else facade.edges_to(enterprise_id, e.id, type="PROMOTED_TO")
                )
                for edge in promoted:
                    if edge.source_kind != "entity":
                        continue
                    label = _resolve_label(facade, enterprise_id, edge.source_id, label_cache)
                    if label:
                        related["promoted_from_hypothesis"] = label
                resulted = (
                    outbound.get(e.id, []) if batched
                    else facade.edges_from(enterprise_id, e.id, type="RESULTED_IN")
                )
                for edge in resulted:
                    if edge.target_kind != "entity":
                        continue
                    label = _resolve_label(facade, enterprise_id, edge.target_id, label_cache)
                    if label:
                        related["resulted_in_artifact"] = label
            elif kind == "hypothesis":
                validated = (
                    inbound.get(e.id, []) if batched
                    else facade.edges_to(enterprise_id, e.id, type="VALIDATES")
                )
                for edge in validated:
                    if edge.source_kind != "entity":
                        continue
                    label = _resolve_label(facade, enterprise_id, edge.source_id, label_cache)
                    if label:
                        related["validated_by_outcome"] = label
            elif kind == "outcome":
                validates = (
                    outbound.get(e.id, []) if batched
                    else facade.edges_from(enterprise_id, e.id, type="VALIDATES")
                )
                for edge in validates:
                    if edge.target_kind != "entity":
                        continue
                    label = _resolve_label(facade, enterprise_id, edge.target_id, label_cache)
                    if label:
                        related["validates_hypothesis"] = label
        except Exception as exc:  # noqa: BLE001 — ledger enrichment is best-effort
            logger.info(
                "Ask KG retrieval: ledger chain resolution failed kind=%s id=%s (%s)",
                kind, e.id, exc,
            )
        if related:
            entry["related"] = related
        out.append(entry)
    return out


def _shared_ask_embedding(
    enterprise_id: str, question: str
) -> Optional[tuple[Optional[list[float]], bool]]:
    """This ask's shared `(vector, degraded)` when an ask is in scope, else None.

    Three outcomes, mapping onto `ask_runner`'s three slot states:
      * no slot        -> None. The caller is not inside an ask (oncall,
                          research, tests); it should embed for itself.
      * slot pending   -> resolve it now and memoise, so this becomes the ask's
                          one shared vector and later consumers reuse it.
      * slot resolved  -> hand back what the ask already computed, including a
                          degraded `(None, True)`, which must NOT be retried.

    Imported lazily because `app.ask_runner` imports THIS module — a top-level
    import would be circular. Same idiom as `embed_texts` below.

    Never raises: any failure to reach the ask context degrades to "no slot",
    which is the pre-existing self-embed path."""
    try:
        from app import ask_runner

        slot = ask_runner._active_question_embedding.get()
        if slot is None:
            return None
        if slot is ask_runner._EMBED_PENDING:
            return ask_runner._resolve_question_embedding(enterprise_id, question)
        return slot  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 — sharing is an optimisation, never a dependency
        return None


def retrieve_context(
    facade: GraphFacade,
    enterprise_id: str,
    question: str,
    *,
    k: int = _DEFAULT_THEME_K,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    signals_per_theme: int = _SIGNALS_PER_THEME,
    recent_signals: int = _RECENT_SIGNALS,
    question_embedding: Optional[list[float]] = None,
    skip_semantic: bool = False,
    min_theme_score: float = _MIN_THEME_SCORE,
) -> dict[str, Any]:
    """Retrieve a ranked, deduped KG context bundle for a chat question.

    `k`, `signals_per_theme` and `recent_signals` size the CANDIDATE POOL;
    `token_budget` then caps what survives into the bundle. They are separate
    knobs because raising one without the other does nothing: a caller that
    lifts only the budget is still bounded by the pool it was allowed to gather
    (`k * signals_per_theme + recent_signals`), and a caller that lifts only the
    pool still gets cut back to the budget. The voice-of-customer path raises
    all four together — see the `VOC_*` preset above.

    `question_embedding=None` (the default) means "compute one for me" —
    self-embeds unless `skip_semantic=True`, which means "there is no usable
    embedding, do not try": the theme kNN step is skipped entirely rather than
    running on a zero vector. A zero or wrong-dimension vector reaching
    `qvec` by any route is also dropped before it can reach `find_candidates`
    (see the defence-in-depth check below) — `skip_semantic` avoids the
    redundant embedding call; the dimension/zero check is the belt-and-braces
    behind it.

    Steps:
      1. Embed the question (best-effort; if embeddings are unavailable we
         skip the kNN theme match and fall back to recent signals only).
      2. `find_candidates(type="theme")` → the k nearest themes, then drop
         anything scoring below `min_theme_score` (default `_MIN_THEME_SCORE`)
         — the kNN primitive returns nearest, not relevant, so this is the
         gate that keeps noise themes out of the bundle entirely.
      3. For each surviving candidate theme, gather its inbound signal edges
         (`edges_to`, source_kind == "signal"), boosted by the theme's
         similarity score.
      4. Fold in recent non-stale `active_signals` (fresh connector data not
         yet wired to a resolved theme), without a theme boost.
      5. Dedupe by signal id, rank by (evidence weight × recency + theme
         boost), and cap the serialized content to `token_budget`.
      6. Attach `load_session_context` (hypotheses / decisions / outcomes),
         each enriched with its decision-chain edges (`_ledger_entities`) so
         the causal link between them is resolvable, not just three parallel
         lists of bare labels.

    Returns a dict:
      {
        "signals":   [ {signal_id, content, kind, source_type, provenance,
                        theme, confidence, rank}, ... ],   # ranked, capped
        "themes":    [ {entity_id, label, score}, ... ],   # matched themes
        "decisions": [ {entity_id, label, properties, related?}, ... ],  # recent
        "hypotheses":[ {entity_id, label, properties, related?}, ... ],
        "outcomes":  [ {entity_id, label, properties, related?}, ... ],
        # `related` (present only when a chain edge resolved) is one of:
        #   decision:   {promoted_from_hypothesis?, resulted_in_artifact?}
        #   hypothesis: {validated_by_outcome?}
        #   outcome:    {validates_hypothesis?}
        "kg_refs":   [ ...signal+entity ids used... ],     # for the decision log
        "token_estimate": <int>,
        "signals_dropped": <int>,   # ranked candidates the budget cut
        "empty": <bool>,
      }

    `signals_dropped` is what makes the cap honest. `token_estimate` reports
    what was SPENT, which reads identically whether the budget was generous or
    the bundle was clipped in half — so nothing downstream could tell a user
    their answer was built from a sample. Callers that present retrieved signal
    to a user should say so when this is non-zero.

    Pure retrieval: tenant-scoped (everything reads through `enterprise_id`),
    no writes, no LLM. Never raises on a partial-KG read — degrades to an
    emptier bundle and logs.
    """
    now = datetime.now(timezone.utc)

    # 1) Embed the question. Embeddings can be unconfigured (no OPENAI key) or
    #    the call can fail; either way we still return recent signals.
    #
    #    `question_embedding`, when the caller supplies it, is used as-is and
    #    no embedding call is made here. The ask path computes the question's
    #    vector once and shares it with document selection, which runs before
    #    this does — without that sharing the same question would be embedded
    #    twice per ask. A caller that passes nothing AND does not set
    #    `skip_semantic` keeps the original self-contained behaviour, which is
    #    what every other caller relies on.
    #
    #    `skip_semantic=True` is the caller stating "there is no embedding, do
    #    not compute one" — distinct from the `question_embedding=None`
    #    default, which means "compute one for me". Without this distinction a
    #    caller that already determined its embedding is unusable (no key, or
    #    an all-zero vector came back) had no way to say so: `None` collapsed
    #    back to "self-embed", which re-issued the same doomed call and got the
    #    same zero vector back. Set by the Ask path whenever its shared
    #    embedding is degraded; every other caller leaves it at the default and
    #    is unaffected.
    qvec: Optional[list[float]] = question_embedding
    try:
        if qvec is None and not skip_semantic:
            # Before embedding anything, ask whether THIS ask already has a
            # vector in scope. `ask_runner` publishes one request-scoped slot
            # per ask precisely so the question is embedded once and shared —
            # but this function is reached by a second route the sharing never
            # covered: `connector_lookup.knowledge_graph.search` calls it with
            # neither `question_embedding` nor `skip_semantic`, so it fell
            # straight through to the self-embed below and paid for a second
            # vector of the SAME question (both legs logged `input_tokens=8`).
            #
            # `_shared_ask_embedding` returns None for every caller that has no
            # ask in scope — oncall, research, the tests — so those keep
            # self-embedding here exactly as before.
            shared = _shared_ask_embedding(enterprise_id, question)
            if shared is not None:
                # A degraded share is authoritative: `(None, True)` means the
                # ask already tried and failed, and retrying it here is the
                # doomed second call `skip_semantic` exists to prevent.
                qvec, _shared_degraded = shared
            else:
                from app.graph.embeddings import embed_texts

                vecs = embed_texts([question], enterprise_id=enterprise_id,
                                   purpose="kg_retrieval")
                qvec = vecs[0] if vecs else None
    except Exception as exc:  # noqa: BLE001 — retrieval must not hard-fail Ask
        logger.info("Ask KG retrieval: embedding unavailable (%s); recent-only", exc)

    # 1b) Defence-in-depth: a zero-vector or wrong-dimension "embedding" is not
    #     a real one — the no-key fallback in `embed_texts` returns an
    #     all-zero vector, which in cosine kNN ranks arbitrarily and is worse
    #     than nothing. Mirrors `document_catalog.find_candidates`'s exact
    #     check so the invariant holds even for a caller (this module's own
    #     self-embed above, or an external caller) that hands a zero vector to
    #     `qvec` directly rather than going through `skip_semantic`.
    if qvec is not None and (len(qvec) != EMBEDDING_DIM or not any(qvec)):
        qvec = None

    # 2) kNN theme match (returns [] on the fake/no-pgvector backend).
    matched_themes: list[tuple[Any, float]] = []
    if qvec is not None:
        try:
            matched_themes = facade.find_candidates(enterprise_id, "theme", qvec, k=k)
        except Exception as exc:  # noqa: BLE001
            logger.info("Ask KG retrieval: find_candidates failed (%s)", exc)
            matched_themes = []

    # 2b) Noise floor. `find_candidates` is a pure kNN — nearest, not
    #     relevant — so on a KG with >= k themes every question returns all
    #     k candidates regardless of topical fit. Drop anything below
    #     `min_theme_score` here, before a theme's label is rendered as
    #     first-class evidence and before its signals are pulled into the
    #     budget below; everything downstream (the signal walk, kg_refs,
    #     themes_out) reads the filtered list, so admission is fixed, not
    #     just rendering.
    returned_count = len(matched_themes)
    top_score = 0.0
    for _, score in matched_themes:
        try:
            top_score = max(top_score, float(score))
        except (TypeError, ValueError):
            continue

    def _clears_floor(score: Any) -> bool:
        try:
            return float(score) >= min_theme_score
        except (TypeError, ValueError):
            return False

    matched_themes = [(theme, score) for theme, score in matched_themes if _clears_floor(score)]

    if len(matched_themes) < returned_count:
        logger.info(
            "Ask KG retrieval: theme candidates below noise floor dropped "
            "enterprise_id=%s returned=%d kept=%d floor=%s top_score=%s",
            enterprise_id, returned_count, len(matched_themes), min_theme_score,
            round(top_score, 4),
        )

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

    # ONE batched inbound-edge read covering every matched theme, replacing the
    # per-theme `edges_to` that cost twelve serial round trips at the default
    # k=12. `edges_to_many` filters on `(enterprise_id, target_id)`, the same
    # `kg_rel_to_idx` index the per-id form used, so this is fewer trips over
    # the same plan — no migration, no new index.
    #
    # Regrouped by target_id in ARRIVAL ORDER, which is load-bearing: the cap
    # further down keeps the first `_SIGNALS_PER_THEME` edges rather than the
    # highest-ranked ones, so which evidence survives depends on the order rows
    # come back in. Appending in iteration order reproduces exactly what the
    # per-theme reads saw.
    #
    # The fallback is not belt-and-braces. The old shape degraded ONE theme
    # when a read failed and kept the rest; a single batched query turns any
    # failure into "no KG evidence at all", and the ask would then answer
    # corpus-only without ever saying that is what it did.
    edges_by_theme: Optional[dict[str, list[Any]]] = None
    try:
        _batched = facade.edges_to_many(
            enterprise_id, [t.id for t, _ in matched_themes]
        )
        edges_by_theme = {}
        for _e in _batched:
            edges_by_theme.setdefault(_e.target_id, []).append(_e)
    except Exception as exc:  # noqa: BLE001
        logger.info(
            "Ask KG retrieval: batched edges_to_many failed (%s); falling back "
            "to per-theme reads", exc,
        )
        edges_by_theme = None

    for theme, score in matched_themes:
        themes_out.append(
            {"entity_id": theme.id, "label": theme.canonical_label, "score": round(float(score), 4)}
        )
        # Normalize the similarity score (cosine ~0..1) into a modest boost so
        # theme-matched evidence floats above generic recent signals without
        # swamping the evidence-weight term.
        boost = max(0.0, float(score)) * 0.5
        if edges_by_theme is not None:
            # A theme with no inbound edges is absent from the map, not an
            # error — same as the empty list the per-theme read returned.
            edges = edges_by_theme.get(theme.id, [])
        else:
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
            if sig is None or signal_is_retired(sig.properties):
                continue
            rank = _signal_rank(sig, now, boost)
            payload = _signal_payload(sig, theme_label=theme.canonical_label, rank=rank)
            prev = by_id.get(sig.id)
            if prev is None or rank > prev[0]:
                by_id[sig.id] = (rank, payload)
            kept += 1
            if kept >= signals_per_theme:
                break

    # 4) Recent non-stale signals (no theme boost). Covers fresh connector
    #    data the synthesis pass hasn't wired to a theme yet.
    try:
        # Only the newest `_RECENT_SIGNALS` survive the slice below, so asking
        # for the facade's full 1000-row default shipped ~1000 rows — content,
        # properties and provenance JSON included — to read eight of them. This
        # tenant was already logging `active_signals hit the fetch limit`, i.e.
        # paying that in full on every ask.
        #
        # 250 rather than 8: the stale filter runs in Python AFTER the fetch
        # (`facade.active_signals` does this so the in-memory test fake, which
        # supports neither OR nor `gt`, behaves like real Supabase), so the
        # fetch has to carry enough headroom that staleness can't starve the
        # slice. 250 leaves 242 stale signals of room before the result could
        # differ from the old width — and if a tenant ever did have that many
        # consecutive stale recent signals, the effect is a slightly shorter
        # recent list, not a wrong one.
        #
        # DERIVED, not fixed, since `recent_signals` became a caller knob: a
        # caller asking for 400 recent signals out of a 250-row fetch would be
        # silently held to 250 — the starvation this width exists to prevent,
        # reintroduced through the parameter. Widening ADDS the same headroom on
        # top rather than scaling it, so the default (any slice at or under
        # `_RECENT_SIGNALS`) still fetches exactly 250 and the reasoning above
        # still holds verbatim.
        fetch_width = (
            _RECENT_SIGNALS_FETCH
            if recent_signals <= _RECENT_SIGNALS
            else recent_signals + _RECENT_SIGNALS_FETCH
        )
        recent = facade.active_signals(enterprise_id, limit=fetch_width)
    except Exception as exc:  # noqa: BLE001
        logger.info("Ask KG retrieval: active_signals failed (%s)", exc)
        recent = []
    recent.sort(key=lambda s: s.transaction_at, reverse=True)
    for sig in recent[:recent_signals]:
        if signal_is_retired(sig.properties):
            continue
        if sig.id in by_id:
            continue
        rank = _signal_rank(sig, now, 0.0)
        by_id[sig.id] = (rank, _signal_payload(sig, theme_label=None, rank=rank))

    # 5) Rank globally + apply the token budget cap.
    #
    # The cut is COUNTED, not just taken. This loop used to `break` and leave no
    # trace: the bundle reported `token_estimate` (what it spent) and nothing at
    # all about what it discarded, so an answer built from a third of a tenant's
    # feedback was indistinguishable from one built from all of it — by the
    # caller, and therefore by the user. `signals_dropped` is that missing
    # number; callers that show retrieved signal to a person are expected to
    # surface it when it is non-zero.
    ranked = sorted(by_id.values(), key=lambda t: -t[0])
    signals_out: list[dict] = []
    used_tokens = 0
    signals_dropped = 0
    for _, payload in ranked:
        cost = _approx_tokens(payload["content"])
        if signals_out and used_tokens + cost > token_budget:
            # Every remaining candidate is dropped, not just this one, because
            # this is a `break` and not a `continue` — the bundle keeps the
            # highest-ranked CONTIGUOUS prefix rather than skipping past an
            # oversized signal to pack smaller, lower-ranked ones behind it.
            # That choice is unchanged here; the count simply has to match it.
            signals_dropped = len(ranked) - len(signals_out)
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

    label_cache: dict[str, Optional[str]] = {}
    decisions_out = _ledger_entities(
        facade, enterprise_id, ctx.get("recent_decisions") or [], kind="decision",
        label_cache=label_cache,
    )
    hypotheses_out = _ledger_entities(
        facade, enterprise_id, ctx.get("active_hypotheses") or [], kind="hypothesis",
        label_cache=label_cache,
    )
    outcomes_out = _ledger_entities(
        facade, enterprise_id, ctx.get("recent_outcomes") or [], kind="outcome",
        label_cache=label_cache,
    )

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
        "signals_dropped": signals_dropped,
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
         recently written (a theme can recur across Top Insights briefs).
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
        if sig is None or signal_is_retired(sig.properties):
            continue
        by_id[sig.id] = _trail_signal_payload(sig, edge_type="SUPPORTS")

    # 2) Theme convergence signals — every non-stale signal wired to the theme,
    #    for breadth the SUPPORTS set may not fully carry. Doesn't overwrite a
    #    signal already tagged SUPPORTS.
    kept = 0
    for sid in theme_edge_ids:
        sig = signals_by_id.get(sid)
        if sig is None or signal_is_retired(sig.properties):
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


def task_evidence_trail(
    facade: GraphFacade,
    enterprise_id: str,
    task: str,
) -> Optional[dict[str, Any]]:
    """Best-effort evidence trail for a FREE-TEXT TASK (the chat "generate a
    PRD for <need>" path) — the task has no theme_id or synthesis-written
    hypothesis to walk, so evidence is found by SEMANTIC RETRIEVAL instead:
    `retrieve_context` embeds the task and kNN-matches themes + signals (the
    ingested connector data), exactly like the Ask path.

    Returns the same trail shape `insight_evidence_trail` produces (so
    `render_evidence_trail_section` and the kg_refs decision-log plumbing work
    unchanged), or None when the KG yields nothing / errors — the caller then
    decides to skip evidence / fall back to the corpus."""
    try:
        bundle = retrieve_context(facade, enterprise_id, task)
    except Exception:  # noqa: BLE001 — retrieval must never break generation
        logger.exception(
            "task evidence trail: retrieval failed (enterprise=%s)", enterprise_id
        )
        return None
    if not bundle or bundle.get("empty") or not bundle.get("signals"):
        return None
    return {
        "insight": {"title": task},
        "theme_id": None,
        "hypothesis": None,
        "signals": bundle["signals"],
        "kg_refs": bundle.get("kg_refs") or [],
        "empty": False,
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


def _format_ledger_properties(properties: dict[str, Any]) -> str:
    """Render a hypothesis/decision/outcome entity's `properties` dict as
    inline `key=value` pairs (e.g. `prd_id=..., actual_impact=...`), skipping
    unset keys. `properties` already reaches the bundle (`_ledger_entities`)
    but was previously discarded entirely at render time."""
    parts = [f"{k}={v}" for k, v in properties.items() if v is not None]
    return ", ".join(parts)


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

    # Hypotheses/decisions/outcomes render the §2 ledger spine. Beyond the
    # bare label, each line surfaces the entity's real properties and — when
    # a decision-chain edge resolved (`_ledger_entities` in retrieve_context)
    # — the causal link to the entity it was promoted from / resulted in /
    # validates, so this reads as a connected chain rather than three
    # parallel, unconnected lists a model can't relate to each other.
    hyps = bundle.get("hypotheses") or []
    if hyps:
        lines.append(
            "\n## Hypotheses (status — validated/still open — is stated per entry below)"
        )
        for h in hyps:
            props_txt = _format_ledger_properties(h.get("properties") or {})
            related = h.get("related") or {}
            outcome_label = related.get("validated_by_outcome")
            line = f"- {h['label']}"
            if props_txt:
                line += f" ({props_txt})"
            if outcome_label:
                line += f" — validated by outcome: {outcome_label}"
            else:
                line += " — not yet validated by a measured outcome"
            lines.append(line)

    decisions = bundle.get("decisions") or []
    if decisions:
        lines.append("\n## Decisions (already made, not merely proposed)")
        for d in decisions:
            props_txt = _format_ledger_properties(d.get("properties") or {})
            related = d.get("related") or {}
            hyp_label = related.get("promoted_from_hypothesis")
            artifact_label = related.get("resulted_in_artifact")
            line = f"- {d['label']}"
            if props_txt:
                line += f" ({props_txt})"
            if hyp_label:
                line += f" — promotes hypothesis: {hyp_label}"
            if artifact_label:
                line += f"; resulted in artifact: {artifact_label}"
            lines.append(line)

    outcomes = bundle.get("outcomes") or []
    if outcomes:
        lines.append(
            "\n## Measured outcomes (work that shipped and was measured — "
            "not open/in-progress items)"
        )
        for o in outcomes:
            props_txt = _format_ledger_properties(o.get("properties") or {})
            related = o.get("related") or {}
            hyp_label = related.get("validates_hypothesis")
            line = f"- {o['label']}"
            if props_txt:
                line += f" ({props_txt})"
            if hyp_label:
                line += f" — validates hypothesis: {hyp_label} (this hypothesis's loop has closed)"
            lines.append(line)

    return "\n".join(lines)
