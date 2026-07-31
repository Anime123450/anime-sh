"""HTTP client retry behaviour — rate limits must not fail outright."""

from __future__ import annotations

import pytest

from anime_sh.infra.http.client import HttpClient, HttpError, _retry_after


def test_retry_after_parses_seconds_and_ignores_junk():
    assert _retry_after({"Retry-After": "3"}) == 3.0
    assert _retry_after({"retry-after": "0.5"}) == 0.5
    # HTTP-date form and nonsense fall back to the fixed backoff.
    assert _retry_after({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}) is None
    assert _retry_after({}) is None


async def test_429_is_retried_rather_than_raised(monkeypatch):
    """A rate limit used to fall into the generic 4xx raise and fail the call.

    AniList caps requests per minute, so a large `sync push` (or brisk browsing)
    walked straight into a hard failure.
    """
    calls: list[int] = []

    async def fake_send(method, url, params, json, headers):
        calls.append(1)
        if len(calls) < 3:
            return 429, "slow down", 0.0  # Retry-After: 0 → no real waiting
        return 200, '{"ok": true}', None

    client = HttpClient(retries=3)
    monkeypatch.setattr(client, "_send", fake_send)
    assert await client.get_json("https://example.test/x") == {"ok": True}
    assert len(calls) == 3  # two 429s, then the success


async def test_429_that_never_clears_still_raises(monkeypatch):
    async def always_429(method, url, params, json, headers):
        return 429, "slow down", 0.0

    client = HttpClient(retries=1)
    monkeypatch.setattr(client, "_send", always_429)
    with pytest.raises(HttpError, match="429"):
        await client.get_json("https://example.test/x")


async def test_a_long_rate_limit_is_surfaced_not_slept_through(monkeypatch):
    """Interactive callers must not block for a full rate-limit window.

    AniList answers 429 with `Retry-After: 60`. Honouring that on a search made
    the app sit silent for a minute — indistinguishable from a hang (measured:
    61s for one query). Anything longer than the caller's budget is raised.
    """
    import time as _time

    from anime_sh.infra.http.client import RateLimited

    async def rate_limited(method, url, params, json, headers):
        return 429, "slow down", 60.0

    client = HttpClient(retries=2, max_retry_wait_s=5.0)
    monkeypatch.setattr(client, "_send", rate_limited)
    started = _time.perf_counter()
    with pytest.raises(RateLimited):
        await client.get_json("https://example.test/x")
    assert _time.perf_counter() - started < 1.0, "slept through the rate limit"


async def test_a_short_rate_limit_is_still_waited_out(monkeypatch):
    calls: list[int] = []

    async def briefly_limited(method, url, params, json, headers):
        calls.append(1)
        if len(calls) == 1:
            return 429, "slow down", 0.0
        return 200, '{"ok": true}', None

    client = HttpClient(retries=2, max_retry_wait_s=5.0)
    monkeypatch.setattr(client, "_send", briefly_limited)
    assert await client.get_json("https://example.test/x") == {"ok": True}
