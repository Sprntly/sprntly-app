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
) -> list[Claim]:
    """Return the claims with `subject_cluster_id` and `subject` filled in.

    A claim with no embedding keeps whatever subject it already had — it is not
    forced into someone else's cluster, and it is never dropped, because a
    missing vector says nothing about whether the claim matters.
    """
    try:
        import numpy as np
    except Exception:  # noqa: BLE001 — degrade to the old behaviour, loudly
        logger.exception("crucible: numpy unavailable; falling back to subject")
        return list(claims)

    vectors: list = []
    indexed: list[int] = []
    for i, c in enumerate(claims):
        raw = embeddings.get(c.id)
        if raw is None:
            continue
        vectors.append(raw)
        indexed.append(i)

    if len(indexed) < 2:
        return list(claims)

    matrix = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # A zero vector would divide to NaN and then match nothing and everything
    # by turns, depending on comparison order.
    norms[norms == 0] = 1.0
    matrix /= norms

    leaders: list[int] = []          # positions within `matrix`
    leader_matrix = np.zeros((0, matrix.shape[1]), dtype=np.float32)
    assignment: list[int] = []

    for row in range(matrix.shape[0]):
        if leaders:
            sims = leader_matrix @ matrix[row]
            best = int(np.argmax(sims))
            if float(sims[best]) >= threshold:
                assignment.append(best)
                continue
        leaders.append(row)
        leader_matrix = np.vstack([leader_matrix, matrix[row : row + 1]])
        assignment.append(len(leaders) - 1)

    out = list(claims)
    for position, claim_index in enumerate(indexed):
        cluster = assignment[position]
        leader_claim = claims[indexed[leaders[cluster]]]
        label = label_for(leader_claim.assertion or leader_claim.subject)
        out[claim_index] = replace(
            out[claim_index],
            subject_cluster_id=f"c{cluster}",
            subject=label,
        )
    return out


def parse_embedding(raw) -> Optional[list[float]]:
    """pgvector comes back as a JSON string over PostgREST, not a list."""
    if raw is None:
        return None
    if isinstance(raw, str):
        import json

        try:
            parsed = json.loads(raw)
        except Exception:  # noqa: BLE001 — a malformed vector is a missing one
            return None
        return parsed if isinstance(parsed, list) else None
    if isinstance(raw, (list, tuple)):
        return list(raw)
    return None
