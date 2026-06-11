"""Live validation harness for the codebase map service.

Runs build_map against each configured repo through the GitHub App installation
and asserts the expected posture label, node count floor, required route
membership, resolved-edge count floor, and shell shape (brand, nav items, logo
render kind).

Usage (from backend/):
    export DESIGN_AGENT_MAP_CHECK_INSTALLATION_ID=<installation_id>
    python -m scripts.codebase_map_live_check
    # or: python scripts/codebase_map_live_check.py

Exit 0 iff all repos PASS. Non-zero on any failing assertion; the first
failing assertion per repo is named in the output.

Output prints identifiers and counts only — never file bodies or tokens.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Resolve installation_id from env (never hardcoded)
# ---------------------------------------------------------------------------
_INSTALL_ID_KEY = "DESIGN_AGENT_MAP_CHECK_INSTALLATION_ID"
_raw_install_id = os.environ.get(_INSTALL_ID_KEY, "").strip()
if not _raw_install_id:
    print(f"ERROR: env var {_INSTALL_ID_KEY} is not set", file=sys.stderr)
    sys.exit(1)
try:
    _DEFAULT_INSTALLATION_ID = int(_raw_install_id)
except ValueError:
    print(
        f"ERROR: {_INSTALL_ID_KEY} must be an integer, got: {_raw_install_id!r}",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Load expectations from sibling JSON (no secrets in that file)
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
_EXPECTATIONS_PATH = _HERE / "codebase_map_expectations.json"
try:
    with open(_EXPECTATIONS_PATH) as _fh:
        _EXPECTATIONS: dict = json.load(_fh)
except FileNotFoundError:
    print(f"ERROR: expectations file not found: {_EXPECTATIONS_PATH}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Service imports — after env is validated and expectations loaded
# ---------------------------------------------------------------------------
from app.design_agent.codebase_map.repo_reader import RepoSnapshot, read_repo  # noqa: E402
from app.design_agent.codebase_map.service import build_map  # noqa: E402
from app.design_agent.codebase_map.types import MapResult  # noqa: E402


# ---------------------------------------------------------------------------
# Core check function
# ---------------------------------------------------------------------------

def _check_repo(profile: dict) -> tuple[bool, list[str]]:
    """Check one repo against its expected profile. Returns (passed, failures)."""
    repo: str = profile["repo"]
    ref: Optional[str] = profile.get("ref")
    # per-repo installation_id override; falls back to the env default
    installation_id: int = int(profile.get("installation_id", _DEFAULT_INSTALLATION_ID))

    expected_posture: str = profile["expected_posture"]
    min_nodes: int = profile.get("min_nodes", 0)
    must_have_routes: list[str] = profile.get("must_have_routes", [])
    min_resolved_edges: int = profile.get("min_resolved_edges", 0)
    shell_cfg: dict = profile.get("shell", {})

    # call read_repo to capture the truncated flag from the snapshot
    snapshot: Optional[RepoSnapshot] = read_repo(installation_id, repo, ref)
    truncated: object = snapshot.truncated if snapshot is not None else "unknown"

    # time and call build_map (makes a second read_repo call internally)
    t0 = time.monotonic()
    result: Optional[MapResult] = build_map(installation_id, repo, ref)
    build_ms = int((time.monotonic() - t0) * 1000)

    failures: list[str] = []

    if result is None:
        failures.append("build_map returned None — snapshot fetch failed")
        _print_report_line(repo, None, truncated, build_ms, failures)
        return False, failures

    # posture — exact match
    if result.posture != expected_posture:
        failures.append(
            f"posture expected={expected_posture!r} actual={result.posture!r}"
        )

    # node count floor
    n_nodes = len(result.nodes)
    if n_nodes < min_nodes:
        failures.append(f"n_nodes={n_nodes} < min_nodes={min_nodes}")

    # must_have_routes — node set membership (component-name-derived routes)
    node_routes = {n.route for n in result.nodes}
    for route in must_have_routes:
        if route not in node_routes:
            failures.append(f"required route missing from node set: {route!r}")

    # resolved-edge floor
    n_resolved = sum(1 for e in result.edges if e.resolved)
    n_total_edges = len(result.edges)
    n_unresolved = len(result.unresolved)
    if n_resolved < min_resolved_edges:
        failures.append(
            f"resolved_edges={n_resolved} < min_resolved_edges={min_resolved_edges}"
        )

    # shell: brand non-empty
    if shell_cfg.get("brand_nonempty") and not result.shell.brand:
        failures.append("shell.brand is empty")

    # shell: nav-item count floor
    n_nav = len(result.shell.nav_items)
    min_nav: int = shell_cfg.get("min_nav_items", 0)
    if n_nav < min_nav:
        failures.append(f"shell.nav_items={n_nav} < min_nav_items={min_nav}")

    # shell: logo render_kind in allowed set (must not be absent)
    allowed_kinds: list[str] = shell_cfg.get("logo_render_kind_in", [])
    actual_kind: str = result.shell.logo.render_kind
    if allowed_kinds and actual_kind not in allowed_kinds:
        failures.append(
            f"logo.render_kind={actual_kind!r} not in allowed set {allowed_kinds}"
        )

    _print_report_line(repo, result, truncated, build_ms, failures)
    return len(failures) == 0, failures


def _print_report_line(
    repo: str,
    result: Optional[MapResult],
    truncated: object,
    build_ms: int,
    failures: list[str],
) -> None:
    if result is None:
        verdict = "FAIL"
        print(f"  {repo} · result=None · build_ms={build_ms} · {verdict}")
        for f in failures:
            print(f"    FAIL: {f}")
        return

    n_nodes = len(result.nodes)
    n_resolved = sum(1 for e in result.edges if e.resolved)
    n_total_edges = len(result.edges)
    n_unresolved = len(result.unresolved)
    brand = repr(result.shell.brand) if result.shell.brand else "(empty)"
    n_nav = len(result.shell.nav_items)
    logo_kind = result.shell.logo.render_kind
    verdict = "PASS" if not failures else "FAIL"

    print(
        f"  {repo}"
        f" · posture={result.posture}"
        f" · nodes={n_nodes}"
        f" · edges={n_resolved}/{n_total_edges} resolved"
        f" · unresolved={n_unresolved}"
        f" · brand={brand} nav={n_nav} logo={logo_kind}"
        f" · truncated={truncated}"
        f" · build_ms={build_ms}"
        f" · {verdict}"
    )
    if failures:
        for f in failures:
            print(f"    FAIL: {f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    profiles: list[dict] = _EXPECTATIONS.get("repos", [])
    if not profiles:
        print("ERROR: no repos found in expectations file", file=sys.stderr)
        return 1

    print(f"Checking {len(profiles)} repo(s) via installation {_DEFAULT_INSTALLATION_ID} ...")
    all_passed = True
    for profile in profiles:
        passed, _failures = _check_repo(profile)
        if not passed:
            all_passed = False

    if all_passed:
        print("ALL PASS")
        return 0
    else:
        print("FAILED — see assertions above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
