-- Role scoping for customer-issued MCP tokens (see 20260707120000_mcp_tokens.sql).
--
-- A token is minted as either 'developer' or 'pm' from Settings, and the
-- `mcp/` service uses this to gate which tools the connected AI client can
-- see and call:
--   developer -> ticket + PRD tools only
--   pm        -> everything (adds list_datasets / get_backlog / get_current_brief)
--
-- Default 'pm' so every token minted BEFORE this column existed keeps the
-- full tool set it was created with — adding the column must not silently
-- narrow anyone's working setup.

alter table mcp_tokens
    add column if not exists token_role text not null default 'pm'
    check (token_role in ('developer', 'pm'));
