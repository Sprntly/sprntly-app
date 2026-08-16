-- custom_artifacts.error_code — WHY a document could not be written, in a form
-- the product can show the person who asked for it.
--
-- `error` already exists and holds `str(exc)` — the operator's detail. It is
-- deliberately never returned by the API, because it is raw exception text: a
-- transport error carries URLs, a provider error carries whatever the provider
-- put in its message, and neither is something to render into a shared team
-- library. So the API returned nothing at all about a failure, and the product
-- could only ever say "this document could not be written", identically, for a
-- generation that came back empty, one the model refused, and one a deploy
-- restarted mid-write. A user cannot tell "ask again and it will work" from
-- "asking again will fail the same way", and nobody outside the database could
-- tell them.
--
-- This column is the half that is safe to return: a short, stable, closed-set
-- code this codebase writes itself and the web maps to its own copy. It is a
-- CODE, not a message — the wording belongs to the surface showing it, which
-- is why the web owns the sentence and this column owns the meaning.
--
-- Nullable with no default and no backfill, on purpose. A NULL means "we do
-- not know why", which is exactly true of every row that failed before this
-- column existed, and inventing a code for them would be a guess rendered as
-- a fact. The web already has copy for the unknown case.
alter table if exists public.custom_artifacts
  add column if not exists error_code text;

comment on column public.custom_artifacts.error_code is
  'Stable machine code for a failed generation (empty | llm_error | too_large '
  '| interrupted). NULL when unknown. Safe to return; `error` is not.';
