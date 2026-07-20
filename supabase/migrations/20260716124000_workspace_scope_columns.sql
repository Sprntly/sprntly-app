-- Workspace scope for the per-workspace product-data tables.
--
-- Adds a nullable workspace_id (FK -> workspaces, cascade) to every table
-- whose rows should be visible only inside one workspace, backfilled to the
-- company's default workspace. Columns start NULLABLE so this migration can
-- land before the code that writes them; a follow-up migration enforces NOT
-- NULL once post-deploy writes are verified. company_id stays and keeps
-- being written (belt and braces + scheduler paths that iterate companies).
--
-- Deliberately NOT touched:
--   * connections.workspace_id — connectors are company-wide by decision;
--     the 20260606120000 column stays as-is.
--   * prototypes/prototype_*/prd_patches/design_agent_* — their column named
--     workspace_id is the session-audience tag ("app"/"demo"), unrelated to
--     the workspaces table.
--   * the enterprise_id world (kg_*, metric_points, backlog_items) — parked
--     company-wide for this slice.

do $$
declare
    t text;
begin
    foreach t in array array[
        'prd_tickets',
        'prd_ticket_sync',
        'ticket_edits',
        'ticket_attachments',
        'ticket_comments',
        'clickup_task_map',
        'jira_issue_map',
        'asana_task_map',
        'tracker_meta',
        'conversations',
        'company_document',
        'roadmap_doc',
        'brief_nudge_sends',
        'brief_opens'
    ]
    loop
        execute format(
            'alter table %I add column if not exists workspace_id uuid '
            'references workspaces (id) on delete cascade', t);
        execute format(
            'update %I set workspace_id = ('
            '  select w.id from workspaces w'
            '  where w.company_id = %I.company_id and w.is_default limit 1)'
            ' where workspace_id is null', t, t);
        execute format(
            'create index if not exists %I on %I (workspace_id)',
            t || '_workspace_id_idx', t);
    end loop;
end;
$$;

-- roadmap_doc: one roadmap per WORKSPACE now, not per company.
alter table roadmap_doc drop constraint if exists roadmap_doc_company_id_key;
create unique index if not exists roadmap_doc_workspace_id_key
    on roadmap_doc (workspace_id);
