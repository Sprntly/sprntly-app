"""CLI for the ds-agent.

Usage:
    ds-agent gen-synthetic --output synthetic.csv [--users 15000]

The chat agent lives in `ds_agent.server`; there's no longer a local
`run` command because the analysis is now driven by Claude inside its
own sandbox rather than by a fixed Python pipeline. The legacy pipeline
is still importable from `ds_agent.legacy` if you need to A/B compare.
"""

from __future__ import annotations

from pathlib import Path

import click
from dotenv import load_dotenv

from . import synthetic


@click.group()
def main() -> None:
    """Sprntly data-science agent."""
    load_dotenv()


@main.command("gen-synthetic")
@click.option("--output", "output_path", required=True, type=click.Path(dir_okay=False), help="Where to write the CSV.")
@click.option("--users", default=15_000, show_default=True, type=int)
@click.option("--seed", default=42, show_default=True, type=int)
def gen_synthetic(output_path: str, users: int, seed: int) -> None:
    df = synthetic.generate(n_users=users, seed=seed)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
    click.echo(f"Wrote {len(df):,} rows to {output_path}")


if __name__ == "__main__":
    main()
