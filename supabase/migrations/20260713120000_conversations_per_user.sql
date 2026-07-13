-- Chats are PER-USER: each conversation belongs to the member who started it
-- (user_id — stamped by the API on create) and is no longer listed to the
-- whole workspace. Only artifacts (PRDs, prototypes, evidence) are
-- workspace-shared. Legacy rows created before stamping (user_id IS NULL)
-- cannot be attributed to an owner, so they remain visible company-wide.
--
-- The user_id column itself has existed since 20260611110000_conversations.sql
-- (it was never written or read); this adds the index the per-user list needs.
create index if not exists idx_conversations_company_user
  on conversations(company_id, user_id, created_at desc);
