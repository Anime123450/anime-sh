"""Intro/outro skip and auto-play-next in PlaybackService, via scripted fakes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from anime_sh.app.playback import PlaybackService
from anime_sh.app.providers import ProviderManager
from anime_sh.domain.models import (
    Anime,
    AnimeId,
    Quality,
    SkipRange,
    SkipTimes,
    Stream,
    StreamKind,
    Title,
)

from .fakes import FakeLibrary, FakeProvider, FakeResolver, make_anime


@dataclass
class _Ev:
    position_s: int
    duration_s: int
    paused: bool
    eof: bool
    reason: str | None = None


class _ScriptedHandle:
    def __init__(self, events):
        self._events = events
        self.seeks: list[int] = []

    async def events(self) -> AsyncIterator[_Ev]:
        for e in self._events:
            yield e

    async def seek(self, s):
        self.seeks.append(s)

    async def stop(self):
        ...


class _ScriptedPlayer:
    name = "scripted"

    def __init__(self, per_episode):
        # per_episode: list of event-lists, one per play() call (for auto-next).
        self._per_episode = list(per_episode)
        self.handles: list[_ScriptedHandle] = []

    def available(self):
        return True

    async def play(self, stream, *, title, start_s=0):
        events = self._per_episode.pop(0) if self._per_episode else []
        h = _ScriptedHandle(events)
        self.handles.append(h)
        return h


class _SkipResolver:
    """A resolver that yields a stream carrying an OP skip range 5..90."""

    name = "skip"
    api_version = 1

    def handles(self, candidate):
        return True

    async def resolve(self, candidate):
        return [
            Stream(
                url="https://cdn/x.m3u8", kind=StreamKind.HLS, quality=Quality.Q1080,
                skip_times=SkipTimes(op=SkipRange(5, 90)),
            )
        ]


def _service(player, resolvers, *, library=None, **kw):
    return PlaybackService(
        providers=ProviderManager([FakeProvider("p", candidate_hosts=["h"])]),
        resolvers=resolvers,
        player=player,
        library=library or FakeLibrary(),
        **kw,
    )


async def test_skips_intro_when_position_enters_op():
    player = _ScriptedPlayer([[
        _Ev(2, 1400, False, False),
        _Ev(10, 1400, False, False),   # inside OP 5..90 -> seek to 90
        _Ev(120, 1400, False, False),
        _Ev(1300, 1400, False, True, reason="eof"),
    ]])
    svc = _service(player, [_SkipResolver()], skip_intro=True, auto_next=False)
    await svc.play_and_track(make_anime(), 1.0)
    assert player.handles[0].seeks == [90]


async def test_does_not_skip_when_disabled():
    player = _ScriptedPlayer([[
        _Ev(10, 1400, False, False),
        _Ev(1300, 1400, False, True, reason="eof"),
    ]])
    svc = _service(player, [_SkipResolver()], skip_intro=False, auto_next=False)
    await svc.play_and_track(make_anime(), 1.0)
    assert player.handles[0].seeks == []


def _anime_with_eps(n):
    return Anime(id=AnimeId(anilist=1), title=Title(romaji="Frieren"), episode_count=n)


async def test_auto_next_advances_on_natural_eof():
    # Two episodes each finishing naturally; a 2-ep season stops after ep 2.
    done = [_Ev(1390, 1400, False, True, reason="eof")]
    player = _ScriptedPlayer([done, done])
    lib = FakeLibrary()
    svc = _service(player, [FakeResolver("h", host="h")], library=lib, auto_next=True)
    await svc.play_and_track(_anime_with_eps(2), 1.0)
    # Played ep 1 then auto-advanced to ep 2, then stopped (no ep 3).
    assert len(player.handles) == 2
    assert [h[1] for h in lib.history] == [1.0, 2.0]


async def test_no_auto_next_when_user_quits():
    # EOF but reason "quit" (user closed mpv) -> do not advance.
    player = _ScriptedPlayer([[_Ev(1390, 1400, False, True, reason="quit")]])
    svc = _service(player, [FakeResolver("h", host="h")], auto_next=True)
    await svc.play_and_track(_anime_with_eps(5), 1.0)
    assert len(player.handles) == 1


async def test_no_auto_next_when_incomplete():
    # Natural-ish EOF but only 10% watched -> not "finished", don't advance.
    player = _ScriptedPlayer([[_Ev(100, 1400, False, True, reason="eof")]])
    svc = _service(player, [FakeResolver("h", host="h")], auto_next=True)
    await svc.play_and_track(_anime_with_eps(5), 1.0)
    assert len(player.handles) == 1
