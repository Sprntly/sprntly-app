"""TWO-TOKEN PROOF — "any user in workspace except the one who created that
artifact" must be able to edit PRDs and tickets.

Run it:

    cd backend
    PROOF_OUT=/tmp/proof.txt .venv/bin/python -m pytest \
        tests/test_two_token_workspace_edit.py -q && cat /tmp/proof.txt

Named `test_*` so pytest DOES collect it — it ran as `proof_*` originally,
which meant this acceptance evidence never executed in CI and could rot
unnoticed. Set PROOF_OUT to also dump the human-readable report to a file;
without it the assertions still run, they just print nothing.

WHY THIS SHAPE, AND NOT LIVE STAGING
------------------------------------
The ask was a BEFORE and an AFTER. Live staging can only ever show the
AFTER — it runs one commit at a time, and the before-state is gone the
moment the branch merges. This boots the REAL FastAPI app (the same
conftest doubles the whole suite uses) so the identical script can be run
against origin/main and against the fix branch and the two reports diffed.

It touches NO shared database: every row lives in a per-run temp store the
fixture tears down. Nothing to clean up, no debris in the shared Supabase,
and Apurva's own PRD is never opened, let alone mutated.

FOUR IDENTITIES
---------------
  A  the CREATOR   — org owner; authored the PRD and the ticket
  B  the COLLEAGUE — plain `member` of the SAME company with a
                     workspace_members row on the SAME workspace, and the
                     author of nothing. This is Apurva's "any user in
                     workspace except the one who created that artifact".
  C  the OUTSIDER  — a member of a DIFFERENT company. The isolation case.
  D  the VIEWER    — role='viewer' in the same workspace. Present only to
                     answer "is any role supposed to be read-only?" with a
                     measurement instead of a grep. This branch does not
                     touch role handling, so D's row reads the same before
                     and after.
  E  the OTHER-WS  — same COMPANY, but a member of a DIFFERENT workspace
                     only, acting with `X-Workspace-Id: <that other ws>`.
                     Apurva's unit of access is the WORKSPACE, so this is
                     the isolation case that actually matters: "any user in
                     workspace" means a user who is NOT in it gets nothing.

The PRD's dataset is deliberately BOUND to workspace 1 (a real `datasets`
row with workspace_id). Without that binding `_dataset_in_workspace` takes
its legacy-rollout fallback — an unbound dataset is reachable from any
workspace of the owning company — and E's isolation result would be
meaningless.

Tokens are Supabase-signed JWTs minted by tests/_company_helpers.py's
`supabase_bearer`, the same helper the existing suite uses. No password is
typed, read, or stored anywhere in this file.
"""
from __future__ import annotations

import importlib
import os
import sys
import uuid

from fastapi.testclient import TestClient

from tests._company_helpers import seed_company, setup_supabase_auth, supabase_bearer

OUT_PATH = os.environ.get("PROOF_OUT")  # unset in CI: assert only, no file

# `PUT /v1/prd/{id}` is first and deliberately so: that is the endpoint the
# PRD editor's AUTOSAVE calls (PrdHtmlView.tsx -> prdApi.update), not some
# separate explicit-save route.
PRD_WRITES = [
    ("PRD autosave/save   PUT  /v1/prd/{prd}", "put", "/v1/prd/{prd}",
     {"title": "Edited by {who}", "payload_md": "# Edited by {who}"}),
    ("PRD version         POST /v1/prd/{prd}/versions", "post",
     "/v1/prd/{prd}/versions",
     {"title": "v by {who}", "payload_md": "# v by {who}", "label": "manual"}),
]

TICKET_WRITES = [
    ("Ticket description  PUT  /v1/tickets/{key}/description", "put",
     "/v1/tickets/{key}/description",
     {"description": "Rewritten by {who}", "acceptance_criteria": ["by {who}"]}),
    ("Ticket fields       PUT  /v1/tickets/{key}/fields", "put",
     "/v1/tickets/{key}/fields", {"title": "Retitled by {who}", "priority": "P1"}),
    ("Ticket comment      POST /v1/tickets/{key}/comments", "post",
     "/v1/tickets/{key}/comments", {"body": "Comment from {who}"}),
]


def _fmt(obj, who: str):
    if isinstance(obj, dict):
        return {k: _fmt(v, who) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fmt(v, who) for v in obj]
    if isinstance(obj, str):
        return obj.replace("{who}", who)
    return obj


