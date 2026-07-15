"""Composition root — the one place that knows both app and infra.

Assembles concrete adapters behind domain ports and hands app services their
dependencies. Kept out of `app/` on purpose so the import-linter contract can
forbid `app -> infra` without exceptions.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..app.playback import PlaybackService
from ..app.providers import ProviderManager
from ..config import Config, load_config
from ..config.paths import cache_db_path, user_db_path
from ..infra import registry
from ..infra.cache.kv import KvCache
from ..infra.db.database import Database
from ..infra.db.library import SqliteLibrary
from ..infra.players import NullPlayer


@dataclass(slots=True)
class Container:
    config: Config
    user_db: Database
    cache_db: Database
    library: SqliteLibrary
    cache: KvCache
    provider_manager: ProviderManager
    resolvers: list
    playback: PlaybackService

    async def aclose(self) -> None:
        await self.user_db.close()
        await self.cache_db.close()


def build_container(config: Config | None = None) -> Container:
    """Wire everything. No I/O happens until a DB is first connected."""
    config = config or load_config()

    user_db = Database(user_db_path(), migrations_dir="migrations")
    cache_db = Database(cache_db_path(), migrations_dir="migrations_cache")

    library = SqliteLibrary(user_db)
    cache = KvCache(cache_db)

    providers = registry.load_providers(disabled=config.providers.disabled)
    resolvers = registry.load_resolvers(disabled=config.resolvers.disabled)

    provider_manager = ProviderManager(
        providers,
        parallel=config.providers.parallel,
        candidates_timeout_s=config.providers.timeout_s,
    )

    playback = PlaybackService(
        providers=provider_manager,
        resolvers=resolvers,
        player=NullPlayer(),  # real players land in M1
        library=library,
        quality=config.playback.quality,
    )

    return Container(
        config=config,
        user_db=user_db,
        cache_db=cache_db,
        library=library,
        cache=cache,
        provider_manager=provider_manager,
        resolvers=resolvers,
        playback=playback,
    )
