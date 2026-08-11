"""Catalog rows must not outlive the thing that created them.

THE LEAK. `document_catalog` rows are written from SEVEN places — uploads, the
uploads backfill, the Drive backfill, the Slack extractor, the Drive extractor,
the Confluence puller and chat attachments — and `deregister_document` was
called from exactly ONE of them, for uploads. Everything a connector registered
was therefore immortal, and the table recorded what had EVER been synced rather
than what is configured now.

WHY IT IS NOT MERELY UNTIDY, and why it lands in this suite rather than a
Slack one: a stale row is indexed to the model as a document the workspace
HAS, is rankable by topic, and — since document resolution shipped — can be
ASSERTED as the subject of a question. The body fetch then fails and the user
is told the contents could not be loaded, which reads as a transient problem
when the truth is that the document is not connected any more. The existence
contract and the resolution contract both depend on the catalog describing the
present, and nothing was keeping it there.

Measured on the shared database 2026-08-07, across six Slack tenants: one row
(`#cerebro-agent-escalations`) whose channel had been deselected, sitting three
days staler than its siblings while continuing to be offered as a document.

WHAT IS DELIBERATELY *NOT* HERE, because a reconciliation sweep is the obvious
fix and it is the wrong one: "delete every row this sync did not return"
deletes a tenant's entire catalog the first time an API call fails or a token
expires mid-enumeration, because a partial result is indistinguishable from a
shrunken one. `test_a_partial_sync_cannot_delete_anything` pins that this
design cannot degrade into that shape.
"""
from __future__ import annotations

import pytest

_CID = "co-dereg"
_OTHER_CID = "co-dereg-other"


def _seed_company(db, company_id=_CID):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": f"s-{company_id}", "display_name": "C"}
        ).execute()


def _seed_row(db, *, provider, external_id, title, company_id=_CID):
    _seed_company(db, company_id)
    db.table("document_catalog").insert({
        "company_id": company_id,
        "provider": provider,
        "external_id": external_id,
        "title": title,
        "source_name": "Slack",
        "content_hash": f"h-{external_id}",
        "summary": "s",
        "topics": [],
        "doc_date": "2026-08-02T10:00:00+00:00",
    }).execute()


def _ids(db, company_id=_CID, provider="slack"):
    rows = (
        db.table("document_catalog").select("external_id")
        .eq("company_id", company_id).eq("provider", provider).execute().data
    )
    return sorted(r["external_id"] for r in rows)


# ══════════════════════════ the accessor itself ════════════════════════════


def test_deregister_documents_drops_only_the_named_ids(isolated_settings):
    from app import document_catalog

    db = isolated_settings["supabase"]
    for cid, name in (("C1", "#general"), ("C2", "#support"), ("C3", "#random")):
        _seed_row(db, provider="slack", external_id=cid, title=name)

    n = document_catalog.deregister_documents(_CID, "slack", ["C2"])

    assert n == 1
    assert _ids(db) == ["C1", "C3"]


def test_deregister_documents_is_company_scoped(isolated_settings):
    """The tenancy property every accessor in this module carries. A channel
    id is not secret and two workspaces can legitimately hold the same one, so
    a deletion keyed on the id alone would reach across tenants."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    _seed_row(db, provider="slack", external_id="C1", title="#general")
    _seed_row(
        db, provider="slack", external_id="C1", title="#general",
        company_id=_OTHER_CID,
    )

    document_catalog.deregister_documents(_CID, "slack", ["C1"])

    assert _ids(db) == []
    assert _ids(db, company_id=_OTHER_CID) == ["C1"], (
        "another tenant's row was deleted by a shared channel id"
    )


def test_deregister_documents_is_provider_scoped(isolated_settings):
    """External ids are only unique WITHIN a provider. An upload whose id
    happened to equal a channel id must survive a Slack deregistration."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    _seed_row(db, provider="slack", external_id="C1", title="#general")
    _seed_row(db, provider="uploads", external_id="C1", title="notes.txt")

    document_catalog.deregister_documents(_CID, "slack", ["C1"])

    assert _ids(db, provider="slack") == []
    assert _ids(db, provider="uploads") == ["C1"]


