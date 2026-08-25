-- prds.first_read_at — the first time a person actually opened this PRD.
--
-- WHY. The pipeline writes PRDs nobody asked for: a weekly brief generates one
-- per top insight (`source='brief'`), and the backlog sweep generates one per
-- ranked theme that never made the brief (`source='backlog'`). They accumulate
-- in the Artifacts library and on Projects alongside the documents people
-- deliberately created from chat, an upload, or an idea — and the auto ones
-- outnumber them. The library stops reading as "what I made" and starts
-- reading as machine exhaust.
--
-- The rule the product wants is "hide the auto-generated ones nobody has
-- read", and until now the second half of that had nothing behind it: there
-- was no read, view, or opened signal on `prds` anywhere. Conversations have
-- read cursors and nudges have an `opened_at`; a PRD had neither. This column
-- is that signal.
--
-- WHY A TIMESTAMP, NOT A BOOLEAN. "When was it first read" answers questions a
-- flag cannot — how long auto PRDs sit untouched, whether the brief is
-- actually driving anyone into them — and it is still just as cheap to test
-- for null. FIRST read, never updated after: this measures whether a document
-- was ever engaged with, not recency of access, and an advance-only write is
-- one statement with no read-modify-write race.
--
-- NO BACKFILL, DELIBERATELY. Every existing row stays NULL, and the listing
-- rule reads "auto-generated AND never read" — so today's auto PRDs are hidden
-- by the RULE rather than by a mass write against live customer data. Nothing
-- here is destructive and nothing is stamped: reverting is deleting a filter,
-- not restoring rows.
--
-- Nullable, no default: adding a column with a volatile default rewrites the
-- whole table, and `prds` is live and prod-shared (staging and prod share one
-- Supabase project). `if not exists` for an idempotent re-run, matching the
-- repo's additive-migration pattern.

alter table prds add column if not exists first_read_at timestamptz;

-- The listing filter's predicate is (source, first_read_at): find the auto rows
-- that have never been opened. Partial on NULL because the hidden set is the
-- one being scanned — a PRD that has been read is never a candidate again.
create index if not exists prds_unread_auto_idx
    on prds (source)
    where first_read_at is null;

-- prds.auto_generated — the pipeline wrote this one; nobody asked for it.
--
-- WHY A NEW COLUMN AND NOT `source`. `source` records where a PRD's SUBJECT
-- came from, not who set it going, and `brief` is written by FIVE call sites:
-- the pipeline's full-regen fan-out (routes/brief.py, one PRD per insight),
-- a user clicking Generate on a Top Insights card (routes/prd.py
-- POST /generate), the multi-agent run, and the runner — all taking the
-- column default. Filtering the library on `source='brief'` therefore hid
-- documents people had deliberately created. It was tried; it did exactly
-- that.
--
-- Only the code that STARTS a generation knows whether a person asked for it,
-- so that is the only place that can record it. A listing looking at the
-- finished row cannot tell the two apart, and no amount of inference from the
-- stored columns will make it able to.
--
-- `default false`: a constant default is metadata-only in PG11+, so this does
-- not rewrite a live prod-shared table. Every EXISTING row reads as
-- user-initiated — deliberately. Their origin genuinely is not recorded, and
-- showing a machine-written PRD is a smaller harm than hiding one somebody
-- wrote themselves.

alter table prds add column if not exists auto_generated boolean not null default false;

-- The listing predicate is (auto_generated, first_read_at): the unread
-- machine-written rows. Partial, because a row that has been read is never a
-- candidate again.
create index if not exists prds_unread_auto_generated_idx
    on prds (auto_generated)
    where first_read_at is null;
