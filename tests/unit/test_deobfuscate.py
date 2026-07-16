"""The PNG-prefix stripper that makes megaplay/nekostream segments playable."""

from __future__ import annotations

from anime_sh.infra.proxy import strip_media_prefix
from anime_sh.infra.proxy.deobfuscate import _find_ts_offset

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
