"""Typer entry point.

The CLI is one adapter onto the app services and holds no domain logic. Bare
``anime <query>`` is sugar for ``anime play <query>`` (rewritten in ``main``).
Bare ``anime`` on a terminal launches the TUI; piped/non-tty, it prints help.
"""

from __future__ import annotations

import contextlib
import json
import random as rng
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple

import typer
from rich.console import Console
from rich.table import Table

# Only what building the Typer app actually needs is imported eagerly. Typer
# resolves parameter annotations when it decorates a command, so the domain
# types below have to be real objects here — they are also the cheap ones, since
# `domain` depends on nothing.
from ..domain.errors import AnimeShError
from ..domain.models import Audio, Season, WatchProgress

# Everything heavier is deferred. `anime --help`, `anime version` and shell
# tab-completion used to pay for the whole application to be constructed before
# printing a line: pydantic through the config schema (93 ms), httpx through the
# container (91 ms), asyncio (81 ms) and importlib.metadata (27 ms) — imports
# that a command which prints a version string never touches.
#
# These are ordinary functions rather than a lazy-import trick on purpose. They
# keep the module-level names the call sites already use, so nothing else in
# this 1500-line file changes, and `monkeypatch.setattr(cli_main,
# "build_container", ...)` in the tests keeps working exactly as before.


def _run(coro):
    """Run a coroutine to completion.

    The single place `asyncio` is needed in this module, which is what lets the
    import be deferred at all — every command that does real work pays for it,
    and `version`/`--help` do not.
    """
    import asyncio

    return asyncio.run(coro)


def _version() -> str:
    from .. import __version__

    return __version__


def load_config(*args, **kwargs):
    from ..config import load_config as _load_config

    return _load_config(*args, **kwargs)


def config_path(*args, **kwargs):
    from ..config.loader import config_path as _config_path

    return _config_path(*args, **kwargs)


def build_container(*args, **kwargs):
    from .container import build_container as _build_container

    return _build_container(*args, **kwargs)

app = typer.Typer(
    name="anime",
    help="anime-sh — the terminal-native anime client.",
    no_args_is_help=False,
    add_completion=True,  # `anime --install-completion` for tab-completion
)
config_app = typer.Typer(help="View and edit configuration.")
providers_app = typer.Typer(help="Inspect installed provider plugins.")
favorite_app = typer.Typer(help="Manage favorites.")
auth_app = typer.Typer(help="Link an AniList account for watch-status sync.")
sync_app = typer.Typer(help="Sync watch progress with AniList.")
cache_app = typer.Typer(help="Manage the disposable metadata cache (cache.db).")
app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")
app.add_typer(favorite_app, name="favorite")
app.add_typer(auth_app, name="auth")
app.add_typer(sync_app, name="sync")
app.add_typer(cache_app, name="cache")

console = Console()
err = Console(stderr=True)

def _known_commands() -> set[str]:
    """Every registered command/group name, read straight from the Typer app so
    the `anime <query>` sugar can never drift out of sync with the real CLI."""
    import typer.main

    try:
        return set(typer.main.get_command(app).commands.keys())  # type: ignore[attr-defined]
    except Exception:
        return set()


# Words that name a real concept in this project but are not commands. Each is
# a guess someone actually makes: `docs/plugins.md` exists, so `anime plugins`
# is a reasonable thing to type, and the sugar below happily turned it into a
# search for a show called "plugins" and started resolving a stream.
#
# These need to be listed explicitly because they are *not* typos — "plugins"
# scores 0.55 against the closest real command, well inside the range where
# genuine one-word titles live ("bleach" scores 0.67 against "search").
_NOT_A_COMMAND = {
    "plugin": "anime providers ls",
    "plugins": "anime providers ls",
    "server": "anime providers ls",
    "servers": "anime providers ls",
    "watch": "anime play <title>",
    "help": "anime --help",
    "update": "uv tool upgrade anime-sh",
    "upgrade": "uv tool upgrade anime-sh",
    "install": "uv tool install 'anime-sh[tui]'",
    "uninstall": "uv tool uninstall anime-sh",
}

# Above the range real titles occupy. Measured over the command list against
# both plausible typos and one-word anime titles: typos score 0.80 and up
# (serach 0.83, donwload 0.88, provider 0.94) while the worst-case real title
# reaches 0.67. Lowering this starts refusing to play actual shows.
_TYPO_CUTOFF = 0.75


def _command_suggestion(word: str, known: set[str]) -> str | None:
    """A message to print instead of searching for ``word``, or None to search.

    `anime <query>` sugar means anything unrecognised is treated as a title, so
    a mistyped or guessed subcommand silently became a search — `anime plugins`
    went off to resolve a stream for a show called "plugins" rather than saying
    it did not know the command.
    """
    import difflib

    # `main()` only calls this for words that already failed the known-command
    # check, so this is belt-and-braces — but a function that answers "did you
    # mean play?" for `play` is a bug waiting for the next refactor to move it.
    if word in known:
        return None

    target = _NOT_A_COMMAND.get(word.lower())
    if target is None:
        close = difflib.get_close_matches(word.lower(), sorted(known), n=1,
                                          cutoff=_TYPO_CUTOFF)
        target = f"anime {close[0]}" if close else None
    if target is None:
        return None
    return (
        f"[red]Error:[/] [b]{word}[/] is not an anime-sh command.\n"
        f"Did you mean [cyan]{target}[/]?\n"
        f"[dim]If you really did mean the show, use "
        f"[/][cyan]anime play \"{word}\"[/][dim] — and "
        f"[/][cyan]anime --help[/][dim] lists every command.[/]"
    )


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"anime-sh {_version()}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    _version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    # Bare `anime` on a real terminal launches the TUI; otherwise show help.
    if not sys.stdout.isatty():
        typer.echo(ctx.get_help())
        return
    try:
        _launch_tui()
    except ImportError:
        # Note the escaped brackets: unescaped "[tui]" is eaten by Rich markup,
        # which is exactly how users end up copying "anime-sh" without the extra.
        err.print(
            "[yellow]The full-screen app needs its extra dependencies "
            "(Textual + Pillow).[/]\n"
            "Reinstall with the [b]tui[/] extra:\n"
            "  [cyan]uv tool install --force 'anime-sh\\[tui]'[/]   (recommended)\n"
            "  [cyan]pip install --user 'anime-sh\\[tui]'[/]"
        )
        typer.echo(ctx.get_help())


def _launch_tui() -> None:
    import asyncio

    from ..tui import TuiServices, run_tui

    config = load_config()
    c = build_container(config)
    services = TuiServices(
        search=c.search,
        metadata=c.metadata,
        library=c.library_service,
        playback=c.playback,
        aclose=c.aclose,
        tracker=c.tracker,
        sync=c.sync,
    )
    _run(run_tui(services, theme=config.ui.theme))


# --------------------------------------------------------------------------- #
# Simple commands
# --------------------------------------------------------------------------- #
@app.command()
def version() -> None:
    """Print the anime-sh version."""
    typer.echo(f"anime-sh {_version()}")


@app.command()
def doctor() -> None:
    """Check the environment: player, ffmpeg, config, database, plugins."""
    from .doctor import run_doctor

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


