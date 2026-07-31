-- Dynamically-registered OAuth clients (RFC 7591 Dynamic Client Registration).
--
-- Every connector before this one authenticates with a client_id/secret pair a
-- human created by hand in a vendor developer portal, so the pair lives in env
-- vars. Marvin (heymarvin.com) has no such portal: its MCP authorization server
-- publishes a `registration_endpoint` and expects clients to register
-- THEMSELVES over the wire. The credential is therefore minted at runtime, and
-- has to outlive the process that minted it — re-registering on every boot
-- would spray orphan client records across the provider's side and silently
-- invalidate nothing, just accumulate.
--
-- One row per (provider, issuer). `issuer` is part of the key because a single
-- provider can run several authorization servers — Marvin has a US one
-- (app.heymarvin.com) and an EU one (app.eu.heymarvin.com), and a client
-- registered against one is meaningless to the other.
--
-- The secret is Fernet-encrypted with TOKEN_ENCRYPTION_KEY, exactly like the
-- connector tokens in `connections.token_json_encrypted`. It is NULL for public
-- clients (registered with token_endpoint_auth_method="none", PKCE only).
create table if not exists oauth_dynamic_clients (
    provider text not null,
    issuer text not null,
    client_id text not null,
    client_secret_encrypted text,
    -- The provider's full registration response, minus the secret. Kept for
    -- debugging ("which redirect_uris did we register?") and because RFC 7591
    -- servers may hand back a registration_access_token for later updates.
    registration_json jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (provider, issuer)
);
