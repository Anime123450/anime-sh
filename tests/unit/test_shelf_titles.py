from datetime import datetime, timedelta, timezone

import pytest

from anime_sh.tui.cards import CARD_COLS
from anime_sh.tui.format import CARD_WIDTH, card_titles, shelf_titles, short_countdown, wrap_title

REZERO = [
    "Re:ZERO -Starting Life in Another World-",
    "Re:ZERO -Starting Life in Another World- Season 2",
    "Re:ZERO -Starting Life in Another World- Season 2 Part 2",
    "Re:ZERO -Starting Life in Another World- Season 3",
    "Re:ZERO -Starting Break Time From Zero-",
    "Re:ZERO -Starting Life in Another World- OVAs",
    "Re:ZERO -Starting Break Time From Zero- 2nd Season",
    "Aldnoah.Zero",
]


def test_card_width_matches_cards():
    assert CARD_WIDTH == CARD_COLS


def test_franchise_siblings_are_distinguishable_and_fit():
    out = shelf_titles(REZERO)
    assert len(set(out)) == len(out)
    assert all(len(t) <= CARD_WIDTH for t in out)
    assert out[0] == "Re:ZERO -Starting…"  # the base title keeps the plain cut
    assert out[1].endswith("Season 2") and out[3].endswith("Season 3")
    assert out[7] == "Aldnoah.Zero"   # fits whole: untouched


def test_unrelated_long_titles_keep_plain_cut():
    out = shelf_titles(["Frieren: Beyond Journey's End", "The Apothecary Diaries"])
    assert out == ["Frieren: Beyond J…", "The Apothecary Di…"]


@pytest.mark.parametrize("delta,expected", [
    (timedelta(hours=3, minutes=12), "3h 12m"),
    (timedelta(days=1, hours=7, minutes=5), "1d 7h"),
    (timedelta(minutes=12), "12m"),
    (timedelta(minutes=-5), ""),
])
def test_short_countdown(delta, expected):
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert short_countdown(now + delta, now) == expected


@pytest.mark.parametrize("titles", [
    ["A Returner's Magic Should Be Special Season 2", "Skeleton Knight in Another World"],
    ["Re:ZERO -Starting Life in Another World- Season 3", "Re:ZERO -Starting Life in Another World-"],
    ["Sousou no Frieren: ●● no Mahou Part 2", "Sousou no Frieren: ●● no Mahou Part 3"],
])
def test_card_caption_is_cut_once(titles):
    """A cut title must fit the card's two wrapped lines without a second cut.

    A flat 34-cell budget ignored where word wrap breaks: "A Returner's" leaves
    six cells of line one empty, so the head…tail cut spilled past line two and
    the card end-cut it again — "A Returner's Magi…Special Seas…".
    """
    for title, cut in zip(titles, card_titles(titles, 18, 2)):
        lines = wrap_title(cut, 18, 2)
        printed = "".join(lines)
        assert printed.count("…") == cut.count("…") <= 1, (title, lines)
        if cut != title:
            # the part that tells siblings apart survives
            assert title.endswith(cut.split("…")[-1]), (title, cut)
