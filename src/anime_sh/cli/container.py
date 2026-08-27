"""Composition root — the one place that knows both app and infra.

Assembles concrete adapters behind domain ports and hands app services their
dependencies. Kept out of `app/` on purpose so the import-linter contract can
forbid `app -> infra` without exceptions.

Everything here is built **on first access**, not up front. `anime continue`
reads two tables out of SQLite; it used to pay 470 ms first for httpx, a plugin
scan, an mpv lookup and an AniList tracker it never touched. Each attribute is a
`cached_property` whose heavy imports live inside it, so a command pays for the
slice it actually uses and nothing else. The attribute names and their types are
exactly what they were, so every `c.metadata` / `c.playback` call site is
unchanged.
"""

from __future__ import annotations

import logging
from functools import cached_property

from ..config import Config, load_config

log = logging.getLogger(__name__)


def _order_resolvers(resolvers: list) -> list:
    # Host-specific resolvers first; the generic passthrough runs last.
    return sorted(resolvers, key=lambda r: r.name == "generic")


class Container:
    """Lazily-built object graph. Attribute access is the trigger."""

    def __init__(self, config: Config | None = None) -> None:
        self.config = config or load_config()
        self._closed = False

    # -- databases ---------------------------------------------------------- #
    @cached_property
    def user_db(self):
        from ..config.paths import user_db_path
        from ..infra.db.database import Database

        return Database(user_db_path(), migrations_dir="migrations")

    @cached_property
    def cache_db(self):
        from ..config.paths import cache_db_path
        from ..infra.db.database import Database

        return Database(cache_db_path(), migrations_dir="migrations_cache")

    @cached_property
    def library(self):
        from ..infra.db.library import SqliteLibrary

        return SqliteLibrary(self.user_db)

    @cached_property
    def library_service(self):
        from ..app.library import LibraryService

        return LibraryService(self.library)

    @cached_property
    def cache(self):
        from ..infra.cache.kv import KvCache

        return KvCache(self.cache_db)

    @cached_property
    def download_store(self):
        from ..infra.db.downloads import SqliteDownloadStore

        return SqliteDownloadStore(self.user_db)

    # -- metadata / search -------------------------------------------------- #
    @cached_property
    def metadata(self):
        from ..infra.metadata import AniListMetadata

        return AniListMetadata(cache=self.cache)

    @cached_property
    def search(self):
        from ..app.search import SearchService

        return SearchService(self.metadata)

    # -- providers / resolvers ---------------------------------------------- #
    @cached_property
    def providers(self) -> list:
        from ..infra import registry

        return registry.load_providers(disabled=self.config.providers.disabled)

    @cached_property
    def resolvers(self) -> list:
        from ..infra import registry

        return _order_resolvers(
            registry.load_resolvers(disabled=self.config.resolvers.disabled)
        )

    @cached_property
    def provider_manager(self):
        from ..app.providers import ProviderManager
        from ..infra.db.health import SqliteHealthStore

        return ProviderManager(
            self.providers,
            parallel=self.config.providers.parallel,
            preferred=self.config.providers.preferred,
            candidates_timeout_s=self.config.providers.timeout_s,
            health_store=SqliteHealthStore(self.user_db),
        )

    # -- streaming plumbing ------------------------------------------------- #
    @cached_property
    def stream_proxy(self):
        from ..infra.proxy import DeobfuscatingProxy

        return DeobfuscatingProxy()

    @cached_property
    def stream_probe(self):
        from ..infra.http import HttpStreamProbe

        return HttpStreamProbe()

    @cached_property
    def skip_source(self):
        from ..infra.skiptimes import AniSkipSource

        return AniSkipSource()

    @cached_property
    def player(self):
        from ..infra.players import MpvPlayer, NullPlayer

        name = self.config.player.name.lower()
        if name == "mpv":
            player = MpvPlayer(binary="mpv", extra_args=self.config.player.args)
            if player.available():
                return player
            log.warning("mpv not found on PATH; falling back to NullPlayer")
            return NullPlayer()
        # vlc/iina/potplayer adapters arrive later; NullPlayer keeps wiring valid.
        log.warning("player %r not yet implemented; using NullPlayer", name)
        return NullPlayer()

    # -- tracker ------------------------------------------------------------ #
    @cached_property
    def tracker(self):
        """AniList list-sync is active whenever a token has been saved (`anime
        auth login`) — that token is the opt-in. When active, playback pushes
        progress on completion and `anime sync` can push/pull the whole list."""
        from ..infra.tracker import AniListTracker, load_token

        token = load_token()
        return AniListTracker(token) if token else None

    @cached_property
    def sync(self):
        from ..app.sync import SyncService

        return SyncService(self.library, self.tracker)

    # -- playback / download ------------------------------------------------ #
    @cached_property
    def playback(self):
        from ..app.playback import PlaybackService

        playback = PlaybackService(
            providers=self.provider_manager,
            resolvers=self.resolvers,
            player=self.player,
            library=self.library,
            quality=self.config.playback.quality,
            skip_intro=self.config.playback.skip_intro,
            skip_outro=self.config.playback.skip_outro,
            auto_next=self.config.playback.auto_next,
            stream_proxy=self.stream_proxy,
            probe=self.stream_probe,
            skip_source=self.skip_source,
            downloads=self.download_store,
            prefer_local=self.config.playback.prefer_downloads,
            tracker=self.tracker,
        )
        # Closing the loop the other way: playback prefers an episode already on
        # disk, and DownloadService is what knows where that would be.
        #
        # Deliberately a late-bound lambda rather than `self.download.local_path`.
        # `download` is built *from* `playback`, so resolving it here would
        # recurse; deferring to call time means `playback` is already cached by
        # the point the lambda runs. Passing the bound method eagerly would
        # deadlock the graph — and dropping this line entirely is worse, because
        # it silently disables offline playback rather than failing loudly.
        playback.set_local_source(lambda *a, **kw: self.download.local_path(*a, **kw))
        return playback

    @cached_property
    def download(self):
        from ..app.download import DownloadService
        from ..infra.downloader import FfmpegDownloader

        return DownloadService(
            # A factory, not the service: `anime downloads` only lists rows,
            # and must not build the resolve chain to do it.
            playback=lambda: self.playback,
            downloader=FfmpegDownloader(),
            store=self.download_store,
            library=self.library,
            download_dir=self.config.downloads.dir,
            # Also a factory: listing downloads must not start the proxy.
            stream_proxy=lambda: self.stream_proxy,
        )

    # -- shutdown ----------------------------------------------------------- #
    async def aclose(self) -> None:
        """Close only what was actually built.

        Shutdown is reachable from more than one path (the TUI closes on quit and
        again from its run loop's finally), so closing twice must be harmless.
        Touching an attribute here would *construct* it purely in order to close
        it — starting a proxy thread and an HTTP client during shutdown — so
        every component is looked up in the instance dict instead, which is where
        `cached_property` stores what it has already made.
        """
        if self._closed:
            return
        self._closed = True
        built = self.__dict__

        if (proxy := built.get("stream_proxy")) is not None:
            proxy.stop()
        for name in ("stream_probe", "skip_source", "metadata", "tracker"):
            component = built.get(name)
            if component is not None:
                await component.aclose()

        # A plugin's aclose must not stop the rest of shutdown.
        for component in (*built.get("providers", ()), *built.get("resolvers", ())):
            closer = getattr(component, "aclose", None)
            if closer is None:
                continue
            try:
                await closer()
            except Exception as e:  # pragma: no cover - defensive
                log.debug("closing %s failed: %s", type(component).__name__, e)

        for name in ("user_db", "cache_db"):
            db = built.get(name)
            if db is not None:
                await db.close()


def build_container(config: Config | None = None) -> Container:
    """Wire everything. Nothing is constructed until it is first asked for."""
    return Container(config)
