"""Continue Watching is for things you can act on.

Six of the twenty rows were shows caught up on: dimmed, unplayable, carrying
nothing but a countdown to the next episode. The rail carries that same
countdown, grouped by day. Measured against the real library, all six were on
screen twice at once.

Dimming them was already an admission they are not actionable. Once the rail
says it better, the row should stop saying it at all.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from anime_sh.tui.format import RANK_READY, RANK_RESUME, RANK_WAITING
from anime_sh.tui.rows import Row
from anime_sh.tui.screens.home import HomeScreen
from anime_sh.tui.upcoming import schedule, scheduled_ids

NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _anime(anilist: int, title: str, *, in_days: float | None, episode: int = 5):
    return SimpleNamespace(
        id=SimpleNamespace(anilist=anilist),
        title=SimpleNamespace(preferred=title),
        next_airing_at=None if in_days is None else NOW + timedelta(days=in_days),
        next_airing_episode=None if in_days is None else episode,
    )


class _Screen:
    """HomeScreen's filter, driven without a mounted app."""

    _RAIL_MIN_WIDTH = HomeScreen._RAIL_MIN_WIDTH
    _rail_showing = HomeScreen._rail_showing
    _without_rail_duplicates = HomeScreen._without_rail_duplicates

    def __init__(self, width: int, source: list) -> None:
        self.size = SimpleNamespace(width=width)
        self._upcoming_source = source


def _rows(*specs):
    """(anime, Row, resume) triples from (anime, rank) pairs."""
    return [(a, Row(title=a.title.preferred, rank=rank), 1.0) for a, rank in specs]


def test_a_waiting_row_the_rail_is_carrying_is_dropped():
    soon = _anime(1, "Slime Season 4", in_days=5)
    rows = _rows((soon, RANK_WAITING))
    kept = _Screen(200, [soon])._without_rail_duplicates(rows)
    assert kept == [], "the row and the rail both showed the same countdown"


def test_rows_you_can_act_on_are_never_dropped():
    """An episode part-way watched, or one sitting unwatched, is the whole point
    of the section — and both can belong to a show that is also airing next
    week, so both would be on the rail too."""
    airing = _anime(1, "Skeleton Knight", in_days=1)
    rows = _rows((airing, RANK_RESUME))
    assert _Screen(200, [airing])._without_rail_duplicates(rows) == rows

    rows = _rows((airing, RANK_READY))
    assert _Screen(200, [airing])._without_rail_duplicates(rows) == rows


def test_a_waiting_row_beyond_the_rails_horizon_stays():
    """The rail only looks a week ahead. A show airing in nine days reaches no
    other part of the screen, so dropping its row would lose it entirely.

    Paired with a show that *is* on the rail on purpose. Alone, the far-future
    row survives merely because the rail turns out to be empty and the filter
    returns early — which would keep this test green even against a filter that
    drops every waiting row it sees. The pair is what makes it test the check.
    """
    far = _anime(1, "Far Future", in_days=9)
    soon = _anime(2, "Next Week", in_days=3)
    on_rail = scheduled_ids(schedule([far, soon], NOW))
    assert on_rail == {2}, "test premise: only the near show reaches the rail"

    rows = _rows((far, RANK_WAITING), (soon, RANK_WAITING))
    kept = _Screen(200, [far, soon])._without_rail_duplicates(rows)
    assert [a.id.anilist for a, _, _ in kept] == [1]


def test_nothing_is_dropped_when_there_is_no_rail_to_drop_it_to():
    """Below 120 columns Region C does not exist, and the dimmed row is the only
    countdown on screen."""
    soon = _anime(1, "Slime Season 4", in_days=5)
    rows = _rows((soon, RANK_WAITING))
    assert _Screen(90, [soon])._without_rail_duplicates(rows) == rows


def test_the_filter_matches_on_id_not_title():
    """Two seasons of the same show differ by a handful of characters, and one
    of them was already the reason `subtitle_conflict` exists. Matching titles
    would drop the wrong row."""
    s1 = _anime(101, "Frieren", in_days=None)
    s2 = _anime(102, "Frieren", in_days=2)
    rows = _rows((s1, RANK_WAITING), (s2, RANK_WAITING))
    kept = _Screen(200, [s1, s2])._without_rail_duplicates(rows)
    assert [a.id.anilist for a, _, _ in kept] == [101], (
        "dropped the season that is not on the rail"
    )


def test_the_rail_is_fed_before_the_filter_runs():
    """The ordering that makes this safe: the rail is built from every row, the
    filter from the rail. Feed the rail the filtered rows instead and the show
    leaves the rail, which puts the row back, which puts it on the rail — the
    two flipping against each other on every repaint."""
    import inspect

    src = inspect.getsource(HomeScreen._render_continue)
    assert src.index("self._upcoming_source") < src.index("_without_rail_duplicates")
