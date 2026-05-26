"""Tests for app.brief.comprehensive — DS + Synthesis composition.

Spec source: Master PRD §4.2 (Comprehensive tier ALWAYS) + Synthesis
§3.2 (11-step assembly).

The DS Agent isn't merged on this branch yet (PR #21), so we mock the
`ds_runner` callable directly. Synthesis is real — it runs end-to-end
against a SqliteBackend GraphFacade so the tenant isolation invariant
is also exercised here, not just in test_synthesis_brief_assembly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pytest

from app.graph import (
    GraphFacade,
    KpiTreeNode,
    ProvenanceTag,
    Signal,
    SignalSourceType,
    Workspace,
    WorkspaceStage,
    WorkspaceStrategy,
)
from app.graph.backends.sqlite_backend import SqliteBackend
from app.synthesis.brief_assembly import Brief


# ─────────────────────── fixtures + helpers ───────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def facade(tmp_path) -> GraphFacade:
    backend = SqliteBackend(db_path=str(tmp_path / "graph.db"))
    backend.initialize_schema()
    return GraphFacade(backend)


def _workspace(workspace_id: str = "ws-1") -> Workspace:
    now = _now()
    return Workspace(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        company_name="Acme",
        industry="SaaS",
        stage=WorkspaceStage.GROWTH,
        business_model="B2B SaaS",
        strategy=WorkspaceStrategy(
            okrs=["Increase activation"],
            current_priorities=["onboarding"],
            dead_ends=[],
        ),
        kpi_tree=[
            KpiTreeNode(
                name="Activation",
                role="north_star",
                target_value=0.5,
                current_value=0.3,
            )
        ],
        competitors=[],
        created_at=now - timedelta(days=1),
        updated_at=now,
    )


def _signal(
    sid: str,
    *,
    workspace_id: str = "ws-1",
    source_type: SignalSourceType = SignalSourceType.ANALYTICS,
    source_tool: str = "amplitude",
    content: str = "metric trended up 12% week-over-week",
) -> Signal:
    now = _now()
    return Signal(
        workspace_id=workspace_id,
        valid_at=now - timedelta(seconds=1),
        transaction_at=now,
        signal_id=sid,
        content=content,
        source_type=source_type,
        source_tool=source_tool,
        provenance_tag=ProvenanceTag.CONNECTOR_INGEST,
        confidence=0.8,
        stale_after=now + timedelta(days=30),
    )


def _rec(i: int, supporting: list[str]) -> dict[str, Any]:
    return {
        "title": f"Recommendation {i}",
        "claim": f"Ship feature {i}.",
        "signal_summary": f"Pattern {i} confirmed by mixed sources.",
        "hypothesis": f"Building feature {i} lifts activation.",
        "predicted_metric": "Activation",
        "predicted_impact_low": 2.0 + i,
        "predicted_impact_high": 6.0 + i,
        "predicted_impact_basis": "DS Comprehensive finding.",
        "supporting_signal_ids": supporting,
        "reversal_condition": f"Roll back feature {i} if metric drops.",
        "confidence": "high",
    }


def _llm_factory(recs: list[dict[str, Any]]):
    """Return a fake llm_call that emits a fixed list of recommendations."""
    calls: list[dict[str, Any]] = []

    def _fake(*, system: str, user: str, schema: dict | None = None, **kwargs):
        calls.append({"system": system, "user": user, "schema": schema, "kwargs": kwargs})
        return {"recommendations": recs}

    _fake.calls = calls  # type: ignore[attr-defined]
    return _fake


def _seed_workspace_with_signals(
    facade: GraphFacade, workspace_id: str = "ws-1", n: int = 4
) -> None:
    facade.write_workspace(workspace_id, _workspace(workspace_id))
    for i in range(n):
        # Mix source types so promotion (3+ signals, 2+ source_types)
        # works in the assembled hypotheses.
        st = (
            SignalSourceType.ANALYTICS
            if i % 2 == 0
            else SignalSourceType.CUSTOMER_VOICE
        )
        tool = "amplitude" if i % 2 == 0 else "zendesk"
        facade.write_signal(
            workspace_id,
            _signal(
                f"sig-{i}",
                workspace_id=workspace_id,
                source_type=st,
                source_tool=tool,
            ),
        )


# ─────────────────────── happy path ───────────────────────


def test_run_brief_comprehensive_happy_path(facade, isolated_settings):
    """Mocked DS runner + mocked LLM + real GraphFacade → Brief with
    3–5 recommendations, persisted + cached."""
    from app.brief.comprehensive import run_brief_comprehensive
    from app.brief.cache import get_cached_brief, week_start_iso

    _seed_workspace_with_signals(facade, "ws-1", n=4)

    ds_calls: list[Any] = []

    def fake_ds(user_table, goal_metric):
        ds_calls.append({"user_table": user_table, "goal_metric": goal_metric})
        return {
            "tier": "comprehensive",
            "findings": [
                {
                    "title": "DS-found step-3 drop",
                    "predicted_metric": "Activation",
                    "predicted_impact_low": 4.0,
                    "predicted_impact_high": 9.0,
                }
            ],
        }

    llm = _llm_factory([_rec(i, ["sig-0", "sig-1", "sig-2"]) for i in range(1, 5)])

    # data_format soft-deps aren't merged yet, so the wrapper returns
    # the permissive MEDIUM verdict and `rows = []`. Force MEDIUM-or-
    # better by stubbing `_assess_quality` so the happy path doesn't
    # short-circuit into INSUFFICIENT (empty rows → INSUFFICIENT by
    # default).
    with patch(
        "app.brief.comprehensive._assess_quality", return_value="MEDIUM"
    ):
        brief = run_brief_comprehensive(
            workspace_id="ws-1",
            dataset_slug="ws-1",
            graph=facade,
            llm_call=llm,
            ds_runner=fake_ds,
        )

    assert isinstance(brief, Brief)
    assert brief.workspace_id == "ws-1"
    assert 3 <= len(brief.recommendations) <= 5
    # DS runner was called with the resolved north_star metric.
    assert ds_calls and ds_calls[0]["goal_metric"] == "Activation"
    # LLM was called with the DS findings reaching it.
    assert llm.calls, "LLM should be called"
    assert "DS-found step-3 drop" in llm.calls[0]["user"]
    # Metadata records the DS contribution + tier.
    assert brief.metadata.get("ds_agent_tier") == "comprehensive"
    assert brief.metadata.get("ds_findings_count") == 1
    # Cache row exists for this week.
    cached = get_cached_brief("ws-1", week_start_iso())
    assert cached is not None
    assert cached["workspace_id"] == "ws-1"


# ─────────────────────── quality gate ───────────────────────


def test_quality_gate_insufficient_returns_degraded_brief(facade, isolated_settings):
    """INSUFFICIENT data quality → single data-quality warning recommendation
    + degraded flag in metadata + no DS / LLM calls."""
    from app.brief.comprehensive import run_brief_comprehensive

    _seed_workspace_with_signals(facade, "ws-1", n=2)

    def angry_ds(*args, **kwargs):
        raise AssertionError("DS must not be called when quality is INSUFFICIENT")

    def angry_llm(**kwargs):
        raise AssertionError("LLM must not be called when quality is INSUFFICIENT")

    with patch(
        "app.brief.comprehensive._assess_quality", return_value="INSUFFICIENT"
    ):
        brief = run_brief_comprehensive(
            workspace_id="ws-1",
            dataset_slug="ws-1",
            graph=facade,
            llm_call=angry_llm,
            ds_runner=angry_ds,
        )

    assert isinstance(brief, Brief)
    assert brief.metadata.get("degraded") is True
    assert brief.metadata.get("quality_verdict") == "INSUFFICIENT"
    assert len(brief.recommendations) == 1
    assert "data quality" in brief.recommendations[0].title.lower()
    assert any("insufficient" in c.lower() for c in brief.caveats)


def test_quality_gate_low_still_runs_and_annotates_caveat(facade, isolated_settings):
    """LOW data still runs the full pipeline; Brief.caveats records the
    low-quality warning."""
    from app.brief.comprehensive import run_brief_comprehensive

    _seed_workspace_with_signals(facade, "ws-1", n=4)
    llm = _llm_factory([_rec(1, ["sig-0", "sig-1", "sig-2"])])
    ds_called = {"count": 0}

    def fake_ds(user_table, goal_metric):
        ds_called["count"] += 1
        return {"findings": []}

    with patch(
        "app.brief.comprehensive._assess_quality", return_value="LOW"
    ):
        brief = run_brief_comprehensive(
            workspace_id="ws-1",
            dataset_slug="ws-1",
            graph=facade,
            llm_call=llm,
            ds_runner=fake_ds,
        )

    assert ds_called["count"] == 1
    assert llm.calls, "LLM should have been called for LOW quality"
    assert brief.metadata.get("quality_verdict") == "LOW"
    assert any("low data quality" in c.lower() for c in brief.caveats)
    assert len(brief.recommendations) >= 1


# ─────────────────────── cache ───────────────────────


def test_cache_hit_returns_cached_brief_without_rerunning_ds(
    facade, isolated_settings
):
    """Second call within the same week reads the cache; DS runner is
    never invoked the second time, LLM is never invoked the second time."""
    from app.brief.comprehensive import run_brief_comprehensive

    _seed_workspace_with_signals(facade, "ws-1", n=4)
    ds_counter = {"calls": 0}

    def fake_ds(user_table, goal_metric):
        ds_counter["calls"] += 1
        return {"findings": []}

    llm = _llm_factory([_rec(1, ["sig-0", "sig-1", "sig-2"])])

    with patch(
        "app.brief.comprehensive._assess_quality", return_value="MEDIUM"
    ):
        first = run_brief_comprehensive(
            workspace_id="ws-1",
            dataset_slug="ws-1",
            graph=facade,
            llm_call=llm,
            ds_runner=fake_ds,
        )
        # Second call must NOT trigger DS / LLM.
        ds_counter["calls"] = 0
        llm.calls.clear()  # type: ignore[attr-defined]
        second = run_brief_comprehensive(
            workspace_id="ws-1",
            dataset_slug="ws-1",
            graph=facade,
            llm_call=llm,
            ds_runner=fake_ds,
        )

    assert ds_counter["calls"] == 0, "DS should not re-run on cache hit"
    assert llm.calls == [], "LLM should not re-run on cache hit"
    assert second.brief_id == first.brief_id
    assert second.workspace_id == first.workspace_id


def test_cache_bypassed_when_use_cache_false(facade, isolated_settings):
    """use_cache=False forces re-runs (manual smoke-test path)."""
    from app.brief.comprehensive import run_brief_comprehensive

    _seed_workspace_with_signals(facade, "ws-1", n=4)
    ds_counter = {"calls": 0}

    def fake_ds(*args, **kwargs):
        ds_counter["calls"] += 1
        return {"findings": []}

    llm = _llm_factory([_rec(1, ["sig-0", "sig-1", "sig-2"])])

    with patch(
        "app.brief.comprehensive._assess_quality", return_value="MEDIUM"
    ):
        run_brief_comprehensive(
            "ws-1", "ws-1", facade, llm, fake_ds, use_cache=True
        )
        run_brief_comprehensive(
            "ws-1", "ws-1", facade, llm, fake_ds, use_cache=False
        )

    # Two real DS calls — the second bypassed the cache.
    assert ds_counter["calls"] == 2


def test_week_start_iso_anchors_on_monday():
    """Sanity: week_start_iso returns a Monday."""
    from app.brief.cache import week_start_iso

    # Pick a known Wednesday in UTC.
    weds = datetime(2026, 5, 27, 14, 0, 0, tzinfo=timezone.utc)
    monday_iso = week_start_iso(weds)
    parsed = datetime.fromisoformat(monday_iso)
    assert parsed.weekday() == 0  # Monday
    assert monday_iso == "2026-05-25"


# ─────────────────────── DS failure resilience ───────────────────────


def test_ds_runner_failure_does_not_abort_brief(facade, isolated_settings):
    """If DS raises, Synthesis still runs against an empty ds_output."""
    from app.brief.comprehensive import run_brief_comprehensive

    _seed_workspace_with_signals(facade, "ws-1", n=4)

    def explosive_ds(user_table, goal_metric):
        raise RuntimeError("DS Agent crashed mid-stage-3")

    llm = _llm_factory([_rec(1, ["sig-0", "sig-1", "sig-2"])])

    with patch(
        "app.brief.comprehensive._assess_quality", return_value="MEDIUM"
    ):
        brief = run_brief_comprehensive(
            workspace_id="ws-1",
            dataset_slug="ws-1",
            graph=facade,
            llm_call=llm,
            ds_runner=explosive_ds,
        )

    assert isinstance(brief, Brief)
    # Synthesis still generated recs from the signal pool.
    assert len(brief.recommendations) >= 1
    # ds_findings_count is 0 because DS blew up and we treated as {}.
    assert brief.metadata.get("ds_findings_count") == 0
    assert llm.calls, "LLM should still have been called"


# ─────────────────────── tenant isolation ───────────────────────


def test_tenant_isolation_brief_never_references_other_workspace(
    facade, isolated_settings
):
    """Workspace X's Brief must never reference workspace Y's signals,
    regardless of LLM payloads / DS findings."""
    from app.brief.comprehensive import run_brief_comprehensive

    # Tenant X — the one we run the Brief for.
    _seed_workspace_with_signals(facade, "ws-x", n=3)

    # Tenant Y — has a secret signal that must NOT leak.
    facade.write_workspace("ws-y", _workspace("ws-y"))
    facade.write_signal(
        "ws-y",
        _signal(
            "sig-y-secret",
            workspace_id="ws-y",
            content="SECRET-Y-only competitor strategy memo",
        ),
    )

    def fake_ds(user_table, goal_metric):
        return {"findings": []}

    llm = _llm_factory([_rec(1, ["sig-0", "sig-1", "sig-2"])])

    with patch(
        "app.brief.comprehensive._assess_quality", return_value="MEDIUM"
    ):
        brief = run_brief_comprehensive(
            workspace_id="ws-x",
            dataset_slug="ws-x",
            graph=facade,
            llm_call=llm,
            ds_runner=fake_ds,
        )

    # Brief is scoped to ws-x.
    assert brief.workspace_id == "ws-x"
    # No ws-y signal IDs anywhere in the Brief body.
    rec_blob = brief.model_dump_json()
    assert "sig-y-secret" not in rec_blob
    assert "SECRET-Y" not in rec_blob
    # LLM payload also clean.
    prompt = llm.calls[0]["user"]
    assert "sig-y-secret" not in prompt
    assert "SECRET-Y" not in prompt
    # Hypotheses written sit in ws-x only.
    x_hyps = facade._backend.all_entity_ids("ws-x")["hypotheses"]
    y_hyps = facade._backend.all_entity_ids("ws-y")["hypotheses"]
    assert len(x_hyps) >= 1
    assert len(y_hyps) == 0


# ─────────────────────── scheduler smoke test ───────────────────────


def test_scheduler_fans_out_to_explicit_workspace_list(facade, isolated_settings):
    """The Monday scheduler runs Comprehensive for every workspace in the
    list, skipping any that raise."""
    from app.brief.scheduler import run_monday_brief_for_all_workspaces

    _seed_workspace_with_signals(facade, "ws-a", n=3)
    _seed_workspace_with_signals(facade, "ws-b", n=3)

    def fake_ds(user_table, goal_metric):
        return {"findings": []}

    llm = _llm_factory([_rec(1, ["sig-0", "sig-1", "sig-2"])])

    with patch(
        "app.brief.comprehensive._assess_quality", return_value="MEDIUM"
    ):
        briefs = run_monday_brief_for_all_workspaces(
            graph=facade,
            llm_call=llm,
            ds_runner=fake_ds,
            workspaces=[("ws-a", "ws-a"), ("ws-b", "ws-b")],
        )

    assert {b.workspace_id for b in briefs} == {"ws-a", "ws-b"}
