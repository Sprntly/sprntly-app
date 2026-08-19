"""Group claims that are about THE SAME THING, deterministically.

WHY THIS EXISTS. Clustering used to key on `properties.subject`, which sounded
reasonable and is present on exactly zero real signals — so every cluster
collapsed to the claim's `kind` and a run over 2,777 signals produced nine
"findings" that were the corpus taxonomy read back ("1200 claims concern
finding"). That is not a wrong number, it is a category error: it answers "what
kinds of thing did we store" when the user asked what is happening in their
business.

WHY EMBEDDINGS. The spec's rule is "deduplicate by mechanism, not by wording",
and the Phase 0 corpus carried four different labels for one theme, splitting
its accounts four ways so each looked smaller than it was. Matching on words
reproduces that split. `kg_signal.embedding` is already computed and stored for
every signal, so this costs one read and no model call.

WHY NOT k-means, DBSCAN, OR ANYTHING FITTED. Reproducibility is the claim this
engine makes against asking a general model the same question — "the same
substrate produces the same ranking". Anything with a random initialisation, or
whose output depends on how many workers happened to run, cannot offer that.
This is single-pass leader clustering in a FIXED input order: first claim to
appear becomes a leader, each later claim joins the nearest leader above the
threshold or becomes one itself. Same claims in the same order, same clusters,
every time — and the caller orders by id precisely so that holds.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Iterable, Mapping, Optional, Sequence

from app.crucible.types import Claim

logger = logging.getLogger(__name__)

#: Cosine similarity above which two claims are about the same thing.
#: Calibrated on a 2,777-signal tenant: 0.80 over-merges distinct onboarding
#: and billing themes, 0.88 splits the same theme across near-duplicate
#: phrasings. 0.84 gave 469 multi-claim clusters with a largest of 14 — the
#: shape of a book of business, rather than of a taxonomy.
DEFAULT_THRESHOLD = 0.84

#: A label is a topic, not an assertion. Anything from here on is the source's
#: own reasoning, and carrying it into a finding's statement would put a causal
#: claim in our mouth that the evidence does not support (I5).
_CUTS = (" because ", " due to ", " so that ", " which caused ", " leading to ",
         " resulting in ", " as a result", " therefore ", " drives ", " causes ")

#: Long enough to identify the theme, short enough to read in a list.
_LABEL_MAX = 90


def label_for(text: str) -> str:
    """A short, neutral topic label from a signal's own words.

    Truncated at the first causal connective rather than mid-word: the label
    describes WHAT the claims are about, and the moment it starts explaining
    WHY it has stopped being a label and started being an unsupported finding.
    """
    s = " ".join((text or "").split())
    low = s.lower()
    for cut in _CUTS:
        i = low.find(cut)
        if i > 0:
            s = s[:i]
            low = s.lower()
    if len(s) > _LABEL_MAX:
        s = s[:_LABEL_MAX].rsplit(" ", 1)[0]
    return s.rstrip(" ,;:.") or "unlabelled"


def assign_clusters(
    claims: Sequence[Claim],
    embeddings: Mapping[str, Sequence[float]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[Claim], dict]:
    """Return the claims with `subject_cluster_id`/`subject` filled in, and stats.

    A claim with no embedding keeps whatever subject it already had — it is not
    forced into someone else's cluster, and it is never dropped, because a
    missing vector says nothing about whether the claim matters.
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001 — degrade to the old behaviour, loudly
        logger.exception("crucible: numpy unavailable; falling back to subject")
        return list(claims), {"embedded": 0, "degenerate": 0, "clusters": 0}

    vectors: list = []
    indexed: list[int] = []
    for i, c in enumerate(claims):
        raw = embeddings.get(c.id)
        if raw is None:
            continue
        vectors.append(raw)
        indexed.append(i)

    if len(indexed) < 2:
        return list(claims), {"embedded": len(indexed), "degenerate": 0,
                              "clusters": len(indexed)}

    try:
        matrix = np.asarray(vectors, dtype=np.float32)
    except ValueError:
        # Ragged input — vectors of differing length, which numpy refuses to
        # square off rather than silently padding. Grouping on a matrix we
        # cannot form would be arbitrary, so decline and say so.
        logger.error("crucible: embeddings are ragged; skipping clustering")
        return list(claims), {"embedded": 0, "degenerate": len(indexed),
                              "clusters": 0}
    if matrix.ndim != 2:
        logger.error("crucible: embeddings are not a matrix; skipping")
        return list(claims), {"embedded": 0, "degenerate": len(indexed),
                              "clusters": 0}

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # DEGENERATE VECTORS ARE EXCLUDED, NOT NORMALISED TO SOMETHING.
    # `embed_texts` returns all-ZERO vectors when no OpenAI key is configured,
    # and a zero vector has cosine 0.0 against everything — so it never reaches
    # the threshold, every claim becomes its own cluster, and the run reports
    # "only 1 supporting claim — an anecdote" for the entire corpus while
    # saying `ready`. That is the quiet-failure shape this whole design is
    # supposed to make impossible, so the caller is told (`degenerate`) and
    # renders it.
    finite = np.isfinite(matrix).all(axis=1)
    usable = finite & (norms[:, 0] > 0)
    degenerate = int((~usable).sum())
    if degenerate:
        logger.warning(
            "crucible: %d of %d embeddings are zero or non-finite; those "
            "claims are left ungrouped", degenerate, matrix.shape[0],
        )
    if not usable.any():
        return list(claims), {"embedded": 0, "degenerate": degenerate,
                              "clusters": 0}

    safe_norms = np.where(norms == 0, 1.0, norms)
    matrix = matrix / safe_norms

    # PREALLOCATED AND DOUBLED, not `np.vstack` per leader. Growing by vstack
    # reallocates and copies the whole leader block on every new cluster, which
    # is quadratic in the number of clusters — and a real tenant produced 1,744
    # clusters from 2,777 signals, so the pathological case IS the normal case.
    capacity = 256
    leader_matrix = np.zeros((capacity, matrix.shape[1]), dtype=np.float32)
    leaders: list[int] = []
    assignment: list[int] = []

    for row in range(matrix.shape[0]):
        if not usable[row]:
            assignment.append(-1)          # ungrouped, never forced anywhere
            continue
        if leaders:
            sims = leader_matrix[: len(leaders)] @ matrix[row]
            best = int(np.argmax(sims))
            if float(sims[best]) >= threshold:
                assignment.append(best)
                continue
        if len(leaders) == capacity:
            capacity *= 2
            grown = np.zeros((capacity, matrix.shape[1]), dtype=np.float32)
            grown[: len(leaders)] = leader_matrix[: len(leaders)]
            leader_matrix = grown
        leader_matrix[len(leaders)] = matrix[row]
        leaders.append(row)
        assignment.append(len(leaders) - 1)

    # THE LABEL IS THE MEDOID, NOT THE LEADER. The leader is simply whichever
    # member appeared first in the input, and naming a theme after an arbitrary
    # member is how nine claims about billing end up titled with the one
    # sentence about a calendar invite. The medoid is the member closest to the
    # group's centre, which is the one a reader would pick as representative —
    # and it is deterministic, unlike "first seen".
    members: dict[int, list[int]] = {}
    for position, cluster in enumerate(assignment):
        if cluster >= 0:
            members.setdefault(cluster, []).append(position)

    labels: dict[int, str] = {}
    for cluster, positions in members.items():
        block = matrix[positions]
        centroid = block.mean(axis=0)
        centroid_norm = float(np.linalg.norm(centroid))
        if centroid_norm:
            centroid = centroid / centroid_norm
        medoid = positions[int(np.argmax(block @ centroid))]
        source = claims[indexed[medoid]]
        labels[cluster] = label_for(source.assertion or source.subject)

    out = list(claims)
    for position, claim_index in enumerate(indexed):
        cluster = assignment[position]
        if cluster < 0:
            continue
        out[claim_index] = replace(
            out[claim_index],
            subject_cluster_id=f"c{cluster}",
            subject=labels[cluster],
        )
    return out, {"embedded": int(usable.sum()), "degenerate": degenerate,
                 "clusters": len(leaders)}


def parse_embedding(raw):
    """pgvector comes back as a JSON string over PostgREST, not a list.

    Returns a float32 numpy array when numpy is available, so the caller never
    holds 1536 boxed Python floats per signal.
    """
    if raw is None:
        return None
    try:
        import numpy as np
    except Exception:  # noqa: BLE001
        np = None                                        # type: ignore[assignment]

    parsed = raw
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw)
        except Exception:  # noqa: BLE001 — a malformed vector is a missing one
            return None
    if not isinstance(parsed, (list, tuple)):
        return None
    # float32 immediately. A list of 1536 Python floats is ~8x the memory of
    # the same numbers packed, and on a large tenant that difference is the
    # difference between fitting in RAM and not.
    if np is not None:
        try:
            return np.asarray(parsed, dtype=np.float32)
        except Exception:  # noqa: BLE001 — a vector we cannot pack is missing
            return None
    return list(parsed)
