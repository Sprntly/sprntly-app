"""A catalog row records WHICH provider workspace it came from.

WHY THE COLUMN EXISTS. A company's connection to a provider is not permanent.
A Slack install can be disconnected and replaced by an install into a
DIFFERENT workspace, and the rows the old install wrote stay behind — indexed
as documents the company has, rankable, and (since document resolution
shipped) assertable as the subject of a question, after which the body fetch
fails and the user is told the contents could not be loaded. #1119 removes
rows on channel DESELECTION, which hands it an explicit id list produced by a
user action. The disconnect path has no such list, and the question it must
answer first — "does any connection still serve this company?" — was not
answerable from stored state, because nothing recorded a row's workspace.

Measured on the shared database 2026-08-11: one such row across 66, its
permalink workspace matching no active connection its company holds, written
18 minutes before that company's current Slack connection row was created.

WHAT THESE TESTS PIN, in priority order:

  1. The value comes from the STORED CONNECTION CONFIG, not from the permalink
     subdomain beside it and not from a live API call. This is the failure
     mode that would be invisible: a column that looks populated, and silently
     never matches the thing it is meant to be compared against.
  2. A promoted personal install does NOT lose its catalog. This is the exact
     hazard #1119 refused to guess at, and the column is only worth having if
     it makes that case safe BY CONSTRUCTION.
  3. NULL means UNKNOWN. A caller that does not know the workspace can neither
     clear a known value nor have one invented for it.

The consuming deletion rule is deliberately NOT in this change, so nothing
here deletes anything; these pin the invariants that rule will rest on.
"""
from __future__ import annotations

import inspect

import pytest

from app import document_catalog
from app.kg_ingest import slack_extract

_TEAM_ID = "T0FAKE1234"
_TEAM_DOMAIN = "northwind"


@pytest.fixture
def catalog(isolated_settings, monkeypatch):
    """A real `register_document` upsert against the fake Supabase, with the
    summariser and embedder stubbed — the isolation the other catalog-writer
    suites use."""
    monkeypatch.setattr(
        document_catalog, "llm_call",
        lambda **kw: type("R", (), {"output": {"summary": "s", "topics": ["t"]}})(),
    )
    monkeypatch.setattr(
        document_catalog, "embed_texts",
        lambda texts, **k: [[0.1] * 1536 for _ in texts],
    )
    db = isolated_settings["supabase"]
    cid = "co-workspace-id"
    db.table("companies").insert(
        {"id": cid, "slug": f"slug-{cid}", "display_name": "C"}
    ).execute()
    return {"db": db, "cid": cid}


def _doc(channel_id="C1", channel_name="product-feedback", text="## #x\n\nbody\n"):
    return slack_extract.SlackChannelDoc(
        channel_id=channel_id, channel_name=channel_name,
        text=text, latest_ts="1754000000.000000",
    )


def _row(catalog, external_id="C1"):
    rows = (
        catalog["db"].table("document_catalog").select("*")
        .eq("company_id", catalog["cid"]).eq("external_id", external_id)
        .execute().data
    )
    assert rows, f"no catalog row for {external_id}"
    return rows[0]


# ═════════════════ 1. Populated from the RIGHT source ══════════════════════


def test_slack_row_stores_the_team_id(catalog):
    slack_extract.register_slack_catalog(
        catalog["cid"], [_doc()], team_domain=_TEAM_DOMAIN, team_id=_TEAM_ID,
    )
    assert _row(catalog)["provider_workspace_id"] == _TEAM_ID


def test_the_workspace_id_is_never_the_permalink_subdomain(catalog):
    """THE WRONG-SOURCE GUARD, and the reason this file exists.

    `team_domain` sits right beside `team_id` at the call site, is also a
    workspace identifier in casual speech, and is already threaded through
    every function in the chain — so it is the value someone reaches for. It
    is a DISPLAY NAME a workspace admin can change, and it is not what
    `connections.config.team.id` holds. Storing it would produce a column that
    is populated, plausible, and matches nothing, so the disconnect rule built
    on it would either delete nothing forever or delete everything.
    """
    slack_extract.register_slack_catalog(
        catalog["cid"], [_doc()], team_domain=_TEAM_DOMAIN, team_id=_TEAM_ID,
    )
    row = _row(catalog)

    assert row["provider_workspace_id"] != _TEAM_DOMAIN
    assert _TEAM_DOMAIN not in (row["provider_workspace_id"] or ""), (
        "the permalink subdomain leaked into provider_workspace_id — it must "
        "hold the stable team id from the stored connection config"
    )
    # The domain still does its own job.
    assert _TEAM_DOMAIN in (row["url"] or ""), "the permalink lost its domain"


def test_sync_reads_the_team_id_from_stored_config_not_from_the_api():
    """The id must come off the connection row this sync already loaded.

    Sourcing it from `fetch_team_info` (the call that resolves the domain a
    line earlier) would make the column unwritable whenever Slack is
    unreachable — for a fact already on disk — and would introduce a second
    source of truth for a value whose entire purpose is to be compared,
    field-for-field, against `connections.config.team.id`.

    Read at the SOURCE rather than by stubbing the network: the assertion is
    about which of two in-scope values is chosen, which a behavioural test
    passes on for the wrong reason whenever both happen to agree.
    """
    from app.connectors import slack_sync

    src = inspect.getsource(slack_sync.sync_slack)
    team_id_line = next(
        (ln for ln in src.splitlines() if "team_id = " in ln), None
    )
    assert team_id_line, "sync_slack no longer derives a team_id — update this guard"
    assert "config" in team_id_line, (
        "team_id is not being read from the stored connection config; if it "
        "now comes from _slack_team_domain or fetch_team_info, that is the "
        "defect this test exists for"
    )
    assert "fetch_team_info" not in team_id_line


