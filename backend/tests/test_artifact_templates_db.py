"""Persistence for artifact format templates (app/db/artifact_templates.py,
migration 20260805120000_artifact_templates.sql).

Covered:
- insert / list / get-by-id round-trip, including the JSON and boolean decoding
  the caller shape depends on
- COMPANY ISOLATION: another tenant's identically named template is never
  returned, never updated, never deleted, never activated, and never resolves as
  the active one — the whole tenancy boundary for this table is the company_id
  filter in this module, because the backend holds the service-role key and RLS
  is bypassed
- workspace_id is STAMPED on every write and never appears in a query filter, so
  a template uploaded from one workspace is visible from another
- activate_template DEACTIVATES SIBLINGS BEFORE ACTIVATING — proven by showing
  the partial unique index really does refuse the other order
- a race inside that window surfaces as ActiveTemplateConflict (the route's 409)
- get_active_template picks the active row in Python, and answers None once the
  active row is deactivated or deleted
- set_compile_result LEAVES `compiled` STANDING when it moves a row to
  `compiling`, so an active format keeps serving its last good skeleton through
  a recompile instead of dropping the company to the built-in
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db.artifact_templates import (
    ActiveTemplateConflict,
    activate_template,
    deactivate_template,
    delete_template,
    get_active_template,
    get_template_by_id,
    insert_template,
    list_templates,
    set_compile_result,
    update_template,
)

_SOURCE = "# Acme PRD\n\n## Context\n\n## Requirements\n"


def _seed_company(db, company_id: str) -> None:
    """artifact_templates has no FK in the SQLite mirror, but companies rows
    keep the fixture honest about which ids are real tenants."""
    if not db.table("companies").select("id").eq("id", company_id).execute().data:
        db.table("companies").insert(
            {"id": company_id, "slug": f"acme-{company_id}", "display_name": "Acme"}
        ).execute()


def _add(company_id, *, name="Acme PRD v3", artifact_type="prd",
         workspace_id="ws-1", source_md=_SOURCE) -> dict:
    return insert_template(
        company_id=company_id,
        workspace_id=workspace_id,
        artifact_type=artifact_type,
        name=name,
        source_md=source_md,
        content_hash="abc123def456",
        uploader_id="user-1",
        uploader_name="Ada",
    )


def _ready(company_id, template_id) -> dict:
    """Bring a template to the one state activation accepts."""
    return set_compile_result(
        company_id=company_id,
        template_id=template_id,
        compile_status="ready",
        compiled="<html><style></style><h1>Acme PRD</h1></html>",
        section_map={"sections": [], "unmapped_house": [], "extra_sections": []},
        compile_notes=[],
    )


# ─── insert / read ───────────────────────────────────────────────────────────


def test_insert_lands_pending_and_inactive(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _add("co-1")

    assert row["name"] == "Acme PRD v3"
    assert row["artifact_type"] == "prd"
    # A brand-new format governs nothing until it compiles AND is activated.
    assert row["compile_status"] == "pending"
    assert row["is_active"] is False
    assert row["compiled"] == ""
    # JSON columns come back decoded, never as their storage text.
    assert row["section_map"] == {}
    assert row["compile_notes"] == []
    # source_chars is denormalised so the library list never selects source_md.
    assert row["source_chars"] == len(_SOURCE)
    assert row["content_hash"] == "abc123def456"


def test_list_is_newest_first_and_type_filtered(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    _add("co-1", name="Old PRD")
    _add("co-1", name="New PRD")
    _add("co-1", name="Ticket form", artifact_type="tickets")

    names = [r["name"] for r in list_templates("co-1")]
    assert names == ["Ticket form", "New PRD", "Old PRD"]
    assert [r["name"] for r in list_templates("co-1", "prd")] == ["New PRD", "Old PRD"]
    assert [r["name"] for r in list_templates("co-1", "tickets")] == ["Ticket form"]


def test_list_carries_the_notes_and_the_char_count(isolated_settings):
    # The library screen renders from the list alone: it needs the notes (for
    # the reason line and the "See all N" count) and source_chars, without
    # pulling every row's 50k-character source to get them.
    #
    # That the list SELECT actually narrows its columns is not observable here
    # — the SQLite fake ignores `.select(cols)` and always returns SELECT *
    # (tests/_fake_supabase.py:323). The narrowing is a real-Postgres property;
    # what this suite can and does guard instead is that the route's list
    # payload reads nothing outside _LIST_COLUMNS, which
    # test_artifact_templates_routes.py asserts directly.
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _add("co-1")
    set_compile_result(
        company_id="co-1", template_id=row["id"], compile_status="needs_review",
        compile_notes=[{"code": "missing_evidence_list", "message": "No evidence list."}],
    )
    listed = list_templates("co-1")[0]

    assert listed["compile_notes"] == [
        {"code": "missing_evidence_list", "message": "No evidence list."}
    ]
    assert listed["source_chars"] == len(_SOURCE)
    assert listed["compile_status"] == "needs_review"
    assert listed["is_active"] is False


def test_get_by_id_returns_the_full_row(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _add("co-1")
    full = get_template_by_id("co-1", row["id"])

    assert full["source_md"] == _SOURCE
    assert full["section_map"] == {}
    assert full["is_active"] is False


# ─── company isolation ───────────────────────────────────────────────────────


def test_another_companys_identical_template_is_never_returned(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    _seed_company(db, "co-2")
    mine = _add("co-1", name="Acme PRD v3")
    theirs = _add("co-2", name="Acme PRD v3")

    assert [r["id"] for r in list_templates("co-1")] == [mine["id"]]
    assert [r["id"] for r in list_templates("co-2")] == [theirs["id"]]
    # A foreign id resolves to nothing at all — indistinguishable from missing.
    assert get_template_by_id("co-1", theirs["id"]) is None
    assert get_template_by_id("co-2", mine["id"]) is None


def test_writes_never_reach_another_companys_row(isolated_settings):
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    _seed_company(db, "co-2")
    theirs = _add("co-2", name="Acme PRD v3")

    assert update_template(
        company_id="co-1", template_id=theirs["id"], name="Hijacked"
    ) is None
    assert set_compile_result(
        company_id="co-1", template_id=theirs["id"], compile_status="ready"
    ) is None
    assert activate_template("co-1", "prd", theirs["id"]) is None
    assert deactivate_template("co-1", theirs["id"]) is None
    assert delete_template("co-1", theirs["id"]) is None

    # Every attribute of the other tenant's row survives untouched.
    still = get_template_by_id("co-2", theirs["id"])
    assert still is not None
    assert still["name"] == "Acme PRD v3"
    assert still["compile_status"] == "pending"
    assert still["is_active"] is False


def test_activation_is_scoped_to_one_company_and_one_type(isolated_settings):
    # Two companies may each have an active PRD format, and one company may
    # have an active PRD format AND an active ticket format. The partial unique
    # index is on (company_id, artifact_type), not on either alone.
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    _seed_company(db, "co-2")
    a = _ready("co-1", _add("co-1", name="A")["id"])
    b = _ready("co-2", _add("co-2", name="B")["id"])
    t = _ready("co-1", _add("co-1", name="T", artifact_type="tickets")["id"])

    assert activate_template("co-1", "prd", a["id"])["is_active"] is True
    assert activate_template("co-2", "prd", b["id"])["is_active"] is True
    assert activate_template("co-1", "tickets", t["id"])["is_active"] is True

    assert get_active_template("co-1", "prd")["id"] == a["id"]
    assert get_active_template("co-2", "prd")["id"] == b["id"]
    assert get_active_template("co-1", "tickets")["id"] == t["id"]
    assert get_active_template("co-2", "tickets") is None


def test_activate_rejects_an_id_of_a_different_artifact_type(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    tickets = _ready("co-1", _add("co-1", artifact_type="tickets")["id"])

    # Claiming a ticket format is a PRD format resolves to nothing rather than
    # activating it into the wrong slot.
    assert activate_template("co-1", "prd", tickets["id"]) is None
    assert get_active_template("co-1", "prd") is None
    assert get_active_template("co-1", "tickets") is None


# ─── workspace_id is stamped, never filtered ─────────────────────────────────


def test_workspace_id_is_stamped_but_never_narrows_a_read(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    from_ws1 = _add("co-1", name="From ws1", workspace_id="ws-1")
    from_ws2 = _add("co-1", name="From ws2", workspace_id="ws-2")

    assert get_template_by_id("co-1", from_ws1["id"])["workspace_id"] == "ws-1"
    assert get_template_by_id("co-1", from_ws2["id"])["workspace_id"] == "ws-2"
    # Company-scoped library: both workspaces see both formats.
    assert {r["id"] for r in list_templates("co-1")} == {from_ws1["id"], from_ws2["id"]}
    # And a format uploaded from ws-1 is resolvable as active for the whole
    # company, not just for ws-1.
    _ready("co-1", from_ws1["id"])
    activate_template("co-1", "prd", from_ws1["id"])
    assert get_active_template("co-1", "prd")["id"] == from_ws1["id"]


# ─── activation order ────────────────────────────────────────────────────────


def test_activate_deactivates_the_outgoing_sibling(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    v2 = _ready("co-1", _add("co-1", name="Acme PRD v2")["id"])
    v3 = _ready("co-1", _add("co-1", name="Acme PRD v3")["id"])

    activate_template("co-1", "prd", v2["id"])
    activated = activate_template("co-1", "prd", v3["id"])

    assert activated["is_active"] is True
    assert get_template_by_id("co-1", v2["id"])["is_active"] is False
    assert get_active_template("co-1", "prd")["id"] == v3["id"]
    # The outgoing one stays in the library — switching back is a click.
    assert {r["id"] for r in list_templates("co-1")} == {v2["id"], v3["id"]}


def test_the_partial_unique_index_really_refuses_the_other_order(isolated_settings):
    # This is WHY activate_template deactivates first. Writing the second
    # is_active=true directly — the naive order — trips the constraint, so the
    # order in that function is load-bearing rather than stylistic.
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    a = _add("co-1", name="A")
    b = _add("co-1", name="B")
    activate_template("co-1", "prd", a["id"])

    with pytest.raises(Exception) as exc:
        (
            db.table("artifact_templates")
            .update({"is_active": True})
            .eq("company_id", "co-1")
            .eq("id", b["id"])
            .execute()
        )
    assert "unique" in str(exc.value).lower()


def _break_nth_update(monkeypatch, exc: Exception, n: int = 2, sticky: bool = False):
    """Make the Nth `update()` in app.db.artifact_templates raise `exc`.

    activate_template issues its updates in a fixed order — 1 deactivate the
    siblings, 2 activate the target, 3 (only on failure) put the outgoing one
    back — so N=2 injects a failure into exactly the non-transactional gap the
    rollback exists to close, and leaves update 3 free to run. `sticky=True`
    breaks update N and every one after it, which is how the rollback itself is
    made to fail. Everything else, including every SELECT, goes to the real fake
    client untouched.

    Returns a `restore()` that puts the module's own `require_client` back.
    Callers MUST use it rather than `monkeypatch.undo()`: `monkeypatch` is ONE
    function-scoped instance shared with `isolated_settings`, so undoing
    everything also un-wires the fake Supabase and the next read tries to reach
    the real project over the network (httpx.ConnectError, not a useful
    failure)."""
    import app.db.artifact_templates as mod

    real_client = mod.require_client()

    class _Query:
        """Fluent proxy: `update`/`execute` are intercepted, every other
        builder method is forwarded and re-wrapped so the chain stays fluent."""

        def __init__(self, inner, owner):
            self._inner = inner
            self._owner = owner
            self._doomed = False

        def update(self, patch):
            self._owner.updates += 1
            seen = self._owner.updates
            self._doomed = seen >= n if sticky else seen == n
            self._inner = self._inner.update(patch)
            return self

        def execute(self):
            if self._doomed:
                raise exc
            return self._inner.execute()

        def __getattr__(self, name):
            attr = getattr(self._inner, name)
            if not callable(attr):
                return attr

            def call(*a, **k):
                res = attr(*a, **k)
                return self if res is self._inner else res

            return call

    class _Client:
        def __init__(self):
            self.updates = 0

        def table(self, name):
            return _Query(real_client.table(name), self)

    original = mod.require_client
    monkeypatch.setattr(mod, "require_client", lambda: _Client())

    def restore() -> None:
        monkeypatch.setattr(mod, "require_client", original)

    return restore


def test_a_race_inside_the_activation_window_raises_the_409(isolated_settings, monkeypatch):
    """Another caller takes the active slot between the deactivate and the
    activate. Postgres answers 23505; the route turns this into a 409 telling
    the user to refresh, which is the only thing they can act on."""
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _ready("co-1", _add("co-1")["id"])
    _break_nth_update(
        monkeypatch,
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: index 'artifact_templates_active_uniq'"
        ),
    )

    with pytest.raises(ActiveTemplateConflict):
        activate_template("co-1", "prd", row["id"])


def test_a_failed_activation_puts_the_outgoing_format_back(
    isolated_settings, monkeypatch
):
    """The two statements are not in one transaction, so ANY failure of the
    second used to leave the company with zero active formats: v2 switched off,
    v3 never switched on, every future document silently back on the built-in,
    and the only signal a failed button click. The outgoing row is read before
    the deactivate and restored before the exception propagates."""
    _seed_company(isolated_settings["supabase"], "co-1")
    v2 = _ready("co-1", _add("co-1", name="Acme PRD v2")["id"])
    v3 = _ready("co-1", _add("co-1", name="Acme PRD v3")["id"])
    activate_template("co-1", "prd", v2["id"])

    # Not a unique violation — a dropped connection or a statement timeout,
    # which is the case the old `except` re-raised with nothing re-activated.
    restore = _break_nth_update(
        monkeypatch, sqlite3.OperationalError("connection lost")
    )

    with pytest.raises(sqlite3.OperationalError):
        activate_template("co-1", "prd", v3["id"])

    restore()  # read through the real fake client again
    active = get_active_template("co-1", "prd")
    assert active is not None, "the company was left with NO active PRD format"
    assert active["id"] == v2["id"]
    assert get_template_by_id("co-1", v3["id"])["is_active"] is False


def test_the_raced_409_also_leaves_an_active_format_behind(
    isolated_settings, monkeypatch
):
    """The 409 path exits through the same rollback. Here nobody actually holds
    the slot, so the restore succeeds and the company keeps v2; in a real race
    the winner holds it and the restore trips the same partial unique index and
    is swallowed, which is the correct outcome — the winner stays."""
    _seed_company(isolated_settings["supabase"], "co-1")
    v2 = _ready("co-1", _add("co-1", name="Acme PRD v2")["id"])
    v3 = _ready("co-1", _add("co-1", name="Acme PRD v3")["id"])
    activate_template("co-1", "prd", v2["id"])
    restore = _break_nth_update(
        monkeypatch,
        sqlite3.IntegrityError(
            "UNIQUE constraint failed: index 'artifact_templates_active_uniq'"
        ),
    )

    with pytest.raises(ActiveTemplateConflict):
        activate_template("co-1", "prd", v3["id"])

    restore()
    assert get_active_template("co-1", "prd")["id"] == v2["id"]


def test_a_rollback_that_itself_fails_is_swallowed_not_raised(
    isolated_settings, monkeypatch
):
    """The caller has to see the ORIGINAL failure. A rollback that cannot land
    (the winner of a real race already holds the slot) must not replace the
    exception the caller needs with one about the rollback."""
    _seed_company(isolated_settings["supabase"], "co-1")
    v2 = _ready("co-1", _add("co-1", name="Acme PRD v2")["id"])
    v3 = _ready("co-1", _add("co-1", name="Acme PRD v3")["id"])
    activate_template("co-1", "prd", v2["id"])
    # sticky: break the activate (update 2) AND the rollback (update 3), which
    # is what a real 23505 race looks like from the rollback's point of view.
    _break_nth_update(
        monkeypatch, sqlite3.OperationalError("still down"), n=2, sticky=True
    )

    import app.db.artifact_templates as mod

    original = mod._restore_active
    calls = []

    def _boom(c, company_id, prior_id):
        calls.append(prior_id)
        return original(c, company_id, prior_id)

    monkeypatch.setattr(mod, "_restore_active", _boom)

    with pytest.raises(sqlite3.OperationalError) as exc:
        activate_template("co-1", "prd", v3["id"])
    assert "still down" in str(exc.value)
    assert calls == [v2["id"]]


def test_deactivate_leaves_the_type_on_the_builtin(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _ready("co-1", _add("co-1")["id"])
    activate_template("co-1", "prd", row["id"])

    out = deactivate_template("co-1", row["id"])
    assert out["is_active"] is False
    assert get_active_template("co-1", "prd") is None
    # Idempotent: a double-click is not an error.
    assert deactivate_template("co-1", row["id"])["is_active"] is False


def test_deleting_the_active_template_leaves_no_active_row(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _ready("co-1", _add("co-1")["id"])
    activate_template("co-1", "prd", row["id"])

    deleted = delete_template("co-1", row["id"])
    assert deleted["is_active"] is True  # the caller needs this to say so
    assert get_active_template("co-1", "prd") is None
    assert list_templates("co-1") == []


# ─── compile results ─────────────────────────────────────────────────────────


def test_set_compile_result_keeps_the_last_good_skeleton_while_recompiling(
    isolated_settings,
):
    """The highest-consequence invariant in this module.

    An ACTIVE format being re-uploaded goes back to a non-ready status while it
    is rechecked. If that transition nulled `compiled`, every document the
    company generated for the duration of the recheck would silently come out
    in Sprntly's built-in format — and nobody would connect the two events."""
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _ready("co-1", _add("co-1")["id"])
    activate_template("co-1", "prd", row["id"])
    good = get_template_by_id("co-1", row["id"])["compiled"]
    assert good

    mid = set_compile_result(
        company_id="co-1", template_id=row["id"], compile_status="compiling",
    )
    assert mid["compile_status"] == "compiling"
    assert mid["compiled"] == good
    # Still the company's active format, so generation keeps using it.
    assert mid["is_active"] is True
    assert get_active_template("co-1", "prd")["compiled"] == good


