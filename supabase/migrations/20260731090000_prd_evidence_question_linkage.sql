-- Originating-question linkage for PRDs and Evidence, plus the Evidence half
-- of the conversation<->artifact binding that already exists for PRDs.
--
-- CONTEXT. Reports (db/reports.py) already stamps `question` / `ask_id` /
-- `conversation_id` on every captured report so a reader can see what asked for
-- it. PRDs and Evidence had no such trail: `conversations.prd_id` already links
-- a chat thread to the PRD it produced (see the 20260611 conversations
-- migration + db/conversations.bind_conversation_to_prd), but there was no
-- equivalent for Evidence, and neither table recorded the originating
-- QUESTION TEXT itself (only reachable, if at all, by walking the bound
-- conversation's turns).
--
-- DESIGN — reuse over duplication. `conversations.prd_id` is the existing,
-- shipped conversation<->PRD binding; this migration does NOT add a redundant
-- `prds.conversation_id` column that would just mirror it. Instead it extends
-- the SAME mechanism to Evidence (`conversations.evidence_id`, mirroring
-- `prd_id` exactly), so "which conversation produced this artifact" is always
-- answered by reading FROM `conversations`, for both artifact types.
--
-- `question` / `ask_id` have no existing home (PRDs/Evidence carry no analogue
-- of `ask_jobs`), so those land directly on `prds` / `evidences` — additive,
-- nullable, no backfill: a row generated before this ships simply has NULLs,
-- which the reader treats as "no originating-question context" rather than an
-- error (mirrors reports.py's posture, minus its NOT NULL DEFAULT '' — these
-- are genuinely optional here since most PRDs/Evidence are generated from a
-- brief insight click, not a chat question).
alter table prds
    add column if not exists question text,
    add column if not exists ask_id bigint;

alter table evidences
    add column if not exists question text,
    add column if not exists ask_id bigint;

-- Evidence half of the conversation<->artifact binding (mirrors
-- conversations.prd_id from the 20260611110000_conversations.sql migration).
alter table conversations
    add column if not exists evidence_id bigint references evidences (id) on delete set null;

-- Reverse lookup support ("which conversation belongs to this evidence"),
-- mirroring the intent of the (pre-existing, unindexed) prd_id column — this
-- one is indexed since it's introduced for exactly that read pattern.
create index if not exists conversations_evidence_idx on conversations (evidence_id);
