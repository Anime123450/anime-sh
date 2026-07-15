"""Shared helpers for mapping host-reported resolutions to domain Quality."""

from __future__ import annotations

from ..domain.models import Quality, StreamKind

_RES_TO_QUALITY = {
    "2160": Quality.Q2160,
    "4k": Quality.Q2160,
    "1080": Quality.Q1080,
    "720": Quality.Q720,
    "480": Quality.Q480,
    "360": Quality.Q360,
}


def quality_from_str(value: str | None) -> Quality:
    if not value:
        return Quality.UNKNOWN
    digits = "".join(ch for ch in value if ch.isdigit())
    return _RES_TO_QUALITY.get(digits, _RES_TO_QUALITY.get(value.lower(), Quality.UNKNOWN))


def kind_from_url(url: str) -> StreamKind:
    lowered = url.split("?", 1)[0].lower()
    if lowered.endswith(".m3u8") or ".m3u8" in lowered:
        return StreamKind.HLS
    if lowered.endswith(".mpd"):
        return StreamKind.DASH
    return StreamKind.MP4
