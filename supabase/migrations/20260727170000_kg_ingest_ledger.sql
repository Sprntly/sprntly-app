-- Per-record ingest ledger: content hashes already extracted into the KG.
--
-- sync_provider re-pulls EVERY provider record on every sync and, before this
-- table, re-ran the LLM extraction on all of them — dedup happened only at the
-- signal WRITE (content-keyed uuid5 → PK conflict → "skipped"), i.e. after
-- paying for the model call. kg_ingest/extract_document was ~38% of total LLM
-- spend the week this landed, most of it re-extraction of unchanged records.
--
-- The runner now consults this ledger BEFORE extraction and only sends unseen
-- records to the model; hashes are recorded per successfully-extracted batch.
-- A changed record (new status/text) renders differently → new hash → is
-- extracted again, which is the desired behavior.
create table if not exists kg_ingest_ledger (
    enterprise_id uuid not null,
    content_hash text not null,
    provider text,
    created_at timestamptz not null default now(),
    primary key (enterprise_id, content_hash)
);
