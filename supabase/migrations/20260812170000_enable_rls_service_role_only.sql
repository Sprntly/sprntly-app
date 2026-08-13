-- Lock every server-side table to the service role, closing anon API access.
--
-- Why this migration exists: Supabase's security advisor (email of 2026-08-09,
-- lint 0013 `rls_disabled_in_public`) flagged tables in the public schema with
-- RLS disabled. Any such table is readable AND writable by anyone holding the
-- project URL + anon key over PostgREST — and our anon key is genuinely public,
-- inlined into the static web bundle by web/app/lib/supabase/client.ts. The
-- backend talks to Supabase with the service-role key (which bypasses RLS), so
-- RLS was never load-bearing for the app itself; it is, however, the ONLY
-- barrier between the anon key and these tables. Auditing the migration set
-- found two distinct defect classes:
--
--   Class A — 19 tables created without ever enabling RLS (the ones the
--   advisor flags). Fully exposed.
--
--   Class B — 25 policies written as
--       create policy "srv_x" on x for all using (true) with check (true);
--   with no TO clause. Per the Postgres CREATE POLICY docs, omitting TO
--   defaults the policy to PUBLIC — every role, INCLUDING anon and
--   authenticated. Combined with Supabase's default grants to those roles on
--   the public schema, these tables pass the advisor's lint (RLS is enabled)
--   while being effectively as open as Class A. The comments beside those
--   policies ("server-side only", "backend uses service-role key") show the
--   intent was always service-role-only access; the missing TO clause silently
--   broadened them to everyone.
--
-- The fix, for both classes: RLS enabled, and exactly one policy per table —
-- `for all to service_role using (true) with check (true)`. service_role has
-- BYPASSRLS, so the policy is belt-and-braces rather than load-bearing; the
-- point is that anon/authenticated match NO policy and are denied outright
-- (the intended model, spelled out in 20260525120000_briefs.sql: "No policies
-- -> only service_role can read or write").
--
-- Deliberately NOT done here:
--   * No FORCE ROW LEVEL SECURITY — the postgres owner role must keep
--     bypassing RLS for direct psql/dashboard administration.
--   * No change to the browser-facing tables (companies, company_members,
--     products, profiles, workspaces, workspace_members, workspace_invites) —
--     the web client queries those directly with the user's JWT and they
--     already carry member-scoped policies.
--   * No change to auth.uid()-scoped policies (connections_member_select,
--     design_systems_member_all): anon has a NULL auth.uid(), matches no rows,
--     so they are not part of the hole. Same for the intentional insert-only
--     "anon insert" policy on interest_signups (the public interest form).
--
-- Idempotency: ALTER TABLE ... ENABLE ROW LEVEL SECURITY is a no-op when
-- already enabled, and Postgres has no CREATE POLICY IF NOT EXISTS, hence the
-- drop-then-create pairs. Class B policies are recreated under their original
-- names so a re-run converges on the same state.

-- ---------------------------------------------------------------------------
-- Class A: tables that never had RLS enabled. (backlog_items was renamed to
-- ideation_items by 20260715000000_ideation_rename_shortlist.sql; it is
-- targeted under its current name.)
-- ---------------------------------------------------------------------------

alter table agent_decision_log enable row level security;
drop policy if exists "srv_agent_decision_log" on agent_decision_log;
create policy "srv_agent_decision_log" on agent_decision_log
  for all to service_role using (true) with check (true);

alter table artifact_share_joins enable row level security;
drop policy if exists "srv_artifact_share_joins" on artifact_share_joins;
create policy "srv_artifact_share_joins" on artifact_share_joins
  for all to service_role using (true) with check (true);

alter table artifact_shares enable row level security;
drop policy if exists "srv_artifact_shares" on artifact_shares;
create policy "srv_artifact_shares" on artifact_shares
  for all to service_role using (true) with check (true);

alter table brief_finding_state enable row level security;
drop policy if exists "srv_brief_finding_state" on brief_finding_state;
create policy "srv_brief_finding_state" on brief_finding_state
  for all to service_role using (true) with check (true);

alter table brief_nudge_sends enable row level security;
drop policy if exists "srv_brief_nudge_sends" on brief_nudge_sends;
create policy "srv_brief_nudge_sends" on brief_nudge_sends
  for all to service_role using (true) with check (true);

alter table brief_opens enable row level security;
drop policy if exists "srv_brief_opens" on brief_opens;
create policy "srv_brief_opens" on brief_opens
  for all to service_role using (true) with check (true);

alter table document_source enable row level security;
drop policy if exists "srv_document_source" on document_source;
create policy "srv_document_source" on document_source
  for all to service_role using (true) with check (true);

alter table document_source_file enable row level security;
drop policy if exists "srv_document_source_file" on document_source_file;
create policy "srv_document_source_file" on document_source_file
  for all to service_role using (true) with check (true);

alter table enterprise_config enable row level security;
drop policy if exists "srv_enterprise_config" on enterprise_config;
create policy "srv_enterprise_config" on enterprise_config
  for all to service_role using (true) with check (true);

alter table ideation_items enable row level security;
drop policy if exists "srv_ideation_items" on ideation_items;
create policy "srv_ideation_items" on ideation_items
  for all to service_role using (true) with check (true);

alter table kg_entity enable row level security;
drop policy if exists "srv_kg_entity" on kg_entity;
create policy "srv_kg_entity" on kg_entity
  for all to service_role using (true) with check (true);

alter table kg_ingest_ledger enable row level security;
drop policy if exists "srv_kg_ingest_ledger" on kg_ingest_ledger;
create policy "srv_kg_ingest_ledger" on kg_ingest_ledger
  for all to service_role using (true) with check (true);

alter table kg_relationship enable row level security;
drop policy if exists "srv_kg_relationship" on kg_relationship;
create policy "srv_kg_relationship" on kg_relationship
  for all to service_role using (true) with check (true);

alter table kg_signal enable row level security;
drop policy if exists "srv_kg_signal" on kg_signal;
create policy "srv_kg_signal" on kg_signal
  for all to service_role using (true) with check (true);

alter table kg_source enable row level security;
drop policy if exists "srv_kg_source" on kg_source;
create policy "srv_kg_source" on kg_source
  for all to service_role using (true) with check (true);

alter table knowledge_entities enable row level security;
drop policy if exists "srv_knowledge_entities" on knowledge_entities;
create policy "srv_knowledge_entities" on knowledge_entities
  for all to service_role using (true) with check (true);

alter table knowledge_relationships enable row level security;
drop policy if exists "srv_knowledge_relationships" on knowledge_relationships;
create policy "srv_knowledge_relationships" on knowledge_relationships
  for all to service_role using (true) with check (true);

alter table metric_points enable row level security;
drop policy if exists "srv_metric_points" on metric_points;
create policy "srv_metric_points" on metric_points
  for all to service_role using (true) with check (true);

alter table pipeline_runs enable row level security;
drop policy if exists "srv_pipeline_runs" on pipeline_runs;
create policy "srv_pipeline_runs" on pipeline_runs
  for all to service_role using (true) with check (true);

-- ---------------------------------------------------------------------------
-- Class B: RLS already enabled, but the existing permissive policy bound to
-- PUBLIC (no TO clause). Each is recreated under its original name, now
-- restricted to service_role. RLS is NOT re-enabled here — it already is.
-- ---------------------------------------------------------------------------

drop policy if exists "Service role full access" on prd_versions;
create policy "Service role full access" on prd_versions
  for all to service_role using (true) with check (true);

drop policy if exists "srv_ticket_edits" on ticket_edits;
create policy "srv_ticket_edits" on ticket_edits
  for all to service_role using (true) with check (true);

drop policy if exists "srv_ticket_attachments" on ticket_attachments;
create policy "srv_ticket_attachments" on ticket_attachments
  for all to service_role using (true) with check (true);

drop policy if exists "srv_ticket_comments" on ticket_comments;
create policy "srv_ticket_comments" on ticket_comments
  for all to service_role using (true) with check (true);

drop policy if exists "srv_conversations" on conversations;
create policy "srv_conversations" on conversations
  for all to service_role using (true) with check (true);

drop policy if exists "srv_conv_turns" on conversation_turns;
create policy "srv_conv_turns" on conversation_turns
  for all to service_role using (true) with check (true);

drop policy if exists "service_role_full_access" on multi_agent_docs;
create policy "service_role_full_access" on multi_agent_docs
  for all to service_role using (true) with check (true);

drop policy if exists "srv_design_agent_map_cache" on design_agent_map_cache;
create policy "srv_design_agent_map_cache" on design_agent_map_cache
  for all to service_role using (true) with check (true);

drop policy if exists "srv_design_agent_jobs" on design_agent_jobs;
create policy "srv_design_agent_jobs" on design_agent_jobs
  for all to service_role using (true) with check (true);

drop policy if exists "srv_design_agent_worker_heartbeat" on design_agent_worker_heartbeat;
create policy "srv_design_agent_worker_heartbeat" on design_agent_worker_heartbeat
  for all to service_role using (true) with check (true);

drop policy if exists "srv_roadmap_doc" on roadmap_doc;
create policy "srv_roadmap_doc" on roadmap_doc
  for all to service_role using (true) with check (true);

drop policy if exists "srv_company_template" on company_template;
create policy "srv_company_template" on company_template
  for all to service_role using (true) with check (true);

drop policy if exists "srv_company_document" on company_document;
create policy "srv_company_document" on company_document
  for all to service_role using (true) with check (true);

drop policy if exists "srv_prd_tickets" on prd_tickets;
create policy "srv_prd_tickets" on prd_tickets
  for all to service_role using (true) with check (true);

drop policy if exists "srv_clickup_task_map" on clickup_task_map;
create policy "srv_clickup_task_map" on clickup_task_map
  for all to service_role using (true) with check (true);

drop policy if exists "srv_jira_issue_map" on jira_issue_map;
create policy "srv_jira_issue_map" on jira_issue_map
  for all to service_role using (true) with check (true);

drop policy if exists "srv_prd_ticket_sync" on prd_ticket_sync;
create policy "srv_prd_ticket_sync" on prd_ticket_sync
  for all to service_role using (true) with check (true);

drop policy if exists "srv_tracker_meta" on tracker_meta;
create policy "srv_tracker_meta" on tracker_meta
  for all to service_role using (true) with check (true);

drop policy if exists "srv_asana_task_map" on asana_task_map;
create policy "srv_asana_task_map" on asana_task_map
  for all to service_role using (true) with check (true);

drop policy if exists "srv_custom_skills" on custom_skills;
create policy "srv_custom_skills" on custom_skills
  for all to service_role using (true) with check (true);

drop policy if exists "srv_reports" on reports;
create policy "srv_reports" on reports
  for all to service_role using (true) with check (true);

drop policy if exists "srv_artifact_templates" on artifact_templates;
create policy "srv_artifact_templates" on artifact_templates
  for all to service_role using (true) with check (true);

drop policy if exists "srv_ticket_sets" on ticket_sets;
create policy "srv_ticket_sets" on ticket_sets
  for all to service_role using (true) with check (true);

drop policy if exists "srv_skill_sources" on skill_sources;
create policy "srv_skill_sources" on skill_sources
  for all to service_role using (true) with check (true);

drop policy if exists "srv_call_transcripts" on call_transcripts;
create policy "srv_call_transcripts" on call_transcripts
  for all to service_role using (true) with check (true);
