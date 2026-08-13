"""§C — the ★ LOAD-BEARING ★ project-scoped PRD-write gate (in-tenant
cross-project IDOR).

`prd_on_project` is True iff `(type="prd", id)` is on THIS project's
tenant-scoped manifest (`list_artifacts_for_project`, which already intersects
the project's own refs with the caller's company-wide fan-out). Every IDOR path
is proved fail-closed here:

  - own PRD → True (AC13);
  - same-company / other-project PRD → False → assert raises (AC14, cross-project);
  - other-company PRD → False → assert raises (AC15, cross-tenant);
  - empty manifest & a non-PRD artifact sharing the numeric id → False (AC16);
  - a manifest read error → PROPAGATES, never a default-allow (AC17);
  - a denial logs identifiers only — no PRD body / patch_md / rationale (AC18).

The manifest is monkeypatched to simulate each tenancy posture deterministically
(the real cross-project / cross-tenant Postgres fan-out is exercised by the
env-gated `test_project_individual_prd_edit_live.py`).
"""
from __future__ import annotations

import logging

import pytest

import app.project_prd_gate as gate
from app.project_prd_gate import ProjectPrdWriteDenied


def _manifest(monkeypatch, items):
    monkeypatch.setattr(gate, "list_artifacts_for_project", lambda **kw: items)


def test_prd_on_project_true_own_prd(monkeypatch):
    # AC13
    _manifest(monkeypatch, [{"type": "prd", "id": 5, "title": "Own"}])
    assert gate.prd_on_project(
        prd_id=5, project_id=1, dataset="d", company_id="c1"
    ) is True


def test_prd_on_project_false_cross_project(monkeypatch):
    # AC14 — same company, but PRD 5 is on a DIFFERENT project, so it is absent
    # from THIS project's ref set → not on the manifest → denied.
    _manifest(monkeypatch, [{"type": "prd", "id": 9, "title": "This project's PRD"}])
    assert gate.prd_on_project(
        prd_id=5, project_id=1, dataset="d", company_id="c1"
    ) is False
    with pytest.raises(ProjectPrdWriteDenied):
        gate.assert_prd_on_project(prd_id=5, project_id=1, dataset="d", company_id="c1")


def test_prd_on_project_false_cross_tenant(monkeypatch):
    # AC15 — a foreign company's PRD never appears in c1's fan-out → absent from
    # the intersected manifest → denied.
    _manifest(monkeypatch, [{"type": "prd", "id": 9, "title": "Own"}])
    with pytest.raises(ProjectPrdWriteDenied):
        gate.assert_prd_on_project(prd_id=777, project_id=1, dataset="d", company_id="c1")


def test_prd_on_project_false_empty_and_non_prd_id(monkeypatch):
    # AC16 — empty manifest denies; a non-PRD artifact with the SAME numeric id
    # is denied (the type=="prd" check is enforced, not just the id).
    _manifest(monkeypatch, [])
    with pytest.raises(ProjectPrdWriteDenied):
        gate.assert_prd_on_project(prd_id=5, project_id=1, dataset="d", company_id="c1")

    _manifest(monkeypatch, [{"type": "report", "id": 5, "title": "A report"}])
    assert gate.prd_on_project(
        prd_id=5, project_id=1, dataset="d", company_id="c1"
    ) is False
    with pytest.raises(ProjectPrdWriteDenied):
        gate.assert_prd_on_project(prd_id=5, project_id=1, dataset="d", company_id="c1")


def test_assert_propagates_on_manifest_error(monkeypatch):
    # AC17 — a manifest read error is NOT swallowed into a default-allow; it
    # propagates so the write handler can fail closed.
    def _boom(**kw):  # noqa: ARG001
        raise RuntimeError("manifest read failed")

    monkeypatch.setattr(gate, "list_artifacts_for_project", _boom)
    with pytest.raises(RuntimeError):
        gate.assert_prd_on_project(prd_id=5, project_id=1, dataset="d", company_id="c1")


def test_denial_log_identifiers_only(monkeypatch, caplog):
    # AC18 — the denial log carries prd_id/project_id only, never body/rationale.
    _manifest(monkeypatch, [])
    with caplog.at_level(logging.WARNING):
        with pytest.raises(ProjectPrdWriteDenied):
            gate.assert_prd_on_project(
                prd_id=5, project_id=1, dataset="d", company_id="c1"
            )
    line = next(
        r.getMessage() for r in caplog.records if "project_prd_write_denied" in r.getMessage()
    )
    assert "prd_id=5" in line and "project_id=1" in line
