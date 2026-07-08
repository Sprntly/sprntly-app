-- "User input needed" as structured, answerable questions.
--
-- The prd-author skill emits a "User input needed" section (≤5 items, tagged
-- [ESCALATE]/[NEED], each with an owner) as decorative HTML inside the PRD
-- document. This table lifts those items into STRUCTURED rows so the PRD's chat
-- can surface each as a message with clickable answer options, and answering one
-- can patch only the affected part of the PRD.
--
-- One row per question, keyed to prd_id (a regenerated PRD is a NEW prds row and
-- gets a fresh set of questions — mirrors how the whole PRD family works). The
-- rows are DERIVED from the PRD HTML by a lightweight extraction pass at
-- generation time (app.prd_questions.extract_input_questions); they are stored
-- (not re-inferred on read) because regenerating them is an LLM call — neither
-- free nor deterministic (per storage-decisions-name-cost-model).
--
-- `options` is the small set of proposed answers for an [ESCALATE] product
-- decision (each {label, description?}), rendered as buttons; [NEED] items carry
-- an empty array and answer as free text.
--
-- `status` is a genuine INPUT (the user answers or dismisses), not a derived
-- label, so it is a real column. `answer`/`answered_by`/`answered_at` capture the
-- resolution for audit + so a reopened PRD shows resolved questions, not re-asks.

create table if not exists prd_input_questions (
    id           bigint generated always as identity primary key,
    prd_id       bigint not null references prds(id) on delete cascade,
    ordinal      int    not null default 0,                  -- order within the PRD
    tag          text   not null default 'need',             -- 'escalate' | 'need'
    prompt       text   not null,                            -- the question text
    owner        text,                                       -- owner label (e.g. "PM"), nullable
    options      jsonb  not null default '[]'::jsonb,         -- [{label, description?}] — [] for [NEED]
    status       text   not null default 'pending',           -- 'pending' | 'answered' | 'dismissed'
    answer       text,                                       -- the chosen/typed answer (null until answered)
    answered_by  text,                                       -- user name that answered (null until answered)
    answered_at  timestamptz,
    created_at   timestamptz not null default now()
);

create index if not exists prd_input_questions_prd_id_idx  on prd_input_questions (prd_id);
create index if not exists prd_input_questions_status_idx   on prd_input_questions (status);

alter table prd_input_questions enable row level security;
-- No policies -- matches Sprntly's pattern (backend uses the service-role key and
-- bypasses RLS; the browser has no direct table access).

-- Defence-in-depth CHECKs; the helpers also validate. Idempotent drop+add.
alter table prd_input_questions drop constraint if exists prd_input_questions_tag_check;
alter table prd_input_questions
    add constraint prd_input_questions_tag_check
    check (tag in ('escalate', 'need'));

alter table prd_input_questions drop constraint if exists prd_input_questions_status_check;
alter table prd_input_questions
    add constraint prd_input_questions_status_check
    check (status in ('pending', 'answered', 'dismissed'));
