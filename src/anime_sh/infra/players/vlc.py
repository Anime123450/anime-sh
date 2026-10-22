"""VLC as a player backend, driven over its loopback HTTP control interface.

Each play starts its own VLC process — never a running VLC the user has open:
``--no-one-instance`` (and its started-from-file variant, on by default on
Windows) stop VLC from handing the URL to an existing window. That process is
the only one this module ever stops or kills.

Control is VLC's HTTP interface, bound to 127.0.0.1 on a port picked at run
time and protected by a fresh random password per play. The password is passed
to that one child on its command line and kept in memory; it is never written
to config, settings or logs (the debug log redacts it).

VLC has no "end of file" event over HTTP, so a natural end is inferred: a
transition from playing to stopped, with a known duration, the last position
within ``max(5 s, 2 %)`` of the end, and no stop requested by us. That is
best-effort — stopping by hand inside that window reads as a natural end and
can start the next episode. Unknown duration or the window simply closing never
counts as finished.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ntpath
import os
import posixpath
import secrets
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import AsyncIterator, Callable, Mapping

from ...domain.errors import PlayerError, PlayerUnavailable
from ...domain.models import Stream
from .mpv import _DEFAULT_UA
from .vlc_http import VlcAuthRejected, VlcControlError, VlcHttpClient, VlcUnreachable

log = logging.getLogger(__name__)

_PORT_ATTEMPTS = 3
#: Headers VLC can send for an HTTP input. Anything else cannot be expressed.
_SUPPORTED_HEADERS = frozenset({"user-agent", "referer"})


def reached_end(position_s: float, duration_s: float) -> bool:
    """Whether a stop at ``position_s`` counts as the episode having ended."""
    if duration_s <= 0:
        return False
    return position_s >= duration_s - max(5.0, 0.02 * duration_s)


def _free_port() -> int:
    """A port the OS says is free on loopback right now.

    There is a window between closing this socket and VLC binding it; a
    collision there shows up as a refused password and is retried.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- #
# Finding vlc
# --------------------------------------------------------------------------- #
def _standard_locations(platform: str, env: Mapping[str, str]) -> list[str]:
    if platform == "win32":
        roots = [env.get("ProgramFiles"), env.get("ProgramW6432"),
                 env.get("ProgramFiles(x86)")]
        found = [ntpath.join(r, "VideoLAN", "VLC", "vlc.exe") for r in roots if r]
        if env.get("LOCALAPPDATA"):
            found.append(ntpath.join(env["LOCALAPPDATA"], "Programs", "VideoLAN",
                                     "VLC", "vlc.exe"))
        return found
    if platform == "darwin":
        return ["/Applications/VLC.app/Contents/MacOS/VLC"]
    return ["/usr/bin/vlc", "/usr/local/bin/vlc", "/snap/bin/vlc",
            "/var/lib/flatpak/exports/bin/org.videolan.VLC"]


def _in_folder(folder: str, platform: str) -> list[str]:
    if platform == "win32":
        return [ntpath.join(folder, "vlc.exe")]
    if platform == "darwin":
        return [posixpath.join(folder, "Contents", "MacOS", "VLC"),
                posixpath.join(folder, "VLC.app", "Contents", "MacOS", "VLC"),
                posixpath.join(folder, "vlc")]
    return [posixpath.join(folder, "vlc")]


def find_vlc(
    path_override: str = "", *,
    which: Callable[[str], str | None] = shutil.which,
    platform: str = sys.platform,
    env: Mapping[str, str] | None = None,
    exists: Callable[[str], bool] = os.path.isfile,
) -> tuple[str | None, str]:
    """``(executable, detail)``: the configured path, then PATH, then the usual
    install locations. A configured path that is wrong is reported as such,
    rather than quietly replaced by some other VLC."""
    env = os.environ if env is None else env
    override = path_override.strip().strip('"')
    if override:
        override = os.path.expanduser(override)
        if exists(override):
            return override, "from player.vlc_path"
        if os.path.isdir(override):
            for candidate in _in_folder(override, platform):
                if exists(candidate):
                    return candidate, "from player.vlc_path"
        return None, (f"player.vlc_path is set to {override!r}, but there is no "
                      "VLC there — fix it or clear it to auto-detect")
    on_path = which("vlc")
    if on_path:
        return on_path, "found on PATH"
    for candidate in _standard_locations(platform, env):
        if exists(candidate):
            return candidate, "found in the standard install location"
    return None, ("VLC not found on PATH or in the standard install location — "
                  "install it from https://www.videolan.org or set player.vlc_path")


# --------------------------------------------------------------------------- #
# Playback
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class VlcEvent:
    position_s: int
    duration_s: int
    paused: bool
    eof: bool
    # "eof" (inferred natural end), "stop" (stopped early / by us) or
    # "disconnect" (the window closed). Only "eof" may trigger auto-next.
    reason: str | None = None


