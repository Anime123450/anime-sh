"""Split an HLS master playlist into "one video variant + one audio track".

Why this exists: some hosts (ZokoAnime among them) publish a master playlist
with every dub as a separate ``EXT-X-MEDIA:TYPE=AUDIO`` rendition — 15 of them
on a 2026 episode — beside four video variants. Handed the master, ffmpeg (and
so mpv) opens and probes every one of those playlists before the first frame:
31 s measured, against a 25 s "did it start?" window, so the episode was
abandoned as unplayable although every URL in it worked. Opening the chosen
variant with the chosen audio as a second input started in under 8 s.

Pure and offline so it is unit-testable; the resolver does the fetching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

from ..domain.models import Quality

_ATTR_RE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
_HEIGHTS = (
    (2160, Quality.Q2160),
    (1080, Quality.Q1080),
    (720, Quality.Q720),
    (480, Quality.Q480),
    (360, Quality.Q360),
)


@dataclass(frozen=True, slots=True)
class Variant:
    url: str
    quality: Quality
    audio_url: str | None


def _attrs(line: str) -> dict[str, str]:
    body = line.split(":", 1)[1] if ":" in line else ""
    return {k: v.strip('"') for k, v in _ATTR_RE.findall(body)}


def _quality(attrs: dict[str, str], uri: str) -> Quality:
    height = 0
    res = attrs.get("RESOLUTION", "")
    if "x" in res:
        try:
            height = int(res.split("x", 1)[1])
        except ValueError:
            height = 0
    if not height:
        m = re.search(r"(?<!\d)(2160|1080|720|480|360)(?:p)?(?!\d)", uri)
        height = int(m.group(1)) if m else 0
    for floor, quality in _HEIGHTS:
        if height >= floor - 40:  # 1072, 714 etc. still count as the tier
            return quality
    return Quality.UNKNOWN


def _pick_audio(renditions: list[dict[str, str]], prefer_lang: str | None):
    if not renditions:
        return None
    if prefer_lang:
        want = prefer_lang.lower()
        for r in renditions:
            if r.get("LANGUAGE", "").lower().startswith(want):
                return r
    for r in renditions:
        if r.get("DEFAULT", "").upper() == "YES":
            return r
    return renditions[0]


def split_master(text: str, base_url: str, *, prefer_lang: str | None = None) -> list[Variant]:
    """The master's video variants, each paired with one audio rendition.

    Returns ``[]`` when there is nothing to gain — not a master playlist, or no
    separate audio renditions (audio muxed into the variants plays fine from
    the master, and the master keeps mpv's own bitrate switching).
    ``prefer_lang`` is an ISO-639 prefix ("en" for dub); otherwise the
    playlist's DEFAULT=YES rendition, which is the original-language track.
    """
    if "#EXTM3U" not in text or "#EXT-X-STREAM-INF" not in text:
        return []
    groups: dict[str, list[dict[str, str]]] = {}
    lines = [ln.strip() for ln in text.splitlines()]
    for line in lines:
        if line.startswith("#EXT-X-MEDIA:"):
            a = _attrs(line)
            if a.get("TYPE") == "AUDIO" and a.get("URI"):
                groups.setdefault(a.get("GROUP-ID", ""), []).append(a)
    if not groups:
        return []

    variants: list[Variant] = []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        uri = next((ln for ln in lines[i + 1:] if ln and not ln.startswith("#")), None)
        if not uri:
            continue
        a = _attrs(line)
        video_url = urljoin(base_url, uri)
        if video_url in seen:
            continue
        seen.add(video_url)
        audio = _pick_audio(groups.get(a.get("AUDIO", ""), []), prefer_lang)
        variants.append(
            Variant(
                url=video_url,
                quality=_quality(a, uri),
                audio_url=urljoin(base_url, audio["URI"]) if audio else None,
            )
        )
    return variants
