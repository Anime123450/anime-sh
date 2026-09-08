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
from anime_sh.infra.http import CloudflareChallenge  # noqa: E402


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
