"""A tiny localhost HLS proxy that un-hides PNG-disguised video segments.

Some hosts (anikoto's megaplay/nekostream CDN) serve each ``.ts`` segment with a
fake PNG header prepended and ``Content-Type: image/png``; the browser player
strips the prefix in JS, so mpv/ffmpeg — which see a PNG — can't play it. This
proxy sits on ``127.0.0.1``: mpv fetches the playlist and segments from it, and
for every segment it strips the decoy prefix (everything before the first
MPEG-TS sync run) and serves clean ``video/mp2t``. Playlists are rewritten so
their child URLs point back through the proxy, carrying the CDN referer.

``strip_media_prefix`` is a pure function and unit-tested directly; the server
is a stdlib ``ThreadingHTTPServer`` in a daemon thread.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx

from ...domain.models import Stream

log = logging.getLogger(__name__)

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

# CDN hosts known to serve PNG-disguised segments (the megaplay family).
_OBFUSCATED_HOSTS = ("nekostream", "mewstream", "lostproject")

_TS_PACKET = 188
_TS_SYNC = 0x47


def _find_ts_offset(data: bytes, scan: int = 65536) -> int:
    """Offset of the first MPEG-TS packet (0x47 sync repeating every 188 bytes),
    or 0 if the data already starts clean / has no detectable prefix."""
    limit = min(len(data), scan)
    i = 0
    while i < limit:
        off = data.find(bytes([_TS_SYNC]), i, limit)
        if off < 0:
            return 0
        if (
            off + 2 * _TS_PACKET < len(data)
            and data[off + _TS_PACKET] == _TS_SYNC
            and data[off + 2 * _TS_PACKET] == _TS_SYNC
        ):
            return off
        i = off + 1
    return 0


def _subtitle_content_type(url: str) -> str:
    path = urlsplit(url).path.lower()
    if path.endswith(".vtt"):
        return "text/vtt"
    if path.endswith(".srt"):
        return "application/x-subrip"
    if path.endswith(".ass") or path.endswith(".ssa"):
        return "text/x-ssa"
    return "text/plain"


def strip_media_prefix(data: bytes) -> bytes:
    """Strip a decoy prefix (e.g. a fake PNG header) before real MPEG-TS.

    Idempotent: clean TS (offset 0) and non-TS payloads are returned unchanged.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        off = _find_ts_offset(data)
        return data[off:] if off > 0 else data
    # Not a PNG wrapper — leave it alone (already clean, or fMP4 we don't touch).
    return data


class DeobfuscatingProxy:
    """Rewrites obfuscated-CDN streams to play through a local strip-proxy."""

    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._base: str | None = None
        self._client = httpx.Client(
            headers={"User-Agent": AGENT}, follow_redirects=True, timeout=25
        )
        self._lock = threading.Lock()

    # -- public API --------------------------------------------------------- #
    def rewrite(self, stream: Stream) -> Stream:
        """Return a proxied Stream if its host needs de-obfuscation, else the
        stream unchanged."""
        host = urlsplit(stream.url).netloc
        if not any(h in host for h in _OBFUSCATED_HOSTS):
            return stream
        self._ensure_started()
        referer = stream.headers.get("Referer") or stream.headers.get("referer") or ""
        # Subtitle files on this CDN are referer-gated (403 without one). Since
        # the proxied stream carries no headers, route the subtitle URLs through
        # the proxy too so they inherit the referer — otherwise mpv silently
        # fails to load them and no subs appear.
        subs = tuple(
            replace(sub, url=self._proxy_url(sub.url, referer, kind="sub"))
            for sub in stream.subtitles
        )
        # Headers are baked into the proxy now, so mpv needs none.
        return replace(
            stream, url=self._proxy_url(stream.url, referer), headers={}, subtitles=subs
        )

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self._client.close()

    # -- server ------------------------------------------------------------- #
    def _ensure_started(self) -> str:
        with self._lock:
            if self._base is not None:
                return self._base
            proxy = self

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *a):  # silence access log
                    pass

                def do_GET(self):
                    # mpv routinely aborts connections when it seeks or stops;
                    # swallow the resulting socket errors so no traceback prints.
                    try:
                        proxy._handle(self)
                    except (BrokenPipeError, ConnectionResetError,
                            ConnectionAbortedError, OSError):
                        pass

            self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            port = self._server.server_address[1]
            self._base = f"http://127.0.0.1:{port}"
            threading.Thread(
                target=self._server.serve_forever, daemon=True, name="anime-sh-proxy"
            ).start()
            log.debug("de-obfuscating proxy started at %s", self._base)
            return self._base

    def _proxy_url(self, real_url: str, referer: str, kind: str | None = None) -> str:
        u = base64.urlsafe_b64encode(real_url.encode()).decode()
        r = base64.urlsafe_b64encode(referer.encode()).decode()
        suffix = f"&k={kind}" if kind else ""
        return f"{self._base}/s?u={u}&r={r}{suffix}"

    def _handle(self, req: BaseHTTPRequestHandler) -> None:
        try:
            qs = parse_qs(urlsplit(req.path).query)
            real = base64.urlsafe_b64decode(qs["u"][0]).decode()
            referer = base64.urlsafe_b64decode(qs.get("r", [b""])[0] or "").decode()
            kind = qs.get("k", [""])[0]
        except Exception:
            req.send_error(400)
            return
        try:
            resp = self._client.get(real, headers={"Referer": referer} if referer else None)
            body = resp.content
        except Exception as e:
            log.debug("proxy fetch failed for %s: %s", real[:60], e)
            with contextlib.suppress(OSError):
                req.send_error(502)
            return

        # Subtitles are plain text (WEBVTT/SRT): pass them through untouched with
        # a subtitle content-type — never run them through the TS de-obfuscator.
        if kind == "sub":
            self._respond(req, body, _subtitle_content_type(real))
        elif body.lstrip()[:7] == b"#EXTM3U":
            payload = self._rewrite_playlist(body.decode("utf-8", "replace"), real, referer)
            self._respond(req, payload.encode(), "application/vnd.apple.mpegurl")
        else:
            self._respond(req, strip_media_prefix(body), "video/mp2t")

    def _rewrite_playlist(self, text: str, base_url: str, referer: str) -> str:
        base = base_url.rsplit("/", 1)[0]
        out: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                absolute = s if s.startswith("http") else f"{base}/{s}"
                out.append(self._proxy_url(absolute, referer))
            else:
                out.append(line)
        return "\n".join(out) + "\n"

    def _respond(self, req: BaseHTTPRequestHandler, data: bytes, content_type: str) -> None:
        try:
            req.send_response(200)
            req.send_header("Content-Type", content_type)
            req.send_header("Content-Length", str(len(data)))
            req.end_headers()
            req.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass  # mpv seeked/closed; harmless
