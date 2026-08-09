-- Which uploaded FORMAT produced each PRD.
--
-- CONTEXT. From this release a company can upload its own PRD format
-- (artifact_templates, 20260805120000) and activate one per artifact type;
-- prd_runner then generates into that skeleton instead of the vendored
-- prd-author one. Without this column there is no record, per PRD, of which
-- format it was written in — and the question is asked constantly in practice:
-- "why does this one look different from that one?", "did this predate the new
-- format?", "which PRDs do we need to regenerate?".
--
-- WHY NOT `template_version`. `prds.template_version` already exists and looks
-- like the natural home, but it is NOT one: main.py's `invalidate_stale_prds`
-- runs on every boot and marks every ready v3 PRD whose template_version
-- differs from PRD_TEMPLATE_VERSION as `invalidated`. Recording a per-company
-- format there would invalidate every customer's PRD list on the next deploy.
-- The per-PRD record of which format produced it is THIS column;
-- template_version stays the record of which house template generation the PRD
-- belongs to.
--
-- NULL means "generated under Sprntly's built-in format" — the state every
-- existing row is in, and the state every PRD stays in for a company that never
-- uploads a format. Additive, nullable, no backfill and no default, so the
-- column costs nothing to a tenant that never uses the feature.
--
-- DELIBERATELY NOT A FOREIGN KEY. `artifact_templates` rows are deletable — the
-- library screen offers Delete on any format, and deleting the active one falls
-- back to the built-in. An `on delete set null` FK would erase this PRD's
-- provenance the moment somebody tidied the library, and `on delete restrict`
-- would make a format undeletable for as long as any PRD referenced it. Keeping
-- it a bare uuid means the id survives the format's deletion: a reader can still
-- tell two PRDs apart by format, and still tell that a third predates the
-- feature entirely.
alter table prds
    add column if not exists artifact_template_id uuid;

-- Reverse lookup: "which PRDs did this format produce?" — the read behind
-- "you changed your format; here is what was written under the old one".
-- Partial, because the overwhelming majority of rows are NULL (built-in) and
-- indexing those buys nothing.
create index if not exists prds_artifact_template_idx
    on prds (artifact_template_id)
    where artifact_template_id is not null;
