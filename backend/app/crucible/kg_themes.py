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

#: Words that carry no topic. Deliberately TINY. The overlap rule below
#: discriminates by counting shared content words, so every word removed here
#: is discrimination given away — stoplisting 'enterprise', 'compliance',
#: 'agent', 'model' or 'infrastructure' because they look like filler would
#: reduce real topic pairs to one shared token and split them right back up.
_STOPWORDS = frozenset(
    {"and", "the", "of", "for", "a", "to", "in", "on", "with"}
)

#: Shared content words above which two labels name the same topic. Two is the
#: whole rule: one shared word is a coincidence of vocabulary
#: ('Prototype Agent' / 'Strategy Agent'), two is a subject.
_MIN_SHARED_TOKENS = 2

#: Above this many labels, a merged group has stopped being a topic and become
#: a CATEGORY. On a real tenant the overlap rule below correctly formed a
#: 15-label go-to-market group — every pair independently qualified — holding
#: ICP, partnerships, sales, traction, fundraising, beta program, global scale
#: and pitch strategy. A reader told "your recommendation: go-to-market" learns
#: nothing; that is a different failure from the shredding this module fixes,
#: and not obviously a smaller one. Such a group is discarded whole and its
#: members fall back to exact-label matching.
#:
#: A CAP RATHER THAN A STRICTER RULE, deliberately. The mechanism behind those
#: groups is that a short generic label ('go to market', 'architecture') is a
#: subset of every longer label containing it, so it donates membership to all
#: of them. Requiring two words on the subset side would stop that — and would
#: also stop 'benchmarking' / 'benchmarking & validation' and 'billing' /
#: 'billing & pricing', which are exactly the merges worth having. The cap is
#: orthogonal: it does not change which PAIRS qualify, it only declines to act
#: when the result has outgrown being a topic.
#:
#: 9 IS FITTED TO ONE CORPUS AND IS A JUDGEMENT CALL, not a measured constant.
#: It is the size of the largest group on that tenant a human read as a real
#: topic — enterprise compliance, which is also the group holding the two deep
#: findings that motivated this fix. Above it sat ICP fit (10), competitive
#: landscape (11) and go-to-market (17), all of which read as categories.
#:
#: AND THE DIAL SITS DIRECTLY ON THAT BOUNDARY, which is worth knowing before
#: moving it. Group sizes depend on the order candidates are offered in, and
#: that order is citation-weighted, so the SAME corpus produces a different
#: size depending on how much each label was cited: the enterprise compliance
#: group measures 9 under the weighting this module actually uses and 8 when
#: every label is counted once. A cap of 8 therefore dissolves it on the real
#: path and un-merges the exact pair this fix exists for. One member either way
#: flips it. Treat this as a dial someone chose against one corpus, not a
#: number the data produced, and re-measure before trusting it on another.
_MAX_TOPIC_GROUP = 9

