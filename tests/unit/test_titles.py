"""Season detection — keeps a sequel from being offered as a prequel's source."""

from __future__ import annotations

import pytest

from anime_sh.domain.titles import season_number


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
