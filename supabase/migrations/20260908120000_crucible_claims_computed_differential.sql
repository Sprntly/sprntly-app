-- crucible_claims.claim_type gains the eighth type.
--
-- WHY THIS IS A CORRECTION AND NOT A FEATURE. `app.crucible.types.ClaimType`
-- gained `computed_differential` when the computed-comparison producer landed;
-- the CHECK constraint written in 20260819100000_crucible_core.sql still listed
-- seven. Nothing has broken in production because nothing writes this table yet
-- (`select count(*) from crucible_claims` is 0 fleet-wide), but the code can
-- already BUILD a claim the database would reject, and it would reject it deep
-- inside a run rather than at a boundary.
--
-- DROP-THEN-ADD, NOT `ADD ... NOT VALID`. The table is empty, so there are no
-- rows to validate and no lock worth avoiding; and re-running the file has to
-- be safe, which `drop constraint if exists` followed by a plain `add` is and
-- a bare `add` is not.
--
-- The constraint name is Postgres's own default for an inline column CHECK
-- (`<table>_<column>_check`), which is what 20260819100000 created; the
-- `if exists` covers a database where that file was applied under any other
-- name, and the `add` then pins the name explicitly so the next correction has
-- something stable to drop.

alter table crucible_claims
    drop constraint if exists crucible_claims_claim_type_check;

alter table crucible_claims
    add constraint crucible_claims_claim_type_check
    check (claim_type in (
        'magnitude', 'mechanism', 'preference', 'constraint',
        'direction', 'existence', 'attempt', 'computed_differential'
    ));
