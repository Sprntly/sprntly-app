-- Make the catalog's lexical channel able to see filenames.
--
-- `document_catalog.search_tsv` folded the title in RAW, and Postgres's
-- default parser classifies `Sprntly_vs_Productboard_Comparison.docx` as a
-- single `host` token — the trailing extension makes it look like a hostname.
-- Confirmed against this exact schema:
--
--   select alias, token, lexemes
--     from ts_debug('english', 'Sprntly_vs_Productboard_Comparison.docx');
--    alias |                  token                  |          lexemes
--   -------+-----------------------------------------+---------------------------
--    host  | Sprntly_vs_Productboard_Comparison.docx | {sprntly_vs_..._comparison.docx}
--
-- One lexeme, and one no real question ever contains. So the title
-- contributed NOTHING to the lexical rank and the channel rested entirely on
-- `summary` and `topics`. A row whose summariser has not run yet — or whose
-- summariser failed — carries `summary = ''` and `topics = '{}'`, and was
-- therefore invisible to the lexical channel permanently, returning zero rows
-- with no error: indistinguishable from a healthy empty result.
--
-- Fixed by folding a NORMALISED title into the search text instead: extension
-- stripped, `_ - .` collapsed to spaces. The same normalisation
-- `ask_runner._normalize` already applies to filenames when it decides
-- whether a question NAMES a document, so the two selection stages now agree
-- on what a filename's words are.
--
-- Trade-off, stated: the raw title is no longer in the search text, so a
-- question that spells the filename VERBATIM (extension included) no longer
-- matches lexically. That costs nothing — a verbatim filename is matched by
-- `ask_runner._select_documents`, which normalises both sides and substring-
-- matches, and which loads BEFORE any ranking. Normalising strictly widens
-- what the lexical channel can reach: "the productboard comparison doc" now
-- matches, and it never did before.
--
-- ── Why this is a NEW file rather than an edit to 20260803120000 ───────────
-- That migration is already tracked in `supabase_migrations.schema_migrations`
-- and applied in production. The runner skips an already-tracked version by
-- name, so editing it in place would have changed nothing anywhere it
-- mattered — including production — while looking like a fix in the diff.
--
-- And `create or replace function` alone is NOT enough here: replacing the
-- function a STORED generated column was built from does not recompute the
-- stored values, and Postgres does not re-evaluate them until the row is
-- written again. The column has to be rebuilt, which is what the drop/add
-- below does.
--
-- Cost of the rebuild: `document_catalog` was created hours ago by
-- 20260803120000 and holds single-digit rows (nine observed in the workspace
-- that reported this). ADD COLUMN ... GENERATED ALWAYS AS ... STORED takes
-- ACCESS EXCLUSIVE and rewrites the table; at this size the rewrite is
-- sub-millisecond and the lock is not meaningfully held. There is no data
-- loss: every column the rebuild touches is derived from columns that stay.

-- Extension off the end, then `_ - .` to spaces, then whitespace collapsed.
-- Mirrors `ask_runner._normalize` exactly (which additionally lowercases —
-- unnecessary here, `to_tsvector` folds case itself). Immutable: three
-- regexp_replace calls and a btrim, nothing locale- or setting-dependent.
create or replace function document_catalog_normalize_title (p_title text)
returns text language sql immutable parallel safe as $$
    select btrim(
        regexp_replace(
            regexp_replace(
                regexp_replace(coalesce(p_title, ''), '\.[A-Za-z0-9]{1,6}$', ''),
                '[_.\-]+', ' ', 'g'
            ),
            '\s+', ' ', 'g'
        )
    );
$$;

-- Same signature as 20260803120000's version, so the generated column below
-- can keep referring to it by name. Only the title arm changes.
create or replace function document_catalog_search_text (
    p_title       text,
    p_source_name text,
    p_summary     text,
    p_topics      text[]
) returns text language sql immutable parallel safe as $$
    select document_catalog_normalize_title(p_title) || ' '
        || coalesce(p_source_name, '') || ' '
        || coalesce(p_summary, '') || ' '
        || coalesce((select string_agg(t, ' ') from unnest(p_topics) as t), '');
$$;

-- Rebuild the stored column so existing rows are re-tokenised. Dropping it
-- also drops document_catalog_search_gin (an index cannot outlive its
-- column), so that is recreated immediately after.
alter table document_catalog drop column if exists search_tsv;

alter table document_catalog add column search_tsv tsvector
    generated always as (
        to_tsvector(
            'english',
            document_catalog_search_text(title, source_name, summary, topics)
        )
    ) stored;

create index if not exists document_catalog_search_gin
    on document_catalog using gin (search_tsv);

-- ── Ties in the fusion must not be settled by recency ──────────────────────
--
-- Symmetric RRF over two channels ties EXACTLY whenever two documents swap
-- ranks between the channels: 1/(50+1) + 1/(50+2) is the same number as
-- 1/(50+2) + 1/(50+1). That is not a rare coincidence, it is the arithmetic,
-- and it lands on precisely the pair a topical question is trying to choose
-- between. Measured against this schema with a real question embedding, for
-- "give me a summary of the product board vs sprntly discussion":
--
--   Sprntly_vs_Productboard_Comparison.docx   lexical #2   semantic #1
--   Sprntly_vs_OpenAI_PRD.docx                lexical #1   semantic #2
--   -> both fuse to 0.038839, an exact tie
--
-- The previous tie-break was `updated_at desc`, so the NEWER document won —
-- and the newer document was the wrong one. Recency is not a relevance
-- signal, and Stage T is a relevance stage; letting it settle the top of a
-- topical ranking is how a query-independent "newest document wins" result
-- comes to be labelled a topic match.
--
-- So an exact fusion tie is now broken by the SEMANTIC rank first, then the
-- lexical rank, and only then by recency. Semantic leads because it is the
-- channel that models what a document is ABOUT, which is the question Stage T
-- asks; the lexical channel is a keyword proxy that cannot see that "product
-- board" and "Productboard" are the same subject, and it demonstrably
-- mis-ranks exactly that case.
--
-- This changes nothing when only one channel ran: with p_embedding null the
-- score is 1/(50+l.rank), strictly monotonic in a rank that row_number() makes
-- unique, so there are no ties to break and the ordering is byte-identical to
-- before. The new rule can only bite where both channels ranked a document —
-- which is the case it exists for.
--
-- Everything else about the function is carried over unchanged from
-- 20260803120000, including the `l.id is not null or s.id is not null`
-- guard: a document no channel ranked is still not a candidate, so an empty
-- fusion returns zero rows rather than degrading into a recency listing.
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
                d.conversation_id is null
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
                   v.updated_at, s.rank as sem_rank, l.rank as lex_rank,
                   (coalesce(1.0 / (50 + l.rank), 0.0)
                    + coalesce(1.0 / (50 + s.rank), 0.0))::real as score
              from visible v
              left join lexical l on l.id = v.id
              left join semantic s on s.id = v.id
             where l.id is not null or s.id is not null
           ) f
     order by f.score desc,
              f.sem_rank asc nulls last,
              f.lex_rank asc nulls last,
              f.updated_at desc
     limit p_k;
$$;
