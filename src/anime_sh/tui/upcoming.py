"""The "Coming Up" rail: episodes you are waiting for, grouped by the day they air.

Pure functions over already-loaded shows. This deliberately makes no requests of
its own — every field it reads (`next_airing_at`, `next_airing_episode`) is
already on the Anime objects the Continue Watching rows were built from, and the
launch path has been rate-limited into the ground once already by a screen that
fetched what it could have reused.

The rows size themselves to their content and stop short of a wide terminal's
edge (a readable measure, see `rows.MEASURE_MAX`). This fills what they leave
with the one thing the main list cannot show: what has *not* aired yet — and,
because it says it better, Continue Watching now drops the dimmed rows that were
saying the same thing. See `HomeScreen._without_rail_duplicates`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class Upcoming:
    """One episode that has not aired yet."""

    airs_at: datetime  # local time, already converted — see `schedule`
    episode: int
    title: str
    # Which show this is, so a caller can ask "is this already on the rail?"
    # without re-deriving the rail's own predicate. Continue Watching uses it to
    # drop the rows the rail has taken over; matching on title instead would
    # break on the two shows whose titles differ only by season.
    anilist_id: int | None = None


@dataclass(frozen=True, slots=True)
class Day:
    """A day heading and the episodes landing on it, earliest first."""

    heading: str
    episodes: tuple[Upcoming, ...]


def _heading(when: datetime, now: datetime) -> str:
    """`Today` / `Tomorrow` / `Sat 30 Aug`.

    Compared on calendar date rather than by hours elapsed: an episode at 00:30
    tonight is "Tomorrow" to a person even though it is four hours away, and
    "in 4h" is already what the countdown column says.
    """
    days = (when.date() - now.date()).days
    if days <= 0:
        return "Today"
    if days == 1:
        return "Tomorrow"
    if days < 7:
        return when.strftime("%A")  # Saturday
    return when.strftime("%a %d %b")  # Sat 30 Aug


def schedule(animes, now: datetime, *, days: int = 7,
             limit: int = 24) -> list[Day]:
    """Group the next unaired episode of each show by the day it airs.

    Only shows with a *future* airing time appear: an episode that has already
    aired is not something to wait for, and it is already sitting in Continue
    Watching as a row you can play. Ties are broken by title so the order is
    stable between repaints rather than reshuffling on every refresh.
    """
    horizon = now + timedelta(days=days)
    # AniList schedules are UTC and that is how they are stored, but a clock time
    # is only meaningful in the reader's own zone — and so is "Today", which is a
    # calendar-date comparison, not a number of hours. Convert once, here, so
    # every heading and time below is already local.
    local_now = now.astimezone()
    entries: list[Upcoming] = []
    for anime in animes:
        at = getattr(anime, "next_airing_at", None)
        ep = getattr(anime, "next_airing_episode", None)
        if at is None or ep is None or not (now < at <= horizon):
            continue
        entries.append(Upcoming(
            at.astimezone(), int(ep), anime.title.preferred,
            anilist_id=getattr(getattr(anime, "id", None), "anilist", None),
        ))

    entries.sort(key=lambda e: (e.airs_at, e.title))
    entries = entries[:limit]

    out: list[Day] = []
    for entry in entries:
        heading = _heading(entry.airs_at, local_now)
        if out and out[-1].heading == heading:
            out[-1] = Day(heading, out[-1].episodes + (entry,))
        else:
            out.append(Day(heading, (entry,)))
    return out


def render(days: list[Day], width: int) -> str:
    """Rich markup for the rail.

    Every line is Label typography — dim — because this is the panel you glance
    at, not the one you act on. The main list stays the only thing at full
    strength, so the eye is never asked to choose between two competing columns.
    """
    if not days:
        return "[dim]Nothing airing in the next week.[/dim]"

    # time (5) + gap (2) + episode (up to 6) + gap (2) = 15 before the title.
    title_w = max(12, width - 15)
    lines: list[str] = []
    for i, day in enumerate(days):
        if i:
            lines.append("")
        lines.append(f"[dim]{day.heading}[/dim]")
        for ep in day.episodes:
            label = f"Ep {ep.episode}"
            title = ep.title
            if len(title) > title_w:
                title = title[: title_w - 1].rstrip() + "…"
            lines.append(
                f"[dim]{ep.airs_at:%H:%M}  {label:<6} {title}[/dim]"
            )
    return "\n".join(lines)


def scheduled_ids(days: list[Day]) -> set[int]:
    """The AniList ids the rail is actually showing.

    "Actually" is the load-bearing word: `schedule` drops shows beyond the
    horizon and truncates at `limit`, so a caller must not assume that a show
    with a future airing time made it onto the rail. Continue Watching hides its
    waiting rows against this set rather than against that assumption, so a show
    airing in nine days stays visible in the list where it is the only place it
    would appear at all.
    """
    return {
        e.anilist_id
        for day in days
        for e in day.episodes
        if e.anilist_id is not None
    }