# ═════════════ 2. A promoted personal install keeps its catalog ════════════


def test_a_promoted_personal_install_keeps_the_same_workspace_id(catalog):
    """THE HAZARD #1119 REFUSED TO GUESS AT.

    A Slack row can be a personal install that is later promoted to serve the
    whole company, so purging a catalog when "the" connection goes away could
    delete one that is still live. Keying on the WORKSPACE rather than on the
    connection row makes that safe by construction: both installs into one
    workspace carry the same team id, so a company that still has any active
    connection to that workspace still matches every row it wrote.

    Registration under the promoted install must therefore be a no-op on this
    column, not a rewrite to some connection-specific value.
    """
    company = catalog["cid"]
    slack_extract.register_slack_catalog(
        company, [_doc(text="v1")], team_domain=_TEAM_DOMAIN, team_id=_TEAM_ID,
    )
    first = _row(catalog)["provider_workspace_id"]

    # Same workspace, different connection row (a different user's install,
    # now the one serving the company) — and a changed document, so this is a
    # full re-registration rather than the no-op path.
    slack_extract.register_slack_catalog(
        company, [_doc(text="v2")], team_domain="renamed-domain", team_id=_TEAM_ID,
    )

    assert _row(catalog)["provider_workspace_id"] == first == _TEAM_ID, (
        "a promoted install changed the row's workspace id — a disconnect rule "
        "keyed on it would then delete a catalog that is still being served"
    )


def test_rows_from_two_workspaces_are_distinguishable(catalog):
    """The property that makes the orphan findable at all: two rows on ONE
    company, from two different workspaces, must not look alike. This is the
    shape of the live orphan — a row from a workspace the company no longer
    holds any connection to, sitting beside rows from the current one."""
    company = catalog["cid"]
    slack_extract.register_slack_catalog(
        company, [_doc(channel_id="C_OLD")], team_id="T_OLD_WORKSPACE",
    )
    slack_extract.register_slack_catalog(
        company, [_doc(channel_id="C_NEW")], team_id=_TEAM_ID,
    )

    assert _row(catalog, "C_OLD")["provider_workspace_id"] == "T_OLD_WORKSPACE"
    assert _row(catalog, "C_NEW")["provider_workspace_id"] == _TEAM_ID


# ═══════════════════ 3. NULL means UNKNOWN, not orphaned ═══════════════════


def test_a_caller_without_a_team_id_leaves_the_column_null(catalog):
    """NULL is the honest answer, and every consumer must read it as UNKNOWN.
    A caller that cannot establish the workspace must not invent one."""
    slack_extract.register_slack_catalog(catalog["cid"], [_doc()], team_id=None)
    assert _row(catalog)["provider_workspace_id"] is None


def test_a_caller_without_a_team_id_cannot_clear_a_known_one(catalog):
    """THE DESTRUCTIVE CASE. The upsert rewrites the whole row, so a
    re-registration by a caller that does not know the workspace would blank
    a value an informed caller recorded — and a blanked value is then read as
    UNKNOWN forever, permanently hiding a genuine orphan."""
    company = catalog["cid"]
    slack_extract.register_slack_catalog(
        company, [_doc(text="v1")], team_id=_TEAM_ID,
    )
    slack_extract.register_slack_catalog(
        company, [_doc(text="v2")], team_id=None,
    )
    assert _row(catalog)["provider_workspace_id"] == _TEAM_ID, (
        "an uninformed re-registration erased a known workspace id"
    )


def test_an_unchanged_document_still_gains_a_missing_workspace_id(catalog):
    """CONVERGENCE, and the reason there is no backfill migration.

    Registration is content-hash keyed: an unchanged document returns early
    without a write. Every row that existed before this column did therefore
    sits at NULL, and a channel nobody posts in again would keep NULL forever
    — leaving exactly the quietest tenants, the ones whose catalogs go
    staleest, permanently unclassifiable. The no-op path fills a BLANK in
    place (one column update, no summary regeneration).
    """
    company = catalog["cid"]
    slack_extract.register_slack_catalog(company, [_doc(text="same")], team_id=None)
    assert _row(catalog)["provider_workspace_id"] is None

    slack_extract.register_slack_catalog(
        company, [_doc(text="same")], team_id=_TEAM_ID,
    )

    assert _row(catalog)["provider_workspace_id"] == _TEAM_ID, (
        "the content-hash no-op path skipped the fill, so this column can "
        "never converge without a backfill"
    )


def test_the_no_op_fill_never_overwrites_a_different_workspace_id(catalog):
    """A fill, not a rewrite. That the same document is now reachable from a
    different workspace is not a fact this path can establish, and quietly
    replacing the stored id would destroy the only evidence the disconnect
    rule has."""
    company = catalog["cid"]
    slack_extract.register_slack_catalog(
        company, [_doc(text="same")], team_id="T_ORIGINAL",
    )
    slack_extract.register_slack_catalog(
        company, [_doc(text="same")], team_id="T_DIFFERENT",
    )
    assert _row(catalog)["provider_workspace_id"] == "T_ORIGINAL"


def test_the_column_is_readable_through_the_documented_accessor(catalog):
    """`list_documents` is the single accessor for this table; a column no
    reader can see is not stored for any purpose."""
    slack_extract.register_slack_catalog(
        catalog["cid"], [_doc()], team_id=_TEAM_ID,
    )
    docs = document_catalog.list_documents(catalog["cid"], provider="slack")
    assert [d.provider_workspace_id for d in docs] == [_TEAM_ID]
