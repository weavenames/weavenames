"""CLI entry point for weavenames.

Usage:

    weavenames "open-source memory layer for AI agents"
    weavenames "..." --tlds .com,.dev,.io --top 30 --csv out.csv
    weavenames "..." --no-trademark --no-fastly --no-prompt

When invoked without arguments we print usage and version (this preserves
the smoke test in tests/test_smoke.py).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console

import weavenames
from weavenames.availability.domains import DEFAULT_TLDS
from weavenames.models import Candidate
from weavenames.pipeline import PipelineConfig, run_pipeline
from weavenames.profile import load as load_profile
from weavenames.profile import (
    record_accept,
    record_reject,
)
from weavenames.profile import save as save_profile
from weavenames.report import render_csv, render_markdown


def _parse_csv_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


def _parse_tlds(value: str | None) -> tuple[str, ...]:
    if not value:
        return DEFAULT_TLDS
    out: list[str] = []
    for raw in value.split(","):
        t = raw.strip()
        if not t:
            continue
        if not t.startswith("."):
            t = "." + t
        out.append(t)
    return tuple(out) if out else DEFAULT_TLDS


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument("description", required=False)
@click.option(
    "--keywords",
    default=None,
    help="Comma-separated stylistic anchor keywords.",
)
@click.option(
    "--tlds",
    default=None,
    help=f"Comma-separated TLDs (default: {','.join(DEFAULT_TLDS)}).",
)
@click.option("--top", "top_n", default=30, show_default=True, help="Top N to report.")
@click.option(
    "--target",
    "target_count",
    default=200,
    show_default=True,
    help="Number of LLM candidates to generate.",
)
@click.option(
    "--rank-rounds",
    default=4,
    show_default=True,
    help="Pairwise ELO rounds.",
)
@click.option("--csv", "csv_path", type=click.Path(), default=None, help="CSV export path.")
@click.option(
    "--output",
    "output_path",
    type=click.Path(),
    default=None,
    help="Markdown report path (default: stdout).",
)
@click.option("--no-prompt", is_flag=True, help="Skip interactive accept/reject prompt.")
@click.option("--no-trademark", is_flag=True, help="Skip USPTO trademark check.")
@click.option("--no-fastly", is_flag=True, help="Skip Fastly premium-domain refinement.")
@click.option(
    "--model",
    default="claude-haiku-4-5-20251001",
    show_default=True,
    help="Anthropic model for generation and ranking.",
)
@click.version_option(weavenames.__version__)
def main(  # noqa: PLR0913
    description: str | None,
    keywords: str | None,
    tlds: str | None,
    top_n: int,
    target_count: int,
    rank_rounds: int,
    csv_path: str | None,
    output_path: str | None,
    no_prompt: bool,
    no_trademark: bool,
    no_fastly: bool,
    model: str,
) -> None:
    """Generate project names and check availability across registries."""

    if not description:
        click.echo(f"Weavenames v{weavenames.__version__}")
        click.echo('Usage: weavenames "description of your project"')
        click.echo("Run `weavenames --help` for all options.")
        return

    cfg = PipelineConfig(
        description=description,
        keywords=_parse_csv_list(keywords),
        tlds=_parse_tlds(tlds),
        target_count=target_count,
        top_n_report=top_n,
        rank_rounds=rank_rounds,
        skip_trademark=no_trademark,
        skip_fastly=no_fastly,
        model=model,
        rank_model=model,
    )
    console = Console(stderr=True)
    top, tail = asyncio.run(run_pipeline(cfg, console=console))

    md = render_markdown(
        cfg.description,
        top,
        tail,
        keywords=cfg.keywords,
    )
    if output_path:
        Path(output_path).write_text(md, encoding="utf-8")
        console.log(f"[green]wrote[/green] {output_path}")
    else:
        click.echo(md)

    if csv_path:
        Path(csv_path).write_text(render_csv(top), encoding="utf-8")
        console.log(f"[green]wrote[/green] {csv_path}")

    if not no_prompt and top:
        _interactive_taste_update(top, console)


def _interactive_taste_update(top: list[Candidate], console: Console) -> None:
    """Walk the user through the top 10; record accepts/rejects in profile."""

    if not sys.stdin.isatty():
        return
    click.echo("\n--- Taste profile update ---", err=True)
    click.echo("For each name: a=accept, r=reject, s=skip, q=quit.", err=True)
    profile = load_profile()
    changed = False
    for c in top[:10]:
        try:
            answer = (
                click.prompt(
                    f"  {c.name} [a/r/s/q]",
                    default="s",
                    show_default=False,
                    err=True,
                )
                .strip()
                .lower()
            )
        except click.exceptions.Abort:
            break
        if answer == "q":
            break
        if answer == "a":
            record_accept(profile, c.name)
            changed = True
        elif answer == "r":
            try:
                reason = click.prompt(
                    "    reason (optional)",
                    default="",
                    show_default=False,
                    err=True,
                ).strip()
            except click.exceptions.Abort:
                reason = ""
            record_reject(profile, c.name, reason or None)
            changed = True
    if changed:
        path = save_profile(profile)
        console.log(f"[green]profile updated[/green] {path}")


if __name__ == "__main__":
    main()
