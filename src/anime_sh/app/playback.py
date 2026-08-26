"""PlaybackService — the money path.

This is the whole thesis of anime-sh in one place: the user asks for an
episode, and behind the curtain we fan out to providers, resolve stream
candidates, and hand the first playable stream to a player — never surfacing a
broken host or dead provider.

The candidate walk in :meth:`_candidate_streams` + the play loop in
:meth:`_play_episode` are the heart of the project. Two things keep it fast and
honest:

* Within a provider the candidate hosts are **resolved concurrently** (bounded),
  so one slow or dead embed no longer blocks the hosts behind it — but results
  are still yielded best-first, and later providers stay untouched until the
  earlier ones are exhausted.
* Each resolved stream is **pre-flighted** by an optional probe, so a CDN that is
  already dead (403/404/gone) is dropped here instead of costing the player its
  full confirm timeout. The probe only ever rejects a definitive dead response.

Then the player plays the first stream that *actually plays* — skipping anything
that resolves but won't start — instead of freezing. Unit-tested against fakes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from ..domain.errors import NoStreamsFound, PlayerError, ResolverError
from ..domain.models import (
    Anime,
    Audio,
    Episode,
    ProviderRef,
    SourceOption,
    Stream,
    StreamCandidate,
    StreamKind,
    WatchProgress,
)
from ..domain.ports import DownloadStore, Library, Player, Resolver, Tracker
from ..domain.ranking import pick_stream
from .providers import ProviderManager

# Writing progress on every mpv position tick would hammer SQLite; throttle.
_SAVE_INTERVAL_S = 5
# Past this fraction of an episode, count it as completed.
_COMPLETE_FRACTION = 0.9
# How many resolved streams to try before giving up on an episode.
_MAX_STREAM_ATTEMPTS = 8
# How long to wait for a stream to actually start playing before abandoning it.
_CONFIRM_TIMEOUT_S = 25.0
# How long to let one resolver work on one candidate before abandoning it — a
# blocked/dead host (e.g. an ISP-blocked embed) must not stall the whole walk.
_RESOLVE_TIMEOUT_S = 10.0
# How many of a provider's candidate hosts to resolve at once. Racing them means
# one slow/dead embed no longer blocks the hosts behind it in line.
_RESOLVE_CONCURRENCY = 4
# Recorded as the "provider" for an episode played off the disk, so history and
# `anime stats` can tell the two apart rather than crediting whichever provider
# happened to fetch it originally.
_LOCAL_PROVIDER = "downloads"

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
        skip_intro: bool = True,
        skip_outro: bool = False,
        auto_next: bool = True,
        stream_proxy=None,
        probe=None,
        skip_source=None,
        downloads: "DownloadStore | None" = None,
        prefer_local: bool = True,
        tracker: "Tracker | None" = None,
        on_event: "Callable[[str], None] | None" = None,
    ) -> None:
        self._providers = providers
        self._downloads = downloads
        # An episode already on this disk beats anything on the internet. Off
        # only when the caller explicitly asked to stream (`anime play --stream`)
        # -- usually because the local copy is suspect.
        self._prefer_local = prefer_local
        # Set by the composition root via set_local_source.
        self._local_path = None
        self._resolvers = resolvers
        self._player = player
        self._library = library
        self._quality = quality
        # Optional de-obfuscating proxy for hostile CDNs (PNG-disguised segments).
        self._stream_proxy = stream_proxy
        # Optional liveness probe: skips a resolved stream whose CDN is already
        # dead (403/404/gone) before we ever hand it to the player.
        self._probe = probe
        # Optional intro/outro timestamp source (AniSkip) — fills skip data in
        # when the provider's stream didn't ship any.
        self._skip_source = skip_source
        # Optional list-sync tracker (AniList): progress is pushed on completion.
        self._tracker = tracker
        self._skip_intro = skip_intro
        self._skip_outro = skip_outro
        self._auto_next = auto_next
        # Optional UI hook for status lines ("Skipped intro", "Next episode…").
        self._notify = on_event or (lambda _msg: None)

    def set_local_source(self, local_path: "Callable[[Anime, float], object] | None") -> None:
        """Late-bind "where is this episode on disk", which DownloadService owns.

        Late because DownloadService is built *from* this service, so the wiring
        cannot run in the other direction at construction time — the same reason
        `set_on_event` exists.
        """
        self._local_path = local_path

    def set_on_event(self, on_event: "Callable[[str], None] | None") -> None:
        """Late-bind the status hook — the CLI/TUI attach after construction."""
        self._notify = on_event or (lambda _msg: None)

    async def resolve(
        self, anime: Anime, episode_number: float, *, audio: Audio = Audio.SUB,
        source: "SourceOption | None" = None, allow_local: bool = False,
    ) -> ResolvedPlayback:
        """Resolve the first playable stream for an episode (for --json /
        downloads). ``play`` uses the full candidate stream, trying each until
        one actually plays.

        ``allow_local`` defaults to False because the download path calls this:
        handing it a local file would make ffmpeg read and write the same path.
        """
        progress = await self._library.get_progress(anime.id, episode_number)
        resume_s = progress.position_s if progress and not progress.completed else 0
        refs = [source.ref()] if source else None
        async for episode, stream, _provider, _host in self._candidate_streams(
            anime, episode_number, audio, refs, allow_local=allow_local
        ):
            return ResolvedPlayback(episode, stream, resume_s)
        raise NoStreamsFound(
            f"no source resolved a stream for {anime.title.preferred!r} "
            f"ep {episode_number:g}"
        )

    async def list_sources(self, anime: Anime, *, audio: Audio = Audio.SUB):
        """All matching provider entries for the source picker."""
        return await self._providers.list_sources(anime, audio)

    async def _candidate_streams(
        self, anime: Anime, episode_number: float, audio: Audio, refs=None,
        *, allow_local: bool = False,
    ):
        """Yield ``(episode, stream, provider, host)`` for every host that
        resolves to a live stream. Uses the given provider refs (a chosen
        source), or fans out across all matched providers in priority order — the
        pool the player walks looking for the first one that *plays*.

        Within a provider the candidate hosts are resolved concurrently, but the
        results are still yielded in the provider's own preference order, so the
        consumer sees best-first while a slow/dead host no longer blocks the ones
        behind it. Providers themselves stay lazy: a later provider is only
        touched if the earlier ones yield nothing playable."""
        # A finished download is the best candidate there is: no provider
        # fan-out, no resolver, no CDN that might be dead by now -- and it works
        # with no network at all. Downloads were write-only before this: you
        # could fetch an episode and anime-sh would still stream it from the
        # internet the next time you asked for it.
        #
        # `allow_local` is opt-in rather than opt-out, and that is a safety
        # property, not a style choice. The *download* path resolves a stream
        # through here too, and dest is the very file this would hand back: with
        # local candidates on by default, re-downloading an episode you already
        # had pointed ffmpeg at its own output, which `-y` truncates before
        # reading. Downloading a file you already have would have destroyed it.
        # Only playback opts in.
        #
        # Skipped when the caller pinned a source, because choosing a provider
        # from the picker is an explicit request for *that* provider.
        if allow_local and refs is None and self._prefer_local:
            local = await self._local_candidate(anime, episode_number, audio)
            if local is not None:
                yield local

        if refs is None:
            refs = await self._providers.resolve_sources(anime, audio)
        if not refs:
            raise NoStreamsFound(f"no provider has {anime.title.preferred!r}")
        for ref in refs:
            episodes = await self._episodes(ref, anime.id)
            episode = _find_episode(episodes, episode_number)
            if episode is None:
                continue
            candidates = await self._providers.candidates_for(episode)
            async for stream, host in self._resolve_candidates(candidates):
                yield episode, stream, ref.provider, host

    async def _local_candidate(self, anime: Anime, episode_number: float,
                               audio: Audio):
        """``(episode, stream, provider, host)`` for a downloaded copy of this
        episode, or None when there isn't one on disk.

        Shaped exactly like a resolved provider stream on purpose: the play loop,
        progress tracking, intro skipping and auto-next then need no idea that
        this one came from the filesystem.
        """
        path = None
        # The recorded path first: a download may have been written somewhere
        # the current naming rule would not put it.
        if self._downloads is not None:
            try:
                path = await self._downloads.local_episode(anime.id, episode_number)
            except Exception as e:
                # Never let a bookkeeping failure cost you the episode --
                # streaming still works, and that is the path this is trying to
                # save, not replace.
                log.debug("local download lookup failed: %s", e)
        # Then where the downloader would put it today. This is what the
        # download command itself checks, and it is the only one that finds
        # episodes fetched before any of this was recorded -- a `downloads` row
        # is not required to own a file.
        if path is None and self._local_path is not None:
            try:
                found = self._local_path(anime, episode_number)
                path = str(found) if found else None
            except Exception as e:
                log.debug("local path lookup failed: %s", e)
        if path is None:
            return None

        ref = ProviderRef(
            provider=_LOCAL_PROVIDER, anime_key=str(anime.id.anilist), audio=audio
        )
        episode = Episode(
            anime_id=anime.id, number=episode_number,
            provider_ref=ref, episode_key=path,
        )
        # MP4 is what the downloader writes; mpv opens a plain path either way.
        stream = Stream(url=path, kind=StreamKind.MP4)
        return episode, stream, _LOCAL_PROVIDER, "your download"

    async def _resolve_candidates(self, candidates: list[StreamCandidate]):
        """Resolve a provider's candidate hosts concurrently (bounded), yielding
        ``(stream, host)`` for each that produced a live stream — in the
        candidates' own order. Hosts race, so the first playable one is ready as
        soon as *any* fast host resolves, not after every slow one ahead of it.

        Tasks for hosts the consumer never reaches (it stopped at a working
        stream) are cancelled on close, so racing costs no orphaned work."""
        if not candidates:
            return
        sem = asyncio.Semaphore(_RESOLVE_CONCURRENCY)

        async def run(candidate: StreamCandidate) -> Stream | None:
            async with sem:
                return await self._resolve_one(candidate)

        tasks = [asyncio.create_task(run(c)) for c in candidates]
        try:
            for candidate, task in zip(candidates, tasks):
                stream = await task
                if stream is not None:
                    yield stream, candidate.host
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _resolve_one(self, candidate: StreamCandidate) -> Stream | None:
        """Resolve one embed candidate to a live, playable stream, or None.
        Tries each resolver that handles the host (bounded by the per-host
        resolve timeout) and pre-flights the result so a dead CDN is dropped
        here rather than bounced off the player."""
        for resolver in self._resolvers:
            if not resolver.handles(candidate):
                continue
            try:
                async with asyncio.timeout(_RESOLVE_TIMEOUT_S):
                    streams = await resolver.resolve(candidate)
            except (ResolverError, TimeoutError, asyncio.TimeoutError) as e:
                # A blocked/dead host must not stall the walk — bound it and
                # move on to the next resolver/host.
                log.debug("resolver %s failed on %s: %s", resolver.name, candidate.host, e)
                continue
            except Exception as e:
                log.warning("resolver %s crashed on %s: %s", resolver.name, candidate.host, e)
                continue
            chosen = pick_stream(streams, self._quality)
            if chosen is None:
                continue
            if await self._is_live(chosen):
                return chosen
            log.debug("pre-flight: %s not serving media, skipping", candidate.host)
        return None

    async def _is_live(self, stream: Stream) -> bool:
        """Whether the stream's CDN is actually serving media. With no probe
        wired this is always True (the pre-cache behaviour). The probe only ever
        rejects on a definitive dead response, so a good stream is never dropped."""
        if self._probe is None:
            return True
        try:
            return await self._probe.is_live(stream)
        except Exception as e:  # a probe failure must never block playback
            log.debug("stream probe errored, assuming live: %s", e)
            return True

    async def _augment_skips(
        self, stream: Stream, anime: Anime, episode_number: float
    ) -> Stream:
        """Fill intro/outro timestamps from the skip source when the resolved
        stream shipped none, so auto-skip works on every provider. The provider's
        own skip data always wins; a lookup miss leaves the stream untouched."""
        if (
            self._skip_source is None
            or stream.skip_times is not None
            or not (self._skip_intro or self._skip_outro)
            or anime.id.mal is None
        ):
            return stream
        length = (anime.duration_min or 24) * 60
        try:
            skips = await self._skip_source.for_episode(
                anime.id.mal, episode_number, episode_length=length
            )
        except Exception as e:  # best-effort — never disturb playback
            log.debug("skip-times lookup errored: %s", e)
            return stream
        return replace(stream, skip_times=skips) if skips else stream

    async def play(
        self, anime: Anime, episode_number: float, *, audio: Audio = Audio.SUB
    ):
        """Resolve then launch the player. Returns a PlaybackHandle."""
        resolved = await self.resolve(
            anime, episode_number, audio=audio, allow_local=True
        )
        title = _window_title(anime, episode_number)
        return await self._player.play(
            resolved.stream, title=title, start_s=resolved.resume_s
        )

    async def play_and_track(
        self, anime: Anime, episode_number: float, *, audio: Audio = Audio.SUB,
        source: "SourceOption | None" = None,
    ) -> None:
        """Play an episode (persisting progress + history), auto-skipping the
        intro/outro, and — when the episode plays out naturally — rolling on to
        the next one, until the season ends or the user quits mpv.

        ``source`` pins playback to one chosen provider entry (from the picker);
        otherwise it fans out across providers.
        """
        number = episode_number
        while True:
            finished = await self._play_episode(anime, number, audio=audio, source=source)
            if not (self._auto_next and finished and self._has_next(anime, number)):
                break
            number += 1
            self._notify(f"Next episode: {_ep_label(anime, number)}")

    async def _play_episode(
        self, anime: Anime, episode_number: float, *, audio: Audio,
        source: "SourceOption | None" = None,
    ) -> bool:
        """Play one episode, trying each resolved stream until one actually
        plays (ani-cli-style "first working stream"). Returns True if it played
        out naturally to completion — the signal to auto-advance.

        A stream that resolves but won't play (dead host, obfuscated CDN, a
        player that exits on a load error) is skipped and the next is tried,
        with a status line per attempt, so the terminal never just freezes.
        """
        progress = await self._library.get_progress(anime.id, episode_number)
        resume_s = progress.position_s if progress and not progress.completed else 0
        await self._library.save_anime(anime)

        refs = [source.ref()] if source else None
        tried = 0
        async for episode, stream, provider, host in self._candidate_streams(
            anime, episode_number, audio, refs, allow_local=True
        ):
            tried += 1
            self._notify(f"Episode {_ep_label(anime, episode_number)} — trying {host}…")
            title = _window_title(anime, episode_number)
            play_stream = (
                self._stream_proxy.rewrite(stream) if self._stream_proxy else stream
            )
            play_stream = await self._augment_skips(play_stream, anime, episode_number)
            try:
                handle = await self._player.play(play_stream, title=title, start_s=resume_s)
            except PlayerError as e:  # mpv exited before playback started
                log.debug("player failed on %s: %s", host, e)
                continue

            played, last_event = await self._track(
                handle, anime, episode_number, provider, play_stream
            )
            if played:
                return _finished_naturally(last_event)
            self._notify(f"{host} didn't play, trying next…")
            if tried >= _MAX_STREAM_ATTEMPTS:
                break

        raise NoStreamsFound(
            f"couldn't play {anime.title.preferred!r} ep {episode_number:g} — "
            f"no source worked (your network may be blocking streaming hosts, "
            f"or the hosts are down)"
        )

    async def _track(self, handle, anime, episode_number, provider, stream):
        """Consume playback events: skip intro/outro, persist progress, and
        record history — but only once playback has actually started. Returns
        ``(played, last_event)`` where ``played`` means a positive position was
        ever seen.

        Until playback is confirmed, each event is awaited with a timeout: a
        host that connects but never actually delivers video (obfuscated CDN,
        stuck buffering) is abandoned instead of hanging the terminal.
        """
        skips = stream.skip_times
        last_saved = 0.0
        last_event = None
        played = False
        skipped_op = skipped_ed = False
        events = handle.events()
        try:
            while True:
                try:
                    timeout = None if played else _CONFIRM_TIMEOUT_S
                    ev = await asyncio.wait_for(events.__anext__(), timeout)
                except StopAsyncIteration:
                    break
                except (asyncio.TimeoutError, TimeoutError):
                    break  # never started playing → give up on this stream
                last_event = ev
                if ev.position_s > 0:
                    played = True
                now = time.monotonic()
                if played and (ev.eof or (now - last_saved) >= _SAVE_INTERVAL_S):
                    await self._save(anime, episode_number, ev)
                    last_saved = now
                if not skipped_op and self._skip_intro and skips and skips.op:
                    if skips.op.start_s <= ev.position_s < skips.op.end_s:
                        await handle.seek(skips.op.end_s)
                        skipped_op = True
                        self._notify("Skipped intro")
                if not skipped_ed and self._skip_outro and skips and skips.ed:
                    if skips.ed.start_s <= ev.position_s < skips.ed.end_s:
                        await handle.seek(skips.ed.end_s)
                        skipped_ed = True
                        self._notify("Skipped outro")
                if ev.eof:
                    break
        finally:
            with contextlib.suppress(Exception):
                await events.aclose()
            with contextlib.suppress(Exception):
                await handle.stop()  # ensure mpv is closed if we gave up
            if played and last_event is not None:
                await self._save(anime, episode_number, last_event)
                await self._library.add_history(
                    anime.id, episode_number,
                    provider=provider,
                    seconds_watched=max(last_event.position_s, 0),
                )
                if _is_complete(last_event):
                    await self._sync_progress(anime, episode_number)
        return played, last_event

    async def _sync_progress(self, anime: Anime, episode_number: float) -> None:
        """Push a completed episode to the list tracker (AniList). Best-effort:
        a sync failure must never disrupt playback."""
        if self._tracker is None or anime.id.anilist is None:
            return
        try:
            await self._tracker.push(
                WatchProgress(
                    anime_id=anime.id,
                    episode=episode_number,
                    position_s=0,
                    duration_s=0,
                    updated_at=datetime.now(timezone.utc),
                    completed=True,
                ),
                total=anime.episode_count,
            )
            self._notify(f"Synced to {self._tracker.name}: episode {episode_number:g}")
        except Exception as e:  # pragma: no cover - network best-effort
            log.debug("tracker push failed: %s", e)

    def _has_next(self, anime: Anime, number: float) -> bool:
        if anime.episode_count is None or number >= anime.episode_count:
            return False
        # For a show still airing, episode_count is AniList's *planned* total, so
        # it runs ahead of what has actually been released. Auto-next must stop at
        # the last aired episode — otherwise finishing the newest episode rolls
        # straight into one that doesn't exist yet and fails to find a stream.
        if anime.next_airing_episode is not None:
            return number < anime.next_airing_episode - 1
        return True

    async def available_episodes(
        self, anime: Anime, *, audio: Audio = Audio.SUB,
        source: "SourceOption | None" = None,
    ) -> list[float]:
        """Episode numbers actually available — for one chosen source, or the
        union across providers. For an airing series this is usually fewer than
        AniList's planned total, so the episode list stays honest."""
        refs = [source.ref()] if source else await self._providers.resolve_sources(anime, audio)
        numbers: set[float] = set()
        for ref in refs:
            for episode in await self._episodes(ref, anime.id):
                numbers.add(episode.number)
        return sorted(numbers)

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

    async def _episodes(self, ref, anime_id) -> list[Episode]:
        provider = self._providers._by_name(ref.provider)
        if provider is None:
            return []
        try:
            return await provider.episodes(ref, anime_id)
        except Exception as e:
            log.warning("provider %s episodes failed: %s", ref.provider, e)
            return []


def _ep_label(anime: Anime, number: float) -> str:
    """"5/12" when the planned total is known, else "5" — so every status
    line and window title says where you are in the season."""
    total = anime.episode_count
    return f"{number:g}/{total}" if total else f"{number:g}"


def _window_title(anime: Anime, number: float) -> str:
    return f"{anime.title.preferred} - Episode {_ep_label(anime, number)}"


def _is_complete(ev) -> bool:
    """Whether a playback event crossed the completion threshold."""
    duration = max(getattr(ev, "duration_s", 0), 0)
    return duration > 0 and (ev.position_s / duration) >= _COMPLETE_FRACTION


def _find_episode(episodes: list[Episode], number: float) -> Episode | None:
    return next((e for e in episodes if e.number == number), None)


def _finished_naturally(ev) -> bool:
    """True when the episode played to its end (mpv EOF) and was watched to
    completion — the gate for auto-advancing. A user quitting mpv early has
    reason != 'eof' (or an incomplete position), so it won't roll on."""
    if ev is None or not ev.eof:
        return False
    if getattr(ev, "reason", None) not in (None, "eof"):
        return False
    duration = max(ev.duration_s, 0)
    return duration > 0 and (ev.position_s / duration) >= _COMPLETE_FRACTION
