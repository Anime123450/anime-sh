"""mpv player driven over its JSON IPC socket.

We talk to mpv through ``--input-ipc-server`` rather than parsing stdout, which
is what makes position tracking, resume, and intro-skip actually work. The
transport differs by OS — a Windows named pipe vs a Unix domain socket — and
that difference is abstracted here on day one (§14), behind a blocking
reader thread that feeds an asyncio queue.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from ...domain.errors import PlayerError, PlayerUnavailable
from ...domain.models import Stream


@dataclass(slots=True)
class MpvEvent:
    position_s: int
    duration_s: int
    paused: bool
    eof: bool


def _ipc_path() -> str:
    token = uuid.uuid4().hex[:12]
    if sys.platform == "win32":
        return rf"\\.\pipe\anime-sh-{token}"
    return os.path.join(tempfile.gettempdir(), f"anime-sh-{token}.sock")


class _Transport:
    """Blocking line transport over a Windows pipe or Unix socket, bridged to
    asyncio via a reader thread."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._win = sys.platform == "win32"
        self._fp = None  # file-like for Windows pipe
        self._sock: socket.socket | None = None

    def connect(self, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline:
            try:
                if self._win:
                    self._fp = open(self._path, "r+b", buffering=0)
                else:
                    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    s.connect(self._path)
                    self._sock = s
                return
            except OSError as e:
                last = e
                time.sleep(0.1)
        raise PlayerError(f"could not connect to mpv IPC at {self._path}: {last}")

    def send(self, obj: dict) -> None:
        data = (json.dumps(obj) + "\n").encode()
        if self._win:
            self._fp.write(data)
            self._fp.flush()
        else:
            self._sock.sendall(data)

    def read_line(self) -> bytes:
        # A concurrent close() (from stop()) can pull the pipe/socket out from
        # under this blocking read; treat that as a clean EOF, not a crash.
        try:
            if self._win:
                buf = b""
                while not buf.endswith(b"\n"):
                    ch = self._fp.read(1)
                    if not ch:
                        return buf
                    buf += ch
                return buf
            chunks = b""
            while not chunks.endswith(b"\n"):
                ch = self._sock.recv(1)
                if not ch:
                    return chunks
                chunks += ch
            return chunks
        except (ValueError, OSError):
            return b""

    def close(self) -> None:
        try:
            if self._win and self._fp is not None:
                self._fp.close()
            elif self._sock is not None:
                self._sock.close()
        except OSError:
            pass


class MpvPlaybackHandle:
    def __init__(self, proc: subprocess.Popen, transport: _Transport) -> None:
        self._proc = proc
        self._t = transport
        self._queue: asyncio.Queue[dict | None] = asyncio.Queue()
        self._loop = asyncio.get_event_loop()
        self._reader = asyncio.to_thread(self._read_loop)
        self._reader_task = asyncio.ensure_future(self._reader)

    def _read_loop(self) -> None:
        # Runs in a worker thread; hands parsed messages to the asyncio queue.
        try:
            while True:
                line = self._t.read_line()
                if not line:
                    break
                for part in line.split(b"\n"):
                    part = part.strip()
                    if not part:
                        continue
                    try:
                        msg = json.loads(part)
                    except json.JSONDecodeError:
                        continue
                    self._loop.call_soon_threadsafe(self._queue.put_nowait, msg)
        finally:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def _send(self, *command) -> None:
        try:
            self._t.send({"command": list(command)})
        except (OSError, PlayerError):
            pass

    async def events(self) -> AsyncIterator[MpvEvent]:
        # Observe the properties we care about; mpv pushes changes.
        self._send("observe_property", 1, "time-pos")
        self._send("observe_property", 2, "duration")
        self._send("observe_property", 3, "pause")
        position = 0
        duration = 0
        paused = False
        while True:
            msg = await self._queue.get()
            if msg is None:  # transport closed / process gone
                yield MpvEvent(position, duration, paused, eof=True)
                return
            event = msg.get("event")
            if event == "property-change":
                name, value = msg.get("name"), msg.get("data")
                if name == "time-pos" and value is not None:
                    position = int(value)
                    yield MpvEvent(position, duration, paused, eof=False)
                elif name == "duration" and value is not None:
                    duration = int(value)
                elif name == "pause":
                    paused = bool(value)
            elif event in ("end-file", "shutdown"):
                yield MpvEvent(position, duration, paused, eof=True)
                return

    async def seek(self, seconds: int) -> None:
        self._send("seek", seconds, "absolute")

    async def stop(self) -> None:
        self._send("quit")
        self._t.close()
        try:
            await asyncio.to_thread(self._proc.wait, 5)
        except Exception:
            self._proc.terminate()

    async def wait(self) -> None:
        """Block until playback finishes (EOF or the window is closed)."""
        async for ev in self.events():
            if ev.eof:
                return


class MpvPlayer:
    name = "mpv"

    def __init__(self, binary: str = "mpv", extra_args: list[str] | None = None) -> None:
        self._binary = binary
        self._extra_args = extra_args or []

    def available(self) -> bool:
        return shutil.which(self._binary) is not None

    async def play(
        self, stream: Stream, *, title: str, start_s: int = 0
    ) -> MpvPlaybackHandle:
        binary = shutil.which(self._binary)
        if binary is None:
            raise PlayerUnavailable(f"{self._binary} not found on PATH")

        path = _ipc_path()
        args = [
            binary,
            f"--input-ipc-server={path}",
            f"--force-media-title={title}",
            "--no-terminal",
            *self._extra_args,
        ]
        if start_s > 0:
            args.append(f"--start=+{start_s}")
        if stream.headers:
            fields = ",".join(f"{k}: {v}" for k, v in stream.headers.items())
            args.append(f"--http-header-fields={fields}")
        for sub in stream.subtitles:
            args.append(f"--sub-file={sub.url}")
        args.append(stream.url)

        proc = await asyncio.to_thread(subprocess.Popen, args)
        transport = _Transport(path)
        await asyncio.to_thread(transport.connect)
        return MpvPlaybackHandle(proc, transport)
