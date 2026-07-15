"""play_and_track persists progress and marks completion — via a fake player
that emits scripted events, no real mpv."""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator

from anime_sh.app.playback import PlaybackService
from anime_sh.app.providers import ProviderManager
from anime_sh.infra.players import NullPlayer  # noqa: F401 (import sanity)

from .fakes import FakeLibrary, FakeProvider, FakeResolver, make_anime


@dataclass
class _Ev:
    position_s: int
    duration_s: int
    paused: bool
    eof: bool


class _ScriptedHandle:
    def __init__(self, events):
        self._events = events
        self.start_s = 0
        self.title = ""

    async def events(self) -> AsyncIterator[_Ev]:
        for e in self._events:
            yield e

    async def seek(self, s):
        ...

    async def stop(self):
        ...


class _ScriptedPlayer:
    name = "scripted"

    def __init__(self, events):
        self._events = events

    def available(self):
        return True

    async def play(self, stream, *, title, start_s=0):
        h = _ScriptedHandle(self._events)
        h.start_s = start_s
        h.title = title
        return h


def _service(events, library):
    return PlaybackService(
        providers=ProviderManager([FakeProvider("p", candidate_hosts=["mp4upload"])]),
        resolvers=[FakeResolver("mp4", host="mp4upload")],
        player=_ScriptedPlayer(events),
        library=library,
        quality="best",
    )


async def test_progress_saved_and_marked_complete():
    lib = FakeLibrary()
    events = [
        _Ev(10, 1000, False, False),
        _Ev(950, 1000, False, False),   # past 90% -> completed
        _Ev(960, 1000, False, True),    # eof
    ]
    await _service(events, lib).play_and_track(make_anime(), 18.0)

    assert lib.saved, "expected at least one progress write"
    final = lib.saved[-1]
    assert final.episode == 18.0
    assert final.completed is True


async def test_partial_watch_not_completed():
    lib = FakeLibrary()
    events = [_Ev(120, 1400, False, True)]  # stopped early
    await _service(events, lib).play_and_track(make_anime(), 1.0)
    assert lib.saved[-1].completed is False
    assert lib.saved[-1].position_s == 120
