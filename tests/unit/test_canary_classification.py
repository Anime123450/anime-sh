"""The canary must tell a broken provider apart from a blocked IP.

anizone answers home connections fine and serves CI runners a Cloudflare
interstitial. Reporting that as a provider regression filed a GitHub issue every
night that nobody could act on — and an alert that always fires is an alert
everyone learns to ignore.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

from canary import _is_cloudflare  # noqa: E402

from anime_sh.domain.errors import ProviderError  # noqa: E402
from anime_sh.domain.models import (  # noqa: E402
    Anime, AnimeId, Audio, Episode, ProviderRef, Quality, Stream, StreamCandidate,
    StreamKind, Title,
)
from anime_sh.infra.http import CloudflareChallenge  # noqa: E402

_ANIME = Anime(id=AnimeId(anilist=154587), title=Title(romaji="Frieren"))


def test_a_direct_challenge_is_recognised():
    assert _is_cloudflare(CloudflareChallenge("behind a Cloudflare challenge"))


def test_a_challenge_wrapped_by_a_provider_is_recognised():
    """Providers re-raise transport failures as ProviderError, so the exception
    type alone doesn't identify it — the cause chain does."""
    try:
        try:
            raise CloudflareChallenge("behind a Cloudflare challenge")
        except CloudflareChallenge as e:
            raise ProviderError("anizone failed") from e
    except ProviderError as chained:
        assert _is_cloudflare(chained)


def test_the_message_is_a_fallback_when_the_chain_is_lost():
    assert _is_cloudflare(
        ProviderError("anizone: https://anizone.to/anime is behind a Cloudflare challenge")
    )


def test_a_real_failure_is_still_a_failure():
    assert not _is_cloudflare(ValueError("boom"))
    assert not _is_cloudflare(ProviderError("anizone: no episodes returned"))


async def test_a_metadata_outage_is_not_reported_as_a_provider_failure():
    """The identity lookup is a precondition of the probe, not part of it.

    On 07/09/2026 AniList disabled its own public API and this canary filed
    "anikoto provider is failing" and "anizone provider is failing" against a
    repo whose providers were both fine. `blocked` exits 0 and files nothing,
    which is the whole point of that status existing.
    """
    import scripts.canary as canary

    class _DeadMetadata:
        async def search(self, *a, **k):
            raise RuntimeError("AniList request failed: POST https://graphql.anilist.co -> 403")

    class _Provider:
        name = "anikoto"
        async def match(self, *a, **k):
            raise AssertionError("the provider must not be contacted at all")

    result = await canary.check_provider(
        "anikoto", _Provider(), _DeadMetadata(), []
    )
    assert result["status"] == "blocked", result
    assert "metadata unavailable" in result["detail"]


# -- "healthy" must mean watchable ------------------------------------------ #
class _Metadata:
    async def search(self, *a, **k):
        return [_ANIME]

    async def aclose(self):
        pass


class _Provider:
    """Read path always works; only the streams differ between tests."""

    name = "probe"

    def __init__(self, hosts=("HostA", "HostB"), origin=None):
        self.hosts = hosts
        #: One origin shared by every label, the way anikoto serves three names
        #: for megaplay.buzz. None gives each label its own host.
        self.origin = origin

    async def match(self, *a, **k):
        return ProviderRef(provider="probe", anime_key="1", audio=Audio.SUB)

    async def episodes(self, ref, anime_id):
        return [Episode(anime_id=anime_id, number=1.0, provider_ref=ref, episode_key="1")]

    async def candidates(self, episode):
        return [
            StreamCandidate(host=h, url=f"https://{self.origin or h + '.test'}/e/1")
            for h in self.hosts
        ]


class _Resolver:
    """Claims every candidate; `behaviour` decides what happens next."""

    name = "fake"

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def handles(self, candidate):
        return True

    async def resolve(self, candidate):
        if self.behaviour == "raise":
            raise RuntimeError("host refused")
        return [Stream(url="https://cdn.test/master.m3u8", kind=StreamKind.HLS,
                       quality=Quality.UNKNOWN)]


