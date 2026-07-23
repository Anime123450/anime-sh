"""The PNG-prefix stripper that makes megaplay/nekostream segments playable."""

from __future__ import annotations

from anime_sh.domain.models import Quality, Stream, StreamKind, Subtitle
from anime_sh.infra.proxy import DeobfuscatingProxy, strip_media_prefix
from anime_sh.infra.proxy.deobfuscate import _find_ts_offset, _subtitle_content_type

# A minimal fake PNG header (magic + IHDR-ish + IEND), then MPEG-TS packets.
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _ts_packet(seq: int) -> bytes:
    # 188-byte packet starting with the 0x47 sync byte.
    return b"\x47" + bytes([seq % 256]) * 187


def _fake_segment(prefix_len: int) -> tuple[bytes, bytes]:
    ts = b"".join(_ts_packet(i) for i in range(5))
    prefix = _PNG_MAGIC + b"\x00" * (prefix_len - len(_PNG_MAGIC))
    return prefix + ts, ts


def test_strips_png_prefix_to_clean_ts():
    blob, ts = _fake_segment(prefix_len=252)
    out = strip_media_prefix(blob)
    assert out == ts
    assert out[0] == 0x47
    assert len(out) % 188 == 0


def test_find_ts_offset_locates_sync_run():
    blob, ts = _fake_segment(prefix_len=100)
    assert _find_ts_offset(blob) == 100


def test_clean_ts_is_unchanged():
    ts = b"".join(_ts_packet(i) for i in range(5))
    # No PNG magic -> returned as-is (idempotent).
    assert strip_media_prefix(ts) == ts


def test_non_png_payload_untouched():
    data = b"just some bytes that are not a png or ts"
    assert strip_media_prefix(data) == data


# -- subtitle handling (referer-gated .vtt through the proxy) ----------------- #
def _stream(url, subs=()):
    return Stream(url=url, kind=StreamKind.HLS, quality=Quality.UNKNOWN,
                  headers={"Referer": "https://megaplay.buzz/"}, subtitles=subs)


def test_rewrite_proxies_subtitle_urls_on_obfuscated_host():
    # Subs on the referer-gated CDN must be routed through the proxy (which
    # supplies the referer) — else mpv fetches them headerless and 403s.
    proxy = DeobfuscatingProxy()
    try:
        sub = Subtitle(url="https://mt.nekostream.site/x/subtitles/English.vtt",
                       lang="English", default=True)
        out = proxy.rewrite(_stream("https://mt.nekostream.site/x/master.m3u8", (sub,)))
        assert out.headers == {}  # baked into the proxy
        assert len(out.subtitles) == 1
        proxied = out.subtitles[0].url
        assert proxied.startswith("http://127.0.0.1:") and "k=sub" in proxied
    finally:
        proxy.stop()


def test_rewrite_leaves_clean_host_subtitles_alone():
    proxy = DeobfuscatingProxy()
    try:
        sub = Subtitle(url="https://cdn.example.com/en.vtt", lang="en")
        stream = _stream("https://cdn.example.com/video.m3u8", (sub,))
        out = proxy.rewrite(stream)
        assert out is stream  # untouched: not an obfuscated host
    finally:
        proxy.stop()


def test_rewrite_proxies_flagged_stream_on_unknown_host():
    # The regression that broke every anikoto play: the CDN rotated to a
    # hostname the allowlist had never heard of, so the proxy stood down and
    # mpv choked on PNG-disguised segments. The resolver's flag must be enough
    # on its own, with no hostname match.
    proxy = DeobfuscatingProxy()
    try:
        stream = Stream(
            url="https://vidtub.brand-new-cdn.example/abc/master.m3u8",
            kind=StreamKind.HLS,
            headers={"Referer": "https://vidtube.site/"},
            obfuscated=True,
        )
        out = proxy.rewrite(stream)
        assert out is not stream
        assert out.url.startswith("http://127.0.0.1:")
        assert out.headers == {}  # referer baked into the proxy
    finally:
        proxy.stop()


def test_rewrite_ignores_unflagged_stream_on_unknown_host():
    # The flag must not make the proxy greedy: a clean host stays direct.
    proxy = DeobfuscatingProxy()
    try:
        stream = Stream(url="https://clean.example/v.m3u8", kind=StreamKind.HLS)
        assert proxy.rewrite(stream) is stream
    finally:
        proxy.stop()


def test_subtitle_content_type_by_extension():
    assert _subtitle_content_type("https://x/a/English.vtt") == "text/vtt"
    assert _subtitle_content_type("https://x/a/sub.srt") == "application/x-subrip"
    assert _subtitle_content_type("https://x/a/sub.ass?tok=1") == "text/x-ssa"
    assert _subtitle_content_type("https://x/a/unknown") == "text/plain"
