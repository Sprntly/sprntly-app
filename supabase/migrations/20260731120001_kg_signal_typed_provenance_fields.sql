-- Promote skill_id/origin/channel/evidence_eligible from informal
-- kg_signal.provenance jsonb keys to real typed columns (app.graph.types.Signal).
--
-- Nullable, no backfill: existing rows keep carrying these values only inside
-- `provenance` (skill_id/origin/channel) — the application layer
-- (GraphFacade._row_to_signal, Signal.__post_init__) falls back to the dict
-- for any row where the typed column is null, so old data keeps resolving
-- identically through the transition. New writes (app.graph.extractor)
-- populate both the typed column and the dict.
alter table kg_signal
    add column if not exists skill_id text,
    add column if not exists origin text,
    add column if not exists channel text,
    add column if not exists evidence_eligible boolean;

comment on column kg_signal.skill_id is
    'Vendored extraction skill (backend/skills/<id>/) that produced this signal, or the literal "generic" tag when none applied. Null on rows written before this column existed — see provenance->>''skill_id'' and Signal.__post_init__ fallback.';
comment on column kg_signal.origin is
    'How this signal''s document reached us — "upload" | "connector" | "web_research" | null. See app.graph.extractor.extract_document docstring.';
comment on column kg_signal.channel is
    'Finer-grained delivery channel within an origin, e.g. "upload" for a connector-category upload. See provenance->>''channel'' for pre-migration rows.';
comment on column kg_signal.evidence_eligible is
    'Does this signal count as brief evidence (app.synthesis.convergence)? Separates "what kind of source" (source_type) from "does it count as evidence" — see app.graph.types.compute_evidence_eligible. Null on pre-migration rows; readers compute it on the fly from source_type + origin in that case.';

create index if not exists kg_signal_skill_id_idx
    on kg_signal (enterprise_id, skill_id);
create index if not exists kg_signal_origin_idx
    on kg_signal (enterprise_id, origin);
