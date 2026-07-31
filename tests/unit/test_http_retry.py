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
