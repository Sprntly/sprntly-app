# Entity resolution — options for cleaning up duplicate KG nodes

Written to answer the KG owner's ask directly: whether to build entity
resolution (ER) on what's already in the stack, or bring in a dedicated
resolution tool. Short version up front: **start with what's already
there — embedding similarity on the fields the `Entity` row already
carries.** No new infra, no new vendor, and it's a prerequisite for the
company-root backfill (reconnecting existing floating `segment`/
`competitor`/`constraint` nodes to each tenant's root), which needs a
duplicate-candidates pass before it wires anything up.

## The problem, concretely

Entities in the KG (`kg_entity`) are created by several independent write
paths — `business_context_projection.py` (segments/competitors from the
onboarding doc), `research/competitor.py` (competitors from the market-
research agent), the extraction pipeline (themes/accounts from ingested
content), and now the `company` root. Each of these already does a
find-or-create against *its own* recent candidates
(`GraphFacade.find_candidates`, a pgvector kNN lookup), but there's no pass
that looks across the *whole* graph for near-duplicates that were created
far apart in time, or via different paths — e.g. "Salesforce" (created by
the onboarding doc) and "SF" (created three weeks later by the market
research agent) end up as two disconnected `competitor` nodes instead of
one, and nothing today notices or merges them.

## Option A — embedding-similarity merge on existing fields

Every `Entity` already carries the two fields a similarity-based merge
needs: `embedding` (1536-d, OpenAI `text-embedding-3-small`, the same
model the find-or-create paths already call via `embed_texts`) and
`canonical_label` / `aliases`. The resolution *policy* already exists too
— `config_layers.PLATFORM_DEFAULTS["resolution"]`: `tau_high = 0.86`
(≥ → same node), `tau_low = 0.72` (< → new node), and an LLM-adjudicated
gray zone in between (`adjudication: "llm"`). Today that policy only runs
**at write time**, inside each path's own `_ensure_entity`-style
find-or-create. Option A is: run the *same* policy as a **standalone sweep**
— for each entity type, pull all existing entities of that type
(`GraphFacade.query_entities`), pgvector-kNN each one against the rest via
the existing `kg_find_candidates` Postgres function, and flag pairs that
clear `tau_high` (near-certain duplicates) or land in the gray zone
(candidates for the same LLM-adjudication path already used at write
time, or a human review queue if we'd rather keep the sweep pass fully
automatic and deterministic).

**What it costs:** a new sweep function in `app.graph` (or a script under
`backend/scripts/`) that calls facade primitives that already exist —
`query_entities`, `find_candidates` — plus whatever merge/alias-append
logic decides how two `Entity` rows collapse into one (which one survives,
how their edges get repointed). That merge step is real work, but it's
application code against an API that's already there — no new service,
no new credentials, no new operational surface.

**What it doesn't solve:** it's per-tenant and per-type by construction
(entities only get compared against others of the same `type` within the
same `enterprise_id`, which is also the tenant-isolation boundary we
already enforce everywhere else) — so it won't catch, say, a `segment`
mislabeled as a `competitor`. In practice that's a labeling bug at the
write path, not an ER problem, so it's a reasonable scope boundary rather
than a limitation worth designing around.

## Option B — a dedicated resolution tool as a sidecar (e.g. Neo4j)

The alternative surfaced on the call — a purpose-built graph/ER tool
running alongside Postgres, with the KG's nodes synced into it, using
whatever resolution algorithm that tool ships (graph-topology-aware
matching, blocking strategies tuned for large duplicate volumes, etc.).

**What it costs:** a second datastore to provision, secure, and keep in
sync with the Postgres source of truth (sync direction, staleness,
failure-mode handling for when the sidecar and Postgres disagree), a new
integration surface, and a new operational dependency for an early-stage
platform that doesn't have dedicated infra headcount yet. It's a real
capability upgrade if duplicate volume or resolution quality genuinely
outgrows what similarity thresholds can do — but that's not evidenced yet;
nobody has run Option A against real data and found it wanting.

## Recommendation

**Start with Option A.** It's near-zero additional infrastructure — pgvector
is already provisioned, the embeddings already exist on every entity, and
the resolution policy (`tau_high`/`tau_low`/LLM adjudication) is already
written and already governs every write-time find-or-create; a sweep pass
is the same policy pointed at the existing graph instead of one new node
at a time. It's also directly reusable by the company-root backfill: that
migration needs a duplicate-candidates pass before it wires existing
floating `segment`/`competitor` nodes to each tenant's root (so a genuine
duplicate pair doesn't get baked in as two permanently-separate branches
of the same tree) — Option A's sweep *is* that pass.

Revisit Option B only if Option A's resolution quality genuinely can't be
reached with embedding similarity + LLM adjudication alone once it's
running against real data — e.g. duplicate volume that makes an O(n²)-ish
per-type sweep too slow, or a duplicate-detection accuracy ceiling that
threshold tuning can't clear. Neither of those is a foregone conclusion;
they're the two conditions worth watching for before reaching for a
sidecar.
