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
    # Counted, because the summariser is the OBSERVABLE that distinguishes
    # the content-hash short-circuit from a full re-registration. Without it a
    # test whose fixture accidentally changes the document takes the full
    # upsert path, still passes, and silently stops testing the fill.
    calls: list = []

    def _fake_llm(**kw):
        calls.append(kw)
        return type("R", (), {"output": {"summary": "s", "topics": ["t"]}})()

    monkeypatch.setattr(document_catalog, "llm_call", _fake_llm)
    monkeypatch.setattr(
        document_catalog, "embed_texts",
        lambda texts, **k: [[0.1] * 1536 for _ in texts],
    )
    db = isolated_settings["supabase"]
    cid = "co-workspace-id"
    db.table("companies").insert(
        {"id": cid, "slug": f"slug-{cid}", "display_name": "C"}
    ).execute()
    return {"db": db, "cid": cid, "summary_calls": calls}


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


#: A REALISTIC stored Slack connection config. Every value here is something
#: a careless read could mistake for "the workspace", which is the whole point
#: — a fixture carrying only the right key cannot catch the wrong read.
_REAL_CONFIG = {
    "team": {"id": _TEAM_ID, "name": "Acme", "domain": "acme"},
    "bot_user_id": "U999",
    "channel_id": "C777",
    "channel_name": "general",
    "target_type": "channel",
}


def test_team_id_from_config_reads_the_id_and_not_a_display_name():
    """THE EXTRACTION ITSELF, pinned behaviourally.

    The earlier version of this guard asserted on `inspect.getsource` — that
    the extracting line contained "config" and not "fetch_team_info". That is
    SPELLING, and it was proven false-green: both `.get("name")` and a read of
    `config["team_domain"]` satisfy it while storing a renameable display name
    in a column whose only job is to match `connections.config.team.id`.

    The consequence of that miss is not cosmetic. The disconnect rule compares
    this value against `connections.config.team.id`; a display name matches
    NOTHING, so every catalogued Slack document classifies as an orphan and a
    tenant's entire Slack catalog is deleted on the next disconnect.

    So: assert the returned VALUE, against a config that carries all three
    tempting neighbours.
    """
    from app.connectors.slack_sync import team_id_from_config

    assert team_id_from_config(_REAL_CONFIG) == _TEAM_ID


@pytest.mark.parametrize("wrong", ["Acme", "acme", "U999", "C777", "general"])
def test_team_id_from_config_returns_none_of_the_neighbouring_values(wrong):
    """Named individually so a failure says WHICH wrong value was picked."""
    from app.connectors.slack_sync import team_id_from_config

    assert team_id_from_config(_REAL_CONFIG) != wrong


@pytest.mark.parametrize("config", [
    {},                                   # never connected
    {"team": {}},                          # team recorded, id absent
    {"team": {"id": ""}},                  # blank id
    {"team": {"id": "   "}},               # whitespace-only id
    {"team": {"name": "Acme", "domain": "acme"}},   # ONLY the wrong keys
])
def test_team_id_from_config_yields_none_rather_than_a_wrong_value(config):
    """No id available => None => the column stays NULL (UNKNOWN).

    The last case is the load-bearing one: a config carrying the display name
    and domain but no id must produce NOTHING, not a fallback to whichever
    string happens to be present. A fallback here is precisely how a column
    becomes populated-but-unmatchable.
    """
    from app.connectors.slack_sync import team_id_from_config

    assert team_id_from_config(config) is None


#: A config that carries NO `team` key at all. This shape demonstrably exists
#: in production — `db/connections.py:list_slack_connections_by_team` guards
#: with `(cfg.get("team") or {})` and `row_config` returns `{}` on any parse
#: failure — and it is the population a "better than nothing" fallback would
#: silently corrupt, because it is exactly where the fallback executes.
_CONFIG_WITHOUT_TEAM = {
    "bot_user_id": "U999", "channel_id": "C777", "channel_name": "general",
}


