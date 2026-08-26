-- kg_signal.source_call_id — the FK that links a distilled signal back to the
-- exact call it was extracted from, in call_index.
--
-- WHY A SEPARATE COLUMN (not source_id). kg_signal.source_id is a uuid FK into
-- kg_source (report/corpus/agent-inferred sources). call_index.id is a bigint
-- identity, so a call's id cannot live in source_id — stamping it there throws
-- `invalid input syntax for type uuid`. call_index stays the call catalog (it is
-- deliberately NOT unified into kg_source), so a call gets its own typed FK here
-- and source_id is left free for the source class it was built for.
--
-- ADDITIVE and NULLABLE — safe on a live table. Every existing signal, and every
-- non-call signal, keeps source_call_id = NULL. A call not yet catalogued at
-- extraction time (the puller/index sync run on independent schedules) also
-- stays NULL, back-linkable later via provenance.external_id — see
-- graph/extractor.extract_document. ON DELETE SET NULL mirrors source_id: de-
-- indexing a call (a disconnect wipes its call_index rows) must not destroy the
-- signals distilled from it, only drop the now-dangling link.
alter table kg_signal
    add column if not exists source_call_id bigint
        references call_index (id) on delete set null;

create index if not exists kg_signal_source_call_idx
    on kg_signal (source_call_id);
