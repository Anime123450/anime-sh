"""Reading season numbers out of anime titles.

A provider search for one season happily returns its sequels too — searching
"From Old Country Bumpkin to Master Swordsman" on a provider gives back both that
season and "…Master Swordsman Season 2". Offering the sequel as a *source* for
the prequel breaks the identity spine: you end up watching season 2's episodes
while progress is recorded against season 1's AniList id.
"""

from __future__ import annotations

import re
import unicodedata

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
    # NFKC first: AniList uses the Unicode roman numerals (U+2160+) for some
    # sequels — "…Demon King Academy Ⅱ" — and those are invisible to an
    # ASCII pattern, so the sequel read as season 1 and could be offered as a
    # source for its own prequel.
    low = unicodedata.normalize("NFKC", title).strip().lower()
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


# Words describing a *release* rather than a *work*: their presence on one side
# of a comparison says nothing about whether the two are the same season.
_RELEASE_WORDS = frozenset({
    "dub", "dubbed", "sub", "subbed", "subtitled", "tv", "bd", "uncensored",
    "censored", "raw", "hd", "sd", "episode", "episodes", "the", "a", "of",
})

_WORD = re.compile(r"[a-z0-9']+")


def _identity_words(title: str | None) -> frozenset[str]:
    """The words that say *which work* this is.

    Season markers are dropped because ``season_number`` already carries them,
    and release words because "Attack on Titan (Dub)" is the same season as
    "Attack on Titan".
    """
    if not title:
        return frozenset()
    low = unicodedata.normalize("NFKC", title).strip().lower()
    for pattern in (_SEASON_N, _NTH_SEASON, _TRAILING_ROMAN):
        low = pattern.sub(" ", low)
    return frozenset(_WORD.findall(low)) - _RELEASE_WORDS


def same_entry(a: str | None, b: str | None) -> bool:
    """Whether two titles name the same season of the same show.

    ``season_number`` alone cannot answer this. Sequels are often marked with a
    subtitle rather than a number — "Attack on Titan: Final Season", "JoJo's
    Bizarre Adventure: Stone Ocean", "Demon Slayer: Entertainment District Arc"
    — and every one of those reads as season 1, exactly like its prequel, so a
    provider offering one as a source for the other passed the season filter
    untouched. That is the identity-spine bug in §1.1: episodes play from one
    season while progress is recorded against another.

    The signal is *asymmetry*. Where one title's words are a strict superset of
    the other's, the extra words are a subtitle only one of them carries, and
    they are different entries. Titles that merely differ in wording — "Attack
    on Titan" against "Shingeki no Kyojin" — are subsets of neither, and are
    left for ranking to decide, because rejecting those would throw away
    legitimate matches to fix a narrower problem.
    """
    return season_number(a) == season_number(b) and not subtitle_conflict(a, b)


def subtitle_conflict(a: str | None, b: str | None) -> bool:
    """True when one title carries a subtitle the other lacks.

    Deliberately answers only the asymmetric case. Two titles that are simply
    worded differently overlap in neither direction and are *not* a conflict —
    saying otherwise would reject "Shingeki no Kyojin" as a source for "Attack
    on Titan", trading a narrow bug for a much wider one.
    """
    wa, wb = _identity_words(a), _identity_words(b)
    if not wa or not wb:
        return False  # nothing to compare on; do not invent a difference
    return wa < wb or wb < wa
