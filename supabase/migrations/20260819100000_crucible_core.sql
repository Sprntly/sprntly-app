-- Goal Analysis (engine: Crucible) — the six tables a run is made of, plus the
-- per-thread flag that arms the feature in chat.
--
-- Plan: backend/docs/GOAL_ANALYSIS.md §4.4. Spec: backend/docs/crucible/.
-- Nothing reads or writes any of this yet; PR2 of a ten-PR sequence, landing
-- ahead of the code so later PRs are pure application changes.
--
-- WHY NEW TABLES RATHER THAN COLUMNS ON kg_signal. A Crucible claim needs four
-- things a signal does not carry — claim type, evidence strength, population,
-- and whether the source may VOTE on that claim type. The knowledge graph is
-- load-bearing for briefs, PRDs and chat, and `synthesis/convergence.py` scores
-- every theme off those same rows, so adding claim semantics there would change
-- brief output for every tenant as a side effect of building a different
-- feature. Claims are PROJECTED out of signals into `crucible_claims` instead:
-- additive, reversible, and droppable without touching the KG.
--
-- SCOPING mirrors `custom_artifacts` / `reports` / `ticket_sets`: every table
-- carries `company_id` and reads filter on it. The child tables carry it too,
-- denormalised, rather than joining up through `run_id` — a tenant filter that
-- depends on a join is one bad query away from crossing tenants, and this is a
-- company's own analysis of its own customers.
--
-- RLS IS SPELLED WITH `TO service_role` ON EVERY POLICY. Omitting it defaults
-- the policy to PUBLIC — every role including `anon`, whose key is public and
-- inlined into the web bundle. That is the Class B defect 20260812170000 exists
-- to close fleet-wide, and a new table must not reintroduce it.
--
-- THREE PLACES THIS SCHEMA ENFORCES AN INVARIANT, because the Python types
-- protect only the paths that go through them — a backfill, a psql session or a
-- future service does not:
--
--   * I9 — `crucible_goal_definitions` cannot hold a row at status='locked'
--     without a recorded human confirmation and an origin of 'adopted' or
--     'elicited'. There is no 'inferred'. A wrong goal definition produces a
--     fully coherent answer to the wrong question, which nothing downstream can
--     detect, so it is the one invariant worth a CHECK constraint.
--
--   * I3 — every measured quantity is NULLABLE WITH NO DEFAULT. `impact_value`,
--     `population_value`, `magnitude`, `predicted_value` and the range bounds
--     all mean "not measured" when NULL, and a `default 0` on any of them would
--     silently turn an unmeasured segment into a worthless one. Read every one
--     of these columns as tri-state.
--
--   * I8 — `assumed_params` defaults to an empty array, never NULL, so a
--     renderer cannot mistake "no assumptions recorded" for "none made" and
--     skip the disclosure.

-- ── Goal definitions ────────────────────────────────────────────────────────
-- Long-lived and NEVER MUTATED — a changed definition supersedes its
-- predecessor via `supersedes` rather than being edited, so a run stays
-- interpretable against the definition it actually used. `definition_hash`
-- covers the definition text plus its source config, and drift on a later run
-- is detected by comparing that hash rather than re-reading the prose.
create table if not exists crucible_goal_definitions (
    id                    bigint generated always as identity primary key,
    company_id            uuid        not null references companies (id) on delete cascade,
    raw_goal_text         text        not null default '',
    metric_name           text        not null default '',
    -- The company's own words, verbatim. Never a paraphrase: "revenue" tidied
    -- into "recognised revenue" is a different metric, asserted by us.
    definition_text       text        not null default '',
    definition_source_ref text,
    source_ref            text,
    currency              text        not null,
    direction             text        not null default 'increase'
                            check (direction in ('increase', 'decrease')),
    status                text        not null default 'unresolved'
                            check (status in ('unresolved', 'candidate', 'locked')),
    -- No 'inferred'. The absence is the invariant.
    origin                text        check (origin in ('adopted', 'elicited')),
    target_value          numeric,
    horizon_weeks         integer,
    population            jsonb       not null default '{}'::jsonb,
    -- Conflicting definitions are CARRIED, not resolved. Silently picking the
    -- more recently updated one is the failure I9 exists to prevent.
    conflicts_found       jsonb       not null default '[]'::jsonb,
    confirmed_by_user_at  timestamptz,
    confirmed_by_user_id  uuid        references auth.users (id) on delete set null,
    definition_hash       text        not null default '',
    supersedes            bigint      references crucible_goal_definitions (id) on delete set null,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now(),

    -- I9 at the storage layer. `locked` is the state that authorises an
    -- expensive run, so it is the state that must not be reachable by a code
    -- path that forgot to check.
    constraint crucible_goal_locked_needs_confirmation check (
        status <> 'locked'
        or (confirmed_by_user_at is not null
            and confirmed_by_user_id is not null
            and origin in ('adopted', 'elicited'))
    )
);

