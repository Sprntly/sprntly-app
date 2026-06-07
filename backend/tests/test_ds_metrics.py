"""DS pilot analyses + Dashboard metrics service.

Covers: weekly aggregate computation from fake RawRecords, anomaly-detection
math (z-score + pct, config-driven thresholds), metric_points upsert
idempotency, Findings written as kg_signal + decision-log row, the series
endpoint shapes + range filtering, CSV export, and route auth (dep override).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.kg_ingest.types import RawRecord


# ─────────────────────────── helpers ───────────────────────────

def _facade(isolated_settings):
    from app.graph import GraphFacade
    return GraphFacade()


def _week(monday: str, days: int = 0) -> str:
    """ISO timestamp inside the given Monday's week."""
    d = date.fromisoformat(monday) + timedelta(days=days)
    return datetime(d.year, d.month, d.day, 12, tzinfo=timezone.utc).isoformat()


def _hubspot_deal(eid: str, amount, stage: str, monday: str) -> RawRecord:
    return RawRecord(
        provider="hubspot", kind="deal", external_id=eid, title=f"Deal {eid}",
        text="", properties={"amount_usd": amount, "stage": stage},
        timestamp=_week(monday),
    )


def _clickup_task(eid: str, status: str, monday: str, tags=None) -> RawRecord:
    return RawRecord(
        provider="clickup", kind="task", external_id=eid, title=f"Task {eid}",
        text="", properties={"status": status, "tags": tags or []},
        timestamp=_week(monday),
    )


def _seed_company(db, cid="ent-A"):
    db.table("companies").insert(
        {"id": cid, "slug": f"slug-{cid}", "display_name": "Acme"}
    ).execute()
    return cid


# ─────────────────────────── aggregate computation ───────────────────────────

def test_hubspot_aggregates_open_value_and_counts(isolated_settings):
    from app.ds import analyses

    recs = [
        _hubspot_deal("1", "1000", "appointmentscheduled", "2026-05-04"),
        _hubspot_deal("2", "500", "qualifiedtobuy", "2026-05-04"),
        _hubspot_deal("3", "9999", "closedwon", "2026-05-04"),   # closed → excluded
        _hubspot_deal("4", "200", "closedlost", "2026-05-04"),   # lost
    ]
    weekly = analyses.compute_weekly_aggregates("hubspot", recs)
    wk = date(2026, 5, 4)
    assert weekly[wk]["open_deal_value_usd"] == 1500.0
    assert weekly[wk]["deals_open_count"] == 2.0
    assert weekly[wk]["deals_lost_count"] == 1.0


def test_clickup_aggregates_open_closed_and_bugs(isolated_settings):
    from app.ds import analyses

    recs = [
        _clickup_task("1", "in progress", "2026-05-04"),
        _clickup_task("2", "open", "2026-05-04", tags=["bug"]),
        _clickup_task("3", "complete", "2026-05-04"),        # closed
        _clickup_task("4", "to do", "2026-05-04", tags=["defect"]),  # bug
    ]
    weekly = analyses.compute_weekly_aggregates("clickup", recs)
    wk = date(2026, 5, 4)
    assert weekly[wk]["tasks_open"] == 3.0
    assert weekly[wk]["tasks_closed_7d"] == 1.0
    assert weekly[wk]["bugs_open"] == 2.0


def test_fireflies_counts_meetings_per_week(isolated_settings):
    from app.ds import analyses

    recs = [
        RawRecord(provider="fireflies", kind="meeting", external_id=str(i),
                  title="m", text="", properties={}, timestamp=_week("2026-05-04"))
        for i in range(3)
    ] + [
        RawRecord(provider="fireflies", kind="meeting", external_id="x",
                  title="m", text="", properties={}, timestamp=_week("2026-05-11")),
    ]
    weekly = analyses.compute_weekly_aggregates("fireflies", recs)
    assert weekly[date(2026, 5, 4)]["meetings_7d"] == 3.0
    assert weekly[date(2026, 5, 11)]["meetings_7d"] == 1.0


