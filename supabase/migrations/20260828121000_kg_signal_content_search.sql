-- kg_signal content/entity search (Leg C) — Postgres functions.
--
-- Two read-only, tenant-scoped kNN/text-search primitives backing
-- `graph.retrieval`'s Leg C, mirroring the shape `kg_find_candidates`
-- (20260603120000_kg_foundation) already established for entities:
--
--   kg_signal_search_by_content  — keyword/full-text sub-leg, word-boundary
--                                  (Postgres tsvector/tsquery, not ILIKE — an
--                                  ILIKE '%AIG%' scan also matches
--                                  "campAIGn"; a tsquery lexeme match doesn't).
--                                  Backed by the GIN expression index in the
--                                  prior migration.
--   kg_find_signal_candidates    — semantic sub-leg, pgvector cosine kNN over
--                                  `kg_signal.embedding`, reusing the
--                                  ALREADY-PRESENT `kg_signal_embed_ivfflat`
--                                  index (20260603120000) — no new index
--                                  needed for this one.
--
-- Both filter `stale_after` server-side (cheap — `kg_signal_stale_idx`
-- already covers it) so a bounded top-k candidate pool isn't half-consumed by
-- rows the caller would just discard. Deliberately NOT filtering "retired"
-- (superseded_by/expired_at in `properties`) here: `app.graph.types.
-- signal_is_retired` is the single source of truth every content reader
-- already calls post-hydration (see that function's docstring on why it
-- can't drift into a second copy), so retrieval keeps doing that check in
-- Python exactly like it does for Legs A and B, rather than growing a
-- second, JSONB-truthiness reimplementation of the same rule in SQL.

-- The question, as an ANY-of-these-terms tsquery — mirrors
-- `document_catalog_or_tsquery` (20260803120000_document_catalog.sql)
-- exactly, one config swap (websearch_to_tsquery instead of
-- plainto_tsquery, per the ticket's word-boundary/full-text requirement;
-- websearch_to_tsquery additionally understands quoted phrases and
-- "-exclude" terms from a raw question, which plainto_tsquery does not).
--
-- Deliberately NOT left AND'd (websearch_to_tsquery's default): AND'ing a
-- whole question's terms is a GATE wearing the costume of a rank — a
-- five-word question would require a signal's content to contain every one
-- of those words, which is a documented false negative for anything but a
-- verbatim quote. Loosened to OR by rewriting the tsquery's `&` connectors to
-- `|` (its `::text` form is stable across Postgres tsquery versions — the
-- same trick `document_catalog_or_tsquery` already relies on), so this ranks
-- on ANY shared term via `ts_rank_cd` and lets the caller's own top-k cap +
-- global re-rank bound the result, exactly like the document leg does.
create or replace function kg_signal_or_tsquery (p_query text)
returns tsquery language sql immutable parallel safe as $$
    select case
        when p_query is null or btrim(p_query) = '' then null::tsquery
        else nullif(
            replace(websearch_to_tsquery('english', p_query)::text, '&', '|'), ''
        )::tsquery
    end;
$$;

create or replace function kg_signal_search_by_content (
    p_enterprise_id uuid,
    p_query         text,
    p_k             int default 30,
    p_now           timestamptz default now()
) returns table (
    id    uuid,
    score real
) language sql stable as $$
    select s.id,
           ts_rank_cd(
               to_tsvector('english', s.content), kg_signal_or_tsquery(p_query)
           )::real as score
      from kg_signal s
     where s.enterprise_id = p_enterprise_id
       and kg_signal_or_tsquery(p_query) is not null
       and to_tsvector('english', s.content) @@ kg_signal_or_tsquery(p_query)
       and (s.stale_after is null or s.stale_after > p_now)
     order by score desc, s.transaction_at desc
     limit p_k;
$$;

create or replace function kg_find_signal_candidates (
    p_enterprise_id uuid,
    p_embedding     vector(1536),
    p_k             int default 30,
    p_now           timestamptz default now()
) returns table (
    id    uuid,
    score real
) language sql stable as $$
    select s.id,
           (1 - (s.embedding <=> p_embedding))::real as score
      from kg_signal s
     where s.enterprise_id = p_enterprise_id
       and s.embedding is not null
       and (s.stale_after is null or s.stale_after > p_now)
     order by s.embedding <=> p_embedding
     limit p_k;
$$;
