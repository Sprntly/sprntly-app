-- Chat-task evidence docs: key by (brief_id, theme_id) instead of insight_index.
--
-- "Generate a PRD for <need>" in chat now also generates an Evidence artifact
-- when semantic KG retrieval finds signals backing the task. Those docs aren't
-- tied to a brief insight, so insight_index (kept as a storage sentinel 0)
-- can't key them — they'd collide with the real insight-0 evidence. Mirror
-- prds.theme_id: a synthetic 'chat:<hash-of-task>' id. Brief-insight docs keep
-- theme_id NULL and their (brief_id, insight_index) keying is unchanged.
alter table evidences add column if not exists theme_id text;

create index if not exists evidences_brief_theme_idx
  on evidences (brief_id, theme_id) where theme_id is not null;
