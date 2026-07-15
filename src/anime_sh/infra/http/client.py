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


class CloudflareChallenge(HttpError):
    """The origin returned a Cloudflare JS interstitial. Not solvable here."""


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
    ) -> None:
        self._headers = {"User-Agent": DEFAULT_UA, **(headers or {})}
        self._impersonate = impersonate
        self._timeout = timeout
        self._retries = retries
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
                    status, text = await self._send(
                        method, url, params, json, merged
                    )
                except (httpx.TransportError, asyncio.TimeoutError) as e:
                    last = HttpError(f"{method} {url} transport error: {e}")
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                if _looks_like_challenge(status, text):
                    raise CloudflareChallenge(f"{url} is behind a Cloudflare challenge")
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
    ) -> tuple[int, str]:
        if self._impersonate:
            return await self._send_cffi(method, url, params, json, headers)
        return await self._send_httpx(method, url, params, json, headers)

    async def _send_httpx(self, method, url, params, json, headers):
        if self._httpx is None:
            self._httpx = httpx.AsyncClient(timeout=self._timeout, follow_redirects=True)
        r = await self._httpx.request(
            method, url, params=params, json=json, headers=headers
        )
        return r.status_code, r.text

    async def _send_cffi(self, method, url, params, json, headers):
        if self._cffi is None:
            from curl_cffi.requests import AsyncSession

            self._cffi = AsyncSession(
                impersonate=self._impersonate, timeout=self._timeout
            )
        r = await self._cffi.request(
            method, url, params=params, json=json, headers=headers
        )
        return r.status_code, r.text


def _loads(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise HttpError(f"expected JSON, got: {text[:200]!r}") from e
