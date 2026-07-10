-- Link a chat conversation to the PRD it's about.
--
-- Conversations were previously keyed only by company/user, with no back-reference
-- to the PRD, so reopening a PRD tab could not recover the user's earlier chat
-- turns. Add a nullable prd_id + a lookup index so `GET /v1/conversations/by-prd`
-- can rehydrate a PRD tab's thread. Plain bigint (no FK) to stay migration-order
-- independent and idempotent for migrate-on-deploy.
alter table conversations add column if not exists prd_id bigint;
create index if not exists idx_conversations_company_prd
  on conversations(company_id, prd_id, updated_at desc);
