-- Re-slug custom skills that collided with a RESTORED built-in method.
--
-- The vendored method library was deleted on 2026-08-03 (#1024) and is restored
-- by this change, now reachable only by an explicit `/<slug>`.
-- `app/skills/resolver.py` resolves BUILT-IN FIRST, so any custom skill holding
-- one of those slugs would become permanently unreachable the moment the
-- directory came back: the built-in would answer its trigger.
--
-- 20260730180000_custom_skills_no_builtin_override.sql already did this once,
-- against the then-live 78-skill list. It cannot have covered these rows: the
-- window that matters is 2026-08-03 -> now, when the directory held only nine
-- to eleven skills and `skills.custom.available_slug` -- which checks the LIVE
-- directory -- therefore handed out bare `sprint-planner`, `retrospective`,
-- `prioritize` and the rest with nothing to collide against. Uploads made in
-- that window are exactly the rows at risk, and nothing else has moved them.
--
-- Same resolution as the upload route and the earlier migration, so a customer
-- sees one consistent rule: the DISPLAY NAME they typed is never touched, only
-- the trigger, which becomes the first free `<slug>-N` skipping both vendored
-- ids and that company's other slugs. Their skill keeps working under the new
-- trigger instead of silently losing to a built-in.
--
-- Forward-only, idempotent and non-destructive: it renames, never deletes, and
-- is a no-op on any environment where nothing collides (re-running it finds no
-- row matching a bare built-in id, because the first pass renamed them).
--
-- The id list is a literal snapshot of the RESTORED directories, deliberately
-- not a live read: a migration is a historical record of the rows it fixed.
-- Every upload after it is disambiguated in the route against the live
-- directory.

do $$
declare
    -- The methods restored alongside this migration (69 skills).
    builtin_ids text[] := array[
        'analytics-instrumentation', 'assumption-risk-map',
        'beachhead-market', 'brief-nudge', 'business-context',
        'campaign-ideas', 'continuous-discovery', 'customer-comms',
        'decision-by-traffic-lights', 'decision-memo',
        'dependency-risk-track', 'exec-narrative', 'experiment-design',
        'experiment-readout', 'fact-check', 'feedback-synthesis',
        'funnel-activation', 'growth-loop', 'growth-vectors',
        'ideation-prioritize', 'incident-runbook', 'interview-guide',
        'interview-synthesis', 'jobs-to-be-done', 'journey-map',
        'launch-gtm', 'lean-canvas', 'legal-doc-draft', 'market-structure',
        'meeting-summary', 'metric-tree', 'naming-brainstorm',
        'negotiation-prep', 'okr-nct', 'opportunity-tree', 'persona-segment',
        'pm-resume-review', 'positioning', 'prd-critique', 'pre-mortem',
        'pricing-packaging', 'prioritize', 'problem-framing',
        'product-market-fit', 'product-one-pager', 'product-strategy-stack',
        'product-vision', 'proofread-polish', 'red-team-review',
        'release-notes', 'retention-churn', 'retrospective', 'roadmap',
        'saas-metrics-diagnosis', 'sales-battlecard', 'scope-slicing',
        'sprint-planner', 'sql-explore', 'stakeholder-map',
        'stakeholder-update', 'status-report', 'story-mapping',
        'strategy-frameworks', 'survey-design', 'synthetic-data',
        'tech-discovery-docs', 'tech-spec', 'test-scenario-builder',
        'working-backwards'
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