@pytest.mark.parametrize("empty", [[], None, ["", "   "]])
def test_deregister_documents_with_nothing_to_do_issues_no_delete_at_all(
    isolated_settings, empty, monkeypatch
):
    """An empty or blank id list must return BEFORE touching the database.

    Asserted as "no delete was issued" rather than "the rows survived", and
    the difference is the whole value of the test. The fake Supabase client
    treats `.in_("external_id", [])` as matching nothing, so the surviving-rows
    version passed identically with the guard REMOVED — it demonstrated the
    fake's behaviour, not the code's. Real PostgREST is the environment that
    matters here and this suite cannot reach it, so the honest thing to pin is
    the mechanism we control: we do not send a delete we did not mean.

    It matters because the caller passes a list computed from a diff, which is
    empty on every save where the user changed nothing — the common path, not
    a rare one."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    _seed_row(db, provider="slack", external_id="C1", title="#general")
    _seed_row(db, provider="slack", external_id="C2", title="#support")

    deletes: list[str] = []
    real_table = document_catalog.require_client().table

    def _spy_table(name):
        handle = real_table(name)
        if name == "document_catalog":
            real_delete = handle.delete

            def _delete(*a, **kw):
                deletes.append(name)
                return real_delete(*a, **kw)

            handle.delete = _delete
        return handle

    monkeypatch.setattr(
        document_catalog, "require_client",
        lambda: type("C", (), {"table": staticmethod(_spy_table)})(),
    )

    n = document_catalog.deregister_documents(_CID, "slack", empty or [])

    assert n == 0
    assert deletes == [], (
        "an empty id list still issued a DELETE — with no id constraint to "
        "narrow it, that is a company/provider-wide wipe waiting on a client "
        "that treats an empty IN as unconstrained"
    )
    assert _ids(db) == ["C1", "C2"]


def test_deregister_documents_requires_a_company(isolated_settings):
    from app import document_catalog

    with pytest.raises(ValueError):
        document_catalog.deregister_documents("", "slack", ["C1"])


def test_deregistering_an_absent_id_is_silent(isolated_settings):
    """Idempotent: re-running a cleanup, or deselecting a channel that was
    never catalogued because its first sync failed, must not raise."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    _seed_row(db, provider="slack", external_id="C1", title="#general")

    assert document_catalog.deregister_documents(_CID, "slack", ["C9"]) == 1
    assert _ids(db) == ["C1"]


# ═══════════════ the design property, stated as a test ═════════════════════


def test_a_partial_sync_cannot_delete_anything(isolated_settings):
    """The reconciliation sweep this design refuses to be.

    The obvious fix for a leak like this is "after each sync, delete every row
    the sync did not return". It is wrong, and dangerously so: when Slack
    returns an auth error for one channel, or a token expires halfway through
    enumeration, the sync's result set is SHORT — and a short result is
    indistinguishable from a genuinely shrunken one. The sweep would delete a
    tenant's catalog on a transient failure.

    So deregistration takes an explicit id list produced by a USER ACTION, and
    nothing here consults a sync result. This test encodes that as behaviour
    rather than as a comment: a sync that returned only one of three channels
    changes nothing, because syncing is not a path that deletes."""
    from app import document_catalog
    from app.kg_ingest import slack_extract

    db = isolated_settings["supabase"]
    for cid, name in (("C1", "#general"), ("C2", "#support"), ("C3", "#random")):
        _seed_row(db, provider="slack", external_id=cid, title=name)

    # A sync that only managed to fetch ONE channel — the shape a mid-run auth
    # failure produces.
    slack_extract.register_slack_catalog(
        _CID,
        [slack_extract.SlackChannelDoc(
            channel_id="C1", channel_name="general",
            text="hello", latest_ts=None,
        )],
        team_domain="acme",
    )

    assert _ids(db) == ["C1", "C2", "C3"], (
        "a partial sync removed catalog rows — registration must never be a "
        "deletion path, however incomplete its input"
    )


def test_registration_still_has_exactly_one_deletion_counterpart(isolated_settings):
    """A guard on the shape of the fix rather than on one call.

    The leak existed because writers outnumbered removers seven to one and
    nothing said so. If a new connector starts registering documents, this
    test does not fail — but the assertion below names the removers, so the
    list has to be edited consciously when one is added, and a reviewer sees
    the imbalance in the diff."""
    from pathlib import Path

    import app

    # A source walk rather than a `grep` subprocess: grep also matches the
    # compiled .pyc files sitting beside the sources, which made this assert
    # on build artefacts rather than on code.
    app_root = Path(app.__file__).parent
    callers = {
        str(path.relative_to(app_root.parent))
        for path in app_root.rglob("*.py")
        if "deregister_document" in path.read_text(encoding="utf-8", errors="ignore")
    }
    assert callers == {
        "app/document_catalog.py",          # the definitions themselves
        "app/document_sources.py",          # uploads, on file delete
        "app/routes/connectors.py",         # Slack, on channel deselection
        "app/connector_lookup/slack_voc.py",  # prose reference only
        # Prose reference only, same as slack_voc above: `invalidate_catalog_cache`
        # names the write paths that must drop the planner's cached catalogs, and
        # `deregister_document` is one of them. This walk greps FILE TEXT, so a
        # comment naming the function reads as a caller — which is the correct
        # trade for a guard that would rather flag a mention than miss a real
        # removal path.
        "app/ask_planner.py",               # prose reference only
    }, (
        f"the set of modules that REMOVE catalog rows changed: {sorted(callers)}. "
        "Registration happens from seven places; if you added an eighth, it "
        "needs a removal path too, or its rows become immortal."
    )
