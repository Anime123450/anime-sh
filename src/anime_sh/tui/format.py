"""Pure presentation helpers for the TUI — formatted, testable, no widgets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain.models import Anime, WatchProgress

# How long past its air time an episode still reads as "airing now" — wide
# enough for a broadcast slot or a feature-length special. Beyond it, a date in
# the past is a cached schedule nobody has refreshed rather than a live
# broadcast, and "airing now" was said exactly as confidently three weeks late
# as three minutes early.
# ponytail: one flat window; per-format runtimes if it ever needs to be tighter.
_AIRING_GRACE_S = 3 * 3600


def countdown(target: datetime, now: datetime | None = None) -> str:
    """Human "in 5d 3h" until ``target``. Past/near targets read naturally."""
    now = now or datetime.now(timezone.utc)
    secs = int((target - now).total_seconds())
    if secs <= 0:
        return "airing now" if -secs <= _AIRING_GRACE_S else "aired"
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"in {d}d {h}h"
    if h:
        return f"in {h}h {m}m"
    return f"in {m}m"


def next_episode_line(anime: Anime, now: datetime | None = None) -> str | None:
    """"Ep 3 in 5d 3h" for a currently-airing show, else None."""
    if anime.next_airing_episode and anime.next_airing_at:
        return f"Ep {anime.next_airing_episode} {countdown(anime.next_airing_at, now)}"
    return None


def episode_air_label(
    anime: Anime, episode: float, now: datetime | None = None
) -> str | None:
    """"airs in 4d 3h" for an episode that hasn't come out yet, projected from
    the known next-airing episode at a weekly cadence. None when we can't tell —
    no schedule, or the episode has already aired."""
    if not (anime.next_airing_episode and anime.next_airing_at):
        return None
    if episode <= (anime.aired_through(now) or 0):
        return None  # already aired
    if anime.next_airing_at <= (now or datetime.now(timezone.utc)):
        # The anchor this projects from has already come true, so every date
        # derived from it is a guess off a stale one. The next episode's time is
        # genuinely unknown here, and saying nothing beats "airs in 4d" for an
        # episode that came out last week.
        return None
    weeks = int(episode) - anime.next_airing_episode
    airs_at = anime.next_airing_at + timedelta(days=7 * weeks)
    return f"airs {countdown(airs_at, now)}"


def progress_bar(
    fraction: float, width: int = 12, *, color: str = "green", track: str = "grey37"
) -> str:
    """A slim markup progress bar ``width`` cells wide, filled to ``fraction``
    (0–1). Thin horizontal rules — heavy for the filled part, light for the
    remaining track — so it reads as a sleek line, not a chunky block.

    The heavy rule ends in a half-width tip (``╸``) when the fill lands
    mid-cell. Whole cells alone gave a seven-cell bar only seven positions,
    which is not enough to tell 18% from 25% — both rounded to one cell, so the
    one row on the screen whose job is to say *how far in* drew the same bar for
    two different places in an episode.

    (Filled blocks were tried here and reverted: at full height they became the
    heaviest thing in Continue Watching, which is not what a progress bar should
    be — the title is.)
    """
    fraction = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
    exact = fraction * width
    full = int(exact)
    tip = "╸" if full < width and exact - full >= 0.5 else ""
    rest = width - full - len(tip)
    return f"[{color}]{'━' * full}{tip}[/{color}][{track}]{'─' * rest}[/{track}]"


def _human_duration(minutes: int) -> str:
    """"3h 20m" / "45m" from a whole number of minutes."""
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h}h {m}m"
    return f"{h}h" if h else f"{m}m"


def watch_summary(
    watched: int, total: int | None, *, width: int = 10, ep_minutes: int | None = None
) -> str:
    """The detail screen's overall-progress line: a bar + ``6/12 · 50% · 6 left``,
    and a rough time-left estimate when the per-episode runtime is known.

    With no known total (still-airing, count unknown) it shows just the watched
    count and no bar, rather than a misleading full/empty ratio."""
    if not total:
        n = f"{watched} watched" if watched else "not started"
        return f"[dim]{n}[/dim]"
    if watched >= total:
        return f"{progress_bar(1.0, width, color='green')}  [b green]✓ complete[/b green]"
    frac = watched / total
    pct = round(frac * 100)
    left = total - watched
    tail = f"{pct}% · {left} left"
    if ep_minutes:
        tail += f" · ~{_human_duration(left * ep_minutes)}"
    return f"{progress_bar(frac, width, color='cyan')}  [b]{watched}/{total}[/b] [dim]· {tail}[/dim]"


def score_badge(score: int | None) -> str | None:
    """A colored ★ badge from a 0-100 AniList score (green ≥75, yellow ≥60)."""
    if not score:
        return None
    color = "green" if score >= 75 else "yellow" if score >= 60 else "red"
    return f"[{color}]★ {score}%[/{color}]"


def meta_line(anime: Anime) -> str:
    """The compact facts line: format · status · eps · year · studio · score."""
    status = anime.status.value.replace("_", " ").title()
    bits = [
        anime.format.value,
        status,
        f"{anime.episode_count} eps" if anime.episode_count else None,
        str(anime.year) if anime.year else None,
        anime.studio,
    ]
    # One space either side of the separator, matching the genres line directly
    # beneath it. These read as one block of facts and were set differently —
    # "TV  ·  Finished" sitting above "Adventure · Drama" — the sort of thing
    # nobody consciously notices and everybody notices the absence of.
    line = " · ".join(b for b in bits if b)
    badge = score_badge(anime.average_score)
    return f"{line}   {badge}" if badge else line


def card_caption(anime: Anime, progress: WatchProgress | None = None,
                 now: datetime | None = None) -> str:
    """The one line of state under a poster. Plain text, ~14 cells.

    A row has forty cells and spends them on a glyph, a position and a status;
    a card has fourteen and the poster above it has already said which show
    this is. So the caption answers only the question the poster cannot: what,
    if anything, is waiting for you here.

    Deliberately not the old list row's status reused at a smaller width. That
    string was built around a seven-cell progress bar and a column grid, neither
    of which exists under a card, and cutting it to fit produced `▁▁▁▁ 4` —
    which is not a shorter version of the information, it is damage.
    """
    if progress is not None and progress.resumable:
        if progress.fraction > 0:
            return f"ep {progress.episode:g} · {round(progress.fraction * 100)}%"
        return f"resume ep {progress.episode:g}"
    nxt = anime.next_airing_episode
    if anime.is_airing and nxt and anime.next_airing_at:
        if progress is not None:
            aired = anime.aired_through(now) or 0
            if aired > progress.episode:
                # The state the home screen exists to surface: something has
                # aired that you have not seen. Said as a number of episodes
                # rather than a date, because the date is not the point.
                waiting = round(aired - progress.episode)
                return f"ep {aired:g} ready" if waiting == 1 else f"{waiting} eps ready"
        return f"ep {nxt} {countdown(anime.next_airing_at, now)}"
    eps = anime.episode_count
    if eps:
        return "1 ep" if eps == 1 else f"{eps} eps"
    return str(anime.year) if anime.year else ""
