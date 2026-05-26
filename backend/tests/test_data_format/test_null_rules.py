"""Each of the 8 null-handling rules from the spec, in isolation."""
from __future__ import annotations

from datetime import date

from app.data_format.null_rules import apply_null_rules


def _mk(uid, sd=date(2026, 1, 1), gm=1.0, **feats):
    base = {"user_id": uid, "signup_date": sd, "goal_metric": gm}
    base.update(feats)
    return base


def test_rule1_drops_null_user_id() -> None:
    rows = [_mk("u1"), _mk(None), _mk("")]
    cleaned, caveats = apply_null_rules(rows)
    assert len(cleaned) == 1
    assert cleaned[0]["user_id"] == "u1"
    assert any("null/empty user_id" in c for c in caveats)


def test_rule1_dedupes_user_id_keeps_earliest_signup() -> None:
    rows = [
        _mk("u1", sd=date(2026, 3, 1)),
        _mk("u1", sd=date(2026, 1, 1)),  # earlier — should win
        _mk("u1", sd=date(2026, 2, 1)),
    ]
    cleaned, caveats = apply_null_rules(rows)
    assert len(cleaned) == 1
    assert cleaned[0]["signup_date"] == date(2026, 1, 1)
    assert any("deduplicated" in c for c in caveats)


def test_rule2_drops_null_signup_date() -> None:
    rows = [_mk("u1"), _mk("u2", sd=None)]
    cleaned, caveats = apply_null_rules(rows)
    assert {r["user_id"] for r in cleaned} == {"u1"}
    assert any("null signup_date" in c for c in caveats)


def test_rule3_drops_null_goal_metric() -> None:
    rows = [_mk("u1", gm=1.0), _mk("u2", gm=None)]
    cleaned, caveats = apply_null_rules(rows)
    assert {r["user_id"] for r in cleaned} == {"u1"}
    assert any("null goal_metric" in c for c in caveats)


def test_rule4_drops_low_coverage_feature() -> None:
    # Need n large enough that we still have rows, but a feature with <50 unique-with-data.
    rows = [_mk(f"u{i}", f1=1.0, f2=(1.0 if i < 40 else None)) for i in range(200)]
    cleaned, caveats = apply_null_rules(rows)
    assert "f2" not in cleaned[0]
    assert "f1" in cleaned[0]
    assert any("<50 unique users" in c for c in caveats)


def test_rule5_drops_high_null_feature() -> None:
    # >30% null, but ≥50 users have data so it doesn't trigger rule 4.
    rows = []
    for i in range(200):
        v = 1.0 if i < 100 else None  # 50% null, 100 with data
        rows.append(_mk(f"u{i}", f1=v))
    cleaned, caveats = apply_null_rules(rows)
    assert "f1" not in cleaned[0]
    assert any(">30% null" in c for c in caveats)


def test_rule6_imputes_medium_null_and_flags_confidence() -> None:
    # ~20% null, ≥50 with data.
    rows = []
    for i in range(200):
        v = float(i) if i >= 40 else None  # 20% null
        rows.append(_mk(f"u{i}", f1=v))
    cleaned, caveats = apply_null_rules(rows)
    assert "f1" in cleaned[0]
    # First 40 rows had null — should be imputed to the median of the other 160.
    null_imputed_vals = {cleaned[i]["f1"] for i in range(40)}
    assert len(null_imputed_vals) == 1  # all imputed to same median
    assert any("confidence reduced by 0.10" in c for c in caveats)


def test_rule7_imputes_low_null_and_flags() -> None:
    rows = []
    for i in range(200):
        v = float(i) if i >= 10 else None  # 5% null
        rows.append(_mk(f"u{i}", f1=v))
    cleaned, caveats = apply_null_rules(rows)
    assert "f1" in cleaned[0]
    # Should have "imputed median" caveat without "confidence reduced".
    assert any("imputed median" in c and "confidence" not in c for c in caveats)


def test_rule8_all_zeros_caveat() -> None:
    rows = [_mk(f"u{i}", f1=0.0) for i in range(200)]
    cleaned, caveats = apply_null_rules(rows)
    assert "f1" in cleaned[0]
    assert any("no variation" in c for c in caveats)


def test_empty_input_returns_empty() -> None:
    cleaned, caveats = apply_null_rules([])
    assert cleaned == []
    assert caveats == []
