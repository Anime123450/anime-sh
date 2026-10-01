"""The de-obfuscating proxy must only serve URLs it handed out itself.

It takes the URL to fetch from its own query string, so while an episode is
playing it will fetch anything anyone asks it to and hand back the response —
with whatever `Referer` they choose. That is a forwarding proxy sitting on
loopback with no door on it, and it runs with the user's network position: any
other process on the machine can point it at a router admin page, a cloud
metadata endpoint, or a service that trusts localhost, and read what comes
back.

The port is random and short-lived, which makes it less likely to be found, not
harder to use once found. Enumerating listening ports is not an obstacle.
"""

from __future__ import annotations

import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from anime_sh.domain.models import Stream, StreamKind
from anime_sh.infra.proxy import DeobfuscatingProxy


@pytest.fixture
def victim():
    """Stands in for anything that trusts localhost — a router page, a metadata
    endpoint, a dev server. Records whether the proxy was made to fetch it."""
    hits: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            body = b"SECRET-INTERNAL-RESPONSE"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/admin", hits
    server.shutdown()


@pytest.fixture
def proxy():
    p = DeobfuscatingProxy()
    yield p
    p.stop()


def _started(proxy: DeobfuscatingProxy, url: str) -> str:
    """Start the proxy the way playback does, and return the URL it gives mpv."""
    stream = Stream(url=url, kind=StreamKind.HLS, headers={"Referer": "https://cdn/"},
                    obfuscated=True)
    return proxy.rewrite(stream).url


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode()


def test_the_proxy_refuses_a_url_it_did_not_hand_out(proxy, victim):
    target, hits = victim
    base = _started(proxy, "https://nekostream.example/a.m3u8").split("/s?")[0]

    # An attacker who has found the port, and nothing else.
    forged = f"{base}/s?u={_b64(target)}&r={_b64('https://evil/')}"
    response = httpx.get(forged)

    assert not hits, f"the proxy fetched an arbitrary URL on request: {hits}"
    assert b"SECRET-INTERNAL-RESPONSE" not in response.content
    assert response.status_code >= 400


def test_a_wrong_token_is_refused(proxy, victim):
    target, hits = victim
    handed_out = _started(proxy, "https://nekostream.example/a.m3u8")
    base, _, query = handed_out.partition("?")

    forged = f"{base}?u={_b64(target)}&r={_b64('')}&t=not-the-token"
    httpx.get(forged)
    assert not hits, "a guessed token was accepted"


def test_the_url_handed_to_mpv_still_works(proxy, victim):
    """The guard is worthless if it also stops playback. Build the URL the way
    the proxy does, pointed at a server under our control."""
    target, hits = victim
    _started(proxy, "https://nekostream.example/a.m3u8")

    allowed = proxy._proxy_url(target, "https://cdn/", kind="sub")
    response = httpx.get(allowed)

    assert hits, "the proxy refused a URL it generated itself"
    assert response.content == b"SECRET-INTERNAL-RESPONSE"


def test_rewritten_playlist_entries_carry_the_token(proxy):
    """Playlist children are fetched by mpv through the proxy too, so they must
    pass the same door — otherwise the guard breaks every multi-segment
    stream."""
    _started(proxy, "https://nekostream.example/a.m3u8")
    playlist = "#EXTM3U\n#EXTINF:4,\nseg1.ts\n#EXTINF:4,\nhttps://cdn/seg2.ts\n"

    out = proxy._rewrite_playlist(playlist, "https://nekostream.example/a.m3u8", "https://cdn/")

    entries = [line for line in out.splitlines() if line.startswith("http")]
    assert len(entries) == 2
    for entry in entries:
        assert "t=" in entry, f"playlist entry has no token: {entry}"
