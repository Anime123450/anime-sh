"""The Coming Up rail: what is waiting, and when, in the reader's own clock.

At a wide terminal the row grid stops at a readable measure and leaves most of
the window empty. The rail fills it with the one thing the rows cannot show —
episodes that have not aired yet — built entirely from Anime objects Continue
Watching has already loaded, because the launch path has been rate-limited into
the ground once already by a screen that re-fetched what it could have reused.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from anime_sh.tui.upcoming import render, schedule


def _show(title: str, episode, airs_at):
    return SimpleNamespace(
        title=SimpleNamespace(preferred=title),
        next_airing_episode=episode,
        next_airing_at=airs_at,
    )


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def test_times_are_shown_in_the_readers_own_zone():
    """The regression test. AniList schedules are UTC, and the first version
    printed them raw: an episode 2h25m away read as `15:30` to someone whose
    clock said `18:34`. A time display five and a half hours out is worse than
    no time display at all.
    """
    airs = NOW + timedelta(hours=2)
    day = schedule([_show("Show", 9, airs)], NOW)[0]

    assert day.episodes[0].airs_at.utcoffset() == airs.astimezone().utcoffset()
    assert f"{day.episodes[0].airs_at:%H:%M}" == f"{airs.astimezone():%H:%M}"


def test_the_day_heading_uses_the_local_calendar_date():
    """"Today" is a calendar comparison, not a count of hours, and the calendar
    that matters is the reader's. Comparing UTC dates puts an episode airing
    tonight under tomorrow's heading for anyone east of Greenwich."""
    local_now = NOW.astimezone()
    # 23:30 local today — comfortably "Today" wherever this runs.
    tonight = local_now.replace(hour=23, minute=30)
    if tonight <= local_now:  # already past 23:30; use the next slot instead
        tonight = local_now + timedelta(minutes=30)

    days = schedule([_show("Tonight", 4, tonight)], NOW)
    assert days[0].heading == "Today", days[0].heading


def test_an_episode_that_already_aired_is_not_something_to_wait_for():
    """It is already a playable row in Continue Watching."""
    assert schedule([_show("Aired", 3, NOW - timedelta(hours=1))], NOW) == []


def test_shows_with_no_schedule_are_skipped_rather_than_guessed_at():
    assert schedule([_show("Finished", None, None)], NOW) == []
    assert schedule([_show("Half", 5, None)], NOW) == []


def test_episodes_are_grouped_by_day_in_time_order():
    days = schedule([
        _show("Later today", 2, NOW + timedelta(hours=6)),
        _show("Sooner today", 7, NOW + timedelta(hours=1)),
        _show("Next week", 1, NOW + timedelta(days=5)),
    ], NOW)

    assert [d.heading for d in days][0] == "Today"
    first = days[0].episodes
    assert [e.title for e in first] == ["Sooner today", "Later today"]
    assert len(days) == 2


def test_nothing_beyond_the_horizon():
    """A month-away premiere is not "coming up"; it is noise in a glance panel."""
    assert schedule([_show("Far", 1, NOW + timedelta(days=30))], NOW) == []


def test_order_is_stable_when_two_episodes_share_a_slot():
    """The rail repaints every minute to tick the countdowns. Two shows airing at
    the same instant must not swap places each time."""
    same = NOW + timedelta(hours=3)
    animes = [_show("Beta", 1, same), _show("Alpha", 2, same)]
    once = schedule(animes, NOW)[0].episodes
    twice = schedule(list(reversed(animes)), NOW)[0].episodes
    assert [e.title for e in once] == [e.title for e in twice] == ["Alpha", "Beta"]


def test_an_empty_schedule_says_so_rather_than_leaving_a_blank_panel():
    assert "Nothing airing" in render([], 34)


def test_long_titles_are_truncated_to_the_rail_width():
    """The rail is 34–42 columns. A light-novel title must not wrap and push the
    whole panel out of rhythm."""
    long = "The Insipid Prince's Furtive Grab for the Throne and Everything After"
    days = schedule([_show(long, 9, NOW + timedelta(hours=2))], NOW)
    out = render(days, 30)

    for line in out.splitlines():
        # Strip the markup the width budget does not pay for.
        assert len(line.replace("[dim]", "").replace("[/dim]", "")) <= 30, line
    assert "…" in out
