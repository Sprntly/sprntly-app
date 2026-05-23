"""CLI for the ds-agent.

Usage:
    ds-agent gen-synthetic --output synthetic.csv [--users 15000]
    ds-agent run --input data.csv --goal retention_30d \\
        [--business-model saas] [--top-k 10] [--no-llm] [--output result.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

from . import pipeline, synthetic


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


@main.command("run")
@click.option("--input", "input_path", required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--goal", "goal_metric", required=True, help="Target metric column, e.g. retention_30d.")
@click.option("--business-model", default="saas", show_default=True)
@click.option("--analytics-tool", default="csv", show_default=True)
@click.option("--top-k", default=10, show_default=True, type=int)
@click.option("--no-llm", is_flag=True, help="Skip the Anthropic narrative synthesis pass.")
@click.option("--output", "output_path", default=None, type=click.Path(dir_okay=False), help="Write JSON to file instead of stdout.")
def run_cmd(
    input_path: str,
    goal_metric: str,
    business_model: str,
    analytics_tool: str,
    top_k: int,
    no_llm: bool,
    output_path: str | None,
) -> None:
    result = pipeline.run(
        csv_path=input_path,
        goal_metric=goal_metric,
        business_model=business_model,
        analytics_tool=analytics_tool,
        synthesize=not no_llm,
        top_k=top_k,
    )
    text = json.dumps(result, default=str, indent=2)
    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(text)
        click.echo(f"Wrote {output_path}")
    else:
        sys.stdout.write(text + "\n")


if __name__ == "__main__":
    main()
