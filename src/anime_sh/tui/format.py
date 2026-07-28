"""Pure presentation helpers for the TUI — formatted, testable, no widgets."""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain.models import Anime


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