def test_records_without_timestamp_are_skipped(isolated_settings):
    from app.ds import analyses

    recs = [RawRecord(provider="fireflies", kind="meeting", external_id="1",
                      title="m", text="", properties={}, timestamp=None)]
    assert analyses.compute_weekly_aggregates("fireflies", recs) == {}


# ─────────────────────────── anomaly math ───────────────────────────

def test_detect_anomaly_needs_min_points(isolated_settings):
    from app.ds import analyses

    pts = [("2026-04-06", 10.0), ("2026-04-13", 10.0), ("2026-04-20", 50.0)]
    # only 3 points < min_points=4 → no judgement
    assert analyses.detect_anomaly(
        pts, min_points=4, z_threshold=2.0, pct_threshold=0.3) is None


def test_detect_anomaly_flags_on_pct_change(isolated_settings):
    from app.ds import analyses

    # flat trailing mean of 10, latest 14 → +40% > 0.3 threshold
    pts = [("a", 10.0), ("b", 10.0), ("c", 10.0), ("d", 14.0)]
    finding = analyses.detect_anomaly(
        pts, min_points=4, z_threshold=99.0, pct_threshold=0.3)
    assert finding is not None
    assert finding["period"] == "d"
    assert finding["pct_change"] == pytest.approx(0.4)


def test_detect_anomaly_flags_on_zscore(isolated_settings):
    from app.ds import analyses

    # trailing 10,10,12 (mean ~10.67, std ~0.94); latest 14 → z ~3.5
    pts = [("a", 10.0), ("b", 10.0), ("c", 12.0), ("d", 14.0)]
    finding = analyses.detect_anomaly(
        pts, min_points=4, z_threshold=2.0, pct_threshold=99.0)
    assert finding is not None
    assert abs(finding["z"]) >= 2.0


def test_detect_anomaly_quiet_when_stable(isolated_settings):
    from app.ds import analyses

    pts = [("a", 10.0), ("b", 10.0), ("c", 10.0), ("d", 10.5)]
    assert analyses.detect_anomaly(
        pts, min_points=4, z_threshold=2.0, pct_threshold=0.3) is None


def test_thresholds_read_from_config(isolated_settings, monkeypatch):
    """A per-enterprise override loosens the pct threshold → a previously-quiet
    move now becomes a Finding via analyze_provider's config read."""
    from app.ds import analyses
    db = isolated_settings["supabase"]
    cid = _seed_company(db)
    # +5% move (10 → 10.5) is below the default 0.3 but above an override of 0.01.
    db.table("enterprise_config").insert({
        "enterprise_id": cid,
        "overrides": {"ds": {"anomaly": {"pct_threshold": 0.01}}},
    }).execute()
    recs = [
        RawRecord(provider="fireflies", kind="meeting", external_id=f"{i}-{wk}",
                  title="m", text="", properties={}, timestamp=_week(wk))
        for wk, n in [("2026-04-06", 10), ("2026-04-13", 10),
                      ("2026-04-20", 10), ("2026-04-27", 11)]
        for i in range(n)
    ]
    res = analyses.analyze_provider(_facade(isolated_settings), cid, "fireflies", records=recs)
    assert res["findings"] == 1
    assert res["anomalies"][0]["metric"] == "meetings_7d"


# ─────────────────────────── upsert idempotency ───────────────────────────

def test_metric_points_upsert_idempotent(isolated_settings):
    from app.ds import analyses
    from app.db.metric_points import list_metric_points

    db = isolated_settings["supabase"]
    cid = _seed_company(db)
    recs = [
        RawRecord(provider="fireflies", kind="meeting", external_id=str(i),
                  title="m", text="", properties={}, timestamp=_week("2026-05-04"))
        for i in range(2)
    ]
    facade = _facade(isolated_settings)
    analyses.analyze_provider(facade, cid, "fireflies", records=recs)
    analyses.analyze_provider(facade, cid, "fireflies", records=recs)  # re-run same week

    rows = list_metric_points(cid, metric="meetings_7d")
    assert len(rows) == 1                       # no duplicate row for the week
    assert rows[0]["value"] == 2.0


