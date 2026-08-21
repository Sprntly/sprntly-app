#!/usr/bin/env python
"""Delete throwaway test companies — DRY RUN BY DEFAULT.

The dry run is not an estimate. It performs the real DELETEs inside a
transaction, reports the exact per-table row counts, and then ROLLS BACK. So
the numbers you approve are the numbers that will be removed, not a guess from
a separate counting query that could drift from the delete itself.

WHAT CASCADES AND WHAT DOES NOT
-------------------------------
50 tables have `ON DELETE CASCADE` FKs to `companies`, so `DELETE FROM
companies` clears them (including `workspaces`, which in turn cascades the
workspace-keyed prototype/design-agent tables). Two more are `SET NULL`.

17 tables carry `company_id`/`enterprise_id` with NO foreign key at all. They
are invisible to the cascade and would be left as orphans pointing at a
company id that no longer exists. They are deleted explicitly here, first. An
earlier cleanup note recorded two of these (`clickup_task_map`, `tracker_meta`);
the schema says there are seventeen.

7 more tables are keyed by the company SLUG rather than its id, so they are
also invisible to the cascade and are deleted by slug.

`llm_usage_events` is deliberately NOT deleted. It is the billing ledger, and
losing a deleted tenant's history would silently change historical cost totals.
Pass --purge-usage if you really want it gone.

SAFETY
------
THIS SCRIPT HAS NO "FIND IDLE COMPANIES AND DELETE THEM" MODE, AND MUST NOT
GROW ONE. Targets are named explicitly, one id per line, by a human who has
looked at them. Idleness is a reason to stop *spending* on a tenant — that is
what the scheduled-work gate is for — never a reason to delete one.

The distinction is not theoretical. The 7-day-idle query that produced the
first candidate list also matched a real customer: three members on their own
corporate domain, 12,000 KG signals, four live connectors, quiet for eight days
because it was the weekend. An automated sweep would have destroyed them.

Given an explicit list, every target is still re-checked against live evidence
(see `assess`) and refused if any of these hold:

  * a member signed in within --idle-days (default 7)
  * a member on a real address — not a known-throwaway domain, not a
    plus-alias (`foo+tag@gmail.com`)
  * more than one member (a shared org is somebody else's too)
  * a connected integration, but ONLY when the identity is not already
    known-throwaway. Test accounts connect Slack and Zoom constantly; that is
    what they are for. Treating a connector as proof of life on its own
    refused every legitimate target on the first run.

--i-know-what-im-doing overrides the refusals. Scope it: run the overridden
company in its OWN invocation with its own one-line ids file, so the override
cannot silently apply to the rest of a batch.

USAGE
    python delete_test_companies.py --ids-file ids.txt              # dry run
    python delete_test_companies.py --ids-file ids.txt --execute    # for real
"""
from __future__ import annotations

import argparse
import os
import re
import sys

import psycopg2
import psycopg2.extras

# Tables carrying company_id/enterprise_id with NO cascading FK — the orphan set.
NO_CASCADE_BY_COMPANY = [
    "asana_task_map", "brief_nudge_sends", "brief_opens", "clickup_task_map",
    "conversations", "delegation_followup_sends", "invite_reminder_sends",
    "jira_issue_map", "prd_ticket_sync", "prd_tickets", "ticket_attachments",
    "ticket_comments", "ticket_edits", "tracker_meta",
]
NO_CASCADE_BY_ENTERPRISE = ["backlog_items", "kg_ingest_ledger"]
# The billing ledger. Excluded unless --purge-usage; see the module docstring.
USAGE_TABLE = "llm_usage_events"

# Keyed by slug, not id — also invisible to the cascade.
BY_SLUG = [
    "ask_jobs", "briefs", "cached_asks", "enterprise_input_sources",
    "knowledge_entities", "knowledge_relationships", "pipeline_runs",
]

# Domains that only ever belong to throwaway accounts or the team itself.
# Anything outside this set is treated as a potentially real person.
TEST_DOMAINS = {"disposablebydefault.ai", "yopmail.com", "example.com", "sprntly.ai"}
# A plus-addressed gmail (foo+tag@gmail.com) is a throwaway alias, not a person.
PLUS_ALIAS = re.compile(r"^[^@+]+\+[^@]+@gmail\.com$", re.I)


def env_from_dotenv(path: str) -> dict:
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k] = v.strip().strip('"').strip("'")
    return out


def connect():
    env = env_from_dotenv(os.path.expanduser("~/Sprntly/backend/.env"))
    ref = re.search(r"https://([a-z0-9]+)\.supabase\.co", env["SUPABASE_URL"]).group(1)
    return psycopg2.connect(
        host="aws-1-us-east-2.pooler.supabase.com", port=5432,
        user=f"postgres.{ref}", password=env["SUPABASE_DB_PASSWORD"],
        dbname="postgres", connect_timeout=15, sslmode="require",
    )


