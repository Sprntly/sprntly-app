#!/usr/bin/env python3
"""Benchmark ticket-generation latency: single vs fan-out, across concurrency.

Answers the practical question — *which lever actually reduces the wall-clock of
breaking a PRD into tickets?* — by timing the real generation paths against a PRD
markdown fixture (no DB, no persistence). It feeds the fixture straight into
`generate_from_input`, so the only external call is Anthropic.

  # single vs fan-out at the current gate, 3 runs each
  python scripts/bench_ticket_gen.py --env-file ~/Sprntly/backend/.env

  # sweep the concurrency gate to see how much fan-out needs it
  python scripts/bench_ticket_gen.py --env-file ~/Sprntly/backend/.env \
      --concurrency 3,6,8 --batch-size 4 --max-parallel 6 --runs 3

Requires a valid ANTHROPIC_API_KEY (from --env-file or the environment). The
platform key is used because the run binds no company (falsy enterprise_id →
`company_llm_key` no-op → platform key). Real API calls cost money — keep --runs
small.
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE = BACKEND / "tests" / "fixtures" / "ticket_gen" / "bench_prd.md"


def _load_env_file(path: Path) -> None:
    """Load ANTHROPIC_API_KEY (only) from a .env file into os.environ BEFORE any
    app import, so pydantic settings picks it up. We deliberately don't import
    the whole env — the bench needs no DB/Supabase/OpenAI config."""
    if not path.exists():
        sys.exit(f"env file not found: {path}")
    for line in path.read_text().splitlines():
        line = line.strip()
        if line.startswith("ANTHROPIC_API_KEY=") and "=" in line:
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip()
            return
    sys.exit(f"ANTHROPIC_API_KEY not found in {path}")


def _pctl(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[int(q) - 1]


def _fmt_ms(ms: float) -> str:
    return f"{ms / 1000:.1f}s"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env-file", type=Path, help="path to a .env with ANTHROPIC_API_KEY")
    ap.add_argument("--prd", type=Path, default=DEFAULT_FIXTURE, help="PRD markdown fixture")
    ap.add_argument("--runs", type=int, default=3, help="runs per config (median reported)")
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--max-parallel", type=int, default=6)
    ap.add_argument(
        "--concurrency", default="",
        help="comma list of LLM_MAX_CONCURRENCY values to sweep (default: leave as-is)",
    )
    ap.add_argument(
        "--strategies", default="single,fanout",
        help="comma list of strategies to run (single,fanout)",
    )
    args = ap.parse_args()

    if args.env_file:
        _load_env_file(args.env_file)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("no ANTHROPIC_API_KEY (pass --env-file or export it)")

    sys.path.insert(0, str(BACKEND))

    # Silence the per-call telemetry DB write so timing is pure LLM latency and
    # the bench needs no database. generate_from_input itself never persists.
    import app.graph.decision_log as _dl

    _dl.log_agent_decision = lambda *a, **k: None  # type: ignore[assignment]

    import app.llm as _llm
    from app.stories.generate import generate_from_input

    prd_input = args.prd.read_text()
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    conc_values = [int(c) for c in args.concurrency.split(",") if c.strip()] or [None]

    print(f"PRD fixture: {args.prd}  ({len(prd_input)} chars)")
    print(f"runs/config: {args.runs}   batch_size={args.batch_size}   "
          f"max_parallel={args.max_parallel}\n")

    header = f"{'strategy':<9} {'gate':>4} {'runs':>4} {'p50':>7} {'p90':>7} " \
             f"{'tickets':>7} {'calls':>5} {'out_tok':>8} {'$/run':>7}"
    print(header)
    print("-" * len(header))

    for conc in conc_values:
        if conc is not None:
            # Rebuild the process-wide gate so a new LLM_MAX_CONCURRENCY takes
            # effect (it's read once at import). _create_with_retries reads the
            # module global at call time, so reassigning it is enough.
            _llm._llm_gate = _llm._PriorityGate(conc, bg_cap=_llm._resolve_bg_cap())
        gate_n = conc if conc is not None else _llm._llm_gate._capacity

        for strat in strategies:
            walls: list[float] = []
            last: dict = {}
            for _ in range(args.runs):
                stats: dict = {}
                t0 = time.monotonic()
                stories = generate_from_input(
                    "",  # falsy company → platform key, no DB
                    prd_input=prd_input,
                    model=None,
                    strategy=strat,
                    batch_size=args.batch_size,
                    max_parallel=args.max_parallel,
                    stats_out=stats,
                )
                wall = (time.monotonic() - t0) * 1000
                walls.append(wall)
                last = stats
                last["_n_returned"] = len(stories)

            calls = last.get("calls", [])
            out_tok = sum(c.get("output_tokens", 0) for c in calls)
            cost = sum(c.get("cost_usd", 0.0) for c in calls)
            print(
                f"{strat:<9} {gate_n:>4} {args.runs:>4} "
                f"{_fmt_ms(_pctl(walls, 50)):>7} {_fmt_ms(_pctl(walls, 90)):>7} "
                f"{last.get('_n_returned', 0):>7} {len(calls):>5} "
                f"{out_tok:>8} {cost:>7.3f}"
            )
            # Per-phase detail for fan-out so the plan/enrich split is visible.
            if strat == "fanout" and calls:
                for c in calls:
                    print(f"    └ {c['label']:<10} {_fmt_ms(c.get('latency_ms', 0)):>7} "
                          f"out={c.get('output_tokens', 0):>6}")
        print()


if __name__ == "__main__":
    main()
