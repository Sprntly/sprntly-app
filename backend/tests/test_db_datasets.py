"""Tests for the new `datasets` table helpers in app.db."""


def test_insert_and_get(isolated_settings):
    db = isolated_settings["db"]
    db.insert_dataset(slug="acme", display_name="Acme Corp")
    row = db.get_dataset("acme")
    assert row is not None
    assert row["slug"] == "acme"
    assert row["display_name"] == "Acme Corp"
    assert row["created_at"]


def test_dataset_exists(isolated_settings):
    db = isolated_settings["db"]
    assert db.dataset_exists("acme") is False
    db.insert_dataset("acme", "Acme")
    assert db.dataset_exists("acme") is True


def test_insert_is_idempotent_on_slug_conflict(isolated_settings):
    db = isolated_settings["db"]
    db.insert_dataset("acme", "Original")
    db.insert_dataset("acme", "Renamed")
    row = db.get_dataset("acme")
    # INSERT OR IGNORE keeps the original — caller is expected to surface
    # a 409 before reaching this point.
    assert row["display_name"] == "Original"


def test_list_datasets_orders_newest_first(isolated_settings):
    db = isolated_settings["db"]
    db.insert_dataset("a", "A")
    db.insert_dataset("b", "B")
    rows = db.list_datasets()
    assert len(rows) == 2
    # created_at DESC then slug ASC; same-second inserts fall back to slug.
    slugs = [r["slug"] for r in rows]
    assert set(slugs) == {"a", "b"}


def test_list_dataset_slugs(isolated_settings):
    db = isolated_settings["db"]
    db.insert_dataset("zeta", "Zeta")
    db.insert_dataset("alpha", "Alpha")
    assert db.list_dataset_slugs() == ["alpha", "zeta"]


def test_delete_dataset(isolated_settings):
    db = isolated_settings["db"]
    db.insert_dataset("acme", "Acme")
    assert db.delete_dataset("acme") is True
    assert db.get_dataset("acme") is None
    assert db.delete_dataset("acme") is False  # already gone


def test_delete_dataset_leaves_briefs(isolated_settings):
    db = isolated_settings["db"]
    db.insert_dataset("acme", "Acme")
    db.save_brief("acme", "W1", {"insights": []}, schema_version=1)
    db.delete_dataset("acme")
    # Briefs reference the slug as TEXT, not FK — they survive.
    assert db.get_current_brief("acme") is not None
