"""The de-obfuscating proxy relays segments as they arrive.

Every anikoto segment arrives PNG-disguised and has to pass through this proxy,
so it sits directly in the playback path. It used to read each segment fully into
memory (`resp.content`) before sending a single byte, which added a whole
segment's download to every segment's latency — playback stalled on a connection
with bandwidth to spare.

The suite did not cover the streaming path at all, which is how a first version
of it referencing three undefined constants passed 446 tests: the code raised
`NameError` on the first segment and fell back to the buffered path, so nothing
failed and nothing got faster. These tests drive real HTTP through a real
`ThreadingHTTPServer`.
"""

from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from anime_sh.domain.models import Stream, StreamKind
from anime_sh.infra.proxy.deobfuscate import DeobfuscatingProxy, _PNG_MAGIC, _TS_PACKET

# A decoy header, then real MPEG-TS: three sync bytes 188 apart is what the
# offset finder confirms on.
DECOY = _PNG_MAGIC + b"\x00" * 244
# Comfortably larger than the proxy's 96 KiB head buffer, so the injected
# stall below happens after the head has been read and relayed.
TS_BODY = b"".join(bytes([0x47]) + bytes(_TS_PACKET - 1) for _ in range(3000))
SEGMENT = DECOY + TS_BODY


@pytest.fixture
def origin():
    """A stand-in CDN. `served` records what it was asked for."""
    served: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def do_GET(self):
            served.append(self.path)
            if self.path.startswith("/nolength"):
                # No Content-Length: the proxy cannot frame this, and must fall
                # back rather than guess.
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"%X\r\n" % len(SEGMENT) + SEGMENT + b"\r\n0\r\n\r\n")
                return
            if self.path.startswith("/slowtail"):
                # Head immediately, tail after a beat. A streaming relay lets the
                # head through at once; a buffering one shows nothing until the
                # whole segment has landed.
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(SEGMENT)))
                self.end_headers()
                self.wfile.write(SEGMENT[:150_000])
                self.wfile.flush()
                time.sleep(1.5)
                self.wfile.write(SEGMENT[150_000:])
                return
            body = SEGMENT if self.path.startswith("/seg") else b"#EXTM3U\n/seg1.ts\n"
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    yield base, served
    server.shutdown()
    server.server_close()


@pytest.fixture
def proxy():
    p = DeobfuscatingProxy()
    yield p
    p.stop()


def _proxied(proxy: DeobfuscatingProxy, url: str) -> str:
    return proxy.rewrite(
        Stream(url=url, kind=StreamKind.HLS, obfuscated=True)
    ).url


def test_a_segment_arrives_stripped_of_its_decoy_header(origin, proxy):
    """The whole point: what mpv receives must be playable TS, not a PNG."""
    base, _ = origin
    got = httpx.get(_proxied(proxy, f"{base}/seg1.ts"), timeout=30)

    assert got.status_code == 200
    assert got.content[:1] == b"\x47", "did not start on a TS sync byte"
    assert got.content == TS_BODY
    assert not got.content.startswith(_PNG_MAGIC)


def test_the_declared_length_matches_the_body_actually_sent(origin, proxy):
    """A wrong Content-Length on a keep-alive connection desynchronises every
    request after it — the failure would look like random corruption several
    segments later, not like a bug here."""
    base, _ = origin
    got = httpx.get(_proxied(proxy, f"{base}/seg1.ts"), timeout=30)

    assert int(got.headers["Content-Length"]) == len(got.content)
    assert len(got.content) == len(SEGMENT) - len(DECOY)


def test_a_response_without_a_length_falls_back_instead_of_guessing(origin, proxy):
    """The streaming path cannot frame a chunked response, so it must hand over
    to the buffered path — which still has to produce correct, stripped output."""
    base, _ = origin
    got = httpx.get(_proxied(proxy, f"{base}/nolength.ts"), timeout=30)

    assert got.status_code == 200
    assert got.content == TS_BODY


def test_a_playlist_is_still_rewritten_not_streamed(origin, proxy):
    """A playlist can arrive on a URL that looked like a segment. Streaming it
    through untouched would hand mpv absolute CDN URLs that bypass the proxy,
    and the episode would not play at all."""
    base, _ = origin
    got = httpx.get(_proxied(proxy, f"{base}/index.m3u8"), timeout=30)

    assert got.text.startswith("#EXTM3U")
    assert "127.0.0.1" in got.text, "playlist entries were not routed back here"


def test_the_connection_is_reused_across_segments(origin, proxy):
    """HTTP/1.0 closed the socket after every response, so mpv paid a TCP
    handshake per segment. Two requests on one client must share a connection."""
    base, _ = origin
    url = _proxied(proxy, f"{base}/seg1.ts")
    with httpx.Client(timeout=30) as client:
        first = client.get(url)
        second = client.get(url)

    assert first.http_version == "HTTP/1.1"
    for resp in (first, second):
        assert resp.headers.get("Connection", "").lower() != "close"
        assert resp.content == TS_BODY


def test_playback_starts_before_the_whole_segment_has_downloaded(origin, proxy):
    """The actual point of streaming, and the only assertion here that fails
    against the buffered implementation.

    The other tests in this file check *correctness*, which the buffered path
    also satisfied — they would have passed against the bug. This one injects a
    1.5 s stall partway through the segment and asserts the first bytes arrive
    before it: buffering waits for the whole body, streaming does not.

    The delay is injected rather than measured, so the margin does not depend on
    how fast or loaded this machine is.
    """
    base, _ = origin
    url = _proxied(proxy, f"{base}/slowtail.ts")

    started = time.perf_counter()
    with httpx.stream("GET", url, timeout=30) as resp:
        # One iterator, consumed once: httpx raises StreamConsumed if the body
        # is iterated a second time.
        body = resp.iter_bytes(8192)
        first_chunk = next(body)
        ttfb = time.perf_counter() - started
        rest = b"".join(body)
    total = time.perf_counter() - started

    assert first_chunk[:1] == b"\x47", "first bytes were not stripped TS"
    assert total > 1.4, "the origin stall did not happen; the test proves nothing"
    assert ttfb < 1.0, (
        f"first byte took {ttfb:.2f}s of a {total:.2f}s segment — "
        f"the proxy is still buffering the whole thing"
    )
    assert first_chunk + rest == TS_BODY