#: FAIL SAFE TOWARD NOT MERGING. On a corpus where labels overlap for reasons
#: we did not anticipate, merging could collapse the whole graph into one
#: mega-finding — a single "everything" recommendation that is confidently
#: wrong, which is worse than the shredding it replaced. If either guard trips,
#: the overlap tier is discarded whole and only the exact-label tier survives;
#: that tier cannot over-merge by construction.
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

    Read in two steps because the tables are shaped very differently: the
    relationship rows are narrow and page cheaply, while `kg_entity` carries an
    embedding column that makes a scan expensive — so entities are fetched by
    the ids the relationships actually referenced, and only their labels are
    selected. The fold below reads words, not vectors, so no embedding is
    fetched at all.

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
        canonical = canonicalize_themes(labels, counts)
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

    TIER 1 OF THE MERGE, and the half that cannot over-merge at all: two
    labels that reduce to the same string are the same topic, full stop.
    'competitive landscape' and 'Competitive landscape' are separate entities
    in the graph today, each with its own recommendation, purely because one
    was capitalised. This is what fixes those — equality is equality.

    Also the input to tier 2, which counts the words this produces.

    Case-folded, unicode-normalised, separators ('&', '/', dashes) flattened to
    a space, edge punctuation stripped, internal whitespace collapsed. NOT
    stemmed and NOT de-pluralised: 'renewal' and 'renewals' plausibly differ,
    and a stemmer would make that call silently on every label to rescue a
    handful — tier 2 already merges the pairs worth merging by counting the
    words they share, without guessing about any single one of them.
    """
    s = unicodedata.normalize("NFKC", label or "")
    s = _SEPARATORS.sub(" ", s)
    s = " ".join(s.split())
    return s.strip(_PUNCT_EDGES).casefold().strip()


def content_tokens(label: str) -> frozenset[str]:
    """The topic-bearing words of a label, normalised.

    Built on `normalize_label`, so it inherits case-folding and separator
    flattening for free — 'EU AI Act / regulatory compliance' and
    'regulatory compliance — EU AI Act' produce the SAME token set, which is
    the point: word order is not topic.
    """
    return frozenset(
        t for t in normalize_label(label).split() if t and t not in _STOPWORDS
    )


def same_topic(a: frozenset[str], b: frozenset[str]) -> bool:
    """Do two label token sets name the same topic?

    TWO WAYS TO QUALIFY, and both are about the words themselves rather than
    about a learned distance:

      SUBSET — one label says everything the other says and possibly more.
      'benchmarking' vs 'benchmarking validation' is one topic named at two
      levels of detail, not two topics.

      TWO SHARED CONTENT WORDS — 'enterprise compliance infrastructure' and
      'enterprise compliance requirements' share {enterprise, compliance}.
      Sharing exactly one is a coincidence of vocabulary: 'Prototype Agent'
      and 'Strategy Agent' share {agent} and are different products.

    An EMPTY set never matches, including against another empty set. It is
    vacuously a subset of everything, so admitting it would let a label made
    entirely of stopwords absorb the entire corpus.
    """
    if not a or not b:
        return False
    if a <= b or b <= a:
        return True
    return len(a & b) >= _MIN_SHARED_TOKENS


def canonicalize_themes(
    labels: Mapping[str, str],
    counts: Mapping[str, int],
) -> dict[str, str]:
    """theme entity id -> the id that should REPRESENT it.

    READ-SIDE ONLY. Nothing here is written back: `kg_entity` keeps its split
    rows, every other consumer of the graph sees exactly what it saw before,
    and Crucible simply stops being fooled into writing one recommendation per
    shard of a topic.

    WHY WORDS AND NOT EMBEDDINGS. The obvious fix is the one the extractor
    already tries — cosine between label embeddings — and it does not work at
    this length. Measured on a real tenant:

        0.7924  'Enterprise compliance infrastructure'
                / 'enterprise compliance requirements'   <- ONE topic
        0.8012  'Prototype Agent' / 'Strategy Agent'     <- TWO products

    The true pair scores BELOW the false one. No threshold separates them in
    either direction, so any cosine gate must either miss real synonyms or fuse
    real distinctions, and the extractor's own gate at 0.86 does the former so
    thoroughly it fired 0 times in 63,903 pairs on that tenant. Counting shared
    content words separates exactly those two cases and, unlike a threshold, a
    reader can check it by looking at the two labels.

    COMPLETE LINKAGE, NOT SINGLE LINKAGE. This is the trap. Under single
    linkage a label only has to match SOMETHING in a group to join it, and
    'compliance' bridged EU AI Act -> provenance -> citation chains into one
    27-member component on this corpus — a mega-finding assembled out of pairs
    that were each individually defensible. So a candidate is admitted only if
    it satisfies the rule against EVERY member already in the group. Groups
    stay cliques, and a bridge word cannot walk one topic into another.

    REPRESENTATIVE SELECTION IS MOST-CITED, TIES BROKEN ON LOWEST ENTITY ID.
    Both halves matter. Most-cited is the shard the graph actually leaned on,
    so its label is the one a reader is most likely to recognise. The tie-break
    is what makes the choice a FUNCTION of the input rather than of dict order
    or of which page a batch landed on — the same corpus has to produce the
    same representative tomorrow, or the same evidence yields different
    findings on different days. Same discipline `figure_class` enforces when it
    classifies once and never re-rolls.

    That ordering is also the order candidates are offered to the clique
    growth, so the first member of a group IS its representative: one rule,
    applied once.
    """
    if not labels:
        return {}

    def rank(entity_id: str) -> tuple[int, str]:
        return (-int(counts.get(entity_id, 0)), str(entity_id))

    # TIER 1 — exact match on the normalised label. No threshold, no
    # judgement: two labels that reduce to the same string are the same topic.
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

    groups: list[list[str]] = [sorted(v, key=rank) for v in by_norm.values()]
    groups.extend([[e] for e in singletons])
    groups.sort(key=lambda g: rank(g[0]))
    exact_merged = sum(len(g) - 1 for g in groups)

    canonical: dict[str, str] = {}
    for g in groups:
        for member in g:
            canonical[member] = g[0]

    # TIER 2 — content-word overlap between the tier-1 representatives. Members
    # of a tier-1 group share a normalised label by construction, so they share
    # a token set too and the representative speaks for all of them.
    reps = [g[0] for g in groups]
    tokens = {r: content_tokens(labels.get(r, "")) for r in reps}
    #
    # GREEDY FIRST FIT, AND THE GROUPS ARE NOT TRANSITIVELY CLOSED. A candidate
    # joins the first group it fully qualifies for, so two labels that satisfy
    # the rule with each other can still end up apart: 'benchmarking' joined a
    # TierMem benchmarking group formed earlier in the order, and
    # 'benchmarking & validation' — a subset match against it — could not then
    # clear every member of that group. Do not read the output as "every
    # qualifying pair is together". It errs toward leaving topics split, which
    # is the safe direction here, but it is a real property and not an
    # oversight.
    cliques: list[list[str]] = []
    for rep in reps:                      # already in (most-cited, id) order
        mine = tokens[rep]
        joined = False
        if mine:
            for clique in cliques:
                if all(same_topic(mine, tokens[m]) for m in clique):
                    clique.append(rep)
                    joined = True
                    break
        if not joined:
            cliques.append([rep])

    # THE SIZE CAP, applied before anything is committed to. An oversized group
    # is dissolved rather than trimmed: keeping an arbitrary 8 of its 15 labels
    # would be a merge nobody chose, and there is no principled way to pick
    # which 7 to evict. Its members drop back to their tier-1 groups, which
    # still hold — case variants stay merged, and only the overlap merges go.
    oversized = [c for c in cliques if len(c) > _MAX_TOPIC_GROUP]
    if oversized:
        logger.info(
            "crucible: %d theme group(s) exceeded %d labels and were dissolved "
            "as categories rather than topics (largest %d)",
            len(oversized), _MAX_TOPIC_GROUP, max(len(c) for c in oversized),
        )
        cliques = [c for c in cliques if len(c) <= _MAX_TOPIC_GROUP]
        cliques.extend([m] for c in oversized for m in c)

    # THE SAFE-FAIL CHECK, before anything is applied.
    largest = max((len(c) for c in cliques), default=0)
    if len(reps) >= _GUARD_MIN_THEMES and (
        largest > _MAX_MERGE_SHARE * len(reps)
        or len(cliques) < max(2, _MIN_GROUP_RATIO * len(reps))
    ):
        logger.warning(
            "crucible: theme overlap merge is degenerate (%d themes -> %d "
            "topics, largest %d); keeping exact-label merging only",
            len(reps), len(cliques), largest,
        )
        return canonical

    final_of = {m: clique[0] for clique in cliques for m in clique}
    for member, rep in list(canonical.items()):
        canonical[member] = final_of.get(rep, rep)

    logger.info(
        "crucible: themes canonicalised %d -> %d (%d by exact label, %d by "
        "content-word overlap)",
        len(labels), len(set(canonical.values())), exact_merged,
        len(reps) - len(cliques),
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