@pytest.mark.parametrize("config,domain,expected", [
    # Happy path: id present, domain resolved.
    (_REAL_CONFIG, "acme", _TEAM_ID),
    # THE DOMAIN LOOKUP FAILED. `_slack_team_domain` returns None on ANY
    # failure by design (its own docstring says so, and `team.info` needs a
    # scope that can be absent), so this is a routine sync, not an edge case.
    # The workspace id is on disk and must still be recorded — gating it on
    # the domain would silently stop populating the column for every tenant
    # whose team.info call is unreachable.
    (_REAL_CONFIG, None, _TEAM_ID),
    # NO `team` KEY. There is no id to record, so the column must stay NULL
    # (UNKNOWN). It must NOT fall back to the domain: a renameable subdomain
    # in a column whose only job is to match `connections.config.team.id`
    # matches nothing, and the disconnect rule would read every document on
    # this tenant as an orphan.
    (_CONFIG_WITHOUT_TEAM, "acme", None),
    # Neither available.
    (_CONFIG_WITHOUT_TEAM, None, None),
])
def test_sync_slack_stores_the_right_workspace_id_for_every_input_shape(
    catalog, monkeypatch, config, domain, expected
):
    """END TO END THROUGH THE REAL `sync_slack`, across the input shapes.

    Every other test here injects `team_id=` into `register_slack_catalog`
    directly, so the expression that DERIVES it was never executed under test
    — which is how the original source-string guard went unnoticed.

    PARAMETRISED, and that is the point rather than tidiness. With one config
    shape and an always-successful domain lookup, any caller-side edit that
    only misbehaves on a DIFFERENT input is invisible. Three such edits
    survived an earlier battery, all of them plausible enough to pass human
    review:

        team_id = team_id_from_config(config) or team_domain
        team_id = team_id_from_config(config) if team_domain else None
        team_id = team_id_from_config(config)[:4]

    The first is the original bug in disguise — it writes a renameable
    subdomain into the column on precisely the tenants whose config lacks a
    `team` key. The second silently stops recording whenever `team.info` is
    unreachable. The third is caught only because the fixture now uses a
    realistic id rather than a 4-character one.

    The stub runs the REAL `register_slack_catalog` synchronously so the
    assertion lands on the STORED COLUMN, not on the kwargs in between —
    stopping at the kwargs would leave the last hop uncrossed.
    """
    from app.connectors import slack_sync

    seen: dict = {}
    monkeypatch.setattr(
        slack_sync, "_get_company_token_and_config",
        lambda cid: ("xoxb-token", config, {"user_id": "u1"}),
    )
    monkeypatch.setattr(slack_sync, "fetch_users", lambda _t: {})
    monkeypatch.setattr(
        slack_sync, "fetch_channels", lambda _t: [{"id": "C1", "name": "general"}]
    )
    monkeypatch.setattr(
        slack_sync, "fetch_channel_history", lambda *a, **k: [
            {"ts": "1754000000.000100", "user": "U1", "text": "hello"}
        ],
    )
    monkeypatch.setattr(slack_sync, "_slack_team_domain", lambda _t: domain)
    monkeypatch.setattr(slack_sync, "_update_sync_status", lambda *a, **k: None)

    # `sync_slack` imports this INSIDE the function, so patching the module
    # attribute is what the local import resolves at call time.
    import app.kg_ingest.slack_extract as se

    monkeypatch.setattr(
        se, "kickoff_slack_extract",
        lambda cid, docs, **kw: (
            seen.update(kw), se.register_slack_catalog(cid, docs, **kw), True
        )[-1],
    )

    slack_sync.sync_slack("ds-team-id", company_id=catalog["cid"])

    assert seen, "sync_slack never reached the extraction kickoff"

    stored = _row(catalog, "C1")["provider_workspace_id"]
    assert stored == expected, (
        f"with config team={config.get('team')!r} and resolved domain "
        f"{domain!r}, the workspace id STORED ON THE CATALOG ROW was "
        f"{stored!r}, expected {expected!r}. It must be config['team']['id'] "
        "in full — never team.name, never the permalink subdomain, never a "
        "truncation, and never a domain fallback when the id is absent. A "
        "display name here matches nothing in connections.config.team.id, so "
        "the disconnect rule would treat every Slack document as an orphan."
    )
    # The distractors were genuinely present and genuinely different, so a
    # pass cannot come from two values coinciding.
    if expected is not None:
        assert _REAL_CONFIG["team"]["name"] != expected
        assert _REAL_CONFIG["team"]["domain"] != expected
        assert domain != expected


# ═════════════ 2. A promoted personal install keeps its catalog ════════════


def _seed_slack_connection(db, company_id, *, user_id, config, created_at):
    db.table("connections").insert({
        "id": f"conn-{user_id}",
        "company_id": company_id,
        "user_id": user_id,
        "provider": "slack",
        "status": "active",
        "scopes": "",
        "token_json_encrypted": "enc",
        "config": config,
        "created_at": created_at,
        "updated_at": created_at,
    }).execute()


