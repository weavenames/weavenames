"""CLI entry point for weavenames.

Phase 1 placeholder — full pipeline implementation lands in subsequent commits.
"""

import click


@click.command()
@click.argument("description", required=False)
@click.version_option()
def main(description: str | None) -> None:
    """Generate project names and check availability across registries."""
    if not description:
        click.echo("Weavenames v0.0.1 — pre-alpha. Pipeline implementation incoming.")
        click.echo("Usage: weavenames \"description of your project\"")
        return

    click.echo(f"Pipeline not yet implemented. Received: {description!r}")
    click.echo("Track progress: https://github.com/weavenames/weavenames")


if __name__ == "__main__":
    main()
