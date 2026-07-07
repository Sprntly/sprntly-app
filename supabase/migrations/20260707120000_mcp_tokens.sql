-- Customer-issued MCP (Model Context Protocol) API tokens.
--
-- Lets a customer connect THEIR OWN AI client (Claude Desktop, Claude Code,
-- claude.ai custom connectors) to their own Sprntly workspace via the `mcp/`
-- service. Same trust model as the OAuth connectors table (`connections`)
-- but INBOUND: this is how something authenticates INTO Sprntly, not a
-- credential Sprntly holds for a third party.
--
-- The raw token is a random secret shown to the user ONCE at creation time
-- (see app/db/mcp_tokens.py) and never stored — only its SHA-256 hash. This
-- is the standard API-key pattern (compare-by-hash), not the Fernet-reversible
-- encryption used for outbound OAuth tokens (app/connectors/tokens.py) — the
-- server never needs to read the raw token back, only verify a presented
-- one matches.

create table if not exists mcp_tokens (
    id            uuid primary key default gen_random_uuid(),
    company_id    uuid not null references companies (id) on delete cascade,
    user_id       uuid not null references auth.users (id) on delete cascade,
    -- User-chosen label so a list of tokens is identifiable ("Claude Desktop",
    -- "CI pipeline"). Defaults so an unlabeled create still reads sensibly.
    name          text not null default 'MCP token',
    token_hash    text not null,
    -- First ~20 chars of the raw token (safe to display — far too short to
    -- brute-force into a working credential), so the settings UI can show
    -- "sprn_mcp_a1b2…" without ever re-displaying the full secret.
    token_prefix  text not null,
    -- Reserved for v2 scoping (read | read_write). Every v1 token is 'read'.
    scopes        text not null default 'read',
    created_at    timestamptz not null default now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);

create unique index if not exists mcp_tokens_hash_idx on mcp_tokens (token_hash);
create index if not exists mcp_tokens_company_idx on mcp_tokens (company_id);

alter table mcp_tokens enable row level security;

-- No client-facing policies: written + read exclusively by the backend via
-- the service-role key (mirrors feedback.sql / drip_email_sends). RLS on
-- with no policies denies all anon/authenticated access by default.