async def _check(resolvers, serves, monkeypatch, provider=None):
    import scripts.canary as canary

    async def fake_playlist(stream):
        return serves

    monkeypatch.setattr(canary, "_playlist_serves", fake_playlist)
    return await canary.check_provider(
        "probe", _Provider(**(provider or {})), _Metadata(), resolvers
    )


async def test_a_stream_that_loads_is_healthy(monkeypatch):
    result = await _check([_Resolver("ok")], None, monkeypatch)
    assert result["status"] == "ok"
    assert result["playable"] is True


async def test_every_host_failing_to_resolve_is_not_healthy(monkeypatch):
    """The regression this was written for: anikoto offered 28 episodes and two
    hosts for five days, resolved none of them, and was reported `ok` with
    "(hosts flaky)" every morning. One flaky host is noise; zero of two is a
    provider nobody can watch."""
    result = await _check([_Resolver("raise")], None, monkeypatch)
    assert result["status"] == "degraded"
    assert "0 of 2 resolved" in result["detail"]
    assert result["playable"] is False


async def test_hosts_no_resolver_claims_is_not_healthy(monkeypatch):
    result = await _check([], None, monkeypatch)
    assert result["status"] == "degraded"
    assert "no resolver handles" in result["detail"]
    assert result["resolvable_hosts"] == 0


async def test_a_resolved_stream_that_does_not_load_is_not_healthy(monkeypatch):
    """hianime resolved perfectly on 17/09/2026 while every playlist behind it
    answered HTTP 522. A URL in hand is not a stream a user can watch."""
    result = await _check([_Resolver("ok")], "HTTP 522", monkeypatch)
    assert result["status"] == "degraded"
    assert "did not load" in result["detail"] and "522" in result["detail"]
    assert result["playable"] is False


async def test_the_reason_every_host_refused_is_carried_into_the_detail(monkeypatch):
    """The line said how many and never why.

    `0 of 3 resolved — nothing playable` is a count. The resolver raised something
    specific for each host and the canary swallowed it, so finding out what took
    re-running the whole walk by hand with the `except` clause removed. anikoto
    read exactly like that for weeks: the status was right, the cause was only in
    an exception nobody kept.
    """
    result = await _check([_Resolver("raise")], None, monkeypatch)
    assert "0 of 2 resolved" in result["detail"]
    assert "host refused" in result["detail"], "the resolver's reason must survive"
    assert "RuntimeError" in result["detail"], "and what kind of failure it was"


async def test_a_resolver_that_returns_nothing_says_so_rather_than_nothing(monkeypatch):
    """The quieter half: returning an empty list raises nothing at all, so there
    was no exception to carry and the detail stayed a bare count."""

    class _Empty(_Resolver):
        async def resolve(self, candidate):
            return []

    result = await _check([_Empty("ok")], None, monkeypatch)
    assert result["status"] == "degraded"
    assert "no streams" in result["detail"]


async def test_three_labels_for_one_host_do_not_read_as_three_hosts(monkeypatch):
    """anikoto offers "HD-1", "Vidstream-1" and "Vidstream-2". All three are
    megaplay.buzz.

    The line counted candidates, so it said "3 hosts, 0 of 3 resolved" -- which
    reads as three independent chances missed, the shape of a flaky afternoon you
    wait out. There was only ever one host, and once it started encrypting its
    playlists the provider was gone until *it* changed. Naming the origin is the
    difference between "try again later" and "this provider is finished".
    """
    result = await _check(
        [_Resolver("raise")], None, monkeypatch,
        provider={"hosts": ("HD-1", "Vidstream-1", "Vidstream-2"),
                  "origin": "megaplay.buzz"},
    )
    assert result["status"] == "degraded"
    assert "3 labels for 1 host (megaplay.buzz)" in result["detail"]
    assert result["hosts"] == 1
    assert result["candidates"] == 3


async def test_genuinely_different_hosts_are_still_counted_as_hosts(monkeypatch):
    """hianime's four hosts really are four, and must not be relabelled."""
    result = await _check(
        [_Resolver("raise")], None, monkeypatch,
        provider={"hosts": ("HostA", "HostB", "HostC")},
    )
    assert "3 hosts" in result["detail"]
    assert "labels" not in result["detail"]
    assert result["hosts"] == 3
