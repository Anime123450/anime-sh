"""The 'first working stream' + anti-hang behaviour: a stream that resolves but
never actually plays (dead host / obfuscated CDN / stuck buffering) is abandoned
and the next is tried; if none play, an honest error is raised instead of
hanging."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

import anime_sh.app.playback as playback_mod
from anime_sh.app.playback import PlaybackService
from anime_sh.app.providers import ProviderManager
from anime_sh.domain.errors import NoStreamsFound

from .fakes import FakeLibrary, FakeProvider, FakeResolver, make_anime


@pytest.fixture(autouse=True)
def _fast_confirm(monkeypatch):
    # Don't wait the real 25s for a stuck stream in tests.
    monkeypatch.setattr(playback_mod, "_CONFIRM_TIMEOUT_S", 0.2)


class _Ev:
    def __init__(self, position_s, duration_s=1400, eof=False, reason=None):
        self.position_s = position_s
        self.duration_s = duration_s
        self.paused = False
        self.eof = eof
        self.reason = reason


class _StuckHandle:
    """Connects but never delivers a positive position — like the PNG-CDN case
    where mpv sits buffering forever."""

    def __init__(self):
        self.stopped = False

    async def events(self) -> AsyncIterator[_Ev]:
        await asyncio.sleep(30)  # longer than the (patched) confirm timeout
        yield _Ev(0, eof=True)

    async def seek(self, s): ...

    async def stop(self):
        self.stopped = True


class _WorkingHandle:
    async def events(self) -> AsyncIterator[_Ev]:
        yield _Ev(5)
        yield _Ev(1390, eof=True, reason="eof")

    async def seek(self, s): ...

    async def stop(self): ...


class _ScriptedPlayer:
    name = "scripted"

    def __init__(self, handles):
        self._handles = list(handles)
        self.made = []

    def available(self):
        return True

    async def play(self, stream, *, title, start_s=0):
        h = self._handles.pop(0) if self._handles else _StuckHandle()
        self.made.append(h)
        return h


def _svc(player, n_hosts):
    hosts = [f"h{i}" for i in range(n_hosts)]
    return PlaybackService(
        providers=ProviderManager([FakeProvider("p", candidate_hosts=hosts)]),
        # one resolver handling every host
        resolvers=[FakeResolver("r", host=h) for h in hosts],
        player=player,
        library=FakeLibrary(),
        auto_next=False,
    )


async def test_stuck_stream_is_abandoned_and_next_is_tried():
    # First host hangs, second plays fine.
    player = _ScriptedPlayer([_StuckHandle(), _WorkingHandle()])
    svc = _svc(player, n_hosts=2)
    await svc.play_and_track(make_anime(), 18.0)
    assert len(player.made) == 2  # gave up on the stuck one, played the next
    assert player.made[0].stopped is True  # the stuck mpv was killed


async def test_all_streams_stuck_raises_not_hangs():
    player = _ScriptedPlayer([_StuckHandle(), _StuckHandle(), _StuckHandle()])
    svc = _svc(player, n_hosts=3)
    with pytest.raises(NoStreamsFound):
        await asyncio.wait_for(svc.play_and_track(make_anime(), 18.0), timeout=5)
