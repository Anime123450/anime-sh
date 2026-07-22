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


def _service(events, library, tracker=None):
    return PlaybackService(
        providers=ProviderManager([FakeProvider("p", candidate_hosts=["mp4upload"])]),
        resolvers=[FakeResolver("mp4", host="mp4upload")],
        player=_ScriptedPlayer(events),
        library=library,
        quality="best",
        tracker=tracker,
    )


class _FakeTracker:
    name = "anilist"

    def __init__(self):
        self.pushed = []

    async def push(self, progress, *, total=None):
        self.pushed.append((progress.anime_id.anilist, int(progress.episode), total))

    async def pull(self):
        return []


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


async def test_completed_episode_pushed_to_tracker():
    lib, tracker = FakeLibrary(), _FakeTracker()
    events = [_Ev(950, 1000, False, True)]  # past 90% -> completed -> synced
    await _service(events, lib, tracker).play_and_track(make_anime(), 18.0)
    assert tracker.pushed == [(154587, 18, None)]  # make_anime has no episode_count


async def test_partial_watch_not_pushed_to_tracker():
    lib, tracker = FakeLibrary(), _FakeTracker()
    events = [_Ev(100, 1400, False, True)]  # stopped early -> no sync
    await _service(events, lib, tracker).play_and_track(make_anime(), 18.0)
    assert tracker.pushed == []


async def test_play_records_history_and_caches_metadata():
    lib = FakeLibrary()
    events = [_Ev(300, 1400, False, True)]
    anime = make_anime()
    await _service(events, lib).play_and_track(anime, 18.0)

    # Metadata cached so the library can render it offline later.
    assert anime in lib.saved_anime
    # Exactly one history row for this session, with the provider recorded.
    assert len(lib.history) == 1
    anime_id, episode, provider, seconds = lib.history[0]
    assert episode == 18.0
    assert provider == "p"  # FakeProvider name
    assert seconds == 300
