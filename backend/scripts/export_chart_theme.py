"""Regenerate the chart-theme mirror consumed by the frontend.

    python scripts/export_chart_theme.py           # writes web/app/lib/chart-theme.json
    python scripts/export_chart_theme.py --check   # exits 1 if the file is stale

`app/charts/theme.py` is the single source of truth for chart styling; the JSON
mirror exists so Phase 2's `VegaChart.tsx` can hand the *identical* Vega config
to `vega-embed` instead of re-typing the theme in TypeScript and drifting from
it. `tests/test_charts_theme.py` runs the `--check` comparison on every CI run,
so a theme edit without a regenerated mirror fails the build rather than
shipping two themes.

Run it from `backend/` after any edit to `theme.py`.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.charts.theme import THEME_MIRROR_PATH, theme_json_bytes  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed mirror is stale",
    )
    args = parser.parse_args()

    expected = theme_json_bytes()
    current = (
        THEME_MIRROR_PATH.read_text(encoding="utf-8")
        if THEME_MIRROR_PATH.exists()
        else None
    )

    if args.check:
        if current == expected:
            print(f"up to date: {THEME_MIRROR_PATH}")
            return 0
        print(
            f"STALE: {THEME_MIRROR_PATH} does not match app/charts/theme.py\n"
            "  run: python scripts/export_chart_theme.py",
            file=sys.stderr,
        )
        return 1

    if current == expected:
        print(f"unchanged: {THEME_MIRROR_PATH}")
        return 0
    THEME_MIRROR_PATH.write_text(expected, encoding="utf-8")
    print(f"wrote {THEME_MIRROR_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
