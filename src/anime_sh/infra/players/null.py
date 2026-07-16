"""A player that plays nothing — completes wiring and unit tests without a
subprocess. The real mpv-over-IPC handle (with a live event stream) arrives in
M1; this establishes the shape the rest of the app codes against."""

from __future__ import annotations

from typing import AsyncIterator

from ...domain.models import Stream


class _NullEvent:
    def __init__(self, position_s: int, duration_s: int, eof: bool) -> None:
        self.position_s = position_s
        self.duration_s = duration_s
        self.paused = False
        self.eof = eof
        self.reason = "eof" if eof else None


class NullPlaybackHandle:
    def __init__(self, stream: Stream, title: str, start_s: int) -> None:
        self.stream = stream
        self.title = title
        self.start_s = start_s
        self._stopped = False

    async def events(self) -> AsyncIterator[_NullEvent]:
        # Immediately signals EOF: nothing is actually rendered.
        yield _NullEvent(self.start_s, self.start_s, eof=True)

    async def seek(self, seconds: int) -> None:  # noqa: D401 - no-op
        return None

    async def stop(self) -> None:
        self._stopped = True


class NullPlayer:
    name = "null"

    def available(self) -> bool:
        return True

    async def play(
        self, stream: Stream, *, title: str, start_s: int = 0
    ) -> NullPlaybackHandle:
        return NullPlaybackHandle(stream, title, start_s)
