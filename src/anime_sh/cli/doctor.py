"""`anime doctor` — environment + config diagnostics.

Built in week 2, not month 6, on purpose: it cuts support volume in half by
letting a user (and a bug report) answer "is mpv installed / is the config
valid / is the database healthy / which providers loaded" without you asking.

``--streams`` answers the other half — *can I actually watch something right
now?* — by really resolving an episode through each provider from this machine.
The nightly canary cannot answer that for you: it runs from a datacenter IP,
where anizone serves a Cloudflare interstitial it never shows a home
connection. When your machine and the status badge disagree, your machine is
the one that matters.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass

from ..config import Config, load_config
from ..config.loader import config_path
from ..config.paths import cache_db_path, user_db_path
from ..domain.errors import NoStreamsFound
from ..infra import registry


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def install_hint(tool: str) -> str:
    """The command that would actually install ``tool`` on this machine.

    "not found on PATH" is a diagnosis, not help. The two things anime-sh needs
    from outside itself are mpv and ffmpeg, and the person most likely to be
    reading this line is the one who just downloaded a single .exe precisely so
    they would not have to think about any of this.

    Picks by what is present rather than by platform, because a Windows user may
    have winget, scoop, chocolatey, or none of them, and naming a package
    manager they do not have is no better than naming none.
    """
    if sys.platform == "win32":
        for manager, template in (
            ("winget", "winget install {pkg}"),
            ("scoop", "scoop install {pkg}"),
            ("choco", "choco install {pkg}"),
        ):
            if shutil.which(manager):
                # winget has no package plainly called `mpv`; the maintained
                # Windows build is published as `shinchiro.mpv`, which is what
                # the README tells people to install. A hint that fails when
                # pasted costs the reader a round of trying it.
                pkg = "shinchiro.mpv" if (manager == "winget" and tool == "mpv") else tool
                return template.format(pkg=pkg)
        return f"install {tool} and put it on PATH — https://mpv.io" if tool == "mpv"             else f"install {tool} and put it on PATH"
    if sys.platform == "darwin":
        return f"brew install {tool}"
    return f"sudo apt install {tool}   (or your distro's equivalent)"


def _check_player(player_name: str) -> Check:
    path = shutil.which(player_name)
    if path:
        return Check(f"player: {player_name}", True, path)
    hint = install_hint(player_name) if player_name == "mpv" else "install it"
    return Check(
        f"player: {player_name}",
        False,
        f"not found on PATH — needed to play anything. Try: {hint}",
    )


def _check_ffmpeg() -> Check:
    path = shutil.which("ffmpeg")
    if path:
        return Check("ffmpeg (HLS downloads)", True, path)
    return Check(
        "ffmpeg (HLS downloads)",
        False,
        "not found — only `anime download` needs it. "
        f"Try: {install_hint('ffmpeg')}",
    )


def _check_config() -> Check:
    try:
        load_config()
        return Check("config", True, f"valid ({config_path()})")
    except Exception as e:  # ConfigError and friends
        return Check("config", False, str(e))


def _plugin_detail(active: list, disabled: list[str], kind: str) -> str:
    """One line describing what will actually be used, and why anything missing
    is missing."""
    names = ", ".join(sorted(p.name for p in active))
    off = ", ".join(sorted(disabled))
    if names and off:
        return f"{names}  ({off} disabled in config)"
    if names:
        return names
    if off:
        return f"none active — {off} disabled in config"
    return f"none installed — anime-sh cannot {kind} without one"


def _check_plugins(cfg: Config | None) -> list[Check]:
    """What the app will *actually* load, not what happens to be installed.

    This used to call `load_providers()` with no arguments, ignoring the
    `disabled` lists in config — so it reported a provider as loaded that the
    app would never use, which is precisely the question this command exists to
    answer for a bug report. Disabled plugins are now named rather than silently
    dropped, because "why is anizone not being used" is exactly the thing
    someone runs doctor to find out.
    """
    disabled_p = list(cfg.providers.disabled) if cfg else []
    disabled_r = list(cfg.resolvers.disabled) if cfg else []
    providers = registry.load_providers(disabled=disabled_p)
    resolvers = registry.load_resolvers(disabled=disabled_r)
    return [
        # No provider means nothing can ever be played — a broken install, not a
        # cosmetic detail, so it fails rather than reporting "healthy".
        Check("providers", bool(providers),
              _plugin_detail(providers, disabled_p, "find episodes")),
        Check("resolvers", bool(resolvers),
              _plugin_detail(resolvers, disabled_r, "turn a source into a stream")),
    ]


async def _check_databases() -> Check:
    from ..infra.db.database import Database

    try:
        user = Database(user_db_path(), migrations_dir="migrations")
        cache = Database(cache_db_path(), migrations_dir="migrations_cache")
        uv = await user.schema_version()
        cv = await cache.schema_version()
        await user.close()
        await cache.close()
        return Check(
            "database",
            True,
            f"user schema v{uv}, cache schema v{cv}",
        )
    except Exception as e:
        return Check("database", False, str(e))


# A show every provider should carry, so a miss here is about the provider and
# not about the title being obscure.
STREAM_CHECK_TITLE = "Frieren"
# Per-provider budget. Long enough for a slow-but-working host, short enough
# that checking four providers is not a coffee break.
STREAM_CHECK_TIMEOUT_S = 25.0


async def _check_streams() -> list[Check]:
    """Really resolve an episode through each provider, from this machine.

    Deliberately the app's own path — ``list_sources`` then ``resolve`` — rather
    than a reimplementation. A diagnostic that takes a different route to the
    answer can pass while the thing it is diagnosing is broken.
    """
    from .container import build_container

    container = build_container()
    try:
        try:
            anime = await container.search.best_match(STREAM_CHECK_TITLE)
        except Exception as e:
            # Same rule the canary learned: failing the identity lookup means no
            # provider was contacted, so it is not a verdict on any of them.
            return [Check("streams", False, f"metadata unavailable, nothing checked: {e}")]
        if anime is None:
            return [Check("streams", False, f"no match for {STREAM_CHECK_TITLE!r}")]

        try:
            options = await container.playback.list_sources(anime)
        except Exception as e:
            return [Check("streams", False, f"no provider answered: {e}")]
        if not options:
            return [Check("streams", False, "no provider has a source for the check title")]

        # One option per provider — the best one, which is what playback uses.
        best: dict[str, object] = {}
        for option in options:
            best.setdefault(option.provider, option)

        checks = []
        for name, option in best.items():
            checks.append(await _check_one_stream(container, anime, name, option))
        return checks
    finally:
        await container.aclose()


async def _check_one_stream(container, anime, name: str, option) -> Check:
    import time

    started = time.monotonic()
    try:
        async with asyncio.timeout(STREAM_CHECK_TIMEOUT_S):
            resolved = await container.playback.resolve(anime, 1.0, source=option)
    except (TimeoutError, asyncio.TimeoutError):
        return Check(f"stream {name}", False, f"timed out after {STREAM_CHECK_TIMEOUT_S:g}s")
    except NoStreamsFound:
        # The provider has the show and offered hosts; none of them produced a
        # stream. Its own message names the title, which is noise here — the
        # title is the one thing the reader already knows.
        return Check(f"stream {name}", False, "found the show, but no host played")
    except Exception as e:
        return Check(f"stream {name}", False, f"{type(e).__name__}: {e}")
    took = time.monotonic() - started
    host = resolved.stream.url.split("/")[2] if "//" in resolved.stream.url else "?"
    return Check(f"stream {name}", True, f"playable via {host} ({took:.1f}s)")


def run_doctor(check_streams: bool = False) -> int:
    """Return a process exit code: 0 if all critical checks pass."""
    cfg = None
    try:
        cfg = load_config()
    except Exception:
        pass
    player_name = cfg.player.name if cfg else "mpv"

    checks: list[Check] = [
        _check_config(),
        _check_player(player_name),
        _check_ffmpeg(),
        asyncio.run(_check_databases()),
        *_check_plugins(cfg),
    ]

    stream_checks: list[Check] = []
    if check_streams:
        print("  checking providers (this hits the real sites)…", file=sys.stderr)
        stream_checks = asyncio.run(_check_streams())

    # Rendering stays dependency-light so doctor works even if rich is missing.
    critical_ok = True
    for c in checks:
        mark = "OK  " if c.ok else "FAIL"
        if not c.ok and c.name in {"config", "database", "providers", "resolvers"}:
            critical_ok = False
        print(f"  [{mark}] {c.name}: {c.detail}", file=sys.stderr)
    for c in stream_checks:
        print(f"  [{'OK  ' if c.ok else 'DEAD'}] {c.name}: {c.detail}", file=sys.stderr)

    print(file=sys.stderr)
    if stream_checks:
        # A dead provider is the normal operating state, not a broken install —
        # so this never fails the command. What matters is whether *something*
        # can play, which is the question the user actually asked.
        playable = [c for c in stream_checks if c.ok]
        if playable:
            names = ", ".join(c.name.removeprefix("stream ") for c in playable)
            print(f"  You can watch right now — working: {names}.", file=sys.stderr)
        else:
            print(
                "  Nothing can play from this connection right now. Providers "
                "break constantly; try again later, or `anime providers ls`.",
                file=sys.stderr,
            )
    if critical_ok:
        print("  anime-sh core looks healthy.", file=sys.stderr)
        return 0
    print("  anime-sh has configuration/database problems (see above).", file=sys.stderr)
    return 1