def test_replacing_the_source_requeues_but_does_not_blank_the_skeleton(
    isolated_settings,
):
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _ready("co-1", _add("co-1")["id"])
    set_compile_result(
        company_id="co-1", template_id=row["id"], compile_status="needs_review",
        compile_notes=[{"code": "missing_hypothesis", "message": "No hypothesis."}],
    )

    updated = update_template(
        company_id="co-1", template_id=row["id"], source_md="# New form\n",
        content_hash="ffffffffffff",
    )
    assert updated["source_md"] == "# New form\n"
    assert updated["source_chars"] == len("# New form\n")
    assert updated["compile_status"] == "pending"
    # Notes describe the OLD text and are dropped; the skeleton is not.
    assert updated["compile_notes"] == []
    assert updated["compiled"]


def test_rename_touches_nothing_else(isolated_settings):
    _seed_company(isolated_settings["supabase"], "co-1")
    row = _ready("co-1", _add("co-1")["id"])
    activate_template("co-1", "prd", row["id"])

    renamed = update_template(company_id="co-1", template_id=row["id"], name="Renamed")
    assert renamed["name"] == "Renamed"
    assert renamed["source_md"] == _SOURCE
    # A rename is not a re-upload: the format stays checked and stays in use.
    assert renamed["compile_status"] == "ready"
    assert renamed["is_active"] is True
    # created_at is never patched, so the library order does not reshuffle.
    assert renamed["created_at"] == row["created_at"]
    # PROVENANCE. A patch that doesn't carry these must not move them — the
    # row's "Uploaded by Ada" line has to stay true after somebody else fixes a
    # typo in the name, and workspace_id has to keep saying where the format
    # CAME FROM or the migration's "a query change, not a backfill" promise is
    # worthless. (This asserts db.update_template's own contract; the bug this
    # guards actually lived one layer up, in store.edit_template — see
    # test_artifact_templates_routes.py::test_a_rename_by_someone_else_does_not
    # _rewrite_who_uploaded_it, which is the reproduction.)
    assert renamed["uploader_id"] == "user-1"
    assert renamed["uploader_name"] == "Ada"
    assert renamed["workspace_id"] == "ws-1"


def test_undecodable_json_degrades_to_empty_rather_than_raising(isolated_settings):
    # A hand-edited or half-written row must not take the library down with it.
    db = isolated_settings["supabase"]
    _seed_company(db, "co-1")
    row = _add("co-1")
    (
        db.table("artifact_templates")
        .update({"section_map": "{not json", "compile_notes": "also not json"})
        .eq("id", row["id"])
        .execute()
    )
    out = get_template_by_id("co-1", row["id"])
    assert out["section_map"] == {}
    assert out["compile_notes"] == []
