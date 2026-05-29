-- Design Agent sharing + completion (P2-06).
-- F6: share_token is an OPAQUE UUID (not a JWT, not derived from prototype_id).
-- F14/F15: is_complete + complete_checkpoint_id support Mark Complete / Resume.
-- AD F6 invariant: a random-UUID scan of /p/<uuid> returns 404 not 401 —
-- depends on share_token being unique-but-not-enumerable.
--
-- Workspace isolation (Rule #20-#23): no new tables here; columns inherit
-- prototypes.workspace_id. find_prototype_by_share_token is the ONE legitimate
-- public-route exception that does NOT filter by workspace_id — the token
-- itself is the access primitive (anyone holding the token holds the access).

alter table prototypes
    add column if not exists share_mode             text    not null default 'private',
    add column if not exists share_token            uuid    unique,
    add column if not exists share_passcode_hash    text,
    add column if not exists is_complete            boolean not null default false,
    add column if not exists complete_checkpoint_id bigint;

-- FK on complete_checkpoint_id; idempotent (drop+add pattern matches the
-- existing current_checkpoint_id FK in 20260528000000_design_agent_prototypes.sql).
alter table prototypes drop constraint if exists prototypes_complete_checkpoint_id_fkey;
alter table prototypes
    add constraint prototypes_complete_checkpoint_id_fkey
    foreign key (complete_checkpoint_id)
    references prototype_checkpoints(id)
    on delete set null;

-- share_token gets a partial index so the public-route lookup is O(log n) even
-- when most rows have NULL token (private mode is the default).
create index if not exists prototypes_share_token_idx
    on prototypes (share_token)
    where share_token is not null;

-- share_mode CHECK (constrained to the three legal values) — guard against
-- typos in the helper code (defence-in-depth; the helper validates too).
alter table prototypes drop constraint if exists prototypes_share_mode_check;
alter table prototypes
    add constraint prototypes_share_mode_check
    check (share_mode in ('private', 'public', 'passcode'));