@config_app.command("get")
def config_get(key: str = typer.Argument(None, help="section.field (omit to dump all).")) -> None:
    """Show a setting, or the whole resolved config."""
    cfg = load_config()
    data = cfg.model_dump()
    if key is None:
        json.dump(data, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return
    section, _, field = key.partition(".")
    if section not in data or (field and field not in data[section]):
        err.print(f"[red]no such setting:[/] {key}")
        raise typer.Exit(code=1)
    typer.echo(data[section][field] if field else data[section])


# `ignore_unknown_options` so a value that begins with `--` is taken as the value
# rather than parsed as a flag of anime-sh's own. Without it,
# `anime config set player.args "--cache=yes"` failed with "No such option:
# --cache" — and `player.args` is the one setting whose values *always* start
# with `--`, so the single most likely use of this command was the broken one.
# The `--` escape still works; it is just no longer required.
@config_app.command("set", context_settings={"ignore_unknown_options": True})
def config_set(
    key: str = typer.Argument(..., help="section.field, e.g. playback.quality"),
    value: str = typer.Argument(..., help="new value (comma-separate lists)"),
) -> None:
    """Change a setting and save it to the config file (validated first).

    Examples: anime config set playback.quality 1080p ·
    anime config set playback.audio dub · anime config set ui.theme nord
    """
    from ..config import set_config_value

    try:
        typed = set_config_value(key, value)
    except Exception as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=1)
    console.print(f"[green]Set[/] {key} = [bold]{typed}[/]")


