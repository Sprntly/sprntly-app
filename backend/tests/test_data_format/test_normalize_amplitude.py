"""Amplitude normalizer: pivot ~50 events for 10 users → 10 CanonicalUserRows."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.data_format.normalizers.amplitude import normalize_amplitude
from app.data_format.schema import CanonicalUserRow


def _ts(d: datetime) -> int:
    return int(d.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _make_events():
    """10 users, ~5 events each, half come back in week 2 for retention=1."""
    events = []
    base = datetime(2026, 1, 1)
    for u in range(10):
        signup = base + timedelta(days=u)
        events.append(
            {
                "user_id": f"user-{u}",
                "event_type": "signup",
                "event_time": _ts(signup),
                "event_properties": {"session_length": 30 + u, "is_bot": False},
                "country": "US",
                "platform": "ios",
            }
        )
        events.append(
            {
                "user_id": f"user-{u}",
                "event_type": "view_home",
                "event_time": _ts(signup + timedelta(hours=1)),
                "event_properties": {"session_length": 20},
            }
        )
        events.append(
            {
                "user_id": f"user-{u}",
                "event_type": "click_cta",
                "event_time": _ts(signup + timedelta(hours=2)),
            }
        )
        # half come back on day-7 (within 30d retention window).
        if u % 2 == 0:
            events.append(
                {
                    "user_id": f"user-{u}",
                    "event_type": "return_visit",
                    "event_time": _ts(signup + timedelta(days=7)),
                }
            )
        # Half come back on day-40 (outside 30d window — should NOT count).
        else:
            events.append(
                {
                    "user_id": f"user-{u}",
                    "event_type": "return_visit",
                    "event_time": _ts(signup + timedelta(days=40)),
                }
            )
    return events


def test_normalize_amplitude_basic_shape() -> None:
    events = _make_events()
    rows = normalize_amplitude(events, goal_metric_window_days=30)
    assert len(rows) == 10
    assert all(isinstance(r, CanonicalUserRow) for r in rows)
    # Each row has a user_id like "user-N".
    assert {r.user_id for r in rows} == {f"user-{u}" for u in range(10)}


def test_normalize_amplitude_signup_date_is_min_event_time() -> None:
    events = _make_events()
    rows = normalize_amplitude(events, goal_metric_window_days=30)
    by_id = {r.user_id: r for r in rows}
    # user-0 first event is base datetime; signup_date = its date.
    assert by_id["user-0"].signup_date.year == 2026


def test_normalize_amplitude_retention_goal() -> None:
    events = _make_events()
    rows = normalize_amplitude(events, goal_metric_window_days=30)
    by_id = {r.user_id: r for r in rows}
    # Even-numbered users come back on day 7 → goal_metric == 1.
    assert by_id["user-0"].goal_metric == 1.0
    assert by_id["user-2"].goal_metric == 1.0
    # Odd users come back on day 40 → goal_metric == 0.
    assert by_id["user-1"].goal_metric == 0.0
    assert by_id["user-3"].goal_metric == 0.0


def test_normalize_amplitude_drops_event_types_under_50_users() -> None:
    # With only 10 users, NO event_type clears the 50-user threshold.
    events = _make_events()
    rows = normalize_amplitude(events, goal_metric_window_days=30)
    for r in rows:
        for f in r.features.keys():
            assert not f.startswith("event__"), (
                f"low-coverage event_type should have been dropped: {f}"
            )


def test_normalize_amplitude_keeps_event_types_at_50_user_threshold() -> None:
    # Scale up: 60 users, each with the same 3 event types — all 3 should be kept.
    events = []
    base = datetime(2026, 1, 1)
    for u in range(60):
        for et in ("signup", "view_home", "click_cta"):
            events.append(
                {
                    "user_id": f"u{u}",
                    "event_type": et,
                    "event_time": _ts(base + timedelta(days=u, minutes=hash(et) % 60)),
                }
            )
    rows = normalize_amplitude(events, goal_metric_window_days=30)
    assert len(rows) == 60
    expected = {"event__signup", "event__view_home", "event__click_cta"}
    for r in rows:
        assert expected.issubset(set(r.features.keys()))


def test_normalize_amplitude_numeric_properties_become_mean() -> None:
    events = [
        {
            "user_id": "uX",
            "event_type": "e",
            "event_time": _ts(datetime(2026, 1, 1)),
            "event_properties": {"x": 10},
        },
        {
            "user_id": "uX",
            "event_type": "e",
            "event_time": _ts(datetime(2026, 1, 2)),
            "event_properties": {"x": 30},
        },
    ]
    rows = normalize_amplitude(events)
    assert len(rows) == 1
    assert rows[0].features["prop__x_mean"] == 20.0


def test_normalize_amplitude_skips_null_user_id() -> None:
    events = [
        {"user_id": None, "event_type": "e", "event_time": _ts(datetime(2026, 1, 1))},
        {"user_id": "", "event_type": "e", "event_time": _ts(datetime(2026, 1, 1))},
        {"user_id": "real", "event_type": "e", "event_time": _ts(datetime(2026, 1, 1))},
    ]
    rows = normalize_amplitude(events)
    assert [r.user_id for r in rows] == ["real"]
