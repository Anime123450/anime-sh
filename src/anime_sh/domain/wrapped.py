"""The year in review, computed from watch history.

Pure and dependency-free: given history rows it returns numbers, and knows
nothing about where they came from or how they will be drawn. That is what
makes every awkward case here — a show watched across midnight, a year with
three episodes in it, a tie between two genres — testable without a database.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class Wrapped:
    """One year of watching, ready to print or draw."""

    year: int | None  # None means "everything, ever"
    episodes: int
    shows: int
    seconds: int
    top_shows: tuple[tuple[str, int], ...] = ()
    top_genres: tuple[tuple[str, int], ...] = ()
    longest_streak: int = 0
    streak_ended: date | None = None
    busiest_day: date | None = None
    busiest_day_episodes: int = 0
    by_month: tuple[int, ...] = field(default=(0,) * 12)

    @property
    def hours(self) -> float:
        return round(self.seconds / 3600, 1)

    @property
    def days(self) -> float:
        return round(self.seconds / 86400, 1)

    @property
    def empty(self) -> bool:
        return self.episodes == 0


def summarise(history, *, year: int | None = None, local_dates=True) -> Wrapped:
    """Fold history rows into a :class:`Wrapped`.

    ``history`` is any iterable of objects with ``anime``, ``watched_at`` and
    ``seconds_watched`` — the shape ``list_history`` returns.

    Dates are taken in local time when ``local_dates`` is set, because "what did
    I watch on Saturday" means the user's Saturday. A session at 01:00 UTC is
    the previous evening for most of the world, and a streak that breaks because
    of a timezone is a wrong answer to a question about someone's habits.
    """
    rows = [h for h in history if year is None or _day(h, local_dates).year == year]
    if not rows:
        return Wrapped(year=year, episodes=0, shows=0, seconds=0)

    shows: Counter[str] = Counter()
    genres: Counter[str] = Counter()
    per_day: Counter[date] = Counter()
    months = [0] * 12
    seconds = 0
    seen_ids = set()

    for h in rows:
        seconds += max(h.seconds_watched, 0)
        title = h.anime.title.preferred
        shows[title] += 1
        for genre in h.anime.genres:
            genres[genre] += 1
        day = _day(h, local_dates)
        per_day[day] += 1
        months[day.month - 1] += 1
        seen_ids.add(h.anime.id.anilist or title)

    streak, streak_end = _longest_streak(per_day)
    busiest, busiest_count = per_day.most_common(1)[0]
    return Wrapped(
        year=year,
        episodes=len(rows),
        shows=len(seen_ids),
        seconds=seconds,
        top_shows=tuple(shows.most_common(5)),
        top_genres=tuple(genres.most_common(5)),
        longest_streak=streak,
        streak_ended=streak_end,
        busiest_day=busiest,
        busiest_day_episodes=busiest_count,
        by_month=tuple(months),
    )


def _day(item, local_dates: bool) -> date:
    when = item.watched_at
    if local_dates and when.tzinfo is not None:
        when = when.astimezone()
    return when.date()


def _longest_streak(per_day: Counter) -> tuple[int, date | None]:
    """Longest run of consecutive days with at least one episode.

    Returns the run's length and the day it ended, so the caller can say "11
    days, ending 3 March" rather than a bare number nobody can place.
    """
    if not per_day:
        return 0, None
    days = sorted(per_day)
    best = run = 1
    best_end = run_end = days[0]
    for previous, current in zip(days, days[1:]):
        if current - previous == timedelta(days=1):
            run += 1
        else:
            run = 1
        run_end = current
        if run > best:
            best, best_end = run, run_end
    return best, best_end
