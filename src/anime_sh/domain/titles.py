"""Reading season numbers out of anime titles.

A provider search for one season happily returns its sequels too — searching
"From Old Country Bumpkin to Master Swordsman" on a provider gives back both that
season and "…Master Swordsman Season 2". Offering the sequel as a *source* for
the prequel breaks the identity spine: you end up watching season 2's episodes
while progress is recorded against season 1's AniList id.
"""

from __future__ import annotations

import re

_ROMAN = {
    "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}

# "Season 2", "2nd Season", and a trailing roman numeral ("… II").
_SEASON_N = re.compile(r"\bseason\s+(\d{1,2})\b")
_NTH_SEASON = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b")
_TRAILING_ROMAN = re.compile(r"\b([ivx]{1,5})\s*$")


def season_number(title: str | None) -> int:
    """Which season a title names. Unmarked titles are season 1.

    Deliberately conservative: only the forms that actually show up in AniList
    and provider listings. Anything unrecognised reads as season 1, which is the
    safe answer — it keeps a title matched to itself.
    """
    if not title:
        return 1
    low = title.strip().lower()
    for pattern in (_SEASON_N, _NTH_SEASON):
        m = pattern.search(low)
        if m:
            return int(m.group(1))
    m = _TRAILING_ROMAN.search(low)
    if m:
        return _ROMAN.get(m.group(1), 1)
    return 1


def same_season(a: str | None, b: str | None) -> bool:
    return season_number(a) == season_number(b)