def assess(cur, company_id: str, idle_days: int) -> tuple[dict, list[str]]:
    """Live evidence for one company plus the reasons it must not be deleted."""
    cur.execute(
        """
        SELECT c.id::text, c.slug, c.display_name,
               (SELECT count(*) FROM company_members m WHERE m.company_id=c.id) AS members,
               (SELECT coalesce(string_agg(u.email,'|'),'')
                  FROM company_members m JOIN auth.users u ON u.id=m.user_id
                 WHERE m.company_id=c.id) AS emails,
               (SELECT max(u.last_sign_in_at)
                  FROM company_members m JOIN auth.users u ON u.id=m.user_id
                 WHERE m.company_id=c.id) AS last_sign_in,
               (SELECT count(*) FROM connections n WHERE n.company_id=c.id) AS conns,
               (SELECT count(*) FROM kg_signal k WHERE k.enterprise_id=c.id) AS signals
          FROM companies c WHERE c.id::text = %s
        """,
        (company_id,),
    )
    row = cur.fetchone()
    if row is None:
        return {"id": company_id, "missing": True}, ["company does not exist"]

    info = dict(zip(
        ["id", "slug", "name", "members", "emails", "last_sign_in", "conns", "signals"],
        row,
    ))
    emails = [e for e in (info["emails"] or "").split("|") if e]
    refusals: list[str] = []

    # IDENTITY is the strong signal. An address that is either on a
    # known-throwaway domain or a plus-alias (foo+tag@gmail.com) belongs to a
    # test account by construction — a person does not accumulate a dozen of
    # them by accident.
    def is_throwaway(email: str) -> bool:
        return (email.split("@")[-1].lower() in TEST_DOMAINS
                or bool(PLUS_ALIAS.match(email)))

    real_addresses = [e for e in emails if not is_throwaway(e)]
    test_identity = bool(emails) and not real_addresses

    cur.execute(
        "SELECT %s::timestamptz > now() - (%s || ' days')::interval",
        (info["last_sign_in"], idle_days),
    )
    recently_active = bool(cur.fetchone()[0]) if info["last_sign_in"] else False

    # --- hard refusals: any one of these means it is not ours to delete ---
    if recently_active:
        refusals.append(
            f"a member signed in within {idle_days}d ({info['last_sign_in']:%Y-%m-%d})")
    if info["members"] > 1:
        refusals.append(f"shared org — {info['members']} members, someone else's too")
    for email in real_addresses:
        refusals.append(f"member on a real address ({email})")

    # --- soft signal: a connector is only evidence when the identity is not
    #     already known-throwaway. Test accounts connect Slack/Zoom all the
    #     time; that is what they are for. Refusing on it alone blocked every
    #     plus-aliased org on the first run.
    if info["conns"] and not test_identity:
        refusals.append(f"{info['conns']} connected integration(s) on a non-test identity")

    info["emails"] = emails
    info["test_identity"] = test_identity
    return info, refusals


def purge(cur, info: dict, purge_usage: bool) -> list[tuple[str, int]]:
    """Delete one company and everything the cascade would miss. Caller owns
    the transaction, so a dry run is simply a rollback."""
    cid, slug = info["id"], info["slug"]
    removed: list[tuple[str, int]] = []

    def run(sql, params, label):
        cur.execute(sql, params)
        if cur.rowcount:
            removed.append((label, cur.rowcount))

    # 1. Tables the cascade cannot see. These must go first: once the company
    #    row is gone there is nothing left to find them by.
    for t in NO_CASCADE_BY_COMPANY:
        run(f"DELETE FROM {t} WHERE company_id = %s", (cid,), t)
    for t in NO_CASCADE_BY_ENTERPRISE:
        run(f"DELETE FROM {t} WHERE enterprise_id = %s", (cid,), t)
    if purge_usage:
        run(f"DELETE FROM {USAGE_TABLE} WHERE company_id = %s", (cid,), USAGE_TABLE)

    # 2. Slug-keyed tables — same problem, different key.
    for t in BY_SLUG:
        run(f"DELETE FROM {t} WHERE dataset = %s", (slug,), t)

    # 3. The company itself; 50 CASCADE FKs clear the rest.
    run("DELETE FROM companies WHERE id = %s", (cid,), "companies (+cascade)")
    return removed