class VlcPlaybackHandle:
    """One VLC process and its control client. Owns exactly that process."""

    def __init__(self, proc, client, *, poll_interval: float = 0.5) -> None:
        self._proc = proc
        self._client = client
        self._poll = poll_interval
        self._stop_requested = False
        self._pos = 0
        self._dur = 0

    def _end(self, reason: str) -> VlcEvent:
        return VlcEvent(self._pos, self._dur, False, eof=True, reason=reason)

    async def events(self) -> AsyncIterator[VlcEvent]:
        seen_playing = False
        while True:
            if self._stop_requested:
                yield self._end("stop")
                return
            if self._proc.poll() is not None:
                yield self._end("disconnect")
                return
            try:
                st = await self._client.status()
            except VlcControlError:
                yield self._end("stop" if self._stop_requested else "disconnect")
                return
            if st.state in ("playing", "paused"):
                seen_playing = True
                self._pos, self._dur = st.time_s, st.length_s
                yield VlcEvent(self._pos, self._dur, st.state == "paused", eof=False)
            elif st.state == "stopped" and seen_playing:
                natural = not self._stop_requested and reached_end(self._pos, self._dur)
                yield self._end("eof" if natural else "stop")
                return
            await asyncio.sleep(self._poll)

    async def seek(self, seconds: int) -> None:
        await self._client.seek(seconds)

    async def pause(self) -> None:
        await self._client.pause()

    async def resume(self) -> None:
        await self._client.resume()

    async def stop(self) -> None:
        self._stop_requested = True
        if self._proc.poll() is None:
            with contextlib.suppress(Exception):
                await self._client.stop()
        await _close(self._proc)

    async def wait(self) -> None:
        async for ev in self.events():
            if ev.eof:
                return


async def _close(proc) -> None:
    """Stop *our* child: terminate, then kill if it ignores that."""
    if proc.poll() is not None:
        return
    with contextlib.suppress(Exception):
        proc.terminate()
    try:
        await asyncio.to_thread(proc.wait, 5)
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()


def _spawn(argv: list[str]):
    return subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _redact(argv: list[str]) -> list[str]:
    return ["--http-password=***" if a.startswith("--http-password=") else a
            for a in argv]


class VlcPlayer:
    name = "vlc"

    def __init__(
        self, executable: str, *, extra_args: list[str] | None = None,
        spawn: Callable[[list[str]], object] | None = None,
        client_factory: Callable[[int, str], object] | None = None,
        pick_port: Callable[[], int] | None = None,
        ready_timeout: float = 15.0, poll_interval: float = 0.5,
    ) -> None:
        self.executable = executable
        self.extra_args = list(extra_args or [])
        self._spawn = spawn or _spawn
        self._client_factory = client_factory or (lambda port, pw: VlcHttpClient(port, pw))
        self._pick_port = pick_port or _free_port
        self._ready_timeout = ready_timeout
        self._poll = poll_interval

    def available(self) -> bool:
        return os.path.isfile(self.executable)

    def _argv(self, stream: Stream, title: str, start_s: int, port: int,
              password: str) -> list[str]:
        headers = {k.lower(): v for k, v in stream.headers.items()}
        argv = [
            self.executable,
            # Our own instance: never hand the URL to a VLC the user has open.
            "--no-one-instance",
            "--no-one-instance-when-started-from-file",
            "--no-playlist-enqueue",
            "--no-qt-privacy-ask",
            "--extraintf=http",
            "--http-host=127.0.0.1",
            f"--http-port={port}",
            f"--http-password={password}",
            f"--http-user-agent={headers.get('user-agent') or _DEFAULT_UA}",
            f"--meta-title={title}",
        ]
        if headers.get("referer"):
            argv.append(f"--http-referrer={headers['referer']}")
        if start_s > 0:
            argv.append(f"--start-time={int(start_s)}")
        subs = sorted(stream.subtitles, key=lambda s: not s.default)
        if subs:
            argv.append(f"--sub-file={subs[0].url}")
            if len(subs) > 1:
                argv.append("--input-slave=" + "#".join(s.url for s in subs[1:]))
        argv += self.extra_args
        argv.append(stream.url)
        return argv

    async def play(
        self, stream: Stream, *, title: str, start_s: int = 0
    ) -> VlcPlaybackHandle:
        unsupported = sorted(k for k in stream.headers
                             if k.lower() not in _SUPPORTED_HEADERS)
        if unsupported:
            # Refused before launch: dropping them would play a broken stream
            # (or nothing) with no hint why.
            raise PlayerError(
                "VLC can't send the HTTP header(s) this source needs: "
                f"{', '.join(unsupported)}. Try another source, or switch to mpv "
                "(`anime config set player.name mpv`), which supports them."
            )

        for attempt in range(1, _PORT_ATTEMPTS + 1):
            port = self._pick_port()
            password = secrets.token_urlsafe(32)
            argv = self._argv(stream, title, start_s, port, password)
            log.debug("launching VLC (attempt %d): %s", attempt, _redact(argv))
            try:
                proc = await asyncio.to_thread(self._spawn, argv)
            except FileNotFoundError:
                raise PlayerUnavailable(f"VLC not found at {self.executable}") from None
            except OSError as e:
                raise PlayerError(f"couldn't start VLC: {e}") from None
            client = self._client_factory(port, password)
            outcome = await self._wait_ready(proc, client)
            if outcome == "ready":
                return VlcPlaybackHandle(proc, client, poll_interval=self._poll)
            await _close(proc)
            if outcome == "timeout":
                raise PlayerError(
                    f"VLC started but its control interface did not answer within "
                    f"{self._ready_timeout:g}s")
            log.debug("VLC control port %d was taken; retrying", port)
        raise PlayerError(
            f"couldn't get a free loopback port for VLC's control interface after "
            f"{_PORT_ATTEMPTS} tries")

    async def _wait_ready(self, proc, client) -> str:
        """``"ready"``, ``"collision"`` or ``"timeout"``; raises if VLC exits."""
        deadline = time.monotonic() + self._ready_timeout
        while True:
            code = proc.poll()
            if code is not None:
                raise PlayerError(
                    f"VLC exited with code {code} before its control interface "
                    "came up")
            try:
                await client.status()
                return "ready"
            except VlcAuthRejected:
                return "collision"
            except VlcUnreachable:
                pass  # not listening yet
            except VlcControlError:
                return "collision"  # someone else's server on our port
            if time.monotonic() >= deadline:
                return "timeout"
            await asyncio.sleep(self._poll)
