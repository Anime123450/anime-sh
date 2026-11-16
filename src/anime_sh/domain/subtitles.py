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


_VTT_BLOCKS = ("NOTE", "STYLE", "REGION")


def repair_webvtt(text: str) -> str:
    """Drop blank lines that sit inside a cue's text.

    WebVTT ends a cue at the first blank line. Some providers put one inside a
    cue (a multi-line stat sheet, say); the line after it then reads as a cue
    with no timestamp, and mpv's parser abandons the rest of the file, so the
    subtitles stop partway through the episode. A blank line only ends a cue
    when what follows starts a new block: a timing line, a cue identifier
    followed by a timing line, or a NOTE/STYLE/REGION block. A well-formed file
    comes back unchanged.
    """
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    out: list[str] = []
    in_cue = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if "-->" in line:
            in_cue = True
        elif in_cue and not line.strip():
            j = i
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                nxt = lines[j]
                after = lines[j + 1] if j + 1 < len(lines) else ""
                starts_block = ("-->" in nxt or "-->" in after
                                or nxt.startswith(_VTT_BLOCKS))
                if not starts_block:
                    i = j  # blank line(s) inside the cue: drop them
                    continue
            in_cue = False
        out.append(line)
        i += 1
    return "\n".join(out)