@app.command("themes")
def themes_cmd(
    set_to: str = typer.Option(None, "--set", help="switch to this theme"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List the themes, and say which one is in use.

    The picker inside the TUI (`t`) is the good way to choose one, because it
    previews live. This is for the times you cannot see it: naming what is
    available without launching the app, and setting it from a script or a
    dotfile.

    Deliberately reads the names from `anime_sh.theme_names`, which is
    dependency-free — reaching for the module that builds the themes would pull
    Textual into a command that only prints strings.
    """
    from ..config import set_config_value
    from ..theme_names import ALL_THEMES, OWN_THEMES

    if set_to is not None:
        # Checked here as well as in the schema. The schema's job is to stop a
        # bad value being stored; this one's is to say so in a sentence, rather
        # than handing back a pydantic ValidationError complete with a link to
        # pydantic's website — which is what a typo produced.
        if set_to not in ALL_THEMES:
            err.print(f"[red]No theme called[/] [bold]{set_to}[/][red].[/]")
            err.print("Available: " + ", ".join(ALL_THEMES))
            raise typer.Exit(code=1)
        try:
            set_config_value("ui.theme", set_to)
        except Exception as e:
            err.print(f"[red]{e}[/]")
            raise typer.Exit(code=1)
        console.print(f"[green]Theme set to[/] [bold]{set_to}[/]")
        return

    current = load_config().ui.theme
    if as_json:
        json.dump(
            [{"name": n, "current": n == current, "builtin": n not in OWN_THEMES}
             for n in ALL_THEMES],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    for name in ALL_THEMES:
        mark = "[green]*[/]" if name == current else " "
        origin = "" if name in OWN_THEMES else "  [dim]textual[/dim]"
        console.print(f" {mark} {name}{origin}")


@providers_app.command("ls")
def providers_ls(as_json: bool = typer.Option(False, "--json")) -> None:
    """List installed providers, and say which ones are switched off.

    This ignored `providers.disabled` in config, so it presented a provider as
    though it were in use when nothing would ever call it. Disabled ones are
    still listed — hiding them turns "why is anizone never used?" into a
    mystery — but they are marked, and `--json` carries the flag too.
    """
    from ..infra import registry

    disabled = set(load_config().providers.disabled)
    # Loaded unfiltered on purpose, so a disabled plugin can still be *named*;
    # the flag is what says whether it is actually in play.
    providers = registry.load_providers()
    rows = [
        {"name": p.name, "priority": getattr(p, "priority", 0),
         "enabled": p.name not in disabled}
        for p in providers
    ]
    if as_json:
        json.dump(rows, sys.stdout)
        sys.stdout.write("\n")
        return
    if not rows:
        typer.echo("no providers installed")
        return
    for r in rows:
        mark = "" if r["enabled"] else "\t(disabled in config)"
        typer.echo(f"{r['name']}\tpriority={r['priority']}{mark}")
    if not any(r["enabled"] for r in rows):
        err.print(
            "[yellow]Every provider is disabled — nothing can be played.[/]\n"
            "Re-enable one with [cyan]anime providers enable <name>[/]"
        )


def _set_provider_enabled(name: str, *, enabled: bool) -> None:
    """Add or remove one provider from `providers.disabled`.

    Doing this through `config set` means restating the whole list by hand, and
    getting it wrong silently switches off a provider you meant to keep. The
    name is checked against what is actually installed, because a typo would
    otherwise be written to the config file and do nothing for ever.
    """
    from ..config import set_config_value
    from ..infra import registry

    installed = {p.name for p in registry.load_providers()}
    if name not in installed:
        err.print(f"[red]No provider named[/] [b]{name}[/].")
        err.print(f"[dim]Installed:[/] {', '.join(sorted(installed)) or 'none'}")
        raise typer.Exit(code=1)

    disabled = set(load_config().providers.disabled)
    if enabled:
        disabled.discard(name)
    else:
        disabled.add(name)

    if not enabled and installed <= disabled:
        err.print(f"[red]{name} is the last provider left[/] — disabling it "
                  f"would leave nothing able to find episodes.")
        raise typer.Exit(code=1)

    # `_coerce` parses a list field from a comma-separated string; an empty
    # string is the empty list.
    set_config_value("providers.disabled", ",".join(sorted(disabled)))
    word = "Enabled" if enabled else "Disabled"
    console.print(f"[green]{word}[/] [b]{name}[/]. "
                  f"[dim]Active now: {', '.join(sorted(installed - disabled))}[/]")


@providers_app.command("enable")
def providers_enable(name: str = typer.Argument(..., help="Provider name.")) -> None:
    """Start using a provider again (removes it from providers.disabled)."""
    _set_provider_enabled(name, enabled=True)


@providers_app.command("disable")
def providers_disable(name: str = typer.Argument(..., help="Provider name.")) -> None:
    """Stop using a provider without uninstalling it."""
    _set_provider_enabled(name, enabled=False)


@providers_app.command("health")
def providers_health(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show each provider's circuit-breaker status (persisted across runs)."""
    _run(_providers_health(as_json))


# "clear" and "purge" are synonyms in English, and nothing in either name says
# which one throws away data you are still using. Rather than swap the meanings
# around — which would silently change what an existing `cache clear` in someone's
# script does — the safe operation gets an unambiguous name, the destructive one
# says what it is about to do and asks, and `cache info` lets you decide first.
@cache_app.command("info")
def cache_info(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show how much metadata is cached, and how much of it is stale."""
    _run(_cache_info(as_json))


@cache_app.command("prune")
def cache_prune() -> None:
    """Drop cache entries that have expired. Nothing you are still using."""
    _run(_cache_op(clear=False))


@cache_app.command("purge", hidden=True)
def cache_purge() -> None:
    """Deprecated alias for `cache prune`."""
    err.print("[dim]`cache purge` is now [/][cyan]cache prune[/][dim].[/]")
    _run(_cache_op(clear=False))


@cache_app.command("clear")
def cache_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Wipe every cached entry, stale or not.

    Only ever touches cache.db — your watch history and progress live in a
    separate database and are never affected.
    """
    _run(_cache_clear(yes))


async def _cache_info(as_json: bool) -> None:
    from ..config.paths import cache_db_path

    c = build_container()
    try:
        total, expired = await c.cache.stats()
        reclaimable = await c.cache.reclaimable_bytes()
    finally:
        await c.aclose()
    # Reuse the downloads helper: the same question ("is this file there, and how
    # big"), and the same need to answer rather than raise on an odd path.
    size = _download_on_disk(SimpleNamespace(path=str(cache_db_path()))).size
    if as_json:
        json.dump({"entries": total, "expired": expired, "size_bytes": size,
                   "reclaimable_bytes": reclaimable,
                   "path": str(cache_db_path())}, sys.stdout)
        sys.stdout.write("\n")
        return
    line = (f"[bold]{total}[/] cached entr{'y' if total == 1 else 'ies'} "
            f"([yellow]{expired}[/] stale) · {_human_size(size)}")
    if reclaimable:
        line += f" [dim]({_human_size(reclaimable)} reclaimable)[/]"
    console.print(line)
    console.print(f"[dim]{cache_db_path()}[/]")
    if expired:
        console.print("[dim]Drop the stale ones with [/][cyan]anime cache prune[/]")
    elif reclaimable > size / 2:
        console.print("[dim]Most of that file is free space — "
                      "[/][cyan]anime cache clear[/][dim] reclaims it.[/]")


async def _cache_clear(yes: bool) -> None:
    c = build_container()
    try:
        total, expired = await c.cache.stats()
        if not total:
            console.print("[dim]Cache is already empty.[/]")
            return
        if not yes:
            fresh = total - expired
            console.print(
                f"This drops all [bold]{total}[/] cached entries — "
                f"[bold]{fresh}[/] of them still current."
            )
            console.print("[dim]Nothing is lost permanently; it is re-fetched on "
                          "demand, so the only cost is slower lookups for a while. "
                          "Watch history and progress are in a different database "
                          "and are untouched.[/]")
            if not typer.confirm("Clear the whole cache?"):
                console.print("[dim]Left alone.[/]")
                return
        n = await c.cache.clear()
    finally:
        await c.aclose()
    console.print(f"[green]Cleared[/] {n} cache entr{'y' if n == 1 else 'ies'}.")


async def _cache_op(*, clear: bool) -> None:
    c = build_container()
    try:
        n = await c.cache.clear() if clear else await c.cache.purge_expired()
    finally:
        await c.aclose()
    verb = "Cleared" if clear else "Pruned"
    console.print(f"[green]{verb}[/] {n} cache entr{'y' if n == 1 else 'ies'}.")


# --------------------------------------------------------------------------- #
# Metadata-driven commands
# --------------------------------------------------------------------------- #
@app.command()
def search(
    query: str = typer.Argument(None, help="Title to search for (optional with filters)."),
    genre: list[str] = typer.Option(None, "--genre", "-g", help="Filter by genre (repeatable)."),
    year: int = typer.Option(None, "--year", help="Filter by release year."),
    fmt: str = typer.Option(None, "--format", help="TV|MOVIE|OVA|ONA|SPECIAL."),
    status: str = typer.Option(None, "--status", help="RELEASING|FINISHED|NOT_YET_RELEASED."),
    sort: str = typer.Option(None, "--sort", help="popularity|score|trending|newest|title."),
    limit: int = typer.Option(20, "-n", "--limit", min=1),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Search AniList (no providers touched — instant).

    With filters and no title it browses, e.g.
    `anime search --genre action --year 2024 --sort score`.
    """
    _run(_search(query, genre, year, fmt, status, sort, limit, as_json))


@app.command()
def trending(
    limit: int = typer.Option(20, "-n", "--limit", min=1),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show trending anime from AniList."""
    _run(_trending(limit, as_json))


@app.command()
def seasonal(
    season: str = typer.Option(None, "--season", help="winter|spring|summer|fall"),
    year: int = typer.Option(None, "--year"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show a season's anime (defaults to the current season)."""
    _run(_seasonal(season, year, as_json))


@app.command()
def calendar(
    days: int = typer.Option(7, "-d", "--days", help="Days ahead to show.", min=1, max=365),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the upcoming airing schedule."""
    _run(_calendar(days, as_json))


@app.command()
def random(
    play: bool = typer.Option(False, "--play", help="Play episode 1 of the pick."),
) -> None:
    """Surprise me — pick a random anime from what's trending."""
    _run(_random(play))


@app.command()
def play(
    query: str = typer.Argument(..., help="Title to play."),
    episode: float = typer.Option(1.0, "-e", "--episode", help="Episode number."),
    dub: bool = typer.Option(False, "--dub", help="Prefer dubbed audio."),
    quality: str = typer.Option(None, "-q", "--quality", help="best|1080p|720p|480p|worst"),
    resolve_only: bool = typer.Option(
        False, "--json", help="Resolve the stream and print it as JSON; do not launch."
    ),
    stream: bool = typer.Option(
        False, "--stream",
        help="Stream from a provider even if the episode is already downloaded.",
    ),
) -> None:
    """Search for a title, pick the best match, and play an episode.

    An episode you have already downloaded plays from disk, which needs no
    network and no provider. Pass --stream to fetch it anyway.
    """
    _run(_play(query, episode, dub, quality, resolve_only, stream))


@app.command(name="continue")
def continue_watching(
    as_json: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(20, "-n", "--limit", min=1),
) -> None:
    """Show episodes you've started but not finished."""
    _run(_continue(limit, as_json))


@app.command()
def resume(
    dub: bool = typer.Option(False, "--dub"),
    quality: str = typer.Option(None, "-q", "--quality"),
) -> None:
    """Resume the most recently watched unfinished episode."""
    _run(_resume(dub, quality))


@app.command()
def history(
    as_json: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(50, "-n", "--limit", min=1),
) -> None:
    """Show your watch history."""
    _run(_history(limit, as_json))


@app.command()
def sources(
    query: str = typer.Argument(..., help="Title to list sources for."),
    dub: bool = typer.Option(False, "--dub"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List every provider entry that matches a title (the source picker)."""
    _run(_sources(query, dub, as_json))


# --------------------------------------------------------------------------- #
# AniList auth + sync
# --------------------------------------------------------------------------- #
@auth_app.command("login")
def auth_login(
    client_id: str = typer.Option(
        None, "--client-id",
        help="Your AniList API client id (from anilist.co/settings/developer).",
    ),
    secret: str = typer.Option(
        None, "--secret",
        help="Your AniList API client secret — enables the paste-a-code flow.",
    ),
    token: str = typer.Option(
        None, "--token", help="Paste an access token directly (skips the browser)."
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open a browser."),
) -> None:
    """Link your AniList account so watch progress syncs automatically.

    One-time setup: create an API client at
    https://anilist.co/settings/developer (any name; set "Redirect URL" to
    https://anilist.co/api/v2/oauth/pin). Then run this with --client-id (and
    --secret to paste a short code instead of a raw token).
    """
    _run(_auth_login(client_id, secret, token, no_browser))


@auth_app.command("status")
def auth_status() -> None:
    """Show whether an AniList account is linked (and who)."""
    _run(_auth_status())


@auth_app.command("logout")
def auth_logout() -> None:
    """Remove the saved AniList token."""
    from ..infra.tracker import clear_token

    if clear_token():
        console.print("[green]Logged out[/] — AniList token removed.")
    else:
        console.print("[dim]No AniList token was saved.[/]")


@sync_app.command("push")
def sync_push() -> None:
    """Push all local watch progress up to AniList."""
    _run(_sync("push"))


@sync_app.command("pull")
def sync_pull() -> None:
    """Import your AniList list into the local library."""
    _run(_sync("pull"))


@app.command()
def download(
    query: str = typer.Argument(..., help="Title to download."),
    episode: str = typer.Option("1", "-e", "--episode",
                                help="Episode(s): 5, a range 1-12, or a list 1,3,5."),
    dub: bool = typer.Option(False, "--dub"),
    quality: str = typer.Option(None, "-q", "--quality"),
) -> None:
    """Download one or more episodes to disk (ffmpeg).

    Batches skip episodes already on disk, so re-running resumes where it left
    off, and one failed episode never aborts the rest.
    """
    _run(_download(query, episode, dub, quality))


@app.command()
def downloads(as_json: bool = typer.Option(False, "--json")) -> None:
    """List downloads."""
    _run(_downloads(as_json))


@app.command()
def next(
    query: str = typer.Argument(..., help="Title to find the sequel of."),
    as_json: bool = typer.Option(False, "--json", help="Show the sequel; don't play."),
) -> None:
    """Find and play the next season (sequel) of a show."""
    _run(_next(query, as_json))


@app.command()
def recommend(
    query: str = typer.Argument(..., help="A show you liked."),
    limit: int = typer.Option(15, "-n", "--limit", min=1),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Suggest shows for people who liked a title (AniList recommendations)."""
    _run(_recommend(query, limit, as_json))


@app.command()
def related(
    query: str = typer.Argument(..., help="Title to show related works for."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List prequels, sequels, side stories and movies tied to a show."""
    _run(_related(query, as_json))


@app.command(name="list")
def list_cmd(
    status: str = typer.Option(
        None, "--status", "-s",
        help="watching|planning|completed|paused|dropped|rewatching",
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show your AniList list (all statuses, or one). Needs `anime auth login`."""
    _run(_list(status, as_json))


@app.command()
def rate(
    query: str = typer.Argument(..., help="Title on your list."),
    score: float = typer.Argument(..., help="Score 0–10."),
) -> None:
    """Set a show's score on AniList (matched against your list)."""
    _run(_rate_or_status(query, score=score))


@app.command()
def status(
    query: str = typer.Argument(..., help="Title on your list."),
    new_status: str = typer.Argument(..., help="watching|planning|completed|paused|dropped|rewatching"),
) -> None:
    """Move a show to a different list status on AniList."""
    _run(_rate_or_status(query, new_status=new_status))


@app.command()
def unmark(query: str = typer.Argument(..., help="Title to clear progress for.")) -> None:
    """Clear all local watch progress for a show (undo a mark / forget it)."""
    _run(_unmark(query))


@app.command()
def stats(as_json: bool = typer.Option(False, "--json")) -> None:
    """Summarize your watch history: episodes, hours, top providers & genres."""
    _run(_stats(as_json))


@app.command()
def mark(
    query: str = typer.Argument(..., help="Title to mark."),
    episode: float = typer.Option(..., "-e", "--episode", help="Episode watched."),
    single: bool = typer.Option(
        False, "--single", help="Mark only this episode (not 1..N). Local only."
    ),
    force: bool = typer.Option(
        False, "--force", help="Allow lowering your AniList progress."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark progress without playing — catch up to an episode you watched
    elsewhere. Sets episodes 1..N complete locally and, if AniList is linked,
    sets your progress there to N.

    Because it *sets* rather than adds, marking below where you already are
    would throw away progress; that is refused unless you pass --force.
    `--single` marks one episode locally and never touches AniList, which has
    no way to record "episode 5.5" on its own."""
    _run(_mark(query, episode, single, force, as_json))


@favorite_app.command("add")
def favorite_add(query: str = typer.Argument(..., help="Title to favorite.")) -> None:
    """Add the best match for a title to favorites."""
    _run(_favorite_add(query))


@favorite_app.command("rm")
def favorite_rm(query: str = typer.Argument(..., help="Title to remove.")) -> None:
    """Remove a favorite by title."""
    _run(_favorite_rm(query))


@favorite_app.command("ls")
def favorite_ls(as_json: bool = typer.Option(False, "--json")) -> None:
    """List favorites."""
    _run(_favorite_ls(as_json))


# --------------------------------------------------------------------------- #
# Async implementations
# --------------------------------------------------------------------------- #
async def _search(query, genre, year, fmt, status, sort, limit, as_json) -> None:
    filtered = any(x for x in (genre, year, fmt, status, sort))
    if not query and not filtered:
        err.print("[red]Give a title to search, or filters to browse[/] "
                  "(e.g. --genre action --sort score).")
        raise typer.Exit(code=1)
    c = build_container()
    try:
        if filtered:
            animes = await c.metadata.search_filtered(
                query, genres=genre, year=year, format=fmt, status=status,
                sort=sort, limit=limit,
            )
        else:
            animes = [r.anime for r in await c.search.search(query, limit=limit)]
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()

    if as_json:
        json.dump([_anime_dict(a) for a in animes], sys.stdout)
        sys.stdout.write("\n")
        return
    heading = f"Results for {query!r}" if query else "Browse"
    _print_anime_table(heading, animes)


# Display order for list statuses.
_STATUS_ORDER = ["watching", "rewatching", "paused", "planning", "completed", "dropped"]


async def _next(query: str, as_json: bool) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        sequel = await c.metadata.sequel(anime.id)
        if sequel is None:
            console.print(f"[dim]{anime.title.preferred} has no next season.[/]")
            raise typer.Exit(code=0)
        if as_json:
            json.dump(_anime_dict(sequel), sys.stdout)
            sys.stdout.write("\n")
            return
        err.print(f"[cyan]▶ Next season:[/] {sequel.title.preferred}")
        audio = Audio.DUB if load_config().playback.audio == "dub" else Audio.SUB
        await c.playback.play_and_track(sequel, 1.0, audio=audio)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


async def _recommend(query: str, limit: int, as_json: bool) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        recs = await c.metadata.recommendations(anime.id, limit=limit)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()
    if as_json:
        json.dump([_anime_dict(a) for a in recs], sys.stdout)
        sys.stdout.write("\n")
        return
    if not recs:
        console.print(f"[dim]No recommendations for {anime.title.preferred}.[/]")
        return
    _print_anime_table(f"Because you liked {anime.title.preferred}", recs)


# Friendly labels for AniList relation types.
_RELATION_LABELS = {
    "PREQUEL": "Prequel", "SEQUEL": "Sequel", "SIDE_STORY": "Side story",
    "PARENT": "Parent", "SPIN_OFF": "Spin-off", "ALTERNATIVE": "Alternative",
    "SUMMARY": "Summary", "CHARACTER": "Character", "OTHER": "Other",
}
# Rough watch/story order so the list reads naturally.
_RELATION_ORDER = ["PARENT", "PREQUEL", "SEQUEL", "SIDE_STORY", "SPIN_OFF",
                   "ALTERNATIVE", "SUMMARY", "OTHER"]


async def _related(query: str, as_json: bool) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        relations = await c.metadata.relations(anime.id)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()
    if as_json:
        json.dump(
            [{"relation": rel, **_anime_dict(a)} for rel, a in relations],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not relations:
        console.print(f"[dim]{anime.title.preferred} has no related anime.[/]")
        return
    relations.sort(key=lambda ra: (
        _RELATION_ORDER.index(ra[0]) if ra[0] in _RELATION_ORDER else len(_RELATION_ORDER)
    ))
    table = Table(title=f"Related to {anime.title.preferred}",
                  title_justify="left", header_style="bold cyan")
    table.add_column("Relation", style="magenta")
    table.add_column("Title", style="bold")
    table.add_column("Format")
    table.add_column("Eps", justify="right")
    table.add_column("Year")
    for rel, a in relations:
        table.add_row(
            _RELATION_LABELS.get(rel, rel.replace("_", " ").title()),
            a.title.preferred,
            a.format.value,
            str(a.episode_count or "—"),
            str(a.year or "—"),
        )
    console.print(table)


async def _list(status: str | None, as_json: bool) -> None:
    from ..infra.tracker.anilist import STATUS_FROM_ANILIST, STATUS_TO_ANILIST

    if status and status.lower() not in STATUS_TO_ANILIST:
        err.print(f"[red]Unknown status[/] {status!r} "
                  f"(use: {', '.join(STATUS_TO_ANILIST)})")
        raise typer.Exit(code=1)
    c = build_container()
    try:
        if c.tracker is None:
            err.print("[yellow]Not linked to AniList.[/] Run [bold]anime auth login[/].")
            raise typer.Exit(code=1)
        entries = await c.tracker.fetch_list()
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()

    if status:
        want = status.lower()
        entries = [e for e in entries if STATUS_FROM_ANILIST.get(e.status) == want]

    if as_json:
        json.dump(
            [{"anilist_id": e.anime.id.anilist, "title": e.anime.title.preferred,
              "status": STATUS_FROM_ANILIST.get(e.status, e.status.lower()),
              "progress": e.progress, "episodes": e.anime.episode_count,
              "score": e.score} for e in entries],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return

    if not entries:
        console.print("[dim]Nothing on your list here.[/]")
        return

    # Group by status in a sensible order.
    groups: dict[str, list] = {}
    for e in entries:
        groups.setdefault(STATUS_FROM_ANILIST.get(e.status, e.status.lower()), []).append(e)
    order = [s for s in _STATUS_ORDER if s in groups] + [
        s for s in groups if s not in _STATUS_ORDER
    ]
    for name in order:
        rows = groups[name]
        console.print(f"\n[b]{name.title()}[/b] [dim]({len(rows)})[/dim]")
        for e in rows:
            total = e.anime.episode_count
            prog = f"{e.progress}/{total}" if total else f"{e.progress}"
            score = f"  [green]★{e.score:g}[/green]" if e.score else ""
            console.print(f"  {e.anime.title.preferred}  [dim]{prog}[/dim]{score}")


def _match_list_entry(entries, query: str):
    """Best title match among the user's own list entries (safer than a global
    search — avoids picking a same-named spin-off). None if nothing close."""
    from difflib import SequenceMatcher

    def norm(s: str) -> str:
        return "".join(ch.lower() for ch in (s or "") if ch.isalnum())

    q = norm(query)
    best, best_score = None, 0.0
    for e in entries:
        titles = (e.anime.title.romaji, e.anime.title.english,
                  *e.anime.title.synonyms)
        sim = max((SequenceMatcher(None, norm(t), q).ratio() for t in titles if t),
                  default=0.0)
        if sim > best_score:
            best, best_score = e, sim
    return best if best_score >= 0.6 else None


async def _rate_or_status(query, *, score=None, new_status=None) -> None:
    c = build_container()
    try:
        if c.tracker is None:
            err.print("[yellow]Not linked to AniList.[/] Run [bold]anime auth login[/].")
            raise typer.Exit(code=1)
        entries = await c.tracker.fetch_list()
        entry = _match_list_entry(entries, query)
        if entry is None:
            err.print(f"[red]{query!r} isn't on your AniList list.[/] "
                      "Add it first (e.g. `anime mark` or on the site).")
            raise typer.Exit(code=1)
        media_id = entry.anime.id.anilist
        title = entry.anime.title.preferred
        if score is not None:
            await c.tracker.set_score(media_id, score)
            console.print(f"[green]Rated[/] {title} [bold]{score:g}/10[/].")
        else:
            await c.tracker.set_status(media_id, new_status)
            console.print(f"[green]Moved[/] {title} → [bold]{new_status.lower()}[/].")
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


async def _unmark(query: str) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        removed = await c.library_service.unmark(anime.id)
        console.print(
            f"[yellow]Cleared[/] {removed} local progress row(s) for "
            f"{anime.title.preferred}."
        )
    finally:
        await c.aclose()


async def _stats(as_json: bool) -> None:
    c = build_container()
    try:
        s = await c.library_service.stats()
    finally:
        await c.aclose()
    if as_json:
        json.dump(
            {"episodes_completed": s.episodes_completed, "shows": s.shows,
             "sessions": s.sessions, "hours": s.hours,
             "total_seconds": s.total_seconds,
             "top_providers": [list(p) for p in s.top_providers],
             "top_genres": [list(g) for g in s.top_genres]},
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if s.sessions == 0:
        console.print("[dim]No watch history yet. Go watch something![/]")
        return
    console.print(
        f"[b]Your anime-sh stats[/b]\n"
        f"  [cyan]{s.episodes_completed}[/cyan] episodes finished across "
        f"[cyan]{s.shows}[/cyan] shows\n"
        f"  [cyan]{s.hours}[/cyan] hours watched over [cyan]{s.sessions}[/cyan] sessions"
    )
    if s.top_genres:
        console.print("  [dim]top genres:[/] " +
                      ", ".join(f"{g} ({n})" for g, n in s.top_genres[:5]))
    if s.top_providers:
        console.print("  [dim]providers:[/]  " +
                      ", ".join(f"{p} ({n})" for p, n in s.top_providers))


async def _anilist_progress(c, anime) -> int | None:
    """The show's current AniList progress, or None if it cannot be read.

    One request. Worth it before a mark, because AniList has no undo and the
    thing being overwritten is the user's watch history.
    """
    if c.tracker is None or anime.id.anilist is None:
        return None
    try:
        for wp in await c.tracker.pull():
            if wp.anime_id.anilist == anime.id.anilist:
                return int(wp.episode)
    except Exception:
        return None  # unreadable is not the same as zero; do not guess
    return None


async def _mark(query: str, episode: float, single: bool, force: bool,
                as_json: bool) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)

        # A catch-up sets AniList progress to exactly this episode, so a mark
        # *below* where you already are throws away watch history — silently,
        # and with no undo on AniList's side. `mark -e 5` on a show you had
        # finished set it back to "watching, 5 episodes". Checked before
        # anything is written, so refusing leaves nothing half-done.
        current = None
        if not single:
            current = await _anilist_progress(c, anime)
            if current is not None and current > episode and not force:
                err.print(
                    f"[red]AniList has you at episode {current} of "
                    f"{anime.title.preferred}; marking {episode:g} would lower "
                    f"it.[/] Pass --force if that is what you meant."
                )
                raise typer.Exit(code=2)

        try:
            numbers = await c.library_service.mark_watched(
                anime, episode, single=single
            )
        except AnimeShError as e:
            err.print(f"[red]{e}[/]")
            raise typer.Exit(code=2)
        synced = False
        # `--single` marks one episode, which is not the same statement as "I am
        # N episodes in" — and AniList only understands the latter. Pushing a
        # single mark as overall progress is a category error: marking a special
        # numbered 5.5 on a finished show reported episode 5 to AniList and
        # dropped it from "completed" back to "watching".
        if not single and c.tracker is not None and anime.id.anilist is not None:
            try:
                await c.tracker.push(
                    WatchProgress(anime.id, episode, 1, 1,
                                  datetime.now(timezone.utc), completed=True),
                    total=anime.episode_count,
                )
                synced = True
            except Exception as e:
                err.print(f"[yellow]Marked locally, but AniList sync failed:[/] {e}")
        if as_json:
            json.dump(
                {"anilist_id": anime.id.anilist, "title": anime.title.preferred,
                 "marked": numbers, "synced_anilist": synced},
                sys.stdout,
            )
            sys.stdout.write("\n")
            return
        # Report what was written, not what was asked for. The two used to be
        # allowed to differ, and the message believed the request.
        span = (f"episode {numbers[0]:g}" if len(numbers) == 1
                else f"episodes {numbers[0]:g}–{numbers[-1]:g}")
        tail = " [dim](synced to AniList)[/]" if synced else ""
        console.print(f"[green]Marked[/] {anime.title.preferred} {span} watched{tail}.")
    finally:
        await c.aclose()


async def _trending(limit: int, as_json: bool) -> None:
    c = build_container()
    try:
        animes = await c.metadata.trending(limit=limit)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()
    if as_json:
        json.dump([_anime_dict(a) for a in animes], sys.stdout)
        sys.stdout.write("\n")
        return
    _print_anime_table("Trending", animes)


def _current_season() -> Season:
    m = date.today().month
    if m in (12, 1, 2):
        return Season.WINTER
    if m in (3, 4, 5):
        return Season.SPRING
    if m in (6, 7, 8):
        return Season.SUMMER
    return Season.FALL


async def _seasonal(season: str | None, year: int | None, as_json: bool) -> None:
    try:
        s = Season(season.upper()) if season else _current_season()
    except ValueError:
        err.print(f"[red]Unknown season[/] {season!r} (winter|spring|summer|fall)")
        raise typer.Exit(code=1)
    y = year or date.today().year
    c = build_container()
    try:
        animes = await c.metadata.seasonal(s, y)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()
    if as_json:
        json.dump([_anime_dict(a) for a in animes], sys.stdout)
        sys.stdout.write("\n")
        return
    _print_anime_table(f"{s.value.title()} {y}", animes)


async def _calendar(days: int, as_json: bool) -> None:
    start = date.today()
    end = start + timedelta(days=max(1, days))
    c = build_container()
    try:
        events = await c.metadata.airing_schedule(start, end)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()
    if as_json:
        json.dump(
            [
                {
                    "title": e.anime.title.preferred,
                    "episode": e.episode,
                    "airing_at": e.airing_at.isoformat(),
                }
                for e in events
            ],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not events:
        console.print("[dim]Nothing scheduled in that window.[/]")
        return
    table = Table(title=f"Airing — next {days}d", title_justify="left", header_style="bold cyan")
    table.add_column("When", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Ep", justify="right")
    for e in events:
        table.add_row(
            e.airing_at.astimezone().strftime("%a %d %b %H:%M"),
            e.anime.title.preferred,
            f"{e.episode:g}",
        )
    console.print(table)


async def _random(play: bool) -> None:
    config = load_config()
    c = build_container(config)
    try:
        pool = await c.metadata.trending(limit=50)
        if not pool:
            err.print("[red]Couldn't fetch anything to pick from.[/]")
            raise typer.Exit(code=1)
        pick = rng.choice(pool)
        console.print(f"[magenta]🎲 Your pick:[/] [bold]{pick.title.preferred}[/]")
        _print_anime_table("", [pick])
        if play:
            audio = Audio.DUB if config.playback.audio == "dub" else Audio.SUB
            err.print("[dim]Searching providers…[/]")
            await c.playback.play_and_track(pick, 1.0, audio=audio)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


async def _identify(c, query: str):
    """Name the show the user asked for, falling back to what is already on this
    machine when AniList cannot be reached.

    Playing a downloaded episode needs no network — except that identifying the
    title did, so an episode sitting on your disk was unplayable on a train
    purely because nobody could look its name up. Every show you have played or
    downloaded is in the local library, which is exactly the set that could have
    a file waiting.
    """
    try:
        return await c.search.best_match(query)
    except AnimeShError as e:
        local = await c.library.find_anime_by_title(query)
        if not local:
            raise
        err.print(f"[yellow]AniList is unreachable[/] [dim]({e})[/]")
        err.print(f"[dim]Using your local library: {local[0].title.preferred}[/]")
        return local[0]


async def _play(query, episode, dub, quality, resolve_only, stream=False) -> None:
    config = load_config()
    if quality:
        config.playback.quality = quality
    if stream:
        config.playback.prefer_downloads = False
    c = build_container(config)
    audio = Audio.DUB if (dub or config.playback.audio == "dub") else Audio.SUB
    try:
        anime = await _identify(c, query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        err.print(f"[cyan]▶[/] {anime.title.preferred} — Episode {episode:g} ({audio.value.lower()})")

        # An episode already on disk needs no provider, and asking anyway is
        # what would break this on a train: `available_episodes` fans out over
        # the network purely to print a status line, and with no connection it
        # fails before the local file is ever reached.
        have_local = (
            config.playback.prefer_downloads
            and c.download.local_path(anime, episode) is not None
        )
        if have_local:
            err.print("[dim]Playing your download — no provider needed.[/]")

        if not resolve_only and not have_local:
            # Status lines from playback ("Episode 5/12 — trying HD-1…",
            # "Next episode: 6/12", "Skipped intro") land on stderr.
            c.playback.set_on_event(lambda msg: err.print(f"[dim]{msg}[/]"))
            available = await c.playback.available_episodes(anime, audio=audio)
            if available:
                planned = f" of {anime.episode_count} planned" if anime.episode_count else ""
                err.print(
                    f"[dim]{len(available)} episode(s) available{planned}: "
                    f"{_ep_list(available)}[/]"
                )
                if episode not in available:
                    err.print(f"[yellow]Episode {episode:g} isn't available (yet).[/]")
                    raise typer.Exit(code=1)
        elif have_local and not resolve_only:
            c.playback.set_on_event(lambda msg: err.print(f"[dim]{msg}[/]"))

        if resolve_only:
            # allow_local, because this reports what `anime play` *would* do.
            # Without it the JSON names a CDN while playing the same episode
            # would read it off the disk — the flag would be describing a
            # different command than the one it belongs to.
            resolved = await c.playback.resolve(
                anime, episode, audio=audio, allow_local=True
            )
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
            if not have_local:
                err.print("[dim]Searching providers and resolving stream…[/]")
            await c.playback.play_and_track(anime, episode, audio=audio)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


async def _auth_login(
    client_id: str | None, secret: str | None, token: str | None, no_browser: bool
) -> None:
    import webbrowser

    from ..infra.tracker import (
        AniListTracker,
        authorize_url,
        exchange_code,
        extract_token,
        save_token,
    )
    from ..infra.tracker.tokens import load_client_id

    if not token:
        client_id = client_id or load_client_id()
        if not client_id:
            err.print(
                "[yellow]One-time setup:[/] create an API client at "
                "https://anilist.co/settings/developer\n"
                "  • Name: anything (e.g. anime-sh)\n"
                "  • Redirect URL: https://anilist.co/api/v2/oauth/pin\n"
                "Then re-run: [bold]anime auth login --client-id <ID> --secret <SECRET>[/]"
            )
            raise typer.Exit(code=1)

        if secret:
            # Auth-code + PIN flow: the page shows a code; exchange it for a token.
            url = authorize_url(client_id, response_type="code")
            console.print(f"\nOpen this URL, authorise, then copy the code shown:\n[cyan]{url}[/]\n")
            if not no_browser:
                with contextlib.suppress(Exception):
                    webbrowser.open(url)
            code = typer.prompt("Paste the code from the page")
            try:
                token = await exchange_code(client_id, secret, code)
            except AnimeShError as e:
                err.print(f"[red]{e}[/]")
                raise typer.Exit(code=1)
        else:
            # Implicit grant: the token itself comes back in the redirect URL.
            url = authorize_url(client_id, response_type="token")
            console.print(f"\nOpen this URL, authorise, then copy the token:\n[cyan]{url}[/]\n")
            if not no_browser:
                with contextlib.suppress(Exception):
                    webbrowser.open(url)
            pasted = typer.prompt("Paste the access token (or the full redirect URL)")
            token = extract_token(pasted)
            if not token:
                err.print("[red]Couldn't find an access token in that input.[/]")
                raise typer.Exit(code=1)

    tracker = AniListTracker(token)
    try:
        viewer = await tracker.viewer()
    except Exception as e:
        err.print(f"[red]AniList rejected the token:[/] {e}")
        raise typer.Exit(code=1)
    finally:
        await tracker.aclose()

    save_token(token, client_id=client_id)
    console.print(
        f"[green]Linked AniList as[/] [bold]{viewer['name']}[/]. "
        "Progress will now sync when you finish an episode.\n"
        "[dim]Tip: `anime sync pull` imports your existing list; "
        "`anime sync push` sends your local history up.[/]"
    )


async def _auth_status() -> None:
    from ..infra.tracker import AniListTracker, load_token

    token = load_token()
    if not token:
        console.print("[dim]Not linked.[/] Run [bold]anime auth login[/] to connect AniList.")
        return
    tracker = AniListTracker(token)
    try:
        viewer = await tracker.viewer()
        console.print(f"[green]Linked[/] as [bold]{viewer['name']}[/] (AniList id {viewer['id']}).")
    except Exception as e:
        console.print(f"[yellow]Token saved but not working:[/] {e}\nRe-run [bold]anime auth login[/].")
    finally:
        await tracker.aclose()


async def _sync(direction: str) -> None:
    c = build_container()
    try:
        if not c.sync.enabled:
            err.print("[yellow]Not linked to AniList.[/] Run [bold]anime auth login[/] first.")
            raise typer.Exit(code=1)
        if direction == "push":
            err.print("[dim]Pushing local progress to AniList…[/]")
            result = await c.sync.push()
            console.print(
                f"[green]Pushed[/] {result.pushed} show(s) to AniList"
                + (f" ([dim]{result.skipped} skipped[/])" if result.skipped else "")
                + "."
            )
        else:
            err.print("[dim]Importing your AniList list…[/]")
            result = await c.sync.pull()
            console.print(f"[green]Imported[/] {result.pulled} ent(y/ies) from AniList.")
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


async def _continue(limit: int, as_json: bool) -> None:
    c = build_container()
    try:
        items = await c.library_service.continue_watching(limit=limit)
    finally:
        await c.aclose()
    if as_json:
        json.dump(
            [
                {
                    "anilist_id": it.anime.id.anilist,
                    "title": it.anime.title.preferred,
                    "episode": it.progress.episode,
                    "position_s": it.progress.position_s,
                    "duration_s": it.progress.duration_s,
                    "percent": round(it.progress.fraction * 100),
                }
                for it in items
            ],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]Nothing in progress. Go watch something![/]")
        return
    table = Table(title="Continue Watching", title_justify="left", header_style="bold cyan")
    table.add_column("Title", style="bold")
    table.add_column("Episode", justify="right")
    table.add_column("Progress", justify="right")
    for it in items:
        table.add_row(
            it.anime.title.preferred,
            f"{it.progress.episode:g}",
            f"{round(it.progress.fraction * 100)}%",
        )
    console.print(table)


async def _resume(dub: bool, quality: str | None) -> None:
    config = load_config()
    if quality:
        config.playback.quality = quality
    c = build_container(config)
    audio = Audio.DUB if (dub or config.playback.audio == "dub") else Audio.SUB
    try:
        items = await c.library_service.continue_watching(limit=1)
        if not items:
            err.print("[yellow]Nothing to resume.[/]")
            raise typer.Exit(code=1)
        top = items[0]
        err.print(
            f"[cyan]▶[/] Resuming {top.anime.title.preferred} — "
            f"Episode {top.progress.episode:g} at {top.progress.position_s}s"
        )
        await c.playback.play_and_track(top.anime, top.progress.episode, audio=audio)
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


async def _history(limit: int, as_json: bool) -> None:
    c = build_container()
    try:
        items = await c.library_service.history(limit=limit)
    finally:
        await c.aclose()
    if as_json:
        json.dump(
            [
                {
                    "anilist_id": it.anime.id.anilist,
                    "title": it.anime.title.preferred,
                    "episode": it.episode,
                    "watched_at": it.watched_at.isoformat(),
                    "provider": it.provider,
                    "seconds_watched": it.seconds_watched,
                }
                for it in items
            ],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]No history yet.[/]")
        return
    table = Table(title="History", title_justify="left", header_style="bold cyan")
    table.add_column("Watched", style="dim")
    table.add_column("Title", style="bold")
    table.add_column("Ep", justify="right")
    table.add_column("Provider")
    for it in items:
        table.add_row(
            it.watched_at.strftime("%Y-%m-%d %H:%M"),
            it.anime.title.preferred,
            f"{it.episode:g}",
            it.provider or "—",
        )
    console.print(table)


async def _favorite_add(query: str) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        await c.library_service.add_favorite(anime)
        console.print(f"[green]★[/] Favorited {anime.title.preferred}")
    finally:
        await c.aclose()


async def _favorite_rm(query: str) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        await c.library_service.remove_favorite(anime.id)
        console.print(f"[yellow]☆[/] Removed {anime.title.preferred} from favorites")
    finally:
        await c.aclose()


async def _sources(query, dub, as_json) -> None:
    config = load_config()
    c = build_container(config)
    audio = Audio.DUB if (dub or config.playback.audio == "dub") else Audio.SUB
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        options = await c.playback.list_sources(anime, audio=audio)
    finally:
        await c.aclose()
    if as_json:
        json.dump(
            [
                {
                    "provider": o.provider, "anime_key": o.anime_key,
                    "title": o.title, "episodes": o.episode_count,
                    "audio": o.audio.value, "confidence": o.confidence,
                }
                for o in options
            ],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not options:
        console.print(f"[yellow]No provider entries matched[/] {anime.title.preferred!r}")
        return
    table = Table(title=f"Sources for {anime.title.preferred}", title_justify="left", header_style="bold cyan")
    table.add_column("Title", style="bold")
    table.add_column("Provider", style="cyan")
    table.add_column("Eps", justify="right")
    table.add_column("Audio")
    for o in options:
        table.add_row(o.title, o.provider, str(o.episode_count or "?"), o.audio.value.lower())
    console.print(table)


async def _download(query, episode, dub, quality) -> None:
    config = load_config()
    if quality:
        config.playback.quality = quality
    try:
        numbers = _parse_episode_spec(episode)
    except ValueError as e:
        # The parser knows *why* — "covers 999999999 episodes", "asks for a
        # negative episode". Swallowing that for one generic line made a
        # refused range look like a syntax error.
        err.print(f"[red]Bad episode spec[/] {episode!r}: {e}")
        err.print("[dim]Use 5, a range 1-12, or a list 1,3,5.[/]")
        raise typer.Exit(code=1)
    if not numbers:
        err.print("[red]No episodes to download.[/]")
        raise typer.Exit(code=1)

    c = build_container(config)
    audio = Audio.DUB if (dub or config.playback.audio == "dub") else Audio.SUB
    try:
        if not c.download.available():
            err.print("[red]ffmpeg not found on PATH.[/] Install it (see `anime doctor`).")
            raise typer.Exit(code=1)
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)

        span = _ep_list(numbers) if len(numbers) > 1 else f"{numbers[0]:g}"
        err.print(f"[cyan]⬇[/] {anime.title.preferred} — Episode(s) {span}")
        saved = skipped = failed = 0
        for n in numbers:
            # Same authority playback uses, so the two can never disagree
            # about whether you already have an episode.
            dest = c.download.local_path(anime, n)
            if dest is not None:
                console.print(f"[dim]• Episode {n:g} already downloaded, skipping.[/]")
                skipped += 1
                continue
            try:
                with console.status(f"Episode {n:g}: resolving & downloading… (ffmpeg)",
                                    spinner="dots"):
                    dest = await c.download.download(anime, n, audio=audio)
                console.print(f"[green]✓[/] Episode {n:g} → {dest}")
                saved += 1
            except AnimeShError as e:
                # One bad episode must not abort the batch.
                console.print(f"[red]✗ Episode {n:g}:[/] {e}")
                failed += 1
    finally:
        await c.aclose()

    if len(numbers) > 1 or failed:
        console.print(
            f"[bold]Done.[/] {saved} saved"
            + (f", {skipped} skipped" if skipped else "")
            + (f", [red]{failed} failed[/]" if failed else "")
        )
    if failed and not saved:
        raise typer.Exit(code=2)


class _OnDisk(NamedTuple):
    exists: bool
    size: int


def _download_on_disk(item) -> _OnDisk:
    """Whether this download's file is still there, and how big it is.

    Best-effort: an unreadable path (a disconnected drive, a permissions change)
    answers "not there" rather than raising, because this is a listing and a
    stat failure is not worth losing the whole table over.
    """
    if not item.path:
        return _OnDisk(False, 0)
    try:
        stat = Path(item.path).stat()
    except (OSError, ValueError):
        # ValueError, not only OSError: a path with an embedded NUL raises that
        # instead, and one unstat-able row must not take down the whole table.
        return _OnDisk(False, 0)
    return _OnDisk(True, stat.st_size)


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # pragma: no cover - unreachable, loop returns first


async def _downloads(as_json: bool) -> None:
    c = build_container()
    try:
        items = await c.download.history()
    finally:
        await c.aclose()
    # The database records what was downloaded; the disk is what you can actually
    # watch. Delete an episode to free space and this listed it as "done" for
    # ever, which is the opposite of useful when you are trying to work out where
    # your disk went.
    disk = [_download_on_disk(it) for it in items]

    if as_json:
        json.dump(
            [
                {
                    "anilist_id": it.anime.id.anilist,
                    "title": it.anime.title.preferred,
                    "episode": it.episode,
                    "status": it.status.value,
                    "path": it.path,
                    "created_at": it.created_at.isoformat(),
                    "on_disk": d.exists,
                    "size_bytes": d.size,
                }
                for it, d in zip(items, disk)
            ],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]No downloads yet.[/] Try [cyan]anime download <title>[/].")
        return
    colors = {"done": "green", "downloading": "yellow", "failed": "red", "queued": "dim"}
    table = Table(title="Downloads", title_justify="left", header_style="bold cyan")
    table.add_column("Title", style="bold")
    table.add_column("Ep", justify="right")
    table.add_column("Status")
    table.add_column("Size", justify="right")
    table.add_column("Path", style="dim")
    for it, d in zip(items, disk):
        status = it.status.value
        if status == "done" and not d.exists:
            # Not a failure — you deleted it — but it is not something you can
            # watch, and saying "done" implies it is.
            shown = "[yellow]gone from disk[/]"
        else:
            shown = f"[{colors.get(status, 'white')}]{status}[/]"
        table.add_row(
            it.anime.title.preferred, f"{it.episode:g}", shown,
            _human_size(d.size) if d.exists else "—", it.path or "—",
        )
    console.print(table)

    on_disk = [d for d in disk if d.exists]
    total = sum(d.size for d in on_disk)
    missing = sum(1 for it, d in zip(items, disk)
                  if it.status.value == "done" and not d.exists)
    summary = f"{len(on_disk)} file(s) on disk · {_human_size(total)}"
    if missing:
        summary += f" · {missing} recorded but gone"
    console.print(f"[dim]{summary}[/]")


async def _providers_health(as_json: bool) -> None:
    c = build_container()
    try:
        snapshot = await c.provider_manager.health_snapshot()
    finally:
        await c.aclose()
    if as_json:
        json.dump(snapshot, sys.stdout)
        sys.stdout.write("\n")
        return
    if not snapshot:
        console.print("[dim]No providers installed.[/]")
        return
    colors = {"closed": "green", "half-open": "yellow", "open": "red"}
    table = Table(title="Provider health", title_justify="left", header_style="bold cyan")
    table.add_column("Provider", style="bold")
    table.add_column("Priority", justify="right")
    table.add_column("Breaker")
    table.add_column("Fails", justify="right")
    for row in snapshot:
        status = row["status"]
        table.add_row(
            row["provider"],
            str(row["priority"]),
            f"[{colors.get(status, 'white')}]{status}[/]",
            str(row["consecutive_failures"]),
        )
    console.print(table)


async def _favorite_ls(as_json: bool) -> None:
    c = build_container()
    try:
        items = await c.library_service.favorites()
    finally:
        await c.aclose()
    if as_json:
        json.dump(
            [
                {
                    "anilist_id": it.anime.id.anilist,
                    "title": it.anime.title.preferred,
                    "added_at": it.added_at.isoformat(),
                    "note": it.note,
                }
                for it in items
            ],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]No favorites yet. Add one with[/] anime favorite add <title>")
        return
    _print_anime_table("Favorites", [it.anime for it in items])


# --------------------------------------------------------------------------- #
# Rendering helpers
# --------------------------------------------------------------------------- #
#: A range is expanded into one list entry per episode before anything is
#: fetched, so an unbounded one is an out-of-memory bug rather than a slow
#: download: `-e 1-999999999` allocates gigabytes and hangs without touching the
#: network. Two thousand is past the longest thing anyone asks for in one go —
#: One Piece is around 1140 — and well short of a typo.
MAX_EPISODE_SPAN = 2000


def _parse_episode_spec(spec: str) -> list[float]:
    """Parse an episode selector into an ordered, de-duplicated list.

    "5" -> [5]; "1-12" -> [1..12]; "1,3,5" -> [1,3,5]; "1-3,5" -> [1,2,3,5].
    Raises ValueError on anything that isn't a number or ``a-b`` range, on a
    negative episode, and on a range too large to mean anything.
    """
    out: list[float] = []
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token[1:]:  # a range (a-b), not a leading minus sign
            a, _, b = token.partition("-")
            start, end = int(float(a)), int(float(b))
            if end < start:
                start, end = end, start
            # Before building the list, not after: the point is to never
            # allocate it. A negative endpoint reaches here as `1--5`, which
            # partitions into a range running from -5.
            if start < 0:
                raise ValueError(f"{token!r} asks for a negative episode")
            if end - start + 1 > MAX_EPISODE_SPAN:
                raise ValueError(
                    f"{token!r} covers {end - start + 1} episodes; "
                    f"the limit is {MAX_EPISODE_SPAN}"
                )
            out.extend(float(n) for n in range(start, end + 1))
        else:
            n = float(token)
            if n < 0:
                raise ValueError(f"{token!r} is not an episode number")
            out.append(n)
    if len(out) > MAX_EPISODE_SPAN:
        raise ValueError(
            f"that selects {len(out)} episodes; the limit is {MAX_EPISODE_SPAN}"
        )
    # De-duplicate while preserving the order first seen.
    seen: set[float] = set()
    return [n for n in out if not (n in seen or seen.add(n))]


def _ep_list(numbers: list[float]) -> str:
    """Compact episode list: contiguous whole-numbered runs collapse to
    "1–12"; gaps and specials stay explicit ("1–3, 5, 13.5")."""
    parts: list[str] = []
    run_start = prev = None
    def flush():
        if run_start is None:
            return
        parts.append(
            f"{run_start:g}" if run_start == prev else f"{run_start:g}–{prev:g}"
        )
    for n in numbers:
        if prev is not None and n == prev + 1:
            prev = n
            continue
        flush()
        run_start = prev = n
    flush()
    return ", ".join(parts)


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
        "average_score": a.average_score,
        "studio": a.studio,
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
    # git-style sugar: `anime <query>` -> `anime play <query>`. Only a bare
    # first arg that isn't a known command (and isn't an option) is rewritten.
    argv = sys.argv[1:]
    known = _known_commands()
    if known and argv and argv[0] not in known and not argv[0].startswith("-"):
        # ...but a mistyped or guessed *command* must not become a search.
        hint = _command_suggestion(argv[0], known)
        if hint is not None:
            err.print(hint)
            raise SystemExit(2)
        sys.argv.insert(1, "play")
    try:
        app()
    except KeyboardInterrupt:
        # Ctrl-C is how people stop a search or a download. It should read as
        # deliberate, not dump a traceback as if anime-sh had crashed.
        console.print("\n[dim]Interrupted.[/]")
        raise SystemExit(130) from None
    except AnimeShError as e:
        # Our own errors already carry a human-readable message (bad config,
        # nothing playable, no such show) — show that, not a stack trace.
        console.print(f"[red]Error:[/] {e}")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
