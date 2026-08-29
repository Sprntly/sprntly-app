-- One permanent referral link per company.
--
-- Referrals were an EMAIL INVITE: you typed a friend's address, we minted a
-- code for that one person, and the row existed before anybody had done
-- anything. That put a form between someone and sharing a link, capped how many
-- people they could tell, and meant a code was useless to anyone but the
-- address it was cut for.
--
-- A company now has ONE code, forever. Share the URL with anyone; whoever signs
-- up through it is attributed. `referrals` rows are created on the way IN — one
-- per person who actually arrived — rather than speculatively on the way out.
--
-- Nullable and filled lazily on first read, so this migration touches no
-- existing row: 21 companies do not need codes minted for links nobody has
-- asked for yet.

alter table companies
  add column if not exists referral_code text;

-- UNIQUE because it is looked up BY code at signup, and two companies sharing
-- one would silently attribute a signup to whichever row came back first.
-- Partial, so the many nulls do not collide with each other.
create unique index if not exists companies_referral_code_uidx
  on companies (referral_code)
  where referral_code is not null;

-- `invitee_email` becomes optional, because nobody types one any more.
--
-- The column was NOT NULL when a referral was CREATED from an address you had
-- typed. A referral row is now created when somebody ARRIVES through a link,
-- and at that moment we know their company, not their address. Existing rows
-- keep the addresses they were created with; only the constraint is loosened,
-- so nothing is dropped and nothing is rewritten.
alter table referrals
  alter column invitee_email drop not null;

-- Two indexes built for the email model have to go, and both would otherwise
-- reject legitimate rows rather than merely being unused.
--
-- `referrals.code` was UNIQUE because each invite carried its own code. Every
-- row now carries the REFERRER'S single permanent code, so the second person
-- to use a link would be rejected — which is the entire feature.
alter table referrals drop constraint if exists referrals_code_key;
drop index if exists referrals_code_key;

-- `(referrer_company_id, lower(invitee_email))` was "one live invite per
-- address". With no address collected, every new row has a null email, and in
-- Postgres a unique index treats nulls as distinct — so this one does not
-- actually reject anything today. Dropped anyway: an index nobody can satisfy
-- a lookup against is a trap for whoever reads the schema next.
drop index if exists referrals_referrer_email_uidx;
