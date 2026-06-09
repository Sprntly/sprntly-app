"""Tests for pipeline._stage_sync_connectors (issue #218 fix).

Pre-fix: the stage called `db.list_connections()` with no args. The db
helper requires `company_id`, so the call raised TypeError, the bare
`except Exception` swallowed it, and the stage reported "skipped /
no connections" — silently no-opping in every prod pipeline run for
the legacy engine path. (BRIEF_ENGINE=synthesis is the default so most
deploys didn't hit it, but BRIEF_ENGINE=legacy was a foot-gun.)

Post-fix: the stage resolves `dataset` slug → `company_id` (via
companies.slug column) and calls `db.list_connections(company_id)`.
Unknown slugs return a clean skipped status; real errors surface
instead of being silently swallowed.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


def test_stage_resolves_company_id_from_dataset_slug_and_passes_it():
    """The fix: list_connections is called with the resolved company_id,
    not with no args (the original bug)."""
    from app.pipeline import _stage_sync_connectors

    captured = {}

    def fake_list(company_id: str):
        captured["company_id"] = company_id
        return []  # empty → "no active connections" branch

    with patch(
        "app.pipeline.company_id_for_slug", return_value="co-abc123"
    ), patch("app.pipeline.db.list_connections", side_effect=fake_list):
        result = asyncio.run(_stage_sync_connectors("acme"))

    assert captured["company_id"] == "co-abc123"
    # Empty active list still returns the expected shape — not a crash.
    assert result["status"] == "skipped"


def test_stage_skips_cleanly_when_dataset_slug_has_no_company():
    """Unknown slug (mid-onboarding, legacy dataset row without a
    company) returns a typed skipped status — never crashes the
    pipeline cycle."""
    from app.pipeline import _stage_sync_connectors

    with patch("app.pipeline.company_id_for_slug", return_value=None), \
         patch("app.pipeline.db.list_connections") as mock_list:
        result = asyncio.run(_stage_sync_connectors("not-a-company"))

    assert result["status"] == "skipped"
    assert result["reason"] == "no_company_for_slug"
    mock_list.assert_not_called()  # we shouldn't even attempt the db read


def test_stage_does_not_silently_swallow_typeerror_on_list_connections(caplog):
    """Pre-fix, the broad except masked the broken signature. Post-fix,
    real errors must log loudly so future regressions surface — even if
    we still return a graceful status to the caller."""
    import logging

    from app.pipeline import _stage_sync_connectors

    def boom(company_id: str):
        raise TypeError("simulated db arity bug")

    with patch("app.pipeline.company_id_for_slug", return_value="co-x"), \
         patch("app.pipeline.db.list_connections", side_effect=boom), \
         caplog.at_level(logging.ERROR):
        result = asyncio.run(_stage_sync_connectors("acme"))

    # Status conveys failure (not the misleading "skipped/no connections").
    assert result["status"] == "error"
    # And the actual exception is in the log — silent failure is the regression.
    assert any(
        "simulated db arity bug" in rec.message
        or "simulated db arity bug" in str(rec.exc_info)
        for rec in caplog.records
    ), "the underlying error must be logged, not swallowed"
