-- Editable child issues (subtasks) on tickets.
--
-- Generated tickets carry subtasks inside prd_tickets.stories (the immutable
-- generated base); making them editable in the ticket panel needs an override
-- channel, so ticket_edits gains a nullable jsonb column following the same
-- override semantics as the other fields: NULL = no override (UI shows the
-- generated subtasks), a json array (incl. []) = an explicit replacement.

alter table ticket_edits
    add column if not exists subtasks jsonb;
