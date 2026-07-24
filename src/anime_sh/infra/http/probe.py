"""A cheap liveness probe for resolved streams.

Streaming CDNs rotate and die constantly; a resolved URL that 403s or 404s will
never play, but the player only discovers that after it launches and waits.
This probe does a single ranged request and reads *only* the status line — never
the body — so a dead CDN is dropped in well under a second instead of costing the
player its full confirm timeout.

Conservative by design: it rejects a stream **only** on a definitive dead
response. A 2xx/3xx, a 5xx, or any network error/timeout is treated as live, so a
good-but-slow or transiently-erroring host is never wrongly skipped — mpv still
gets its chance.
"""

from __future__ import annotations

import logging

import httpx

from ...domain.models import Stream

log = logging.getLogger(__name__)

# "This resource is not going to play." Everything else is treated as live.
_DEAD_STATUSES = frozenset({401, 402, 403, 404, 410, 451})

AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


class HttpStreamProbe:
    """Ranged, header-only GET that rejects only definitively-dead CDNs."""

    def __init__(self, timeout: float = 4.0) -> None:
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def is_live(self, stream: Stream) -> bool:
        client = self._ensure_client()
        headers = {"User-Agent": AGENT, **dict(stream.headers), "Range": "bytes=0-1"}
        try:
            # stream(): open the connection, read the status, close — the body
            # (which for a direct MP4 could be huge) is never downloaded.
            async with client.stream("GET", stream.url, headers=headers) as resp:
                status = resp.status_code
        except Exception as e:
            log.debug("probe network error for %s: %s", stream.url[:60], e)
            return True  # ambiguous — let the player try
        if status in _DEAD_STATUSES:
            log.debug("probe: %s -> HTTP %s (dead)", stream.url[:60], status)
            return False
        return True

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
