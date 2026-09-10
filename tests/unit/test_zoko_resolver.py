"""The ZokoAnime resolver's payload decoding.

The point of interest is that the XOR key is *recovered* rather than
hardcoded, so most of these tests build a payload with a key the code has never
seen and check it still decodes. A test that only ever used the real key would
pass just as well against a hardcoded one, and would say nothing about the day
the site rotates it.
"""

from __future__ import annotations

import base64
import json

import pytest

from anime_sh.domain.errors import ResolverError
from anime_sh.domain.models import Audio, StreamCandidate, StreamKind
from anime_sh.resolvers.zoko.resolver import ZokoResolver, decode_payload

CONFIG = {
    "download_url": "/download/mal/52991/1/sub",
    "src": "https://hls2.example.uk/v/a/b/c/master.m3u8",
    "subtitles": [
        {
            "lang": "en",
            "label": "English",
            "default": True,
            "src": "https://hls2.example.uk/v/a/b/c/subs/en.vtt",
        }
    ],
    "skip": {"intro": {"start": 0, "end": 89}, "outro": {"start": 1460, "end": 1549}},
}


def encode(config: dict, key: bytes) -> str:
    """Build a ``window.__P`` payload the way the site does."""
    plain = json.dumps(config).encode()
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(plain))
    return base64.b64encode(xored).decode()


def test_decodes_the_key_seen_in_the_wild():
    assert decode_payload(encode(CONFIG, b"otaku-embed-v1"))["src"] == CONFIG["src"]


@pytest.mark.parametrize(
    "key", [b"x", b"ab", b"zzzzz", b"a-different-key", b"rotated-once!!!"]
)
def test_decodes_a_key_it_has_never_seen(key):
    """This is the whole reason the key is derived from the payload."""
    assert decode_payload(encode(CONFIG, key))["src"] == CONFIG["src"]


def test_rejects_a_payload_it_cannot_decode():
    """A site change that is *not* a key rotation must fail loudly, so the
    fan-out moves on to another provider instead of playing nothing."""
    garbage = base64.b64encode(b"\x00" * 200).decode()
    with pytest.raises(ResolverError):
        decode_payload(garbage)


def test_rejects_input_that_is_not_base64():
    with pytest.raises(ResolverError):
        decode_payload("not base64 at all !!!")


# -- resolve ---------------------------------------------------------------- #
class _Http:
    def __init__(self, page: str):
        self.page = page
        self.headers_seen: dict | None = None

    async def get_text(self, url, *, params=None, headers=None):
        self.headers_seen = headers
        return self.page

    async def aclose(self):
        pass


def _page(payload: str) -> str:
    return f'<html><body><script>window.__P="{payload}"</script></body></html>'


def _candidate() -> StreamCandidate:
    return StreamCandidate(
        host="ZokoAnime",
        url="https://zokoanime.video/stream/mal/52991/1/sub",
        audio=Audio.SUB,
    )


def test_handles_only_its_own_host():
    resolver = ZokoResolver(http=_Http(""))
    assert resolver.handles(_candidate())
    assert not resolver.handles(
        StreamCandidate(host="HD-1", url="https://megaplay.buzz/stream/s-2/1/sub")
    )


async def test_resolve_returns_the_stream_with_subtitles_and_skips():
    resolver = ZokoResolver(http=_Http(_page(encode(CONFIG, b"otaku-embed-v1"))))
    (stream,) = await resolver.resolve(_candidate())
    assert stream.url == CONFIG["src"]
    assert stream.kind is StreamKind.HLS
    assert [s.lang for s in stream.subtitles] == ["en"]
    assert stream.subtitles[0].default is True
    assert stream.skip_times is not None
    assert (stream.skip_times.op.start_s, stream.skip_times.op.end_s) == (0, 89)
    assert stream.headers["Referer"] == "https://zokoanime.video/"


async def test_resolve_tolerates_an_episode_with_no_skip_marks():
    """``skip`` is null on most episodes; that is not a failure."""
    config = dict(CONFIG, skip=None)
    resolver = ZokoResolver(http=_Http(_page(encode(config, b"otaku-embed-v1"))))
    (stream,) = await resolver.resolve(_candidate())
    assert stream.skip_times is None
    assert stream.url == CONFIG["src"]


async def test_resolve_is_not_marked_obfuscated():
    """Unlike the megaplay family, these segments are clean MPEG-TS — flagging
    them would send the proxy hunting for a decoy header that is not there."""
    resolver = ZokoResolver(http=_Http(_page(encode(CONFIG, b"otaku-embed-v1"))))
    (stream,) = await resolver.resolve(_candidate())
    assert stream.obfuscated is False


async def test_resolve_raises_when_the_page_has_no_payload():
    resolver = ZokoResolver(http=_Http("<html><body>nothing here</body></html>"))
    with pytest.raises(ResolverError, match="no player payload"):
        await resolver.resolve(_candidate())


async def test_resolve_raises_when_the_payload_carries_no_stream():
    config = {"download_url": "/download/x", "subtitles": []}
    resolver = ZokoResolver(http=_Http(_page(encode(config, b"otaku-embed-v1"))))
    with pytest.raises(ResolverError, match="no stream URL"):
        await resolver.resolve(_candidate())
