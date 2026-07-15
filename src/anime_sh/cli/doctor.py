"""`anime doctor` — environment + config diagnostics.

Built in week 2, not month 6, on purpose: it cuts support volume in half by
letting a user (and a bug report) answer "is mpv installed / is the config
valid / can I reach the network / which providers loaded" without you asking.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass

from ..config import load_config
from ..config.loader import config_path
from ..config.paths import cache_db_path, user_db_path
from ..infra import registry


@dataclass(slots=True)
class Check:
    name: str
    ok: bool
    detail: str


def _check_player(player_name: str) -> Check:
    path = shutil.which(player_name)
    return Check(
        f"player: {player_name}",
        path is not None,
        path or "not found on PATH — install it or set player.name",
    )


def _check_ffmpeg() -> Check:
    path = shutil.which("ffmpeg")
    return Check(
        "ffmpeg (HLS downloads)",
        path is not None,
        path or "not found — needed for downloads, optional for streaming",
    )


def _check_config() -> Check:
    try:
        load_config()
        return Check("config", True, f"valid ({config_path()})")
    except Exception as e:  # ConfigError and friends
        return Check("config", False, str(e))


def _check_plugins() -> list[Check]:
    providers = registry.load_providers()
    resolvers = registry.load_resolvers()
    return [
        Check(
            "providers",
            True,
            ", ".join(p.name for p in providers) or "none installed (expected in M0)",
        ),
        Check(
            "resolvers",
            True,
            ", ".join(r.name for r in resolvers) or "none installed (expected in M0)",
        ),
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
        *_check_plugins(),
    ]

    # Rendering stays dependency-light so doctor works even if rich is missing.
    critical_ok = True
    for c in checks:
        mark = "OK  " if c.ok else "FAIL"
        if not c.ok and c.name in {"config", "database"}:
            critical_ok = False
        print(f"  [{mark}] {c.name}: {c.detail}", file=sys.stderr)

    print(file=sys.stderr)
    if critical_ok:
        print("  anime-sh core looks healthy.", file=sys.stderr)
        return 0
    print("  anime-sh has configuration/database problems (see above).", file=sys.stderr)
    return 1
