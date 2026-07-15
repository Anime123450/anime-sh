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

from ..domain.errors import NoStreamsFound, ResolverError
from ..domain.models import (
    Anime,
    Audio,
    Episode,
    Stream,
    StreamCandidate,
)
from ..domain.ports import Library, Player, Resolver
from ..domain.ranking import pick_stream
from .providers import ProviderManager

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
            episodes = await self._episodes(ref)
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

    async def _episodes(self, ref) -> list[Episode]:
        provider = self._providers._by_name(ref.provider)
        if provider is None:
            return []
        try:
            return await provider.episodes(ref)
        except Exception as e:
            log.warning("provider %s episodes failed: %s", ref.provider, e)
            return []


def _find_episode(episodes: list[Episode], number: float) -> Episode | None:
    return next((e for e in episodes if e.number == number), None)
