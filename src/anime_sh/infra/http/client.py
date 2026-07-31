"""One small async HTTP facade over two backends.

* ``httpx`` for well-behaved APIs (AniList).
* ``curl_cffi`` with browser TLS impersonation for providers that reject
  non-browser TLS fingerprints.

Each provider gets its own :class:`HttpClient` with its own headers and a
concurrency semaphore — never share one across providers, because cookies,
referers, and rate limits differ. Cloudflare *managed* challenges (full JS
interstitials) are detected and surfaced as :class:`CloudflareChallenge`; we do
not attempt to solve them.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

import httpx

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class HttpError(Exception):
    """Any non-recoverable HTTP failure."""


class RateLimited(HttpError):
    """The server asked us to slow down (HTTP 429) for longer than we were
    willing to block. Callers can turn this into advice rather than a stack of
    URL noise."""


class CloudflareChallenge(HttpError):
    """The origin returned a Cloudflare JS interstitial. Not solvable here."""


def _retry_after(headers: Mapping[str, str] | Any) -> float | None:
    """Seconds from a ``Retry-After`` header, when it carries a plain number.

    The HTTP-date form is ignored on purpose: the fixed backoff is a fine
    fallback and parsing dates here would only add a way to get it wrong.
    """
    try:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    except Exception:
        return None
    if not raw:
        return None
    try:
        seconds = float(str(raw).strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _looks_like_challenge(status: int, text: str) -> bool:
    if status not in (403, 429, 503):
        return False
    head = text[:600].lower()
    return "just a moment" in head or "challenges.cloudflare.com" in head


class HttpClient:
    def __init__(
        self,
        *,
        headers: Mapping[str, str] | None = None,
        impersonate: str | None = None,
        timeout: float = 15.0,
        max_concurrency: int = 5,
        retries: int = 2,
        max_retry_wait_s: float = 60.0,
    ) -> None:
        self._headers = {"User-Agent": DEFAULT_UA, **(headers or {})}
        self._impersonate = impersonate
        self._timeout = timeout
        self._retries = retries
        # How long we'll honour a Retry-After. Batch work (pushing a whole list)
        # should sit out a full rate-limit window; anything a user is watching
        # must not, because a 60-second sleep is indistinguishable from a hang.
        self._max_retry_wait_s = max_retry_wait_s
        self._sem = asyncio.Semaphore(max_concurrency)
        self._httpx: httpx.AsyncClient | None = None
        self._cffi: Any | None = None

    # -- lifecycle ---------------------------------------------------------- #
    async def aclose(self) -> None:
        if self._httpx is not None:
            await self._httpx.aclose()
            self._httpx = None
        if self._cffi is not None:
            await self._cffi.close()
            self._cffi = None

    # -- requests ----------------------------------------------------------- #
    async def get_json(
        self, url: str, *, params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        text = await self._request("GET", url, params=params, headers=headers)
        return _loads(text)

    async def get_text(
        self, url: str, *, params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        return await self._request("GET", url, params=params, headers=headers)

    async def post_json(
        self, url: str, *, json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        text = await self._request("POST", url, json=json, headers=headers)
        return _loads(text)

    # -- core --------------------------------------------------------------- #
    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        merged = {**self._headers, **(headers or {})}
        last: Exception | None = None
        async with self._sem:
            for attempt in range(self._retries + 1):
                try:
                    status, text, retry_after = await self._send(
                        method, url, params, json, merged
                    )
                except (httpx.TransportError, asyncio.TimeoutError) as e:
                    last = HttpError(f"{method} {url} transport error: {e}")
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                if _looks_like_challenge(status, text):
                    raise CloudflareChallenge(f"{url} is behind a Cloudflare challenge")
                if status == 429:
                    # Rate limited. This used to fall into the generic 4xx raise
                    # and fail outright — which a large `sync push` walks straight
                    # into, since AniList caps requests per minute. Wait the
                    # server's own Retry-After when it sends one.
                    last = RateLimited(f"{method} {url} -> 429 (rate limited)")
                    # `is None` rather than falsy: "Retry-After: 0" means retry
                    # immediately, not "no header".
                    delay = retry_after if retry_after is not None else 2.0 * (attempt + 1)
                    if delay > self._max_retry_wait_s:
                        break  # longer than we're willing to block — surface it
                    await asyncio.sleep(delay)
                    continue
                if status >= 500:
                    last = HttpError(f"{method} {url} -> {status}")
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                if status >= 400:
                    raise HttpError(f"{method} {url} -> {status}")
                return text
        assert last is not None
        raise last

    async def _send(
        self, method: str, url: str,
        params: Mapping[str, Any] | None,
        json: Any,
        headers: Mapping[str, str],
    ) -> tuple[int, str, float | None]:
        """``(status, body, retry_after_seconds)`` — the third is the server's
        Retry-After when it sent a usable one."""
        if self._impersonate:
            return await self._send_cffi(method, url, params, json, headers)
        return await self._send_httpx(method, url, params, json, headers)

    async def _send_httpx(self, method, url, params, json, headers):
        if self._httpx is None:
            self._httpx = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        r = await self._httpx.request(
            method, url, params=params, json=json, headers=headers
        )
        return r.status_code, r.text, _retry_after(r.headers)

    async def _send_cffi(self, method, url, params, json, headers):
        if self._cffi is None:
            from curl_cffi.requests import AsyncSession

            self._cffi = AsyncSession(
                impersonate=self._impersonate, timeout=self._timeout
            )
        r = await self._cffi.request(
            method, url, params=params, json=json, headers=headers
        )
        return r.status_code, r.text, _retry_after(r.headers)


def _loads(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HttpError(f"expected JSON, got: {text[:200]!r}") from e
