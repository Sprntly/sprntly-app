-- Custom skills no longer override a same-named built-in (PRD 1854 revision,
-- product decision 2026-07-30).
--
-- The original rule let an upload whose slug matched a vendored skill id
-- REPLACE that built-in for the whole company: the resolver looked the company
-- library up first, so /prd-author stopped reaching the Sprntly skill. That is
-- reversed. Both skills now coexist and both stay invocable — chat lists them
-- side by side and their descriptions are what tells them apart — which means
-- their triggers have to differ. app/skills/resolver.py resolves BUILT-IN
-- FIRST, and the upload route hands a name-colliding upload the next free
-- trigger in the `<slug>`, `<slug>-2`, `<slug>-3` series
-- (skills.custom.available_slug). The display name the user typed is never
-- changed; only the trigger is.
--
-- (This supersedes the note in 20260728180000_custom_skills.sql that said the
-- upload route rejects slugs shadowing a built-in — it never shipped that way,
-- and now it renumbers the trigger instead.)
--
-- Rows uploaded UNDER THE OLD RULE can still hold a slug equal to a vendored
-- id. Built-in-first resolution would leave those permanently unreachable — the
-- built-in answers their trigger — so re-slug them here, exactly the way the
-- upload route now would: first free `<slug>-N`, skipping both vendored ids and
-- the company's other custom slugs. No-op on any environment where nothing
-- collides.
--
-- The vendored id list is a literal snapshot of backend/skills/*/ as of this
-- migration. A migration is a historical record, so it should NOT track later
-- additions to that directory: it fixes the rows that exist now, and every
-- upload after it is disambiguated in the route against the live directory.

do $$
declare
    -- backend/skills/<id>/SKILL.md as of 2026-07-30 (78 skills).
    builtin_ids text[] := array[
        'analytics-instrumentation', 'assumption-risk-map', 'beachhead-market',
        'brief-nudge', 'business-context', 'campaign-ideas', 'company-research',
        'competitive-intelligence-review', 'continuous-discovery', 'customer-comms',
        'decision-by-traffic-lights', 'decision-memo', 'dependency-risk-track',
        'evidence-brief', 'exec-narrative', 'experiment-design', 'experiment-readout',
        'fact-check', 'feedback-synthesis', 'funnel-activation', 'growth-loop',
        'growth-vectors', 'ideation-prioritize', 'implementation-spec',
        'incident-runbook', 'interview-guide', 'interview-synthesis', 'jobs-to-be-done',
        'journey-map', 'launch-gtm', 'lean-canvas', 'legal-doc-draft',
        'market-structure', 'meeting-summary', 'metric-tree', 'naming-brainstorm',
        'negotiation-prep', 'okr-nct', 'opportunity-tree', 'persona-segment',
        'pm-resume-review', 'positioning', 'prd-author', 'prd-critique', 'pre-mortem',
        'pricing-packaging', 'prioritize', 'problem-framing', 'product-market-fit',
        'product-one-pager', 'product-strategy-stack', 'product-vision',
        'proofread-polish', 'public-feedback-report', 'red-team-review',
        'release-notes', 'retention-churn', 'retrospective', 'roadmap',
        'saas-metrics-diagnosis', 'sales-battlecard', 'scope-slicing', 'sprint-planner',
        'sql-explore', 'stakeholder-map', 'stakeholder-update', 'status-report',
        'story-mapping', 'strategy-frameworks', 'survey-design', 'synthetic-data',
        'tech-discovery-docs', 'tech-spec', 'test-scenario-builder', 'top-insights',
        'user-stories', 'voice-of-customer-report', 'working-backwards'
    ];
    r          record;
    candidate  text;
    n          int;
begin
    for r in
        select id, company_id, slug
        from custom_skills
        where slug = any (builtin_ids)
        order by created_at   -- oldest keeps the lowest suffix, like upload order
    loop
        n := 2;
        loop
            candidate := r.slug || '-' || n;
            exit when not (candidate = any (builtin_ids))
                  and not exists (
                      select 1 from custom_skills
                      where company_id = r.company_id and slug = candidate
                  );
            n := n + 1;
        end loop;
        update custom_skills set slug = candidate where id = r.id;
        raise notice 'custom_skills: re-slugged % -> % (company %)',
            r.slug, candidate, r.company_id;
    end loop;
end $$;
