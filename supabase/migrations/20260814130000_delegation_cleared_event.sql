-- Widen delegation_events.event to admit the terminal 'cleared' event.
-- SUPERSET add: every existing value is retained so the recreated CHECK can
-- never fail validation against rows already in the shared staging+prod DB.
-- The authoritative simplification (dropping accept/decline from the
-- emittable set) is app-layer (db/delegation_events.py). No workspace_id
-- column (same divergence as 20260813140100_delegation_events.sql: tenancy
-- flows delegation_id -> project_delegations -> projects).
alter table delegation_events drop constraint if exists delegation_events_event_check;
alter table delegation_events add constraint delegation_events_event_check
  check (event in (
    'assigned','accepted','in_progress','completed',
    'declined','cancelled','reopened','cleared'
  ));
