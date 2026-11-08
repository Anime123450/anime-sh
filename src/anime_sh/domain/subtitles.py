"""Language preference for subtitle tracks."""

from __future__ import annotations

import re
from collections.abc import Sequence

from .models import Subtitle


_ALIASES = {
    "eng": "en",
    "english": "en",
    "jpn": "ja",
    "japanese": "ja",
    "fra": "fr",
    "fre": "fr",
}


def _language_code(value: str) -> str:
    token = re.split(r"[-_\s(]", value.strip().lower(), maxsplit=1)[0]
    return _ALIASES.get(token, token)


def player_language_tags(value: str | None) -> str:
    """Return the compact language list accepted by mpv/VLC player options."""
    code = _language_code(value or "")
    if not code:
        return ""
    # Both players recognize ISO 639-2 and common two-letter English codes.
    if code == "en":
        return "eng,en"
    return code


def prioritize_subtitles(
    subtitles: Sequence[Subtitle], preferred_language: str | None
) -> tuple[Subtitle, ...]:
    """Put the requested language first, then the provider default, stably.

    Provider labels vary (``en-US``, ``eng``, ``English (US)``), so compare
    normalized primary language codes. If there is no match, retain the source's
    declared default rather than silently choosing an arbitrary language.
    """
    preferred = _language_code(preferred_language or "")

    def rank(item: tuple[int, Subtitle]) -> tuple[int, int]:
        index, subtitle = item
        if preferred and _language_code(subtitle.lang) == preferred:
            return (0, index)
        if subtitle.default:
            return (1 if preferred else 0, index)
        return (2 if preferred else 1, index)

    return tuple(subtitle for _, subtitle in sorted(enumerate(subtitles), key=rank))