# ─────────────────────────── Findings + decision log ───────────────────────────

def test_findings_written_as_signals_and_decision_logged(isolated_settings):
    from app.ds import analyses

    db = isolated_settings["supabase"]
    cid = _seed_company(db)
    # 4 weeks of meetings; last week spikes → anomaly.
    recs = [
        RawRecord(provider="fireflies", kind="meeting", external_id=f"{i}-{wk}",
                  title="m", text="", properties={}, timestamp=_week(wk))
        for wk, n in [("2026-04-06", 5), ("2026-04-13", 5),
                      ("2026-04-20", 5), ("2026-04-27", 20)]
        for i in range(n)
    ]
    res = analyses.run_analyses(
        _facade(isolated_settings), cid, records_by_provider={"fireflies": recs})
    assert res["findings"] == 1

    sigs = db.table("kg_signal").select("*").eq("enterprise_id", cid).execute().data
    anomalies = [s for s in sigs if s["kind"] == "metric_anomaly"]
    assert len(anomalies) == 1
    props = anomalies[0]["properties"]
    if isinstance(props, str):
        props = json.loads(props)
    assert props["metric"] == "meetings_7d"
    assert "z" in props and "pct_change" in props and "period" in props
    assert anomalies[0]["source_type"] == "agent_inferred"

    logs = db.table("agent_decision_log").select("*").eq("enterprise_id", cid).execute().data
    runs = [l for l in logs if l["decision_type"] == "analyze"]
    assert len(runs) == 1 and runs[0]["agent"] == "ds"
    assert runs[0]["reasoning"] and "meetings_7d" in runs[0]["reasoning"]


def test_run_isolates_provider_errors(isolated_settings, monkeypatch):
    from app.ds import analyses

    db = isolated_settings["supabase"]
    cid = _seed_company(db)
    good = [RawRecord(provider="fireflies", kind="meeting", external_id="1",
                      title="m", text="", properties={}, timestamp=_week("2026-05-04"))]

    orig = analyses.compute_weekly_aggregates

    def boom(provider, records):
        if provider == "hubspot":
            raise RuntimeError("aggregator exploded")
        return orig(provider, records)

    monkeypatch.setattr(analyses, "compute_weekly_aggregates", boom)
    res = analyses.run_analyses(
        _facade(isolated_settings), cid,
        records_by_provider={"hubspot": [], "fireflies": good})
    assert "fireflies" in res["providers"]
    assert any("hubspot" in e for e in res["errors"])


# ─────────────────────────── route helpers (auth + dep override) ───────────────────────────

@pytest.fixture
def _override_company(isolated_settings, monkeypatch):
    """Override require_company on the metrics route + seed points, return cid."""
    import app.main as main_mod
    import app.routes.metrics as metrics_route
    from app.auth import CompanyContext

    db = isolated_settings["supabase"]
    cid = _seed_company(db, "co-X")
    require_company = metrics_route.require_company
    main_mod.app.dependency_overrides[require_company] = lambda: CompanyContext(
        company_id=cid, role="member", user_id="u1")
    yield cid
    main_mod.app.dependency_overrides.pop(require_company, None)


def _seed_points(cid):
    from app.db.metric_points import upsert_metric_point
    today = datetime.now(timezone.utc).date()
    base = today - timedelta(days=today.weekday())
    # 3 weekly points ending this week, plus one old point (>30d ago).
    for i, v in [(3, 5.0), (2, 6.0), (1, 9.0)]:
        upsert_metric_point(cid, "meetings_7d", base - timedelta(weeks=i), v, "fireflies")
    upsert_metric_point(cid, "meetings_7d", base - timedelta(days=120), 1.0, "fireflies")
    return base