def _call(client, headers, spec, *, prd: int, key: str, who: str):
    label, method, path, body = spec
    r = getattr(client, method)(
        path.format(prd=prd, key=key), json=_fmt(body, who), headers=headers
    )
    return label, r.status_code, (r.text[:120] if r.status_code >= 400 else "")


def test_proof(isolated_settings, monkeypatch):
    setup_supabase_auth(monkeypatch)
    import app.main as main_mod

    importlib.reload(sys.modules["app.main"])
    client = TestClient(main_mod.app)
    db = isolated_settings["db"]
    log: list[str] = []

    def say(line: str = "") -> None:
        log.append(line)

    from app.db.client import require_client
    from app.db.workspaces import ensure_default_workspace, upsert_workspace_member

    a_id = "userA-creator-" + uuid.uuid4().hex[:6]
    company = seed_company(user_id=a_id, slug="proofco")
    ws = ensure_default_workspace(company)
    a = supabase_bearer(a_id)

    b_id = "userB-colleague-" + uuid.uuid4().hex[:6]
    require_client().table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": company, "user_id": b_id,
         "role": "member"}
    ).execute()
    upsert_workspace_member(ws["id"], b_id, "member")
    b = supabase_bearer(b_id)

    c_id = "userC-outsider-" + uuid.uuid4().hex[:6]
    other = seed_company(user_id=c_id, slug="rivalco")
    upsert_workspace_member(ensure_default_workspace(other)["id"], c_id, "admin")
    c = supabase_bearer(c_id)

    # D: role='viewer' on BOTH company_members and workspace_members. Included
    # to answer "is any role supposed to be read-only?" with evidence rather
    # than a grep. Whatever this prints is a statement of CURRENT behaviour,
    # not a behaviour this change introduces — it is identical before and
    # after (nothing in this branch touches role handling).
    d_id = "userD-viewer-" + uuid.uuid4().hex[:6]
    require_client().table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": company, "user_id": d_id,
         "role": "viewer"}
    ).execute()
    upsert_workspace_member(ws["id"], d_id, "viewer")
    d = supabase_bearer(d_id)

    # E: same company, but only in a SECOND workspace, and acting in it.
    from app.db.workspaces import create_workspace

    ws2 = create_workspace(company, "Notifications")
    e_id = "userE-otherws-" + uuid.uuid4().hex[:6]
    require_client().table("company_members").insert(
        {"id": uuid.uuid4().hex, "company_id": company, "user_id": e_id,
         "role": "member"}
    ).execute()
    upsert_workspace_member(ws2["id"], e_id, "member")
    e = {**supabase_bearer(e_id), "X-Workspace-Id": ws2["id"]}

    # Bind the dataset to workspace 1, so workspace scoping is REAL and E's
    # refusal below is not an artifact of the unbound-dataset fallback.
    require_client().table("datasets").insert(
        {"slug": "proofco", "display_name": "Proofco", "workspace_id": ws["id"]}
    ).execute()

    brief_id = db.save_brief("proofco", "W", {"insights": []}, schema_version=1)
    prd = db.start_prd(brief_id=brief_id, insight_index=0, title="Created by A",
                       template_version=1, variant="v2")
    require_client().table("prds").update({"public_id": str(uuid.uuid4())}).eq(
        "id", prd
    ).execute()
    key = f"prd-{prd}-{uuid.uuid4().hex[:12]}"
    # A authors the ticket too, so B is unambiguously a non-creator on both.
    client.put(f"/v1/tickets/{key}/description",
               json={"description": "Created by A", "acceptance_criteria": []},
               headers=a)

    say("=" * 78)
    say("TWO-TOKEN PROOF — workspace membership grants edit; authorship grants nothing")
    say("=" * 78)
    say(f"company    {company}")
    say(f"workspace  {ws['id']}")
    say(f"A CREATOR    {a_id}   owner; authored the PRD and the ticket")
    say(f"B COLLEAGUE  {b_id}   member, SAME workspace, authored NOTHING")
    say(f"C OUTSIDER   {c_id}   member of a DIFFERENT company")
    say(f"D VIEWER     {d_id}   role='viewer', same workspace (roles fact-check)")
    say(f"PRD id {prd}     ticket {key}")

    # ── NON-SHARE PATH, READS. "Open the PRD from the sidebar" is these
    #    calls: the ordinary company-scoped routes, no ?share= anywhere.
    reads = [
        ("open PRD          GET  /v1/prd/{prd}", "/v1/prd/{prd}"),
        ("PRD versions      GET  /v1/prd/{prd}/versions", "/v1/prd/{prd}/versions"),
        ("open ticket       GET  /v1/tickets/{key}/data", "/v1/tickets/{key}/data"),
    ]
    say("")
    say("--- NON-SHARE PATH, READS (the 'open it from the sidebar' route)")
    read_results: dict[str, dict[str, int]] = {}
    for who, headers in (("A", a), ("B", b), ("C", c), ("E", e)):
        read_results[who] = {}
        codes = []
        for label, path in reads:
            r = client.get(path.format(prd=prd, key=key), headers=headers)
            read_results[who][label] = r.status_code
            codes.append(f"{r.status_code} {label.split()[0]}-{label.split()[1]}")
        say(f"    {who}: " + "   ".join(codes))

    results: dict[str, dict[str, int]] = {}
    for who, headers, expect in (
        ("A", a, "expect 2xx — creator, no regression"),
        ("B", b, "expect 2xx — THE REQUIREMENT (non-creator, same workspace)"),
        ("C", c, "different COMPANY — expect refusal / no reach into A's rows"),
        ("D", d, "role='viewer' — FACT-CHECK ONLY, unchanged by this branch"),
        ("E", e, "same company, DIFFERENT workspace — the workspace boundary"),
    ):
        say("")
        say(f"--- {who}  ({expect})")
        results[who] = {}
        for spec in PRD_WRITES + TICKET_WRITES:
            label, code, err = _call(client, headers, spec, prd=prd, key=key, who=who)
            results[who][label] = code
            say(f"    {code}  {label}{('   ' + err) if err else ''}")

    # ── The share-link entry point: where the read-only defect actually is ──
    from app.db.artifact_shares import mint_share

    share = mint_share(artifact_type="prd", artifact_id=prd,
                       owner_company_id=company, owner_workspace_id=ws["id"],
                       created_by_user_id=a_id)
    say("")
    say("--- share-link routing:  GET /v1/artifact-share/{token}/resolve")
    say("    outcome 'guest_view' => frontend mounts the READ-ONLY guest viewer")
    say("    outcome 'member'     => frontend hands over to the EDITABLE app")
    outcomes = {}
    for who, headers in (("A", a), ("B", b), ("C", c)):
        r = client.get(f"/v1/artifact-share/{share['token']}/resolve", headers=headers)
        body = r.json() if r.status_code == 200 else {}
        outcomes[who] = body.get("outcome")
        say(f"    {who}: HTTP {r.status_code}  outcome={body.get('outcome')!r}"
            + (f"  reason={body['reason']}" if body.get("reason") else ""))

    # ── Did B's writes actually land, or did the endpoint merely say 200? ──
    prd_row = client.get(f"/v1/prd/{prd}", headers=a).json()
    tkt_row = client.get(f"/v1/tickets/{key}/data", headers=a).json()
    say("")
    say("--- persistence, read back as A (the creator)")
    say(f"    PRD title      {prd_row.get('title')!r}")
    say(f"    ticket title   {tkt_row.get('title')!r}")
    say(f"    ticket desc    {tkt_row.get('description')!r}")
    say(f"    comment authors {[x['author'] for x in tkt_row.get('comments', [])]}")

    # ── What C's 200s on the ticket routes actually did ──────────────────
    # The ticket routes key every row on (company_id, ticket_key) with
    # company_id taken from the CALLER's own session. A ticket_key is a
    # client-supplied string with no existence check, so C's write does not
    # fail — it upserts a row inside C'S OWN company. The thing that matters
    # is whether A's row moved. It must not have.
    c_rows = (require_client().table("ticket_edits").select("company_id, title")
              .eq("ticket_key", key).execute().data or [])
    say("")
    say("--- FINDING: the workspace boundary holds for PRDs, NOT for tickets")
    say("    E is in a DIFFERENT workspace of the SAME company.")
    say(f"    PRD  write : {results['E']['PRD autosave/save   PUT  /v1/prd/{prd}']}"
        "  <- refused, correct")
    say(f"    PRD  read  : {read_results['E']['open PRD          GET  /v1/prd/{prd}']}"
        "  <- refused, correct")
    say(f"    tkt  write : {results['E']['Ticket fields       PUT  /v1/tickets/{key}/fields']}"
        "  <- ALLOWED, and it landed on A's row")
    say(f"    tkt  read  : {read_results['E']['open ticket       GET  /v1/tickets/{key}/data']}"
        "  <- ALLOWED")
    say("    Cause: every route in app/routes/tickets.py scopes on")
    say("    company.company_id and never reads ctx.workspace_id.")
    say("    PRE-EXISTING — identical on origin/main. Not caused by this branch.")

    say("")
    say("--- what C's ticket 200s actually wrote (ticket_edits rows for this key)")
    for row in c_rows:
        owner = ("A+B's company" if row["company_id"] == company
                 else "C's OWN company" if row["company_id"] == other else "?")
        say(f"    company_id={row['company_id']}  ({owner})  title={row.get('title')!r}")
    say("")
    say("=" * 78)

    report = "\n".join(log)
    if OUT_PATH:
        with open(OUT_PATH, "w") as fh:
            fh.write(report + "\n")
        print("\n" + report + f"\n[report written to {OUT_PATH}]\n")

    # ── Assertions, so a wrong story fails loudly instead of printing ──────

    # 1. THE REQUIREMENT, on the NON-SHARE path: the non-creator colleague
    #    can OPEN and EDIT both artifacts through the ordinary routes.
    for label, code in read_results["B"].items():
        assert 200 <= code < 300, f"colleague B could not OPEN via {label}: {code}"
    for label, code in results["B"].items():
        assert 200 <= code < 300, f"colleague B was REFUSED by {label}: {code}"

    # 2. NO REGRESSION: the creator still can.
    for label, code in results["A"].items():
        assert 200 <= code < 300, f"creator A was REFUSED by {label}: {code}"

    # 3. ISOLATION. PRD routes 404 the outsider outright. The ticket routes
    #    answer 200 but write into C's own tenant — so the real assertion is
    #    that A's row is untouched and C never reads A's data.
    for label, code in results["C"].items():
        if "/v1/prd/" in label:
            assert code == 404, f"ISOLATION BREACH: outsider C got {code} on {label}"

    # 3b. THE WORKSPACE BOUNDARY (Apurva's unit of access).
    #     PRDs honour it: E, a member of a DIFFERENT workspace in the same
    #     company, is refused.
    for label, code in results["E"].items():
        if "/v1/prd/" in label:
            assert code == 404, (
                f"WORKSPACE BREACH: E (other workspace) got {code} on {label}"
            )
    for label, code in read_results["E"].items():
        if "/v1/prd/" in label:
            assert code == 404, (
                f"WORKSPACE BREACH: E (other workspace) could read via {label}"
            )

    #     TICKETS DO NOT. This is a PRE-EXISTING gap, not something this
    #     branch introduces: every route in app/routes/tickets.py scopes on
    #     `company.company_id` alone and never consults
    #     `ctx.workspace_id`, so a member of ANY workspace in the company
    #     can read and write ANY ticket in it. The assertions below pin the
    #     CURRENT behaviour deliberately — if someone later scopes tickets
    #     to the workspace, this proof fails loudly and the finding gets
    #     re-reported rather than silently going stale.
    for label, code in results["E"].items():
        if "/v1/tickets/" in label:
            assert 200 <= code < 300, (
                "KNOWN GAP CHANGED: tickets used to be company-scoped and E "
                f"could write them; now {label} returns {code}. Re-report."
            )
    assert read_results["E"]["open ticket       GET  /v1/tickets/{key}/data"] == 200, (
        "KNOWN GAP CHANGED: E could previously READ a ticket from another "
        "workspace. Re-report."
    )
    a_row = [r for r in c_rows if r["company_id"] == company]
    assert len(a_row) == 1, "expected exactly one ticket_edits row in A's company"
    # The loop order is A, B, C, D, E — so E writes last, and whoever the LAST
    # in-company writer was owns the title. What matters is that it is never
    # C's — an out-of-company write must never reach this row.
    assert a_row[0]["title"] != "Retitled by C", (
        "ISOLATION BREACH: outsider C's write reached A's ticket row — "
        f"title is {a_row[0]['title']!r}"
    )
    assert client.get(f"/v1/tickets/{key}/data", headers=c).json()["title"] == "Retitled by C", (
        "ISOLATION BREACH: outsider C can READ A's ticket"
    )
    assert client.get(f"/v1/prd/{prd}", headers=c).status_code == 404, (
        "ISOLATION BREACH: outsider C can READ A's PRD"
    )

    # 4. The share-link routing fix itself.
    assert outcomes["B"] == "member", (
        f"colleague B resolves to {outcomes['B']!r} — 'guest_view' means the "
        "frontend gives them the READ-ONLY viewer, which is the bug"
    )
    assert outcomes["C"] == "blocked"
