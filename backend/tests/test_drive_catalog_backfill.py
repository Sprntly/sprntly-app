"""Google Drive's missing half of the document catalog.

THE MEASUREMENT THIS SUITE EXISTS FOR, taken against the shared database on
2026-08-07: `document_catalog` held 27 `confluence` rows across two tenants and
exactly ONE `google_drive` row — for a file that had been edited in Drive the
previous day. Every Drive file synced before the catalog shipped on 2026-08-03
was, and stays, invisible to it.

The cause is an asymmetry nobody wrote down. Confluence's puller re-reads every
page, so registration rides its next sync and the connector backfilled itself.
`drive_extract` only ever iterates files whose `modifiedTime` moved, and the
`register_document` call lives inside that loop — so a Drive file is
catalogued only when a human happens to edit it. `backfill_document_catalog.py`
asserted in its own docstring that BOTH connectors were "covered by their next
sync", which was true of one of them.

This matters far beyond tidiness: document resolution, topical selection and
the existence contract all read the catalog and nothing else. A Drive file
absent from it cannot be ranked, cannot be resolved, and — worst — is a file
the model will state the workspace does not have.

The suite covers the backfill that closes it (`document_sources
.backfill_drive_catalog`), and the one property that decides whether it is
safe to run: a file whose body cannot be located is NOT registered, because an
Index entry with nothing behind it is worse than an absent one.
"""
from __future__ import annotations

import pytest

_CID = "co-drive-bf"


@pytest.fixture
def stub_enrichment(monkeypatch):
    """Real registration, stubbed summariser + embeddings, with a call
    counter — the counter is what proves idempotence costs nothing."""
    from app import document_catalog

    calls = []
    monkeypatch.setattr(
        document_catalog, "llm_call",
        lambda **k: calls.append(k) or type("R", (), {"output": {
            "summary": "Enterprise billing moves from seats to usage in Q1.",
            "topics": ["billing", "usage-based pricing"],
        }})(),
    )
    monkeypatch.setattr(
        document_catalog, "embed_texts", lambda texts, **k: [[0.1] * 1536]
    )
    return calls


def _seed_company(db, company_id=_CID):
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": f"s-{company_id}", "display_name": "C"}
        ).execute()


def _seed_synced_drive_file(
    db, *, file_id, label="Billing model 2026", slug="acme",
    name="billing_model_2026.md", text="Enterprise billing moves to usage in Q1.",
    with_location=True,
):
    """A Drive file as it exists AFTER a sync that predates the catalog: the
    converted markdown on disk and the `kg_source` provenance row, and no
    `document_catalog` row at all.

    `with_location=False` reproduces the older shape where the markdown's
    location was never recorded — the body is unreachable, and that is the
    case the backfill must decline rather than guess at."""
    from app import document_bodies
    from app.datasets import dataset_path

    _seed_company(db)
    target = dataset_path(slug) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    config = {
        "file_id": file_id,
        "modified": "2026-07-30T09:00:00+00:00",
        "mime": "application/vnd.google-apps.document",
        "link": f"https://docs.google.com/document/d/{file_id}/edit",
    }
    if with_location:
        config.update({"md_dataset": slug, "md_file": name})
    db.table("kg_source").insert({
        "id": document_bodies.drive_source_id(_CID, file_id),
        "enterprise_id": _CID,
        "source_type": "google_drive",
        "label": label,
        "config": config,
        "status": "active",
    }).execute()
    return file_id


# ═══════════ The gap itself, asserted before the fix is asserted ═══════════


def test_a_synced_drive_file_is_absent_from_the_catalog_until_backfilled(
    isolated_settings
):
    """The production condition, reproduced: a Drive file fully synced — its
    markdown written, its provenance row present, its text readable — and no
    catalog row anywhere. This is what one row against twenty-seven looks like
    from inside a single tenant."""
    from app import document_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-a")

    assert document_catalog.fetch_document(
        _CID, document_catalog.PROVIDER_GOOGLE_DRIVE, "drive-a"
    ) is None
    assert document_catalog.list_documents(_CID) == []


def test_backfill_registers_a_synced_drive_file(isolated_settings, stub_enrichment):
    """And after the backfill it is a first-class catalog document: title,
    source, url, date and a summary, reachable by the same
    `fetch_document` every reader uses."""
    from app import document_catalog
    from app.document_sources import backfill_drive_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-a")

    counts = backfill_drive_catalog(_CID)

    assert counts["registered"] == 1
    doc = document_catalog.fetch_document(
        _CID, document_catalog.PROVIDER_GOOGLE_DRIVE, "drive-a"
    )
    assert doc is not None
    assert doc.title == "Billing model 2026"
    assert doc.source_name == "Google Drive"
    assert doc.summary
    assert doc.url == "https://docs.google.com/document/d/drive-a/edit"


