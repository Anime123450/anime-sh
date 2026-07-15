"""PlaybackService — the money path.

This is the whole thesis of anime-sh in one place: the user asks for an
episode, and behind the curtain we fan out to providers, walk a chain of
stream candidates trying resolver after resolver, and hand the first playable
stream to a player — never surfacing a broken host or dead provider.

The fallback chain in :meth:`_resolve_stream` is the single most important
piece of logic in the project and is unit-tested against fakes.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from ..domain.errors import NoStreamsFound, ResolverError
from ..domain.models import (
    Anime,
    Audio,
    Episode,
    Stream,
    StreamCandidate,
    WatchProgress,
)
from ..domain.ports import Library, Player, Resolver
from ..domain.ranking import pick_stream
from .providers import ProviderManager

# Writing progress on every mpv position tick would hammer SQLite; throttle.
_SAVE_INTERVAL_S = 5
# Past this fraction of an episode, count it as completed.
_COMPLETE_FRACTION = 0.9

log = logging.getLogger(__name__)


class ResolvedPlayback:
    """Result of resolving (but not yet playing) an episode — handy for tests
    and for `--json` output that stops short of launching a player."""

    __slots__ = ("episode", "stream", "resume_s")

    def __init__(self, episode: Episode, stream: Stream, resume_s: int) -> None:
        self.episode = episode
        self.stream = stream
        self.resume_s = resume_s


class PlaybackService:
    def __init__(
        self,
        *,
        providers: ProviderManager,
        resolvers: list[Resolver],
        player: Player,
        library: Library,
        quality: str = "best",
    ) -> None:
        self._providers = providers
        self._resolvers = resolvers
        self._player = player
        self._library = library
        self._quality = quality

    async def resolve(
        self, anime: Anime, episode_number: float, *, audio: Audio = Audio.SUB
    ) -> ResolvedPlayback:
        """Find a playable stream for an episode, or raise NoStreamsFound."""
        progress = await self._library.get_progress(anime.id, episode_number)
        resume_s = progress.position_s if progress and not progress.completed else 0

        refs = await self._providers.resolve_sources(anime, audio)
        if not refs:
            raise NoStreamsFound(f"no provider has {anime.title.preferred!r}")

        # Walk providers in priority order; for each, walk its candidate hosts.
        for ref in refs:
            episodes = await self._episodes(ref, anime.id)
            episode = _find_episode(episodes, episode_number)
            if episode is None:
                continue
            candidates = await self._providers.candidates_for(episode)
            stream = await self._resolve_stream(candidates)
            if stream is not None:
                return ResolvedPlayback(episode, stream, resume_s)

        raise NoStreamsFound(
            f"exhausted every provider/host for "
            f"{anime.title.preferred!r} ep {episode_number:g}"
        )

    async def play(
        self, anime: Anime, episode_number: float, *, audio: Audio = Audio.SUB
    ):
        """Resolve then launch the player. Returns a PlaybackHandle."""
        resolved = await self.resolve(anime, episode_number, audio=audio)
        title = f"{anime.title.preferred} - Episode {episode_number:g}"
        return await self._player.play(
            resolved.stream, title=title, start_s=resolved.resume_s
        )

    async def play_and_track(
        self, anime: Anime, episode_number: float, *, audio: Audio = Audio.SUB
    ) -> None:
        """Play an episode, persist progress until it ends, and record history.

        Progress is throttled to one write every few seconds, plus a final write
        on pause/EOF, so scrubbing never floods the database. The show's metadata
        is cached so the library renders it later without a network round-trip.
        """
        resolved = await self.resolve(anime, episode_number, audio=audio)
        await self._library.save_anime(anime)

        title = f"{anime.title.preferred} - Episode {episode_number:g}"
        handle = await self._player.play(
            resolved.stream, title=title, start_s=resolved.resume_s
        )
        provider = resolved.episode.provider_ref.provider

        last_saved = 0.0
        last_event = None
        try:
            async for ev in handle.events():
                last_event = ev
                now = time.monotonic()
                if ev.eof or (now - last_saved) >= _SAVE_INTERVAL_S:
                    await self._save(anime, episode_number, ev)
                    last_saved = now
                if ev.eof:
                    break
        finally:
            if last_event is not None:
                await self._save(anime, episode_number, last_event)
                await self._library.add_history(
                    anime.id,
                    episode_number,
                    provider=provider,
                    seconds_watched=max(last_event.position_s, 0),
                )

    async def _save(self, anime: Anime, episode_number: float, ev) -> None:
        duration = max(ev.duration_s, 0)
        completed = duration > 0 and (ev.position_s / duration) >= _COMPLETE_FRACTION
        await self._library.save_progress(
            WatchProgress(
                anime_id=anime.id,
                episode=episode_number,
                position_s=max(ev.position_s, 0),
                duration_s=duration,
                updated_at=datetime.now(timezone.utc),
                completed=completed,
            )
        )

    # -- the fallback chain (§7 step 5) ------------------------------------- #
    async def _resolve_stream(
        self, candidates: list[StreamCandidate]
    ) -> Stream | None:
        """Try each candidate host against each capable resolver. The first
        host that yields streams wins; a failing host is skipped silently."""
        for candidate in candidates:
            for resolver in self._resolvers:
                if not resolver.handles(candidate):
                    continue
                try:
                    streams = await resolver.resolve(candidate)
                except ResolverError as e:
                    log.debug(
                        "resolver %s failed on %s: %s",
                        resolver.name,
                        candidate.host,
                        e,
                    )
                    continue
                except Exception as e:  # a bad resolver must not kill playback
                    log.warning(
                        "resolver %s crashed on %s: %s",
                        resolver.name,
                        candidate.host,
                        e,
                    )
                    continue
                chosen = pick_stream(streams, self._quality)
                if chosen is not None:
                    return chosen
        return None

    async def _episodes(self, ref, anime_id) -> list[Episode]:
        provider = self._providers._by_name(ref.provider)
        if provider is None:
            return []
        try:
            return await provider.episodes(ref, anime_id)
        except Exception as e:
            log.warning("provider %s episodes failed: %s", ref.provider, e)
            return []


def _find_episode(episodes: list[Episode], number: float) -> Episode | None:
    return next((e for e in episodes if e.number == number), None)
