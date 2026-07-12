-- Per-company Claude (Anthropic) API key.
--
-- When a company configures its own key in Admin settings, ALL Claude LLM calls
-- for that company use THEIR key (never the platform key, never the design-agent
-- key). OpenAI embeddings are unaffected. The value is Fernet-encrypted at rest
-- (same TOKEN_ENCRYPTION_KEY as connector OAuth tokens — see
-- app/connectors/tokens.py); the column never holds plaintext.
ALTER TABLE companies
    ADD COLUMN IF NOT EXISTS llm_api_key_encrypted text;
