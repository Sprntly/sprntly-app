-- Stores the Supabase user UUID on authenticated comments so the display
-- name can be resolved from profiles at read time (name changes propagate).
-- author column is preserved as fallback for old/anonymous rows.
ALTER TABLE prototype_comments
    ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES auth.users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS prototype_comments_user_id_idx ON prototype_comments(user_id);
