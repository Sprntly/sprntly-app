-- Records WHY the group agent did or did not reply to a group turn
-- (mention / solo / continuation / gate / gate_stayout), persisted on the
-- human turn that triggered the decision. Nullable, no default: absent =
-- pre-existing rows and any turn recorded before this column existed.
-- Tenancy is inherited from conversations -> projects (mainline uuid-FK
-- convention, not the design-agent aud pattern), same as
-- 20260815160000_projects_instructions.sql.
alter table conversation_turns add column if not exists trigger_kind text;
