"""Lifespan-guard regression for the Design Agent startup invalidation block.

Prod incident (2026-05-30): the design-agent tables are provisioned out-of-band
via Supabase migrations that may not yet be applied in a given environment (e.g.
prod before the feature flag-flip). The lifespan calls design-agent invalidation
helpers that query those tables; against a prod Supabase missing them they raised
and took the entire API down (502 from the rollup until the guard landed).

There are TWO such design-agent calls in the lifespan:
  1. P1-07: invalidate_orphan_generating_prototypes() + invalidate_stale_prototypes(...)
  2. P3-06: invalidate_orphan_running_iterations()

Both must be wrapped in a SINGLE try/except so a missing table on EITHER path
logs a warning and startup CONTINUES — these are best-effort cleanup tasks and
the Design Agent stays dark behind NEXT_PUBLIC_DESIGN_AGENT_ENABLED regardless.

MUTATION CHECK: these tests are written so that un-guarding EITHER call would
fail them. Each test patches the helpers to raise (simulating the missing
table); if the guard were removed (or narrowed to only the prototypes block, or
only the iterations call), entering the lifespan would propagate the exception
and TestClient(main.app) startup would raise instead of serving /healthz → 200.
"""
from fastapi.testclient import TestClient


def _raise_missing_table(*args, **kwargs):  # noqa: ARG001
    # supabase-py surfaces a PostgREST "relation does not exist" as an exception.
    raise Exception('relation "prototypes" does not exist')


def _raise_missing_iterations_table(*args, **kwargs):  # noqa: ARG001
    raise Exception('relation "prototype_pending_iterations" does not exist')


def _patch_all_da_invalidations(monkeypatch, main_mod):
    """Make BOTH design-agent lifespan calls raise (missing-table simulation).

    Patches all three helper references that the guarded block invokes:
      - invalidate_orphan_generating_prototypes  (P1-07)
      - invalidate_stale_prototypes              (P1-07)
      - invalidate_orphan_running_iterations     (P3-06)
    With every one raising, the ONLY way startup survives is the single
    try/except wrapping the whole design-agent invalidation section.
    """
    monkeypatch.setattr(main_mod, "invalidate_orphan_generating_prototypes", _raise_missing_table)
    monkeypatch.setattr(main_mod, "invalidate_stale_prototypes", _raise_missing_table)
    monkeypatch.setattr(
        main_mod, "invalidate_orphan_running_iterations", _raise_missing_iterations_table
    )


def test_startup_survives_both_da_invalidations_failing(fake_llm, monkeypatch):
    """With the guard, BOTH design-agent invalidation calls raising must not crash startup.

    Mutation-proof: if a regression un-guarded the prototypes block OR the
    iterations call, the corresponding raise would propagate out of the lifespan
    and this `with TestClient(...)` would raise on enter instead of reaching the
    /healthz assertion.
    """
    import app.main as main_mod

    _patch_all_da_invalidations(monkeypatch, main_mod)

    # Entering the context manager fires the lifespan startup. Pre-guard this
    # raised; post-guard it is caught + logged and the API serves requests.
    with TestClient(main_mod.app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200


def test_startup_survives_iterations_call_failing_alone(fake_llm, monkeypatch):
    """Even if ONLY the P3-06 iterations call raises, startup must survive.

    This isolates the iterations call: the reference hotfix only covered the
    prototypes block, so a guard that wrapped only the prototypes calls (and left
    invalidate_orphan_running_iterations outside the try) would crash here. The
    prototypes helpers run for real against the isolated test DB.
    """
    import app.main as main_mod

    monkeypatch.setattr(
        main_mod, "invalidate_orphan_running_iterations", _raise_missing_iterations_table
    )

    with TestClient(main_mod.app) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200


def test_startup_logs_warning_when_invalidation_fails(fake_llm, monkeypatch, caplog):
    """The skipped invalidation is observable (warning), not silent."""
    import logging

    import app.main as main_mod

    _patch_all_da_invalidations(monkeypatch, main_mod)

    with caplog.at_level(logging.WARNING):
        with TestClient(main_mod.app):
            pass

    assert any(
        "Design Agent startup invalidation skipped" in rec.message
        for rec in caplog.records
    )
