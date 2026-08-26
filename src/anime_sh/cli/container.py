"""Composition root — the one place that knows both app and infra.

Assembles concrete adapters behind domain ports and hands app services their
dependencies. Kept out of `app/` on purpose so the import-linter contract can
forbid `app -> infra` without exceptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..app.download import DownloadService
from ..app.library import LibraryService
from ..app.playback import PlaybackService
from ..app.providers import ProviderManager
from ..app.search import SearchService
from ..app.sync import SyncService
from ..config import Config, load_config
from ..config.paths import cache_db_path, user_db_path
from ..infra import registry
from ..infra.cache.kv import KvCache
from ..infra.db.database import Database
from ..infra.db.downloads import SqliteDownloadStore
from ..infra.db.health import SqliteHealthStore
from ..infra.db.library import SqliteLibrary
from ..infra.downloader import FfmpegDownloader
from ..infra.http import HttpStreamProbe
from ..infra.metadata import AniListMetadata
from ..infra.players import MpvPlayer, NullPlayer
from ..infra.proxy import DeobfuscatingProxy
from ..infra.skiptimes import AniSkipSource
from ..infra.tracker import AniListTracker, load_token

log = logging.getLogger(__name__)


@dataclass(slots=True)
class Container:
    config: Config
    user_db: Database
    cache_db: Database
    library: SqliteLibrary
    library_service: LibraryService
    cache: KvCache
    metadata: AniListMetadata
    search: SearchService
    provider_manager: ProviderManager
    resolvers: list
    playback: PlaybackService
    download: DownloadService
    stream_proxy: DeobfuscatingProxy
    stream_probe: HttpStreamProbe
    skip_source: AniSkipSource
    sync: SyncService
    tracker: AniListTracker | None

    # Shutdown is reachable from more than one path (the TUI closes on quit and
    # again from its run loop's finally), so closing twice must be harmless.
    _closed: bool = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.stream_proxy.stop()
        await self.stream_probe.aclose()
        await self.skip_source.aclose()
        await self.metadata.aclose()
        if self.tracker is not None:
            await self.tracker.aclose()
        # Providers and resolvers each hold their own HTTP client. They were never
        # closed, so every run leaked those connections (and printed "unclosed
        # client" noise on exit). Plugins are third-party, so a missing or failing
        # aclose must not stop the rest of shutdown.
        for component in (*self.provider_manager.providers, *self.resolvers):
            closer = getattr(component, "aclose", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception as e:  # pragma: no cover - defensive
                log.debug("closing %s failed: %s", type(component).__name__, e)
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
    library_service = LibraryService(library)
    cache = KvCache(cache_db)

    metadata = AniListMetadata(cache=cache)
    search = SearchService(metadata)

    providers = registry.load_providers(disabled=config.providers.disabled)
    resolvers = _order_resolvers(
        registry.load_resolvers(disabled=config.resolvers.disabled)
    )

    provider_manager = ProviderManager(
        providers,
        parallel=config.providers.parallel,
        preferred=config.providers.preferred,
        candidates_timeout_s=config.providers.timeout_s,
        health_store=SqliteHealthStore(user_db),
    )

    stream_proxy = DeobfuscatingProxy()
    stream_probe = HttpStreamProbe()
    skip_source = AniSkipSource()

    # AniList list-sync is active whenever a token has been saved (anime auth
    # login) — that token is the opt-in. When active, playback pushes progress
    # on completion and `anime sync` can push/pull the whole list.
    token = load_token()
    tracker = AniListTracker(token) if token else None

    download_store = SqliteDownloadStore(user_db)
    playback = PlaybackService(
        providers=provider_manager,
        resolvers=resolvers,
        player=_make_player(config),
        library=library,
        quality=config.playback.quality,
        skip_intro=config.playback.skip_intro,
        skip_outro=config.playback.skip_outro,
        auto_next=config.playback.auto_next,
        stream_proxy=stream_proxy,
        probe=stream_probe,
        skip_source=skip_source,
        downloads=download_store,
        prefer_local=config.playback.prefer_downloads,
        tracker=tracker,
    )
    sync = SyncService(library, tracker)

    download = DownloadService(
        playback=playback,
        downloader=FfmpegDownloader(),
        store=download_store,
        library=library,
        download_dir=config.downloads.dir,
        stream_proxy=stream_proxy,
    )
    # Closing the loop the other way: playback prefers an episode already on
    # disk, and DownloadService is what knows where that would be. It has to
    # happen here because DownloadService is built from playback.
    playback.set_local_source(download.local_path)

    return Container(
        config=config,
        user_db=user_db,
        cache_db=cache_db,
        library=library,
        library_service=library_service,
        cache=cache,
        metadata=metadata,
        search=search,
        provider_manager=provider_manager,
        resolvers=resolvers,
        playback=playback,
        download=download,
        stream_proxy=stream_proxy,
        stream_probe=stream_probe,
        skip_source=skip_source,
        sync=sync,
        tracker=tracker,
    )
