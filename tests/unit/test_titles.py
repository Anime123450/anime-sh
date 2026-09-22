"""Season detection — keeps a sequel from being offered as a prequel's source."""

from __future__ import annotations

import pytest

from anime_sh.domain.titles import same_season, season_number


@pytest.mark.parametrize(
    "title,expected",
    [
        ("From Old Country Bumpkin to Master Swordsman", 1),
        ("From Old Country Bumpkin to Master Swordsman Season 2", 2),
        ("From Old Country Bumpkin to Master Swordsman II", 2),
        ("Katainaka no Ossan, Kensei ni Naru", 1),
        ("Katainaka no Ossan, Kensei ni Naru II", 2),
        ("Wistoria: Wand and Sword Season 2", 2),
        ("Classroom of the Elite 4th Season: Second Year, First Semester", 4),
        ("The 100 Girlfriends Who Really, REALLY Love You Season 3", 3),
        ("BLEACH: Thousand-Year Blood War - The Calamity", 1),
        ("Frieren: Beyond Journey's End", 1),
        (None, 1),
        ("", 1),
    ],
)
def test_season_number(title, expected):
    assert season_number(title) == expected


def test_the_reported_case_separates_the_two_seasons():
    """The bug: season 1's identity was offered season 2's provider entry, so the
    user watched season 2 while progress was written against season 1."""
    identity = "From Old Country Bumpkin to Master Swordsman"          # AniList 179955
    sources = [
        "Katainaka no Ossan, Kensei ni Naru",                          # anizone, S1
        "Katainaka no Ossan, Kensei ni Naru II",                       # anizone, S2
        "From Old Country Bumpkin to Master Swordsman",                # anikoto, 12 eps
        "From Old Country Bumpkin to Master Swordsman Season 2",       # anikoto, 4 eps
    ]
    kept = [s for s in sources if season_number(s) == season_number(identity)]
    assert kept == [
        "Katainaka no Ossan, Kensei ni Naru",
        "From Old Country Bumpkin to Master Swordsman",
    ]


@pytest.mark.parametrize(
    "title,expected",
    [
        # AniList writes some sequels with the Unicode roman numerals (U+2160+).
        # An ASCII-only pattern can't see them, so the sequel read as season 1 —
        # and could then be offered as a source for its own prequel.
        ("The Misfit of Demon King Academy Ⅱ", 2),
        ("Show Ⅲ", 3),
        ("Show Ⅳ", 4),
        ("ＦＵＬＬＷＩＤＴＨ Season 2", 2),
    ],
)
def test_unicode_season_markers(title, expected):
    assert season_number(title) == expected


# -- the "S2" shorthand ------------------------------------------------------ #
def test_the_s2_shorthand_is_a_season():
    """AniList writes some sequels this way — "Full-Time Magister S2" — and
    without it the sequel read as season 1, which made it a candidate source
    for its own prequel: you watch season 2 while progress is written against
    season 1's AniList id."""
    assert season_number("Full-Time Magister S2") == 2
    assert season_number("Show S02") == 2
    assert season_number("Show s3") == 3
    assert season_number("Show S10") == 10


def test_the_prequel_and_its_s2_are_not_the_same_season():
    assert not same_season("Full-Time Magister", "Full-Time Magister S2")


@pytest.mark.parametrize(
    "title",
    [
        "Full-Time Magister",
        "Yu-Gi-Oh! 5Ds",       # digits-then-s, the shape most likely to misfire
        "Steins;Gate 0",
        "Macross 7",
        "Mobile Suit Gundam SEED",
        "86 EIGHTY-SIX",
        "Plus Sized Elf",
        "Bus Gamer",
        "Kids on the Slope",
        "Wolf's Rain",
    ],
)
def test_the_shorthand_does_not_misfire_on_ordinary_titles(title):
    """The pattern is anchored at the end and needs a word boundary before the
    S. A title that merely contains an S and a digit must stay season 1."""
    assert season_number(title) == 1
