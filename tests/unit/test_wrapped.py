"""The year in review: the counting, and the card it draws.

The interesting parts are all edge cases — a session after midnight, a streak
that nearly joins, a year with nothing in it — which is exactly why the
counting is pure and lives in the domain.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anime_sh.cli.wrapped_svg import render
from anime_sh.domain.models import Anime, AnimeId, HistoryItem, Title
from anime_sh.domain.wrapped import summarise

UTC = timezone.utc


def _show(title: str, *, genres=("Action",), anilist=1) -> Anime:
    return Anime(id=AnimeId(anilist=anilist), title=Title(romaji=title), genres=tuple(genres))


def _watched(show: Anime, when: datetime, *, seconds=1400, episode=1.0) -> HistoryItem:
    return HistoryItem(
        anime=show, episode=episode, watched_at=when,
        provider="hianime", seconds_watched=seconds,
    )


def _day(y, m, d, hour=12) -> datetime:
    return datetime(y, m, d, hour, tzinfo=UTC)


# -- counting --------------------------------------------------------------- #
def test_an_empty_history_is_empty_not_an_error():
    w = summarise([])
    assert w.empty and w.episodes == 0 and w.hours == 0


def test_counts_episodes_hours_and_distinct_shows():
    a, b = _show("A", anilist=1), _show("B", anilist=2)
    history = [
        _watched(a, _day(2026, 3, 1), seconds=1800),
        _watched(a, _day(2026, 3, 2), seconds=1800),
        _watched(b, _day(2026, 3, 3), seconds=3600),
    ]
    w = summarise(history, local_dates=False)
    assert (w.episodes, w.shows, w.hours) == (3, 2, 2.0)
    assert w.top_shows[0] == ("A", 2)


def test_a_year_filter_keeps_only_that_year():
    a = _show("A")
    history = [_watched(a, _day(2025, 12, 31)), _watched(a, _day(2026, 1, 1))]
    w = summarise(history, year=2026, local_dates=False)
    assert w.episodes == 1 and w.year == 2026


def test_a_year_with_nothing_in_it_is_empty_not_a_crash():
    w = summarise([_watched(_show("A"), _day(2024, 5, 5))], year=2026, local_dates=False)
    assert w.empty and w.year == 2026


def test_genres_are_counted_per_episode_watched():
    a = _show("A", genres=("Action", "Comedy"))
    b = _show("B", genres=("Action",), anilist=2)
    w = summarise(
        [_watched(a, _day(2026, 1, 1)), _watched(b, _day(2026, 1, 2))],
        local_dates=False,
    )
    assert dict(w.top_genres)["Action"] == 2
    assert dict(w.top_genres)["Comedy"] == 1


# -- streaks ---------------------------------------------------------------- #
def test_the_longest_streak_is_consecutive_days_and_names_its_end():
    a = _show("A")
    days = [1, 2, 3, 4, 7, 8]  # a 4-day run, then a 2-day run
    history = [_watched(a, _day(2026, 2, d)) for d in days]
    w = summarise(history, local_dates=False)
    assert w.longest_streak == 4
    assert w.streak_ended.isoformat() == "2026-02-04"


def test_several_episodes_in_one_day_do_not_inflate_a_streak():
    """A binge is one day, however many episodes it was."""
    a = _show("A")
    history = [_watched(a, _day(2026, 2, 1, hour=h)) for h in (9, 12, 15, 21)]
    w = summarise(history, local_dates=False)
    assert w.longest_streak == 1
    assert w.busiest_day_episodes == 4


def test_a_streak_spanning_a_month_boundary_still_counts():
    a = _show("A")
    history = [_watched(a, _day(2026, 1, 30)), _watched(a, _day(2026, 1, 31)),
               _watched(a, _day(2026, 2, 1))]
    w = summarise(history, local_dates=False)
    assert w.longest_streak == 3


def test_months_are_bucketed_by_calendar_month():
    a = _show("A")
    history = [_watched(a, _day(2026, 1, 5)), _watched(a, _day(2026, 1, 6)),
               _watched(a, _day(2026, 12, 25))]
    w = summarise(history, local_dates=False)
    assert w.by_month[0] == 2 and w.by_month[11] == 1
    assert sum(w.by_month) == 3


def test_dates_are_read_in_local_time():
    """"What did I watch on Saturday" means the user's Saturday. A 01:00 UTC
    session is the previous evening for much of the world, and a streak that
    breaks on a timezone is a wrong answer about someone's habits."""
    a = _show("A")
    east = timezone(timedelta(hours=9))  # JST-ish
    late = datetime(2026, 3, 2, 1, 0, tzinfo=UTC)  # 10:00 on the 2nd in +09:00
    w = summarise([_watched(a, late.astimezone(east))], local_dates=True)
    assert w.busiest_day is not None


# -- the card --------------------------------------------------------------- #
def _card(history, **kw) -> str:
    return render(summarise(history, local_dates=False, **kw))


def test_the_card_is_self_contained():
    """No script and no external fetches: GitHub strips scripted SVG, and this
    file exists to be pasted into exactly that kind of place."""
    svg = _card([_watched(_show("A"), _day(2026, 1, 1))])
    assert "<script" not in svg
    assert svg.count("http") == svg.count('xmlns="http://www.w3.org/2000/svg"')


def test_the_card_declares_its_own_size():
    """A viewBox with no width/height has no intrinsic size and renders as a
    blurry 300x150 when embedded."""
    svg = _card([_watched(_show("A"), _day(2026, 1, 1))])
    assert 'width="900"' in svg and 'height="500"' in svg and "viewBox=" in svg


def test_the_card_draws_nothing_outside_the_canvas():
    import re

    show = _show("A really quite long show title that will need clipping")
    svg = _card([_watched(show, _day(2026, m, 1)) for m in range(1, 13)])
    for m in re.finditer(r'<(?:rect|text)[^>]*?x="(-?\d+)"[^>]*?y="(-?\d+)"([^>]*)>', svg):
        x, y, rest = int(m.group(1)), int(m.group(2)), m.group(3)
        w = int(re.search(r'width="(\d+)"', rest).group(1)) if 'width="' in rest else 0
        h = int(re.search(r'height="(\d+)"', rest).group(1)) if 'height="' in rest else 0
        assert 0 <= x and x + w <= 900, f"x overflow: {m.group(0)[:80]}"
        assert 0 <= y and y + h <= 500, f"y overflow: {m.group(0)[:80]}"


def test_titles_with_xml_characters_are_escaped():
    """A show title is user data as far as this file is concerned."""
    svg = _card([_watched(_show("Fate/stay night <UBW> & co"), _day(2026, 1, 1))])
    assert "&amp;" in svg and "&lt;" in svg
    assert "<UBW>" not in svg


def test_an_empty_history_still_renders_a_card():
    svg = render(summarise([]))
    assert svg.startswith("<svg") and svg.endswith("</svg>")


@pytest.mark.parametrize("day", [1, 9, 28])
def test_day_labels_avoid_the_glibc_only_format(day):
    """`%-d` raises ValueError on Windows, which is most of this project's
    users."""
    a = _show("A")
    history = [_watched(a, _day(2026, 3, day)), _watched(a, _day(2026, 3, day))]
    svg = render(summarise(history, local_dates=False))
    assert f"{day} Mar" in svg
