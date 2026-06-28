-- Bugfix: a fields-only edit (priority/status/sprint/assignee) creates a
-- ticket_edits row whose description / acceptance_criteria fall back to their
-- NOT NULL DEFAULTs ('' / '[]'). get_ticket_data then returns those empties as
-- if they were a real override, and the detail view replaces the generated
-- ticket body with nothing.
--
-- Make both nullable so "never set" is NULL (→ the UI keeps the generated
-- story), distinct from an intentionally-saved empty "" the user typed.
alter table ticket_edits alter column description drop default;
alter table ticket_edits alter column description drop not null;
alter table ticket_edits alter column acceptance_criteria drop default;
alter table ticket_edits alter column acceptance_criteria drop not null;

-- Backfill the rows already created by a fields-only edit before this fix:
-- only those that carry field values AND an empty description/criteria, so a
-- genuinely-saved empty description (paired with no field edits) is untouched.
update ticket_edits
   set description = null
 where description = ''
   and (priority is not null or status is not null
        or sprint is not null or assignee is not null);

update ticket_edits
   set acceptance_criteria = null
 where acceptance_criteria = '[]'::jsonb
   and (priority is not null or status is not null
        or sprint is not null or assignee is not null);