def test_backfilled_drive_file_is_then_selectable_and_quotable(
    isolated_settings, stub_enrichment
):
    """The whole point, end to end. Before the backfill the workspace's
    document index does not contain this file at all — so an answer that
    denied its existence would be behaving exactly as designed. After it, the
    file is in the Index with its summary, and the body it points at is the
    markdown the sync wrote."""
    from app import ask_runner
    from app.document_sources import backfill_drive_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-a")

    before, before_manifest = ask_runner.document_grounding(
        _CID, "how is enterprise billing changing?"
    )
    assert "Billing model 2026" not in before
    assert before_manifest == []

    backfill_drive_catalog(_CID)

    after, after_manifest = ask_runner.document_grounding(
        _CID, "how is enterprise billing changing?"
    )
    assert "Billing model 2026" in after
    assert any(m["file_id"] == "google_drive:drive-a" for m in after_manifest)


# ═════════════════════ Safety: what it declines to do ══════════════════════


def test_a_file_with_no_locatable_body_is_not_registered(
    isolated_settings, stub_enrichment
):
    """The decision this backfill exists to get right. A Drive file whose
    markdown location was never recorded has no readable body, and
    registering it anyway would put a title in the Index that resolution can
    pick as a referent and grounding can never quote.

    Counted as `no_body`, apart from `errors`: for an older tenant this is the
    expected outcome, not a fault, and folding it into an error count would
    hide how much of that tenant's Drive is genuinely reachable."""
    from app import document_catalog
    from app.document_sources import backfill_drive_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-lost", with_location=False)

    counts = backfill_drive_catalog(_CID)

    assert counts == {"registered": 0, "skipped": 0, "no_body": 1, "errors": 0}
    assert document_catalog.fetch_document(
        _CID, document_catalog.PROVIDER_GOOGLE_DRIVE, "drive-lost"
    ) is None


def test_dry_run_writes_nothing_and_counts_the_same_work(
    isolated_settings, stub_enrichment
):
    """`apply=False` runs the identical decision path — same hash check, same
    body check — and stops before the write. Counting through the real
    function rather than a parallel estimator is what stops a dry run and an
    apply disagreeing about what would happen."""
    from app import document_catalog
    from app.document_sources import backfill_drive_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-a")

    planned = backfill_drive_catalog(_CID, apply=False)

    assert planned["registered"] == 1
    assert document_catalog.fetch_document(
        _CID, document_catalog.PROVIDER_GOOGLE_DRIVE, "drive-a"
    ) is None
    assert stub_enrichment == [], "a dry run must not pay for a summary"

    applied = backfill_drive_catalog(_CID, apply=True)
    assert applied["registered"] == planned["registered"]


def test_backfill_is_idempotent_by_content_hash(isolated_settings, stub_enrichment):
    """A second run finds every hash unchanged, registers nothing and pays for
    no summaries. Idempotence by hash rather than by a "backfilled" flag is
    what makes it safe to re-run after a partial failure."""
    from app.document_sources import backfill_drive_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-a")

    first = backfill_drive_catalog(_CID)
    summaries_after_first = len(stub_enrichment)
    second = backfill_drive_catalog(_CID)

    assert first["registered"] == 1
    assert second == {"registered": 0, "skipped": 1, "no_body": 0, "errors": 0}
    assert len(stub_enrichment) == summaries_after_first


def test_one_broken_file_does_not_strand_the_rest(isolated_settings, stub_enrichment):
    """Per-file isolation. A tenant's Drive is backfilled as far as it can be,
    and the unreachable file is reported rather than taking the run down —
    the alternative is that one stale provenance row keeps a whole tenant
    uncatalogued."""
    from app.document_sources import backfill_drive_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-lost", with_location=False)
    _seed_synced_drive_file(
        db, file_id="drive-ok", label="Pricing model 2026",
        name="pricing_model_2026.md", text="Pricing moves to usage.",
    )

    counts = backfill_drive_catalog(_CID)

    assert counts["registered"] == 1
    assert counts["no_body"] == 1
    assert counts["errors"] == 0


def test_backfill_never_calls_google(isolated_settings, stub_enrichment, monkeypatch):
    """Bodies come from the corpus markdown the sync already wrote. No Drive
    API, no OAuth, no token-refresh race, no quota — which is what makes this
    runnable against a live tenant without coordinating with the connector."""
    from app import document_bodies
    from app.document_sources import backfill_drive_catalog

    db = isolated_settings["supabase"]
    _seed_synced_drive_file(db, file_id="drive-a")

    reads = []
    real_resolve = document_bodies.resolve_drive_body

    def _spy(company_id, file_id):
        reads.append(file_id)
        return real_resolve(company_id, file_id)

    monkeypatch.setattr(document_bodies, "resolve_drive_body", _spy)
    for forbidden in ("open_session", "fetch_file", "list_files"):
        monkeypatch.setattr(
            "app.connectors.google_drive_sync." + forbidden,
            lambda *a, **k: pytest.fail(
                f"backfill called google_drive_sync.{forbidden}"
            ),
            raising=False,
        )

    backfill_drive_catalog(_CID)

    assert reads == ["drive-a"]
