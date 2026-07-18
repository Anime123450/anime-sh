"""Typer entry point.

The CLI is one adapter onto the app services and holds no domain logic. Bare
``anime <query>`` is sugar for ``anime play <query>`` (rewritten in ``main``).
Bare ``anime`` with no args will launch the TUI (M4); for now it shows help.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random as rng
import sys
from datetime import date, datetime, timedelta, timezone

import typer
from rich.console import Console
from rich.table import Table

from .. import __version__
from ..config import load_config
from ..config.loader import config_path
from ..domain.errors import AnimeShError
from ..domain.models import Audio, Season, WatchProgress
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
favorite_app = typer.Typer(help="Manage favorites.")
auth_app = typer.Typer(help="Link an AniList account for watch-status sync.")
sync_app = typer.Typer(help="Sync watch progress with AniList.")
app.add_typer(config_app, name="config")
app.add_typer(providers_app, name="providers")
app.add_typer(favorite_app, name="favorite")
app.add_typer(auth_app, name="auth")
app.add_typer(sync_app, name="sync")

console = Console()
err = Console(stderr=True)

KNOWN_COMMANDS = {
    "version", "doctor", "config", "providers", "search", "play", "trending",
    "history", "favorite", "continue", "resume", "download", "downloads",
    "seasonal", "calendar", "random", "sources", "auth", "sync", "mark", "stats",
}


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    # Bare `anime` on a real terminal launches the TUI; otherwise show help.
    if not sys.stdout.isatty():
        typer.echo(ctx.get_help())
        return
    try:
        _launch_tui()
    except ImportError:
        err.print("[yellow]The TUI needs extra deps:[/] pip install 'anime-sh[tui]'")
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
    )
    asyncio.run(run_tui(services, theme=config.ui.theme))


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


@config_app.command("set")
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


@providers_app.command("health")
def providers_health(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show each provider's circuit-breaker status (persisted across runs)."""
    asyncio.run(_providers_health(as_json))


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
    limit: int = typer.Option(20, "-n", "--limit"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Search AniList (no providers touched — instant).

    With filters and no title it browses, e.g.
    `anime search --genre action --year 2024 --sort score`.
    """
    asyncio.run(_search(query, genre, year, fmt, status, sort, limit, as_json))


@app.command()
def trending(
    limit: int = typer.Option(20, "-n", "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show trending anime from AniList."""
    asyncio.run(_trending(limit, as_json))


@app.command()
def seasonal(
    season: str = typer.Option(None, "--season", help="winter|spring|summer|fall"),
    year: int = typer.Option(None, "--year"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show a season's anime (defaults to the current season)."""
    asyncio.run(_seasonal(season, year, as_json))


@app.command()
def calendar(
    days: int = typer.Option(7, "-d", "--days", help="Days ahead to show."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show the upcoming airing schedule."""
    asyncio.run(_calendar(days, as_json))


@app.command()
def random(
    play: bool = typer.Option(False, "--play", help="Play episode 1 of the pick."),
) -> None:
    """Surprise me — pick a random anime from what's trending."""
    asyncio.run(_random(play))


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


@app.command(name="continue")
def continue_watching(
    as_json: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(20, "-n", "--limit"),
) -> None:
    """Show episodes you've started but not finished."""
    asyncio.run(_continue(limit, as_json))


@app.command()
def resume(
    dub: bool = typer.Option(False, "--dub"),
    quality: str = typer.Option(None, "-q", "--quality"),
) -> None:
    """Resume the most recently watched unfinished episode."""
    asyncio.run(_resume(dub, quality))


@app.command()
def history(
    as_json: bool = typer.Option(False, "--json"),
    limit: int = typer.Option(50, "-n", "--limit"),
) -> None:
    """Show your watch history."""
    asyncio.run(_history(limit, as_json))


@app.command()
def sources(
    query: str = typer.Argument(..., help="Title to list sources for."),
    dub: bool = typer.Option(False, "--dub"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List every provider entry that matches a title (the source picker)."""
    asyncio.run(_sources(query, dub, as_json))


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
    asyncio.run(_auth_login(client_id, secret, token, no_browser))


@auth_app.command("status")
def auth_status() -> None:
    """Show whether an AniList account is linked (and who)."""
    asyncio.run(_auth_status())


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
    asyncio.run(_sync("push"))


@sync_app.command("pull")
def sync_pull() -> None:
    """Import your AniList list into the local library."""
    asyncio.run(_sync("pull"))


@app.command()
def download(
    query: str = typer.Argument(..., help="Title to download."),
    episode: float = typer.Option(1.0, "-e", "--episode"),
    dub: bool = typer.Option(False, "--dub"),
    quality: str = typer.Option(None, "-q", "--quality"),
) -> None:
    """Download an episode to disk (ffmpeg)."""
    asyncio.run(_download(query, episode, dub, quality))


@app.command()
def downloads(as_json: bool = typer.Option(False, "--json")) -> None:
    """List downloads."""
    asyncio.run(_downloads(as_json))


@app.command()
def stats(as_json: bool = typer.Option(False, "--json")) -> None:
    """Summarize your watch history: episodes, hours, top providers & genres."""
    asyncio.run(_stats(as_json))


@app.command()
def mark(
    query: str = typer.Argument(..., help="Title to mark."),
    episode: float = typer.Option(..., "-e", "--episode", help="Episode watched."),
    single: bool = typer.Option(
        False, "--single", help="Mark only this episode (not 1..N)."
    ),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark progress without playing — catch up to an episode you watched
    elsewhere. Sets episodes 1..N complete locally and, if AniList is linked,
    pushes your progress there too."""
    asyncio.run(_mark(query, episode, single, as_json))


@favorite_app.command("add")
def favorite_add(query: str = typer.Argument(..., help="Title to favorite.")) -> None:
    """Add the best match for a title to favorites."""
    asyncio.run(_favorite_add(query))


@favorite_app.command("rm")
def favorite_rm(query: str = typer.Argument(..., help="Title to remove.")) -> None:
    """Remove a favorite by title."""
    asyncio.run(_favorite_rm(query))


@favorite_app.command("ls")
def favorite_ls(as_json: bool = typer.Option(False, "--json")) -> None:
    """List favorites."""
    asyncio.run(_favorite_ls(as_json))


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


async def _mark(query: str, episode: float, single: bool, as_json: bool) -> None:
    c = build_container()
    try:
        anime = await c.search.best_match(query)
        if anime is None:
            err.print(f"[red]No anime found for[/] {query!r}")
            raise typer.Exit(code=1)
        numbers = await c.library_service.mark_watched(anime, episode, single=single)
        synced = False
        if c.tracker is not None and anime.id.anilist is not None:
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
        span = f"episode {episode:g}" if single else f"episodes 1–{episode:g}"
        tail = " [dim](synced to AniList)[/]" if synced else ""
        console.print(f"[green]Marked[/] {anime.title.preferred} {span} watched{tail}.")
    finally:
        await c.aclose()


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

        if not resolve_only:
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
        err.print(f"[cyan]⬇[/] {anime.title.preferred} — Episode {episode:g}")
        with console.status("Resolving & downloading… (ffmpeg)", spinner="dots"):
            dest = await c.download.download(anime, episode, audio=audio)
        console.print(f"[green]✓ Saved[/] {dest}")
    except AnimeShError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(code=2)
    finally:
        await c.aclose()


async def _downloads(as_json: bool) -> None:
    c = build_container()
    try:
        items = await c.download.history()
    finally:
        await c.aclose()
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
                }
                for it in items
            ],
            sys.stdout,
        )
        sys.stdout.write("\n")
        return
    if not items:
        console.print("[dim]No downloads yet.[/]")
        return
    colors = {"done": "green", "downloading": "yellow", "failed": "red", "queued": "dim"}
    table = Table(title="Downloads", title_justify="left", header_style="bold cyan")
    table.add_column("Title", style="bold")
    table.add_column("Ep", justify="right")
    table.add_column("Status")
    table.add_column("Path", style="dim")
    for it in items:
        table.add_row(
            it.anime.title.preferred, f"{it.episode:g}",
            f"[{colors.get(it.status.value, 'white')}]{it.status.value}[/]",
            it.path or "—",
        )
    console.print(table)


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
    # git-style sugar: `anime <query>` -> `anime play <query>`.
    argv = sys.argv[1:]
    if argv and argv[0] not in KNOWN_COMMANDS and not argv[0].startswith("-"):
        sys.argv.insert(1, "play")
    app()


if __name__ == "__main__":
    main()