def test_series_endpoint_shapes(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    _seed_points(cid)
    client = TestClient(main_mod.app)
    r = client.get("/v1/metrics/series?range=ytd")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["range"] == "ytd"
    m = next(x for x in body["metrics"] if x["metric"] == "meetings_7d")
    assert m["current"] == 9.0
    assert m["pct_change"] == pytest.approx(0.5)   # 9 vs 6
    assert [p["value"] for p in m["points"]][-1] == 9.0


def test_series_range_filters_old_points(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    _seed_points(cid)
    client = TestClient(main_mod.app)
    r = client.get("/v1/metrics/series?range=30d")
    m = next(x for x in r.json()["metrics"] if x["metric"] == "meetings_7d")
    # The 120-day-old point is excluded by the 30d range.
    assert len(m["points"]) == 3


def test_series_orders_kpi_tree_first(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    from app.db.metric_points import upsert_metric_point
    from app.kpi_tree import KpiTree, NorthStar, save_kpi_tree
    import app.main as main_mod

    cid = _override_company
    base = _seed_points(cid)
    # Add an alphabetically-earlier metric so default sort would lead with it.
    for i, v in [(2, 1.0), (1, 2.0)]:
        upsert_metric_point(cid, "aaa_metric", base - timedelta(weeks=i), v, "clickup")
    save_kpi_tree(cid, KpiTree(north_star=NorthStar(metric="meetings_7d")))

    client = TestClient(main_mod.app)
    r = client.get("/v1/metrics/series?range=ytd")
    names = [m["metric"] for m in r.json()["metrics"]]
    assert names[0] == "meetings_7d"   # north star leads despite alpha order


def test_export_csv_content_type_and_rows(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    cid = _override_company
    _seed_points(cid)
    client = TestClient(main_mod.app)
    r = client.get("/v1/metrics/export?range=ytd")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = [ln for ln in r.text.strip().splitlines() if ln]
    assert lines[0] == "metric,period_start,value"
    assert all("meetings_7d" in ln for ln in lines[1:])
    assert len(lines) - 1 == 4   # header + 4 weekly points (ytd keeps the old one)


def test_series_rejects_bad_range(isolated_settings, _override_company):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    client = TestClient(main_mod.app)
    r = client.get("/v1/metrics/series?range=nonsense")
    assert r.status_code == 400


def test_routes_require_auth(isolated_settings):
    from fastapi.testclient import TestClient
    import app.main as main_mod

    client = TestClient(main_mod.app)
    # No auth, no dep override → require_company rejects.
    assert client.get("/v1/metrics/series?range=30d").status_code in (401, 403)
    assert client.post("/v1/metrics/refresh").status_code in (401, 403)


def test_refresh_pulls_connected_provider(isolated_settings, _override_company, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main_mod
    import app.routes.metrics as metrics_route
    from app.db.metric_points import list_metric_points

    cid = _override_company
    # Pretend hubspot is connected; stub the connection read + token decrypt +
    # the puller so no network happens. 4 weeks with a spike → a Finding.
    monkeypatch.setattr(metrics_route.db, "get_connection",
                        lambda c, prov: {"token_json_encrypted": "x"} if prov == "hubspot" else None)
    monkeypatch.setattr(metrics_route, "decrypt_token_json",
                        lambda c: json.dumps({"access_token": "t"}))

    weeks = [("2026-04-06", 1), ("2026-04-13", 1), ("2026-04-20", 1), ("2026-04-27", 10)]
    deals = [
        _hubspot_deal(f"{wk}-{i}", "1000", "qualifiedtobuy", wk)
        for wk, n in weeks for i in range(n)
    ]
    monkeypatch.setitem(metrics_route.PULLERS, "hubspot",
                        (lambda token: iter(deals),) + metrics_route.PULLERS["hubspot"][1:])

    client = TestClient(main_mod.app)
    r = client.post("/v1/metrics/refresh")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["points_written"] >= 1
    # deals_open_count spiked 1→10 → a Finding.
    assert any(a["metric"] == "deals_open_count" for a in body["anomalies"])
    assert list_metric_points(cid, metric="deals_open_count")
