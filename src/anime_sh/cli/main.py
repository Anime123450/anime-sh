"""Typer entry point.

M0 exposes only what the skeleton can honestly back: ``doctor``, ``config``,
``providers``, and ``version``. Search/play arrive in M1 with the AniList
metadata source and the first real provider — stubbed here so the command
surface (and its ``--json`` contract) is fixed from the start.
"""

from __future__ import annotations

import json
import sys

import typer

from .. import __version__
from ..config import load_config
from ..config.loader import config_path
from ..infra import registry
from .doctor import run_doctor

app = typer.Typer(
    name="anime",
    help="anime-sh — the terminal-native anime client.",
    no_args_is_help=False,
    add_completion=False,
)

config_app = typer.Typer(help="View and edit configuration.")
providers_app = typer.Typer(help="Inspect installed provider plugins.")
app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """Bare ``anime`` will launch the TUI (M4). For now, show help."""
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


@app.command()
def version() -> None:
    """Print the anime-sh version."""
    typer.echo(f"anime-sh {__version__}")


@app.command()
def doctor() -> None:
    """Check the environment: player, ffmpeg, config, database, plugins."""
    raise typer.Exit(code=run_doctor())


@config_app.command("path")
def config_path_cmd() -> None:
    """Print the path to the config file."""
    typer.echo(str(config_path()))


@config_app.command("validate")
def config_validate() -> None:
    """Validate the config file; exit non-zero on error."""
    try:
        load_config()
    except Exception as e:
        typer.echo(f"invalid config: {e}", err=True)
        raise typer.Exit(code=1)
    typer.echo("config OK")


@providers_app.command("ls")
def providers_ls(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """List installed provider plugins."""
    providers = registry.load_providers()
    if as_json:
        json.dump(
            [{"name": p.name, "priority": getattr(p, "priority", 0)} for p in providers],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not providers:
        typer.echo("no providers installed (expected in M0)")
        return
    for p in providers:
        typer.echo(f"{p.name}\tpriority={getattr(p, 'priority', 0)}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
