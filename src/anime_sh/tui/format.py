"""Pure presentation helpers for the TUI — formatted, testable, no widgets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain.models import Anime, WatchProgress


def countdown(target: datetime, now: datetime | None = None) -> str:
    """Human "in 5d 3h" until ``target``. Past/near targets read naturally."""
    now = now or datetime.now(timezone.utc)
    secs = int((target - now).total_seconds())
    if secs <= 0:
        return "airing now"
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
    if episode < anime.next_airing_episode:
        return None  # already aired
    weeks = int(episode) - anime.next_airing_episode
    airs_at = anime.next_airing_at + timedelta(days=7 * weeks)
    return f"airs {countdown(airs_at, now)}"


def waiting_subtitle(
    anime: Anime, watched_episode: float, now: datetime | None = None
) -> str | None:
    """Continue-watching row subtitle for a show you're *caught up on*.

    When the show is still airing and you've watched up to the latest aired
    episode (nothing new to watch yet), return the countdown to the next one —
    ``"caught up · Ep 6 in 2d 3h"``. Returns None when there's still an aired
    episode left to watch, so the caller keeps the normal "Ep x · y%" row and
    leaves it bright/actionable."""
    if not (anime.is_airing and anime.next_airing_episode and anime.next_airing_at):
        return None
    aired = anime.next_airing_episode - 1  # episodes already released
    if watched_episode < aired:
        return None  # you still have released episodes to catch up on
    return (
        f"caught up · Ep {anime.next_airing_episode} "
        f"{countdown(anime.next_airing_at, now)}"
    )


def continue_row(
    anime: Anime, progress: WatchProgress, now: datetime | None = None
) -> tuple[str, bool, float] | None:
    """Render one Continue-Watching entry as ``(subtitle, dim, resume_episode)``,
    or None to drop it because the show is finished and fully watched.

    The four states, in order of check:

    * **Resume** — you're partway through the furthest episode: show the percent
      and resume that episode.
    * **Done** — the series has finished airing and you've watched the last
      episode: drop it (nothing left to continue).
    * **Caught up** — a still-airing show whose latest aired episode you've
      finished: greyed, with a live countdown to the next episode.
    * **Up next** — you finished an episode and another is already available:
      point at the next one."""
    ep = progress.episode
    if not progress.completed and progress.position_s > 0 and progress.duration_s > 0:
        pct = round(progress.fraction * 100)
        return (f"Ep {ep:g} · {pct}%", False, ep)
    nxt = ep + 1
    if not anime.is_airing and anime.episode_count and ep >= anime.episode_count:
        return None
    waiting = waiting_subtitle(anime, ep, now)
    if waiting is not None:
        return (waiting, True, nxt)
    # Say how many episodes exist when we know. "up next · Ep 5" next to another
    # season's "caught up · Ep 5 in 5d" is genuinely ambiguous — both read as "the
    # next episode is 5" — whereas "Ep 5 of 12" makes it obvious this one is a
    # finished season you are partway through, not a release you are waiting on.
    total = anime.episode_count
    if total:
        return (f"up next · Ep {nxt:g} of {total}", False, nxt)
    return (f"up next · Ep {nxt:g}", False, nxt)


def progress_bar(
    fraction: float, width: int = 12, *, color: str = "green", track: str = "grey37"
) -> str:
    """A slim markup progress bar ``width`` cells wide, filled to ``fraction``
    (0–1). Uses thin horizontal rules — heavy for the filled part, light for the
    remaining track — so it reads as a sleek line, not a chunky block."""
    fraction = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
    filled = round(fraction * width)
    filled = 0 if filled < 0 else width if filled > width else filled
    return f"[{color}]{'━' * filled}[/{color}][{track}]{'─' * (width - filled)}[/{track}]"


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


def home_subtitle(anime: Anime, now: datetime | None = None) -> str:
    """Compact list-row subtitle. For an airing show it shows how many episodes
    have actually aired (``2/12``) and a live countdown to the next one — not the
    misleading planned total. Finished shows show total eps and year."""
    fmt = anime.format.value
    if anime.is_airing and anime.next_airing_episode and anime.next_airing_at:
        aired = max(anime.next_airing_episode - 1, 0)
        total = anime.episode_count
        count = f"{aired}/{total} eps" if total else f"{aired} eps"
        return (
            f"{fmt} · {count} · Ep {anime.next_airing_episode} "
            f"{countdown(anime.next_airing_at, now)}"
        )
    bits = [fmt]
    if anime.episode_count:
        bits.append(f"{anime.episode_count} eps")
    if anime.year:
        bits.append(str(anime.year))
    return " · ".join(bits)


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
    line = "  ·  ".join(b for b in bits if b)
    badge = score_badge(anime.average_score)
    return f"{line}   {badge}" if badge else line
