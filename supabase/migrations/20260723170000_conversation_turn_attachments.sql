-- Persist the extracted text of chat attachments on their conversation turn.
-- Before this, attachment content lived only in the frontend's transient send
-- string: a later "generate a PRD" (or a reloaded thread) had no way to see a
-- document attached two messages earlier — it was silently forgotten.
-- Shape: [{"name": "...", "content": "..."}] — written by POST
-- /v1/conversations/{id}/turns, read back verbatim by list_turns.
alter table conversation_turns add column if not exists attachments jsonb;
