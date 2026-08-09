-- Second LLM provider: a company can bring an OpenAI key and run Sprntly on it
-- instead of Anthropic.
--
-- Two additive columns on `companies`, mirroring what `llm_api_key_encrypted`
-- already does for Claude (20260711120000):
--
--   openai_api_key_encrypted  Fernet ciphertext of the company's OpenAI key,
--                             same TOKEN_ENCRYPTION_KEY as connector tokens and
--                             the Claude key. NEVER plaintext, never returned in
--                             full by the API (reads are masked).
--   llm_provider              which of the two the workspace actually runs on.
--
-- The two are deliberately INDEPENDENT. A company can hold both keys and flip
-- `llm_provider` between them without re-entering either — that is the whole
-- point of the switch in Settings → Admin, and it means "remove my OpenAI key"
-- and "go back to Claude" stay separate actions.
--
-- `llm_provider` defaults to 'anthropic' so every existing row keeps today's
-- behaviour exactly: the resolver reads this column, sees 'anthropic', and
-- resolves the Claude key + Claude platform fallback as it always has. Nothing
-- moves onto OpenAI until an admin chooses it.
--
-- Forward-only and idempotent (`if not exists` / a guarded constraint add), per
-- supabase/MIGRATIONS.md. No data is read, rewritten, or dropped.

alter table companies
    add column if not exists openai_api_key_encrypted text;

alter table companies
    add column if not exists llm_provider text not null default 'anthropic';

-- Constrain the column to the two providers the backend knows how to build a
-- client for (app/llm.py::get_client). A typo'd value would otherwise silently
-- fall through to the platform Claude key and be near-impossible to spot.
-- Guarded rather than bare so re-running the migration is a no-op.
do $$
begin
    if not exists (
        select 1 from pg_constraint where conname = 'companies_llm_provider_check'
    ) then
        alter table companies
            add constraint companies_llm_provider_check
            check (llm_provider in ('anthropic', 'openai'));
    end if;
end $$;
