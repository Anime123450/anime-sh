"""Composition root — the one place that knows both app and infra.

Assembles concrete adapters behind domain ports and hands app services their
dependencies. Kept out of `app/` on purpose so the import-linter contract can
forbid `app -> infra` without exceptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..app.playback import PlaybackService
from ..app.providers import ProviderManager
from ..app.search import SearchService
from ..config import Config, load_config
from ..config.paths import cache_db_path, user_db_path
from ..infra import registry
from ..infra.cache.kv import KvCache
from ..infra.db.database import Database
from ..infra.db.library import SqliteLibrary
from ..infra.metadata import AniListMetadata
from ..infra.players import MpvPlayer, NullPlayer

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Container:
    config: Config
    user_db: Database
    cache_db: Database
    library: SqliteLibrary
    cache: KvCache
    metadata: AniListMetadata
    search: SearchService
    provider_manager: ProviderManager
    resolvers: list
    playback: PlaybackService

    async def aclose(self) -> None:
        await self.metadata.aclose()
        await self.user_db.close()
        await self.cache_db.close()


def _make_player(config: Config):
    name = config.player.name.lower()
    if name == "mpv":
        player = MpvPlayer(binary="mpv", extra_args=config.player.args)
        if player.available():
            return player
        log.warning("mpv not found on PATH; falling back to NullPlayer")
        return NullPlayer()
    # vlc/iina/potplayer adapters arrive later; NullPlayer keeps wiring valid.
    log.warning("player %r not yet implemented; using NullPlayer", name)
    return NullPlayer()


def _order_resolvers(resolvers: list) -> list:
    # Host-specific resolvers first; the generic passthrough runs last.
    return sorted(resolvers, key=lambda r: r.name == "generic")


def build_container(config: Config | None = None) -> Container:
    """Wire everything. No I/O happens until a DB is first connected."""
    config = config or load_config()

    user_db = Database(user_db_path(), migrations_dir="migrations")
    cache_db = Database(cache_db_path(), migrations_dir="migrations_cache")

    library = SqliteLibrary(user_db)
    cache = KvCache(cache_db)

    metadata = AniListMetadata()
    search = SearchService(metadata)

    providers = registry.load_providers(disabled=config.providers.disabled)
    resolvers = _order_resolvers(
        registry.load_resolvers(disabled=config.resolvers.disabled)
    )

    provider_manager = ProviderManager(
        providers,
        parallel=config.providers.parallel,
        candidates_timeout_s=config.providers.timeout_s,
    )

    playback = PlaybackService(
        providers=provider_manager,
        resolvers=resolvers,
        player=_make_player(config),
        library=library,
        quality=config.playback.quality,
    )

    return Container(
        config=config,
        user_db=user_db,
        cache_db=cache_db,
        library=library,
        cache=cache,
        metadata=metadata,
        search=search,
        provider_manager=provider_manager,
        resolvers=resolvers,
        playback=playback,
    )
