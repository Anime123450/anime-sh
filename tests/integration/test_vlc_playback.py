"""Live smoke test of the VLC backend against a REAL VLC.

Generates a short MP4 with ffmpeg, serves it from a local HTTP server that
records request headers and honours Range (VLC seeks with it), and drives VLC
through the same VlcPlayer the app uses: control-port readiness, resume start
position, seek, pause/resume, natural end detection, stop and cleanup.

Opt-in: set ANIME_SH_LIVE_VLC=1 with VLC and ffmpeg installed. It never reads
or writes your real config (settings go to a temp file) and never touches a
VLC that was already running.

Run over SSH on Windows, VLC lands in a non-interactive session: the control
interface and playback are real, but nobody can see the window. That this test
passes says nothing about the window being visible.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anime_sh.domain.models import Stream, StreamKind
from anime_sh.infra.players import PlayerFactory, VlcPlayer
from anime_sh.infra.players.vlc import find_vlc
from anime_sh.infra.players.vlc_http import VlcHttpClient, VlcUnreachable

_VLC, _ = find_vlc("")
pytestmark = pytest.mark.skipif(
    os.environ.get("ANIME_SH_LIVE_VLC") != "1" or _VLC is None
    or shutil.which("ffmpeg") is None,
    reason="live VLC test; set ANIME_SH_LIVE_VLC=1 with VLC and ffmpeg installed",
)

CLIP_S = 20


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("vlc-live") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={CLIP_S}:size=320x240:rate=25",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_S}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
         "-movflags", "+faststart", str(out)],
        check=True, timeout=120,
    )
    return out


class _MediaServer:
    """Serves one file with Range support and records each request's headers."""

    def __init__(self, path: Path) -> None:
        data = path.read_bytes()
        self.headers: list[dict[str, str]] = []
        seen = self.headers

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                seen.append({k.lower(): v for k, v in self.headers.items()})
                start, end = 0, len(data) - 1
                m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
                if m and (m.group(1) or m.group(2)):
                    if m.group(1):
                        start = int(m.group(1))
                        end = int(m.group(2)) if m.group(2) else end
                    else:
                        start = len(data) - int(m.group(2))
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                else:
                    self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(end - start + 1))
                self.end_headers()
                try:
                    self.wfile.write(data[start:end + 1])
                except (ConnectionError, OSError):
                    pass  # VLC drops connections when it seeks

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/clip.mp4"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def media(clip):
    server = _MediaServer(clip)
    yield server
    server.close()


def _vlc_pids() -> set[int]:
    if sys.platform != "win32":
        out = subprocess.run(["pgrep", "-x", "vlc"], capture_output=True, text=True)
        return {int(p) for p in out.stdout.split()}
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq vlc.exe", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
    )
    return {int(row.split('","')[1]) for row in out.stdout.splitlines()
            if row.startswith('"vlc.exe"')}


def _lan_ip() -> str | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent for a UDP connect
        ip = s.getsockname()[0]
        return None if ip.startswith("127.") else ip
    except OSError:
        return None
    finally:
        s.close()


async def _until(fn, timeout=20.0, every=0.25):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = await fn()
        if last is not None:
            return last
        await asyncio.sleep(every)
    raise AssertionError(f"condition not met in {timeout}s (last={last!r})")


def _player(tmp_path) -> VlcPlayer:
    """Built the way the app builds it, from a temp config, never the real one."""
    from anime_sh.app.settings import SettingsService

    settings = SettingsService(config_path=tmp_path / "config.toml")
    settings.update("player.name", "vlc")
    settings.update("player.vlc_path", _VLC)
    cfg = settings.snapshot()
    player = PlayerFactory().create(cfg.player.name, path_override=cfg.player.vlc_path)
    assert isinstance(player, VlcPlayer)
    player._poll = 0.25
    return player


async def test_live_vlc_control_seek_pause_and_cleanup(tmp_path, media, caplog):
    caplog.set_level(logging.DEBUG)
    before = _vlc_pids()
    player = _player(tmp_path)
    stream = Stream(url=media.url, kind=StreamKind.MP4,
                    headers={"Referer": "https://referer.example/"})
    handle = await player.play(stream, title="anime-sh VLC smoke", start_s=6)
    proc, client = handle._proc, handle._client
    try:
        # Resume: started at the requested position, not at 0.
        st = await _until(lambda: _status_if(client, lambda s: s.state == "playing"
                                             and s.time_s >= 1))
        assert st.time_s >= 5, f"start-time ignored: playing at {st.time_s}s"
        assert st.length_s in range(CLIP_S - 1, CLIP_S + 2)

        # Loopback only: the same port is closed on the LAN address.
        lan = _lan_ip()
        if lan:
            with pytest.raises(OSError):
                socket.create_connection((lan, client.port), timeout=1).close()
        # And the per-run password is required.
        with pytest.raises(Exception):
            await VlcHttpClient(client.port, "wrong").status()

        # Headers reached the media server.
        first = media.headers[0]
        assert first.get("referer") == "https://referer.example/"
        assert first.get("user-agent", "").startswith("Mozilla/")

        await handle.pause()
        await _until(lambda: _status_if(client, lambda s: s.state == "paused"))
        await handle.resume()
        await _until(lambda: _status_if(client, lambda s: s.state == "playing"))

        await handle.seek(14)
        st = await _until(lambda: _status_if(client, lambda s: s.time_s >= 13))
        assert st.time_s < CLIP_S

        await handle.stop()
        assert proc.poll() is not None, "our VLC is still running after stop"
    finally:
        if proc.poll() is None:
            proc.kill()
    with pytest.raises(VlcUnreachable):
        await client.status()
    # Only our child came and went; every VLC that was running still is.
    after = _vlc_pids()
    assert before <= after, "a VLC that was already running was closed"
    assert proc.pid not in after
    pw = re.search(r"--http-password=(\S+)", " ".join(map(str, proc.args)))
    assert pw and pw.group(1) not in caplog.text, "control password was logged"


async def test_live_vlc_natural_end_is_detected(tmp_path, media):
    player = _player(tmp_path)
    stream = Stream(url=media.url, kind=StreamKind.MP4)
    handle = await player.play(stream, title="anime-sh VLC end", start_s=CLIP_S - 6)
    try:
        events = []

        async def run():
            async for ev in handle.events():
                events.append(ev)

        await asyncio.wait_for(run(), timeout=40)
        assert events[-1].eof and events[-1].reason == "eof", events[-3:]
        assert events[-1].duration_s >= CLIP_S - 1
    finally:
        await handle.stop()


async def _status_if(client, pred):
    try:
        st = await client.status()
    except VlcUnreachable:
        return None
    return st if pred(st) else None
