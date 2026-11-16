"""The subtitle prefetch must never hold up the episode launch.

The prefetch runs between Enter and the mpv window. urlopen's timeout bounds
each socket operation, not the download, so a host that keeps sending a few
bytes at a time never trips it: on 2026-11-16 one launch sat inside this fetch
for over four minutes. Past a short budget mpv should stream the track by URL,
exactly as it did before the prefetch existed.
"""
from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anime_sh.domain.models import Stream, StreamKind, Subtitle
from anime_sh.infra.players import mpv as mpv_mod


@pytest.fixture
def trickle_url():
    """A subtitle host that sends one byte every 0.2s and never finishes."""
    stop = threading.Event()

    class Trickle(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # no length: the body ends when we close

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/vtt")
            self.end_headers()
            self.wfile.write(b"WEBVTT\n\n")
            self.wfile.flush()
            while not stop.wait(0.2):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except OSError:
                    return

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Trickle)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/en.vtt"
    stop.set()
    server.shutdown()
    server.server_close()


def test_a_trickling_subtitle_host_gives_up_within_the_budget(monkeypatch, trickle_url):
    monkeypatch.setattr(mpv_mod, "_SUBTITLE_FETCH_DEADLINE_S", 1.0, raising=False)
    outcome = {}

    def fetch():
        try:
            outcome["data"] = mpv_mod._fetch_subtitle(trickle_url, {})
        except Exception as e:  # noqa: BLE001 - the test inspects whatever was raised
            outcome["error"] = e

    started = time.monotonic()
    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(6)
    assert not worker.is_alive(), "the subtitle fetch is still running past its budget"
    assert time.monotonic() - started < 4
    assert isinstance(outcome.get("error"), TimeoutError)


def test_launch_does_not_wait_on_a_stalled_prefetch(monkeypatch):
    """Even a stall the fetch cannot see (a hung DNS lookup) must not block play."""
    monkeypatch.setattr(mpv_mod, "_SUBTITLE_FETCH_DEADLINE_S", 0.2, raising=False)
    monkeypatch.setattr(mpv_mod, "_localize_subtitle",
                        lambda stream: time.sleep(3) or "late.vtt")
    stream = Stream(url="https://cdn.example/master.m3u8", kind=StreamKind.HLS,
                    subtitles=(Subtitle("https://subs.example/en.vtt", "English"),))

    async def launch():
        # Timed inside the loop: asyncio.run joins the abandoned worker thread
        # on shutdown, which is not time the episode launch waits for.
        started = time.monotonic()
        local = await mpv_mod._localize_subtitle_bounded(stream)
        return local, time.monotonic() - started

    local, waited = asyncio.run(launch())
    assert local is None  # mpv falls back to streaming the subtitle URL
    assert waited < 2