def sweep_orphans(cur, purge_usage: bool) -> list[tuple[str, int]]:
    """Delete rows whose company no longer exists.

    These are the residue of earlier deletions that did not know about the
    no-FK tables. Every statement is anchored on `NOT EXISTS (SELECT 1 FROM
    companies ...)`, so a row can only be removed when its owning company is
    genuinely absent — a live tenant's rows are unreachable by construction,
    whatever is wrong with the list of table names below.
    """
    removed: list[tuple[str, int]] = []

    def run(sql, label):
        cur.execute(sql)
        if cur.rowcount:
            removed.append((label, cur.rowcount))

    for t in NO_CASCADE_BY_COMPANY:
        run(f"DELETE FROM {t} x WHERE NOT EXISTS "
            f"(SELECT 1 FROM companies c WHERE c.id = x.company_id)", t)
    for t in NO_CASCADE_BY_ENTERPRISE:
        run(f"DELETE FROM {t} x WHERE NOT EXISTS "
            f"(SELECT 1 FROM companies c WHERE c.id = x.enterprise_id)", t)
    if purge_usage:
        run(f"DELETE FROM {USAGE_TABLE} x WHERE NOT EXISTS "
            f"(SELECT 1 FROM companies c WHERE c.id::text = x.company_id)", USAGE_TABLE)

    # Slug-keyed. A dataset may be `<company-slug>--<workspace>`, so the base
    # slug before the separator is what identifies the company — matching the
    # full string alone would report a live workspace-scoped dataset as an
    # orphan and delete a real tenant's briefs.
    for t in BY_SLUG:
        run(f"DELETE FROM {t} x WHERE NOT EXISTS (SELECT 1 FROM companies c "
            f"WHERE c.slug = x.dataset OR c.slug = split_part(x.dataset,'--',1))", t)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids-file",
                    help="one company UUID per line; # comments allowed")
    ap.add_argument("--sweep-orphans", action="store_true",
                    help="delete rows whose company no longer exists (no ids needed)")
    ap.add_argument("--execute", action="store_true",
                    help="COMMIT. Without this the transaction is rolled back.")
    ap.add_argument("--idle-days", type=int, default=7)
    ap.add_argument("--purge-usage", action="store_true",
                    help="also delete llm_usage_events rows (billing history)")
    ap.add_argument("--i-know-what-im-doing", action="store_true",
                    help="proceed despite refusals — do not use casually")
    args = ap.parse_args()

    if args.sweep_orphans:
        conn = connect()
        conn.autocommit = False
        cur = conn.cursor()
        mode = "EXECUTE (WILL COMMIT)" if args.execute else "DRY RUN (will roll back)"
        print(f"=== ORPHAN SWEEP — {mode} ===\n")
        removed = sweep_orphans(cur, args.purge_usage)
        for t, n in sorted(removed, key=lambda x: -x[1]):
            print(f"  {n:>8,}  {t}")
        print(f"  {sum(n for _, n in removed):>8,}  ALL TABLES")
        conn.commit() if args.execute else conn.rollback()
        print("\nCOMMITTED." if args.execute
              else "\nROLLED BACK — nothing was deleted.")
        conn.close()
        return 0

    if not args.ids_file:
        # Deliberately the only way to name a target. If you find yourself
        # wanting a flag that queries for candidates, write the query
        # separately, read the results, and paste the ids you approve.
        print("--ids-file is required unless --sweep-orphans", file=sys.stderr)
        return 2
    ids = []
    with open(args.ids_file) as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                ids.append(line)
    if not ids:
        print("no ids given", file=sys.stderr)
        return 2

    mode = "EXECUTE (WILL COMMIT)" if args.execute else "DRY RUN (will roll back)"
    print(f"=== {mode} — {len(ids)} target(s), idle threshold {args.idle_days}d ===\n")

    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    approved, refused, totals = [], [], {}
    for cid in ids:
        info, refusals = assess(cur, cid, args.idle_days)
        label = f"{info.get('name','?')} [{info.get('slug','?')}]"
        if refusals and not args.i_know_what_im_doing:
            refused.append((label, cid, refusals))
            print(f"REFUSED  {label}\n         {cid}")
            for r in refusals:
                print(f"         - {r}")
            print()
            continue
        approved.append((label, cid, info))

    if refused and not approved:
        conn.rollback()
        print("Nothing to do — every target was refused.")
        return 1

    print(f"--- deleting {len(approved)} compan(y|ies) ---\n")
    for label, cid, info in approved:
        removed = purge(cur, info, args.purge_usage)
        rows = sum(n for _, n in removed)
        print(f"{label}\n  {cid}  ({info['members']} member(s), "
              f"{info['signals']} signals, last seen {info['last_sign_in']})")
        for t, n in sorted(removed, key=lambda x: -x[1]):
            print(f"    {n:>7,}  {t}")
            totals[t] = totals.get(t, 0) + n
        print(f"    {rows:>7,}  TOTAL\n")

    print("=== per-table totals ===")
    for t, n in sorted(totals.items(), key=lambda x: -x[1]):
        print(f"  {n:>8,}  {t}")
    print(f"  {sum(totals.values()):>8,}  ALL TABLES")

    if args.execute:
        conn.commit()
        print("\nCOMMITTED.")
    else:
        conn.rollback()
        print("\nROLLED BACK — nothing was deleted. Re-run with --execute to apply.")

    # Storage is a separate system; the SQL transaction cannot touch it.
    print("\n=== storage objects to remove separately ===")
    for _, cid, _ in approved:
        print(f"  uploads/{cid}/")

    # Deleting a `connections` row drops our copy of the OAuth token; it does
    # NOT revoke it at the provider. For throwaway orgs that is acceptable
    # (the grant dies with the test workspace), but it should be stated rather
    # than discovered.
    with_conns = [(l, i["conns"]) for l, _, i in approved if i.get("conns")]
    if with_conns:
        print("\n=== connector grants NOT revoked at the provider ===")
        for label, n in with_conns:
            print(f"  {n} grant(s)  {label}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