def test_a_promoted_personal_install_keeps_the_same_workspace_id(catalog):
    """THE HAZARD #1119 REFUSED TO GUESS AT.

    A Slack row can be a personal install later promoted to serve the whole
    company, so purging a catalog when "the" connection goes away could delete
    one that is still live. Keying on the WORKSPACE rather than on the
    connection row makes that safe by construction.

    THIS TEST BUILDS THE SCENARIO ITS NAME CLAIMS. An earlier version did not:
    it called `register_slack_catalog` twice with the same `team_id` and
    asserted the value had not changed, which constructs no second install, no
    promotion, and no connection rows at all — it re-pinned "the argument I
    passed is the value I got back", and it survived every mutation only by
    hiding behind siblings that did the real work.

    So: TWO active per-user installs into ONE workspace, exactly as the
    promotion case has, with different selections so `resolve_company_slack_row`
    genuinely has to choose. The invariant the disconnect rule depends on is
    that the workspace id derived from EITHER row is the same — which is what
    makes "does any active connection still serve this workspace?" answerable
    without caring which connection was resolved.
    """
    from app.connectors.slack_company import resolve_company_slack_row, row_config
    from app.connectors.slack_sync import team_id_from_config

    db, company = catalog["db"], catalog["cid"]
    workspace = {"id": "T123", "name": "Acme", "domain": "acme"}

    # The original personal install, no selection saved.
    _seed_slack_connection(
        db, company, user_id="u-first",
        config={"team": dict(workspace)},
        created_at="2026-08-01T00:00:00+00:00",
    )
    # A second member's install into the SAME workspace, later promoted by
    # having the company's pull-channel selection saved against it.
    _seed_slack_connection(
        db, company, user_id="u-promoted",
        config={"team": dict(workspace), "sync_channel_ids": ["C1"]},
        created_at="2026-08-05T00:00:00+00:00",
    )

    resolved = resolve_company_slack_row(company)
    assert resolved is not None, "no company Slack row resolved"
    assert resolved.get("user_id") == "u-promoted", (
        "resolution did not pick the install carrying the selection — this "
        "test is no longer exercising the promotion case"
    )

    # The load-bearing property: whichever row is resolved, the workspace id
    # is identical, so a disconnect of either install leaves the other still
    # matching every catalog row written under it.
    # Read through `db.list_slack_connections`, the same accessor
    # `resolve_company_slack_row` uses — a raw select returns the jsonb
    # `config` column without the legacy `config_json` key `row_config`
    # reads, so it would silently yield {} for every row and the assertion
    # would pass on two Nones being equal.
    from app import db as app_db

    derived = {
        r["user_id"]: team_id_from_config(row_config(r))
        for r in app_db.list_slack_connections(company)
    }
    assert derived == {"u-first": "T123", "u-promoted": "T123"}, (
        f"the two installs derived different workspace ids ({derived}) — a "
        "disconnect rule keyed on this would delete a catalog still served by "
        "the surviving install"
    )
    assert team_id_from_config(row_config(resolved)) == "T123"


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
    # PRECONDITION, asserted rather than assumed: there is a known value to
    # clear. Without this the test could pass on NULL == NULL if the first
    # registration never stored anything.
    assert _row(catalog)["provider_workspace_id"] == _TEAM_ID

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
    assert len(catalog["summary_calls"]) == 1

    slack_extract.register_slack_catalog(
        company, [_doc(text="same")], team_id=_TEAM_ID,
    )

    # PATH PROOF. Without this the test passes whether or not the fixture
    # actually re-registers an UNCHANGED document: change the second body and
    # it silently takes the full upsert path, which writes the column anyway,
    # and this stops covering the short-circuit entirely. The summariser is
    # the observable that tells the two paths apart.
    assert len(catalog["summary_calls"]) == 1, (
        "a second summarisation ran, so this took the FULL registration path "
        "— the fixture is no longer re-registering an unchanged document. "
        "Check that both _doc() bodies are identical."
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
    # PRECONDITION: a DIFFERENT value is already stored, so the second call is
    # genuinely an overwrite attempt and not a fill.
    assert _row(catalog)["provider_workspace_id"] == "T_ORIGINAL"

    slack_extract.register_slack_catalog(
        company, [_doc(text="same")], team_id="T_DIFFERENT",
    )
    # PATH PROOF, same reasoning as the convergence test above.
    assert len(catalog["summary_calls"]) == 1, (
        "a second summarisation ran, so this took the FULL registration path "
        "— the fixture is no longer re-registering an unchanged document. "
        "Check that both _doc() bodies are identical."
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
