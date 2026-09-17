"""HttpStreamProbe — rejects definitively-dead CDNs, keeps everything else."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from anime_sh.domain.models import Quality, Stream, StreamKind
from anime_sh.infra.http.probe import HttpStreamProbe


def _stream(url: str) -> Stream:
    return Stream(url=url, kind=StreamKind.HLS, quality=Quality.UNKNOWN)


@pytest.fixture
def server():
    """/dead -> 403, /gone -> 404, /boom -> 500, /cf -> 522, else 200."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_GET(self):
            code = (403 if "dead" in self.path else
                    404 if "gone" in self.path else
                    500 if "boom" in self.path else
                    522 if "cf" in self.path else 200)
            body = b"ok"
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except Exception:
                pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


async def test_live_host_passes(server):
    probe = HttpStreamProbe(timeout=3)
    try:
        assert await probe.is_live(_stream(f"{server}/video.m3u8")) is True
    finally:
        await probe.aclose()


async def test_forbidden_and_gone_are_rejected(server):
    probe = HttpStreamProbe(timeout=3)
    try:
        assert await probe.is_live(_stream(f"{server}/dead.m3u8")) is False
        assert await probe.is_live(_stream(f"{server}/gone.m3u8")) is False
    finally:
        await probe.aclose()


async def test_server_error_is_not_rejected(server):
    # A 5xx might be transient — don't drop the stream over it.
    probe = HttpStreamProbe(timeout=3)
    try:
        assert await probe.is_live(_stream(f"{server}/boom.m3u8")) is True
    finally:
        await probe.aclose()


async def test_network_error_is_treated_as_live():
    # Nothing listening -> connection refused -> ambiguous -> keep the stream.
    probe = HttpStreamProbe(timeout=1)
    try:
        assert await probe.is_live(_stream("http://127.0.0.1:9/x.m3u8")) is True
    finally:
        await probe.aclose()


async def test_cloudflare_origin_failures_are_rejected(server):
    """520-527 are Cloudflare saying it could not reach the origin at all —
    nothing behind that edge is serving media, so the next host is a better bet
    than the player.

    hianime's CDN answered 522 for every episode of every show on 17/09/2026.
    The probe waved it through on the "a 5xx might be transient" rule and mpv
    was handed a URL that could not play, while a working provider sat one place
    behind it in the fan-out.
    """
    probe = HttpStreamProbe(timeout=3)
    try:
        assert await probe.is_live(_stream(f"{server}/cf.m3u8")) is False
    finally:
        await probe.aclose()
