-- Document catalog — one tenant-scoped row per document-shaped source item.
--
-- Chat can only find a document today when the question nearly names it
-- (filename-token overlap in ask_runner._select_documents). This table is the
-- layer that makes TOPIC-based retrieval possible: one row per document
-- carrying a short extractive summary, its topics, a summary embedding, and a
-- Postgres-maintained tsvector — so a question about a topic can rank
-- documents that share no filename tokens with it.
--
-- ADDITIVE ONLY. Nothing reads this table yet; registration writes it and the
-- selection change lands separately. Reverting the code that writes it leaves
-- the table in place with inert rows — a revert is not a data cleanup.
--
-- Shape follows document_source_file (20260723120000): denormalized
-- company_id NOT NULL so every read is tenant-filtered in ONE query, and a
-- workspace_id that is RECORDED but not filtered ("connectors are company-wide
-- by decision", 20260716124000_workspace_scope_columns.sql) — a later
-- per-workspace filter stays a read change, not another migration.
--
-- Three scopes, all pre-existing shapes:
--   company       the enforced boundary (uploads / Drive / Confluence).
--   workspace     recorded, not enforced (see above).
--   conversation  chat attachments — visible only inside their own
--                 conversation, and only after ownership is RE-VERIFIED by
--                 joining through `conversations` on company AND user. The
--                 row's conversation_id decides WHICH check to run; it never
--                 substitutes for the check. Filtering on conversation_id
--                 alone was a live IDOR fixed on the ask path 2026-08-03.

create extension if not exists vector;

-- The generated tsvector below needs an IMMUTABLE expression, and the obvious
-- spelling of "fold the topics array into the text" — array_to_string — is
-- marked STABLE (it goes through a type's output function for arbitrary
-- element types), so Postgres rejects it in a generated column. unnest +
-- string_agg over text[] are both genuinely immutable, so this helper states
-- the same intent truthfully rather than mislabelling a stable call.
create or replace function document_catalog_search_text (
    p_title       text,
    p_source_name text,
    p_summary     text,
    p_topics      text[]
) returns text language sql immutable parallel safe as $$
    select coalesce(p_title, '') || ' ' || coalesce(p_source_name, '') || ' '
        || coalesce(p_summary, '') || ' '
        || coalesce((select string_agg(t, ' ') from unnest(p_topics) as t), '');
$$;

create table if not exists document_catalog (
    id              uuid primary key default gen_random_uuid(),
    -- Denormalized tenant key: the same belt-and-braces company_id every other
    -- document table carries, so a read can never omit the tenant filter by
    -- forgetting a join.
    company_id      uuid not null references companies (id) on delete cascade,
    -- Recorded, NOT enforced — mirrors document_source.workspace_id exactly.
    workspace_id    uuid references workspaces (id) on delete cascade,
    -- Session scope: set => this row is visible ONLY inside that conversation,
    -- and only to the user who attached it.
    conversation_id bigint references conversations (id) on delete cascade,
    -- The attacher. Required whenever conversation_id is set (see the check
    -- below) so an ownerless session row is UNREPRESENTABLE, not merely
    -- avoided by convention.
    user_id         uuid,
    -- 'uploads' | 'google_drive' | 'confluence' | 'chat_attachment' | future.
    provider        text not null,
    -- Provider-side id: document_source_file.id | Drive file id | Confluence
    -- page id | 'turn:{turn_id}:attachment:{index}'.
    external_id     text not null,
    title           text not null,
    source_name     text not null default '',
    url             text,
    -- Modified/uploaded. Rendered beside the summary when this is read, so a
    -- reader can tell an outdated document from a current one.
    doc_date        timestamptz,
    -- sha256 of the document's extracted text — the invalidation key. An
    -- unchanged hash re-registers as a no-op (the kg_ingest_ledger discipline
    -- applied to summaries); a changed hash clears and regenerates the
    -- summary, topics and embedding.
    content_hash    text not null,
    summary         text not null default '',
    topics          text[] not null default array[]::text[],
    summary_model   text,
    summary_version text,
    -- Of title + summary + topics. NULL until generated — and deliberately
    -- NULL rather than a zero vector when embedding is unavailable, so a
    -- MISSING embedding is distinguishable from a MEANINGLESS one (a zero
    -- vector in cosine kNN is garbage that ranks arbitrarily).
    embedding       vector(1536),
    -- ONLY for providers with no home table for their body (confluence,
    -- google_drive). Uploads and chat attachments leave this NULL and resolve
    -- their bodies from document_source_file / conversation_turns — the body
    -- is never stored twice.
    body_text       text,
    -- The lexical channel, maintained by Postgres itself: no refresh job, no
    -- ingest hook, stemming and ts_rank_cd included.
    search_tsv      tsvector generated always as (
                        to_tsvector(
                            'english',
                            document_catalog_search_text(
                                title, source_name, summary, topics
                            )
                        )
                    ) stored,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    -- One row per document per tenant; the registration upsert's conflict key.
    unique (company_id, provider, external_id),
    -- A session-scoped row without an owner cannot exist.
    constraint document_catalog_session_needs_owner
        check (conversation_id is null or user_id is not null)
);

create index if not exists document_catalog_company_idx
    on document_catalog (company_id);
create index if not exists document_catalog_search_gin
    on document_catalog using gin (search_tsv);
-- ivfflat keeps writes cheap; lists=100 matches kg_entity/kg_signal and fits
-- the small per-tenant document volumes this is sized for.
create index if not exists document_catalog_embed_ivfflat
    on document_catalog using ivfflat (embedding vector_cosine_ops) with (lists = 100);

-- Backstop only, and stated as such: the backend reads this table with the
-- service-role client, which carries BYPASSRLS, so RLS is NOT the live tenancy
-- control here — the scope filter inside document_find_candidates and the
-- single accessor module are. Enabled anyway so a future PostgREST-facing
-- caller is denied by default rather than allowed by default. Same posture as
-- 20260723130000_llm_context_jobs.sql (RLS on, zero policies).
alter table document_catalog enable row level security;

-- The question, as an ANY-of-these-terms tsquery.
--
-- Deliberately NOT websearch_to_tsquery/plainto_tsquery, which AND their
-- terms: "how does enterprise billing work" would become
-- 'enterpris & bill & work' and match only documents containing ALL three.
-- That is a GATE wearing the costume of a rank — the same shape as the
-- filename-overlap ratio this whole change exists to replace, and it would
-- silently return nothing for most real questions.
--
-- The lexical leg here is one of two RANK channels feeding reciprocal rank
-- fusion, and the top-k cap is what bounds the result set. So it ranks on ANY
-- shared term and lets ts_rank_cd order them: a document sharing three of the
-- question's terms outranks one sharing a single term, and neither is
-- excluded by a rule nobody can see.
create or replace function document_catalog_or_tsquery (p_query text)
returns tsquery language sql immutable parallel safe as $$
    select case
        when p_query is null or btrim(p_query) = '' then null::tsquery
        else nullif(
            replace(plainto_tsquery('english', p_query)::text, '&', '|'), ''
        )::tsquery
    end;
$$;

-- ---------- Scoped hybrid candidate search ----------
-- The scope filter and the conversation-ownership join live INSIDE the
-- function body, mirroring kg_find_candidates (20260603120000_kg_foundation).
-- There is deliberately NO parameter that widens the tenant filter: a caller
-- cannot express the unscoped query through this entry point, so the
-- retrieve-then-filter shape (a post-filter that fails open leaks) is
-- unwritable rather than merely discouraged.
--
-- Two rank channels fused with reciprocal rank fusion at the published
-- defaults (rrf_k = 50, equal weights): lexical (ts_rank_cd over search_tsv)
-- and semantic (cosine kNN over the summary embedding). Rank-based fusion has
-- no score threshold by construction — these constants shape the fusion, they
-- are not a relevance gate to re-tune when a match is missed. Either channel
-- may be empty (no query text, or no embedding available) and the other still
-- ranks: degraded, not dead.
create or replace function document_find_candidates (
    p_company_id      uuid,
    p_conversation_id bigint,
    p_user_id         uuid,
    p_query           text,
    p_embedding       vector(1536),
    p_k               int default 10
) returns table (
    id              uuid,
    provider        text,
    external_id     text,
    title           text,
    source_name     text,
    summary         text,
    topics          text[],
    url             text,
    doc_date        timestamptz,
    conversation_id bigint,
    score           real
) language sql stable as $$
    with visible as (
        select d.id, d.provider, d.external_id, d.title, d.source_name,
               d.summary, d.topics, d.url, d.doc_date, d.conversation_id,
               d.embedding, d.search_tsv, d.updated_at
          from document_catalog d
         where d.company_id = p_company_id
           and (
                -- Company/workspace-scoped rows: visible to the tenant.
                d.conversation_id is null
                -- Session-scoped rows: only inside THEIR conversation, and
                -- only when that conversation really belongs to this company
                -- AND this user. Never trusted from the row itself.
                or (
                    p_conversation_id is not null
                    and p_user_id is not null
                    and d.conversation_id = p_conversation_id
                    and exists (
                        select 1
                          from conversations c
                         where c.id = d.conversation_id
                           and c.company_id = p_company_id
                           and c.user_id = p_user_id
                    )
                )
           )
    ),
    lexical as (
        select v.id,
               row_number() over (
                   order by ts_rank_cd(
                       v.search_tsv, document_catalog_or_tsquery(p_query)
                   ) desc, v.updated_at desc
               ) as rank
          from visible v
         where document_catalog_or_tsquery(p_query) is not null
           and v.search_tsv @@ document_catalog_or_tsquery(p_query)
         order by ts_rank_cd(
             v.search_tsv, document_catalog_or_tsquery(p_query)
         ) desc, v.updated_at desc
         limit greatest(p_k * 4, 20)
    ),
    semantic as (
        select v.id,
               row_number() over (order by v.embedding <=> p_embedding) as rank
          from visible v
         where p_embedding is not null
           and v.embedding is not null
         order by v.embedding <=> p_embedding
         limit greatest(p_k * 4, 20)
    )
    select f.id, f.provider, f.external_id, f.title, f.source_name, f.summary,
           f.topics, f.url, f.doc_date, f.conversation_id, f.score
      from (
            select v.id, v.provider, v.external_id, v.title, v.source_name,
                   v.summary, v.topics, v.url, v.doc_date, v.conversation_id,
                   v.updated_at,
                   (coalesce(1.0 / (50 + l.rank), 0.0)
                    + coalesce(1.0 / (50 + s.rank), 0.0))::real as score
              from visible v
              left join lexical l on l.id = v.id
              left join semantic s on s.id = v.id
             where l.id is not null or s.id is not null
           ) f
     order by f.score desc, f.updated_at desc
     limit p_k;
$$;
