-- PRD 2-part output (prd-author skill): Part A (human PRD) lives in payload_md
-- as before; Part B (the LLM-readable Implementation Spec) is stored alongside
-- in this new column so downstream coding agents consume it without re-parsing
-- the human document. NULL/empty for legacy single-part PRDs.
ALTER TABLE prds ADD COLUMN IF NOT EXISTS llm_part text;