create index if not exists crucible_goal_definitions_company_idx
    on crucible_goal_definitions (company_id, created_at desc);

-- ── Runs ────────────────────────────────────────────────────────────────────
-- THE DURABLE JOB STATE. There is no in-memory job store, deliberately: the row
-- is created before the multi-minute work starts, so the panel has an id to
-- poll and a process death mid-run is recoverable by a sweep rather than
-- invisible. Same lifecycle shape as `custom_artifacts`, with two extra states
-- for the human gates.
--
-- `heartbeat_at` exists because `custom_artifacts` had to derive its orphan age
-- gate from MAX_ATTEMPTS x LONG_REQUEST_TIMEOUT_S — those rows carry no
-- heartbeat, so its sweep can only guess at 90 minutes. A run is longer and
-- more expensive than a document, so it gets the precise signal instead.
create table if not exists crucible_runs (
    id                 bigint generated always as identity primary key,
    company_id         uuid        not null references companies (id) on delete cascade,
    conversation_id    bigint      references conversations (id) on delete set null,
    goal_definition_id bigint      references crucible_goal_definitions (id) on delete set null,
    goal_text          text        not null default '',
    status             text        not null default 'draft'
                         check (status in (
                             'draft', 'resolving_goal', 'awaiting_confirmation',
                             'planning', 'awaiting_approval', 'running',
                             'ready', 'failed', 'cancelled'
                         )),
    -- Closed-set machine code, safe to return to the user. `error` holds raw
    -- exception text and is never returned — the `custom_artifacts.error_code`
    -- split, for the same reason: a transport error carries URLs.
    error_code         text,
    error              text,
    -- Every degradation that thinned this run, rendered where it affects a
    -- finding. A quietly thinner run is indistinguishable from a complete one,
    -- which is worse than the failure it replaced.
    coverage_notes     jsonb       not null default '[]'::jsonb,
    -- The framework named in the plan and used in the output. Stored so a later
    -- re-order is shown as a change rather than presented as if it had always
    -- been the criteria.
    prioritisation     jsonb       not null default '{}'::jsonb,
    claim_count        integer     not null default 0,
    tokens_spent       integer     not null default 0,
    started_at         timestamptz,
    finished_at        timestamptz,
    heartbeat_at       timestamptz,
    created_by         uuid        references auth.users (id) on delete set null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create index if not exists crucible_runs_company_created_idx
    on crucible_runs (company_id, created_at desc);
create index if not exists crucible_runs_conversation_idx
    on crucible_runs (conversation_id);
-- The orphan sweep's access pattern: unfinished runs whose heartbeat has gone
-- quiet. Partial, so it stays small however many finished runs accumulate.
create index if not exists crucible_runs_inflight_idx
    on crucible_runs (heartbeat_at)
    where status in ('resolving_goal', 'planning', 'running');

-- ── Claims ──────────────────────────────────────────────────────────────────
-- One row per projected claim. `raw` is retained on every row (spec acceptance
-- criterion 6): a disputed finding has to be traceable back to what the source
-- actually said, and a claim whose original payload was dropped cannot be
-- checked by anybody.
create table if not exists crucible_claims (
    id                 bigint generated always as identity primary key,
    run_id             bigint      not null references crucible_runs (id) on delete cascade,
    company_id         uuid        not null references companies (id) on delete cascade,
    assertion          text        not null default '',
    claim_type         text        not null
                         check (claim_type in (
                             'magnitude', 'mechanism', 'preference', 'constraint',
                             'direction', 'existence', 'attempt'
                         )),
    subject            text        not null default '',
    subject_cluster_id text,
    -- Connector id, NOT an enum — adding a connector must not need a migration.
    source_id          text        not null default '',
    artifact_id        text        not null default '',
    artifact_type      text        not null default '',
    strength           text        not null
                         check (strength in (
                             'causally_tested', 'measured', 'correlated',
                             'inferred', 'reported'
                         )),
    -- Computed from the connector registry, never self-declared by the claim.
    authoritative      boolean     not null default false,
    population         jsonb       not null default '{}'::jsonb,
    -- NULL = not measured. Never 0. See the I3 note in the header.
    population_value   numeric,
    magnitude          numeric,
    direction          text        not null default 'neutral'
                         check (direction in ('positive', 'negative', 'neutral')),
    -- REQUIRED: drives per-claim-type decay. A claim with no date cannot be
    -- aged, and defaulting it to now() would make stale evidence look fresh.
    observed_at        timestamptz not null,
    -- Provenance back to the KG row this was projected from, where there was
    -- one. NULL for a claim read straight from a document.
    signal_id          uuid,
    raw                jsonb       not null default '{}'::jsonb,
    created_at         timestamptz not null default now()
);

create index if not exists crucible_claims_run_idx on crucible_claims (run_id);
create index if not exists crucible_claims_company_idx on crucible_claims (company_id);
create index if not exists crucible_claims_cluster_idx
    on crucible_claims (run_id, subject_cluster_id);

-- ── Findings ────────────────────────────────────────────────────────────────
-- `impact_value` and `confidence_band` are denormalised out of the jsonb so the
-- panel can sort without parsing every row. `impact_value` IS NULLABLE and that
-- is load-bearing: an unsizeable finding sorts into its own section rather than
-- to the bottom of the list as though it had been measured and found worthless.
--
-- `surfaced_by` lives HERE and on nothing the impact scorer reads (I1). Which
-- sweeps found a finding feeds confidence and may never feed size.
create table if not exists crucible_findings (
    id              bigint generated always as identity primary key,
    run_id          bigint      not null references crucible_runs (id) on delete cascade,
    company_id      uuid        not null references companies (id) on delete cascade,
    -- Passes the causal lint before it is written (I5).
    statement       text        not null default '',
    claim_ids       jsonb       not null default '[]'::jsonb,
    adjudication    text        not null default 'no_authoritative_source'
                      check (adjudication in (
                          'conflict', 'single_authoritative',
                          'corroborated', 'no_authoritative_source'
                      )),
    impact          jsonb       not null default '{}'::jsonb,
    confidence      jsonb       not null default '{}'::jsonb,
    -- NULL = not sizeable. Never 0.
    impact_value    numeric,
    currency        text,
    confidence_band text        check (confidence_band in ('high', 'medium', 'low')),
    -- Confidence input only. Never read by impact scoring.
    surfaced_by     jsonb       not null default '[]'::jsonb,
    -- I8: never NULL, so "none recorded" cannot be mistaken for "none made".
    assumed_params  jsonb       not null default '[]'::jsonb,
    evidence        jsonb       not null default '[]'::jsonb,
    tier            text        not null default 'shallow'
                      check (tier in ('deep', 'shallow')),
    created_at      timestamptz not null default now()
);

create index if not exists crucible_findings_run_idx
    on crucible_findings (run_id, impact_value desc nulls last);
create index if not exists crucible_findings_company_idx on crucible_findings (company_id);

-- ── Ledger ──────────────────────────────────────────────────────────────────
-- Considered and not prioritised. NOT A GRAVEYARD: each entry keeps the claim
-- set, the screening inputs and the stage it stopped at, because the ledger is
-- the credibility of the ranking. A reader who asks why item fourteen placed
-- there gets real analysis resumed from here, not the one-line dismissal
-- restated — and every candidate that entered tiering appears either in
-- findings or here, so count in equals count out.
create table if not exists crucible_ledger (
    id                     bigint      generated always as identity primary key,
    run_id                 bigint      not null references crucible_runs (id) on delete cascade,
    company_id             uuid        not null references companies (id) on delete cascade,
    label                  text        not null default '',
    reason                 text        not null default '',
    stopped_at_stage       text        not null default '',
    claim_ids              jsonb       not null default '[]'::jsonb,
    screening_inputs       jsonb       not null default '{}'::jsonb,
    -- NULL = not measured. Never 0.
    impact_value           numeric,
    confidence_score       numeric,
    -- Set when a rejection can come back: "unbuildable this half" is a
    -- condition, not a verdict. Only mechanism_invalid is permanent.
    reactivation_condition text,
    created_at             timestamptz not null default now()
);

create index if not exists crucible_ledger_run_idx on crucible_ledger (run_id);
create index if not exists crucible_ledger_company_idx on crucible_ledger (company_id);

-- ── Predictions ─────────────────────────────────────────────────────────────
-- WRITTEN FROM RUN ONE, READ BY NOTHING UNTIL PHASE 4. That is deliberate and
-- it is the only reason this table is in PR2 rather than later: calibration is
-- cheap to record now and impossible to reconstruct afterwards, because you
-- cannot recover predictions you never logged. Without it, "high confidence" is
-- an assertion nobody can ever check — and if high-confidence items turn out to
-- land no more often than medium ones, every band in every report has been
-- decoration.
--
-- `adoption` is the other half, and it answers a question outcomes alone
-- cannot: a recommendation that is consistently right and consistently ignored
-- is a communication defect, and nothing else in the system would surface it.
create table if not exists crucible_predictions (
    id                  bigint      generated always as identity primary key,
    run_id              bigint      not null references crucible_runs (id) on delete cascade,
    company_id          uuid        not null references companies (id) on delete cascade,
    finding_id          bigint      references crucible_findings (id) on delete cascade,
    band                text        not null check (band in ('high', 'medium', 'low')),
    currency            text,
    -- NULL = not measured. Never 0.
    predicted_value     numeric,
    range_low           numeric,
    range_high          numeric,
    adoption            text        check (adoption in (
                            'shipped', 'modified_shipped', 'deferred', 'rejected'
                        )),
    adoption_reason     text,
    outcome_value       numeric,
    landed_in_range     boolean,
    outcome_recorded_at timestamptz,
    created_at          timestamptz not null default now()
);

create index if not exists crucible_predictions_company_band_idx
    on crucible_predictions (company_id, band);
create index if not exists crucible_predictions_run_idx
    on crucible_predictions (run_id);

-- ── The chat-side switch ────────────────────────────────────────────────────
-- Sticky per thread. A run is a conversation, not a one-shot: follow-ups
-- ("expand number fourteen", "re-rank with effort weighted higher") belong to
-- the run, so the mode has to survive a reload and the next message.
--
-- DEFAULT FALSE with NOT NULL, so every existing conversation is off and no
-- backfill is needed. This column decides whether a message bypasses
-- `chat_intent.resolve` entirely — the feature never touches the intent
-- envelope, which is what keeps it removable.
--
-- No `if exists` guard on the table: `conversations` is created by
-- 20260611110000_conversations.sql, and if it is genuinely missing this
-- migration should fail loudly rather than no-op and leave the column absent.
alter table public.conversations
  add column if not exists crucible_mode boolean not null default false;

comment on column public.conversations.crucible_mode is
  'Goal Analysis mode is armed for this thread: messages route to '
  '/v1/crucible/runs instead of the chat intent envelope. Sticky until the '
  'user removes the composer chip.';

-- ── RLS — service role only, TO clause spelled out on every policy ──────────
alter table crucible_goal_definitions enable row level security;
drop policy if exists "srv_crucible_goal_definitions" on crucible_goal_definitions;
create policy "srv_crucible_goal_definitions" on crucible_goal_definitions
    for all to service_role using (true) with check (true);

alter table crucible_runs enable row level security;
drop policy if exists "srv_crucible_runs" on crucible_runs;
create policy "srv_crucible_runs" on crucible_runs
    for all to service_role using (true) with check (true);

alter table crucible_claims enable row level security;
drop policy if exists "srv_crucible_claims" on crucible_claims;
create policy "srv_crucible_claims" on crucible_claims
    for all to service_role using (true) with check (true);

alter table crucible_findings enable row level security;
drop policy if exists "srv_crucible_findings" on crucible_findings;
create policy "srv_crucible_findings" on crucible_findings
    for all to service_role using (true) with check (true);

alter table crucible_ledger enable row level security;
drop policy if exists "srv_crucible_ledger" on crucible_ledger;
create policy "srv_crucible_ledger" on crucible_ledger
    for all to service_role using (true) with check (true);

alter table crucible_predictions enable row level security;
drop policy if exists "srv_crucible_predictions" on crucible_predictions;
create policy "srv_crucible_predictions" on crucible_predictions
    for all to service_role using (true) with check (true);
