"""`anime doctor` — environment + config diagnostics.

Built in week 2, not month 6, on purpose: it cuts support volume in half by
letting a user (and a bug report) answer "is mpv installed / is the config
valid / is the database healthy / which providers loaded" without you asking.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass

from ..config import Config, load_config
from ..config.loader import config_path
from ..config.paths import cache_db_path, user_db_path
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


def run_doctor() -> int:
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

    # Rendering stays dependency-light so doctor works even if rich is missing.
    critical_ok = True
    for c in checks:
        mark = "OK  " if c.ok else "FAIL"
        if not c.ok and c.name in {"config", "database", "providers", "resolvers"}:
            critical_ok = False
        print(f"  [{mark}] {c.name}: {c.detail}", file=sys.stderr)

    print(file=sys.stderr)
    if critical_ok:
        print("  anime-sh core looks healthy.", file=sys.stderr)
        return 0
    print("  anime-sh has configuration/database problems (see above).", file=sys.stderr)
    return 1
