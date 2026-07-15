"""Typer entry point.

The CLI is one adapter onto the app services and holds no domain logic. Bare
``anime <query>`` is sugar for ``anime play <query>`` (rewritten in ``main``).
Bare ``anime`` with no args will launch the TUI (M4); for now it shows help.
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from .. import __version__
from ..config import load_config
from ..config.loader import config_path
from ..domain.errors import AnimeShError
from ..domain.models import Audio
from ..infra import registry
from .container import build_container
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

console = Console()
err = Console(stderr=True)

KNOWN_COMMANDS = {
    "version", "doctor", "config", "providers", "search", "play", "trending",
}


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())


# --------------------------------------------------------------------------- #
# Simple commands
# --------------------------------------------------------------------------- #
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
    typer.echo(str(config_path()))


@config_app.command("validate")
def config_validate() -> None:
    try:
        load_config()
    except Exception as e:
        err.print(f"[red]invalid config:[/] {e}")
        raise typer.Exit(code=1)
    typer.echo("config OK")


@providers_app.command("ls")
def providers_ls(as_json: bool = typer.Option(False, "--json")) -> None:
    providers = registry.load_providers()
    if as_json:
        json.dump([{"name": p.name, "priority": getattr(p, "priority", 0)} for p in providers], sys.stdout)
        sys.stdout.write("\n")
        return
    if not providers:
        typer.echo("no providers installed")
        return
    for p in providers:
        typer.echo(f"{p.name}\tpriority={getattr(p, 'priority', 0)}")


# --------------------------------------------------------------------------- #
# Metadata-driven commands
# --------------------------------------------------------------------------- #
@app.command()
def search(
    query: str = typer.Argument(..., help="Title to search for."),
    limit: int = typer.Option(20, "-n", "--limit"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Search AniList for anime (no providers touched — instant)."""
    asyncio.run(_search(query, limit, as_json))


@app.command()
def trending(
    limit: int = typer.Option(20, "-n", "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show trending anime from AniList."""
    asyncio.run(_trending(limit, as_json))


@app.command()
def play(
    query: str = typer.Argument(..., help="Title to play."),
    episode: float = typer.Option(1.0, "-e", "--episode", help="Episode number."),
    dub: bool = typer.Option(False, "--dub", help="Prefer dubbed audio."),
    quality: str = typer.Option(None, "-q", "--quality", help="best|1080p|720p|480p|worst"),
    resolve_only: bool = typer.Option(
        False, "--json", help="Resolve the stream and print it as JSON; do not launch."
    ),
) -> None:
    """Search for a title, pick the best match, and play an episode."""
    asyncio.run(_play(query, episode, dub, quality, resolve_only))


# --------------------------------------------------------------------------- #
# Async implementations
# --------------------------------------------------------------------------- #
async def _search(query: str, limit: int, as_json: bool) -> None:
    c = build_container()
    try:
        results = await c.search.search(query, limit=limit)
    finally:
        await c.aclose()

    if as_json:
        json.dump([_anime_dict(r.anime) for r in results], sys.stdout)
        sys.stdout.write("\n")
        return
    _print_anime_table(f"Results for {query!r}", [r.anime for r in results])


async def _trending(limit: int, as_json: bool) -> None:
    c = build_container()
    try:
        animes = await c.metadata.trending(limit=limit)
    finally:
        await c.aclose()
    if as_json:
        json.dump([_anime_dict(a) for a in animes], sys.stdout)
        sys.stdout.write("\n")
        return
    _print_anime_table("Trending", animes)


async def _play(query, episode, dub, quality, resolve_only) -> None:
    config = load_config()
    if quality:
        config.playback.quality = quality
    c = build_container(config)
    audio = Audio.DUB if (dub or config.playback.audio == "dub") else Audio.SUB
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        err.print(f"[cyan]▶[/] {anime.title.preferred} — Episode {episode:g} ({audio.value.lower()})")

        if resolve_only:
            resolved = await c.playback.resolve(anime, episode, audio=audio)
            json.dump(
                {
                    "anime": _anime_dict(anime),
                    "episode": episode,
                    "stream": {
                        "url": resolved.stream.url,
                        "kind": resolved.stream.kind.value,
                        "quality": resolved.stream.quality.value,
                    },
                    "resume_s": resolved.resume_s,
                },
                sys.stdout,
            )
            sys.stdout.write("\n")
        else:
            err.print("[dim]Searching providers and resolving stream…[/]")
            await c.playback.play_and_track(anime, episode, audio=audio)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
def _anime_dict(a) -> dict:
    return {
        "anilist_id": a.id.anilist,
        "mal_id": a.id.mal,
        "title": a.title.preferred,
        "romaji": a.title.romaji,
        "format": a.format.value,
        "status": a.status.value,
        "episodes": a.episode_count,
        "season": a.season.value if a.season else None,
        "year": a.year,
        "genres": list(a.genres),
    }


def _print_anime_table(heading: str, animes: list) -> None:
    table = Table(title=heading, title_justify="left", header_style="bold cyan")
    table.add_column("Title", style="bold")
    table.add_column("Format")
    table.add_column("Eps", justify="right")
    table.add_column("Season")
    table.add_column("Status")
    for a in animes:
        table.add_row(
            a.title.preferred,
            a.format.value,
            str(a.episode_count or "—"),
            f"{a.season.value.title()} {a.year}" if a.season and a.year else (str(a.year) if a.year else "—"),
            a.status.value.replace("_", " ").title(),
        )
    console.print(table)


def _force_utf8() -> None:
    # Windows consoles default to cp1252 and choke on titles like "Journey’s".
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _force_utf8()
    # git-style sugar: `anime <query>` -> `anime play <query>`.
    argv = sys.argv[1:]
    if argv and argv[0] not in KNOWN_COMMANDS and not argv[0].startswith("-"):
        sys.argv.insert(1, "play")
    app()


if __name__ == "__main__":
    main()
