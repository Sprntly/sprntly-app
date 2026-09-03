"""Use the knowledge graph's OWN themes to group claims.

WHY THIS REPLACED EMBEDDING CLUSTERING AS THE PRIMARY PATH.

`app/crucible/cluster.py` derives themes by clustering signal embeddings. It
works, and on a real tenant it produced 1,744 groups labelled with truncated
sentences — "Completed MRT stages currently cannot be revised, leaving
documentation errors…". Meanwhile the graph beside it already held **2,345
theme entities** joined to **12,932 of 15,569 signals** by `signal -> entity`
relationships, labelled by the extractor as "Parts request dashboard", "Bulk
Pause Endpoint", "Machine record data quality".

So the engine was re-deriving, worse, semantics the KG had already computed and
stored. That is the answer to "why aren't we understanding the knowledge
graph": we weren't reading it. Themes first, embeddings only for the signals
the graph left unthemed.

WHAT THIS IS NOT. It is not a second source of truth. A theme edge is an
assignment the extractor already made and a human can already see elsewhere in
the product, so grouping by it makes a run's output line up with the rest of
Sprntly rather than inventing a private taxonomy.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import replace
from typing import Iterable, Mapping, Optional, Sequence

from app.crucible.cluster import leader_groups, parse_embedding
from app.crucible.types import Claim

logger = logging.getLogger(__name__)

#: Rows per page. Relationships are small; entities carry an embedding, so they
#: are fetched by explicit id in batches instead of scanned.
_REL_PAGE = 1000
_ENTITY_BATCH = 200

#: `kg_entity.type` that represents a theme. The graph also holds hypothesis /
#: artifact / decision entities, which are NOT groupings of evidence and would
#: merge unrelated signals if treated as such.
THEME_TYPE = "theme"

#: Cosine above which two THEME LABELS name the same topic.
#:
#: WHY THIS CONSTANT EXISTS AT ALL. The extractor already has a find-or-create
#: merge gate at 0.86 on bare-label embeddings, and on a real tenant it fired 0
#: times in 63,903 pairs — `text-embedding-3-small` does not put two- to
#: four-word labels that high even when they differ only in case
#: ('entity resolution' / 'Entity resolution' = 0.857). So the graph holds one
#: topic under several entity ids, and a run that groups on entity id writes
#: one recommendation per SHARD: on that tenant, 71 kept findings covering ~39
#: distinct topics, with two of the five deep findings being shards of each
#: other. This is the read-side defence; the gate itself is a graph-wide change
#: with a backfill behind it and is not attempted here.
#:
#: CALIBRATION, measured on that tenant. True-synonym label pairs:
#: 'entity resolution'/'Entity resolution' 0.857,
#: 'billing & pricing'/'billing / pricing model' 0.824,
#: 'EU AI Act / regulatory compliance'/'regulatory compliance — EU AI Act'
#: 0.798. Kept findings by threshold: none 71, 0.86 71 (the live gate, a
#: no-op), 0.82 67, 0.80 54, 0.75 43, 0.72 32.
#:
#: 0.80 is the conservative pick that still roughly halves the shredding. It
#: misses the EU AI Act pair by 0.002, and that is the stated cost of not
#: over-merging: a reader seeing one topic twice is a worse-looking report,
#: while a reader seeing two topics fused into one is a wrong report.
#:
#: AND THE ORDERING AROUND 0.80 IS NOISY — read this before tuning it. On the
#: same corpus 'Enterprise compliance infrastructure' /
#: 'enterprise compliance requirements' (genuinely one topic, and two separate
#: deep findings in a five-item list) scores 0.7924, while 'Prototype Agent' /
#: 'Strategy Agent' (two DIFFERENT products) scores 0.8012. No threshold on
#: bare-label cosine separates those two pairs, in either direction. Moving
#: this number buys one and sells the other; the real fix is comparing
#: something richer than a two-word label, which belongs with the extractor's
#: merge gate and not here.
LABEL_MERGE_THRESHOLD = 0.80

#: FAIL SAFE TOWARD NOT MERGING. On a corpus where labels are near-identical
#: for reasons we did not anticipate, similarity merging could collapse the
#: whole graph into one mega-finding — a single "everything" recommendation
#: that is confidently wrong, which is worse than the shredding it replaced.
#: If either guard trips, the embedding tier is discarded whole and only the
#: exact-label tier survives; that tier cannot over-merge by construction.
_MAX_MERGE_SHARE = 0.5      # no single merged topic may hold >50% of themes
_MIN_GROUP_RATIO = 0.1      # merging may not cut the topic count below 10%

#: Below this many themes the guards above describe NORMAL small corpora, not
#: pathology — three themes of which two are synonyms already puts 66% in one
#: topic, and tripping there would disable merging for exactly the tenants
#: whose reports are shortest and where a duplicated finding is most visible.
_GUARD_MIN_THEMES = 10

#: Separators that carry no meaning of their own in a theme label. '&', '/' and
#: dashes are how the extractor joins two words of one topic, and it does not
#: join them the same way twice ('billing & pricing' / 'billing / pricing').
_SEPARATORS = re.compile(r"[-&/+|_,\u2010-\u2015]+")
_PUNCT_EDGES = ' \t\n\r.,;:!?\'"“”‘’()[]{}<>*-–—/&|_'


def load_theme_map(company_id: str) -> dict[str, tuple[str, str]]:
    """signal id -> (representative theme entity id, its label), from the graph.

    Read in stages because the tables are shaped very differently: the
    relationship rows are narrow and page cheaply, while `kg_entity` carries an
    embedding column that makes a scan expensive — so entities are fetched by
    the ids the relationships actually referenced, and their vectors only for
    the ones that turn out to be themes.

    The id returned is CANONICAL, not raw: synonym theme entities are folded
    onto one representative first (see `canonicalize_themes`), because the
    graph routinely holds one topic under several ids and every one of them
    would otherwise become its own finding.
    """
    from app.db.client import require_client

    client = require_client()

    edges: list[dict] = []
    for page in range(60):
        try:
            chunk = (
                client.table("kg_relationship")
                .select("source_id,target_id,type")
                .eq("enterprise_id", company_id)
                .eq("source_kind", "signal")
                .eq("target_kind", "entity")
                .order("id")
                .range(page * _REL_PAGE, page * _REL_PAGE + _REL_PAGE - 1)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001 — no graph is a degraded run, not a
            # dead one: clustering falls back to embeddings.
            logger.exception("crucible: theme edges unreadable for %s", company_id)
            return {}
        edges.extend(chunk)
        if len(chunk) < _REL_PAGE:
            break

    if not edges:
        return {}

    target_ids = sorted({e["target_id"] for e in edges if e.get("target_id")})
    themes: dict[str, str] = {}
    for i in range(0, len(target_ids), _ENTITY_BATCH):
        batch = target_ids[i : i + _ENTITY_BATCH]
        try:
            rows = (
                client.table("kg_entity")
                .select("id,type,canonical_label")
                .in_("id", batch)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001 — a lost batch costs those themes only
            logger.warning("crucible: entity batch %d unreadable", i // _ENTITY_BATCH)
            continue
        for r in rows:
            if r.get("type") == THEME_TYPE and (r.get("canonical_label") or "").strip():
                themes[r["id"]] = r["canonical_label"].strip()

    out: dict[str, tuple[str, str]] = {}
    for e in edges:
        label = themes.get(e.get("target_id"))
        if not label:
            continue
        signal_id = str(e.get("source_id"))
        # FIRST EDGE WINS, in id order. A signal can be joined to several
        # themes; picking deterministically matters more than picking the
        # "best" one, because a run that groups differently on a re-run is not
        # reproducible, which is the whole claim this engine makes.
        out.setdefault(signal_id, (e["target_id"], label))

    # SYNONYM THEMES ARE FOLDED BEFORE ANYTHING DOWNSTREAM SEES THEM.
    #
    # `assign_themes` stamps `kg:{entity_id}` as the cluster key and the
    # pipeline groups on that key verbatim, so two entity ids for one topic are
    # two findings — the pipeline lowercases and strips that key, but the key
    # is an opaque UUID, so the wording-split defence it was written for never
    # engages. It cannot: by the time the key exists the wording is already
    # gone. That is precisely why the fold has to happen HERE, one level up,
    # while the labels are still readable.
    #
    # Only the themes a signal actually bound to are considered — the graph
    # holds thousands of entities and none of the unused ones can change a
    # grouping.
    in_play = {v[0] for v in out.values()}
    if len(in_play) > 1:
        counts: dict[str, int] = {}
        for entity_id, _ in out.values():
            counts[entity_id] = counts.get(entity_id, 0) + 1
        labels = {e: themes[e] for e in in_play if e in themes}
        vectors = _load_theme_embeddings(client, sorted(in_play))
        canonical = canonicalize_themes(labels, vectors, counts)
        if canonical:
            out = {
                signal_id: (
                    canonical.get(entity_id, entity_id),
                    labels.get(canonical.get(entity_id, entity_id), label),
                )
                for signal_id, (entity_id, label) in out.items()
            }
    return out


def normalize_label(label: str) -> str:
    """A theme label reduced to the topic it names.

    TIER 1 OF THE MERGE, and the half that carries no threshold risk: two
    labels that reduce to the same string are the same topic, full stop.
    'competitive landscape' and 'Competitive landscape' are separate entities
    in the graph today, each with its own recommendation, purely because one
    was capitalised. This is what fixes those, and it cannot over-merge —
    equality is equality.

    Case-folded, unicode-normalised, separators ('&', '/', dashes) flattened to
    a space, edge punctuation stripped, internal whitespace collapsed. NOT
    stemmed and NOT de-pluralised: 'renewal' and 'renewals' plausibly differ,
    and guessing about that belongs in the similarity tier where a measured
    threshold decides it, not in the tier whose whole value is being certain.
    """
    s = unicodedata.normalize("NFKC", label or "")
    s = _SEPARATORS.sub(" ", s)
    s = " ".join(s.split())
    return s.strip(_PUNCT_EDGES).casefold().strip()


def _load_theme_embeddings(client, theme_ids: Sequence[str]) -> dict[str, object]:
    """Label vectors for theme entities, fetched in a SECOND pass.

    Deliberately separate from the label read. `target_ids` covers every entity
    kind the relationships touched — hypotheses, artifacts, decisions — and
    `embedding` is 1536 floats arriving as JSON text, so selecting it in the
    first pass would pull megabytes for rows we discard a line later. Themes
    are the minority of that set, so asking twice moves less data than asking
    once.

    A batch that fails costs those themes their similarity merge and nothing
    else: they keep their own ids and stay separate, which is exactly today's
    behaviour.
    """
    out: dict[str, object] = {}
    for i in range(0, len(theme_ids), _ENTITY_BATCH):
        batch = list(theme_ids[i : i + _ENTITY_BATCH])
        try:
            rows = (
                client.table("kg_entity")
                .select("id,embedding")
                .in_("id", batch)
                .execute()
            ).data or []
        except Exception:  # noqa: BLE001 — degrades to exact-label merging only
            logger.warning(
                "crucible: theme embedding batch %d unreadable; those themes "
                "merge on exact label only", i // _ENTITY_BATCH,
            )
            continue
        for r in rows:
            vec = parse_embedding(r.get("embedding"))
            if vec is not None:
                out[r["id"]] = vec
    return out


def canonicalize_themes(
    labels: Mapping[str, str],
    embeddings: Mapping[str, object],
    counts: Mapping[str, int],
    *,
    threshold: float = LABEL_MERGE_THRESHOLD,
) -> dict[str, str]:
    """theme entity id -> the id that should REPRESENT it.

    READ-SIDE ONLY. Nothing here is written back: `kg_entity` keeps its 358
    split rows, every other consumer of the graph sees exactly what it saw
    before, and Crucible simply stops being fooled into writing one
    recommendation per shard of a topic.

    REPRESENTATIVE SELECTION IS MOST-CITED, TIES BROKEN ON LOWEST ENTITY ID.
    Both halves matter. Most-cited is the shard the graph actually leaned on,
    so its label is the one a reader is most likely to recognise. The tie-break
    is what makes the choice a FUNCTION of the input rather than of dict order
    or of which page a batch landed on — the same corpus has to produce the
    same representative tomorrow, or the same evidence yields different
    findings on different days. Same discipline `figure_class` enforces when it
    classifies once and never re-rolls.

    That ordering is also the order the similarity tier sees, so the leader of
    each group IS its representative: one rule, applied once.
    """
    if not labels:
        return {}

    # TIER 1 — exact match on the normalised label.
    by_norm: dict[str, list[str]] = {}
    singletons: list[str] = []
    for entity_id, label in labels.items():
        key = normalize_label(label)
        if not key:
            # A label that reduces to nothing ('—', '...') is not a topic two
            # entities can share. Merging on the empty string would fuse every
            # such entity into one finding on the strength of no words at all.
            singletons.append(entity_id)
            continue
        by_norm.setdefault(key, []).append(entity_id)

    def rank(entity_id: str) -> tuple[int, str]:
        return (-int(counts.get(entity_id, 0)), str(entity_id))

    groups: list[list[str]] = [sorted(v, key=rank) for v in by_norm.values()]
    groups.extend([[e] for e in singletons])
    # Deterministic order in, deterministic leaders out.
    groups.sort(key=lambda g: rank(g[0]))
    exact_merged = sum(len(g) - 1 for g in groups)

    canonical: dict[str, str] = {}
    for g in groups:
        for member in g:
            canonical[member] = g[0]

    # TIER 2 — label-embedding similarity between the tier-1 representatives.
    reps = [g[0] for g in groups]
    vectors = [embeddings.get(r) for r in reps]
    if sum(v is not None for v in vectors) < 2:
        return canonical

    try:
        import numpy as np
    except Exception:  # noqa: BLE001 — exact-label merging still stands
        logger.warning("crucible: numpy unavailable; themes merge on label only")
        return canonical

    usable_rows = [i for i, v in enumerate(vectors) if v is not None]
    try:
        matrix = np.asarray([vectors[i] for i in usable_rows], dtype=np.float32)
    except ValueError:
        logger.warning("crucible: theme embeddings are ragged; label merge only")
        return canonical
    if matrix.ndim != 2:
        return canonical

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A zero vector has cosine 0.0 with everything, so it cannot merge anyway —
    # but it also cannot be normalised, and dividing by it would put NaNs into
    # every comparison and take the whole merge down with it.
    usable = np.isfinite(matrix).all(axis=1) & (norms[:, 0] > 0)
    if not usable.any():
        return canonical
    matrix = matrix / np.where(norms == 0, 1.0, norms)

    leaders, assignment = leader_groups(matrix, usable, threshold=threshold)

    merged: dict[str, str] = {}
    for row, cluster in enumerate(assignment):
        if cluster < 0:
            continue
        merged[reps[usable_rows[row]]] = reps[usable_rows[leaders[cluster]]]

    # THE SAFE-FAIL CHECK, before anything is applied.
    final_of = {r: merged.get(r, r) for r in reps}
    sizes: dict[str, int] = {}
    for r in reps:
        target = final_of[r]
        sizes[target] = sizes.get(target, 0) + 1
    topic_count = len(sizes)
    largest = max(sizes.values(), default=0)
    if len(reps) >= _GUARD_MIN_THEMES and (
        largest > _MAX_MERGE_SHARE * len(reps)
        or topic_count < max(2, _MIN_GROUP_RATIO * len(reps))
    ):
        logger.warning(
            "crucible: theme similarity merge is degenerate (%d themes -> %d "
            "topics, largest %d); keeping exact-label merging only",
            len(reps), topic_count, largest,
        )
        return canonical

    for member, rep in list(canonical.items()):
        canonical[member] = final_of.get(rep, rep)

    logger.info(
        "crucible: themes canonicalised %d -> %d (%d by exact label, %d by "
        "label similarity >= %.2f)",
        len(labels), len(set(canonical.values())), exact_merged,
        len(reps) - topic_count, threshold,
    )
    return canonical


def assign_themes(
    claims: Sequence[Claim],
    theme_map: Mapping[str, tuple[str, str]],
) -> tuple[list[Claim], list[int], dict]:
    """Stamp the graph's theme onto every claim it knows about.

    Returns the claims, the indexes of those the graph did NOT theme (so the
    caller can fall back to embeddings for exactly those), and stats.
    """
    out = list(claims)
    unthemed: list[int] = []
    for i, claim in enumerate(claims):
        found = theme_map.get(str(claim.id))
        if not found:
            unthemed.append(i)
            continue
        entity_id, label = found
        out[i] = replace(out[i], subject_cluster_id=f"kg:{entity_id}", subject=label)
    return out, unthemed, {
        "themed": len(claims) - len(unthemed),
        "unthemed": len(unthemed),
        "kg_themes": len({v[0] for v in theme_map.values()}),
    }
