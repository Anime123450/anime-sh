"""Pure presentation helpers for the TUI — formatted, testable, no widgets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..domain.models import Anime, WatchProgress
from .rows import POSITION_W, Row

# The Continue-Watching bar shares a column with text statuses, so it stays
# short enough to leave room for the percentage beside it.
_RESUME_BAR = 7

# Continue Watching reads top-down as "what can I do right now": the episode you
# are part-way through, then episodes waiting unwatched, then shows you are
# caught up on and cannot act on at all.
RANK_RESUME, RANK_READY, RANK_WAITING = 0, 1, 2

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
    aired = anime.aired_through(now)
    if aired is None or watched_episode < aired:
        return None  # you still have released episodes to catch up on
    return (
        f"caught up · Ep {anime.next_airing_episode} "
        f"{countdown(anime.next_airing_at, now)}"
    )


def continue_cells(
    anime: Anime, progress: WatchProgress, now: datetime | None = None
) -> tuple[Row, float] | None:
    """One Continue-Watching row as ``(Row, resume_episode)``, or None to drop it
    because the show is finished and you have watched all of it.

    The four states, in order of check, each with its own glyph and colour so the
    list can be read by shape before it is read by word:

    * **Resume** (``▸`` cyan) — partway through an episode: the episode number
      and how far in.
    * **Done** — finished airing and fully watched: dropped, nothing to continue.
    * **Waiting** (``○`` dim) — a still-airing show whose latest aired episode
      you have finished: a live countdown to the next one, dimmed because you
      cannot act on it.
    * **Ready** (``●`` green) — an aired episode is sitting there unwatched.

    Ready is the state the screen exists to surface, so it is the only one drawn
    in the accent colour at full brightness.
    """
    ep = progress.episode
    if progress.resumable:
        # A known position with an unknown duration still resumes *this* episode —
        # mpv can quit before it reports a duration. Only the bar needs the
        # duration, so it is the bar that is dropped, not the row's episode. The
        # earlier test here required a duration and so sent such a row to the next
        # episode, losing the place it had.
        if progress.fraction > 0:
            tail = f"{round(progress.fraction * 100)}%"
            status = f"{progress_bar(progress.fraction, _RESUME_BAR, color='cyan')}  {tail}"
            cells = _RESUME_BAR + 2 + len(tail)
        else:
            tail = "resume"
            status = tail
            cells = len(tail)
        return (
            Row(
                title=anime.title.preferred,
                glyph="[cyan]▸[/cyan]",
                position=f"Ep {ep:g}",
                status=status,
                status_cells=cells,
                rank=RANK_RESUME,
            ),
            ep,
        )

    nxt = ep + 1
    if not anime.is_airing and anime.episode_count and ep >= anime.episode_count:
        return None

    waiting = waiting_subtitle(anime, ep, now)
    if waiting is not None:
        # "caught up · Ep 6 in 2d 3h" — the grid already says which episode in
        # its own column, so the row only needs the countdown.
        _, _, when = waiting.partition(f"Ep {anime.next_airing_episode} ")
        return (
            Row(
                title=anime.title.preferred,
                glyph="○",
                position=f"Ep {anime.next_airing_episode:g}",
                status=when or "caught up",
                dim=True,
                rank=RANK_WAITING,
            ),
            nxt,
        )

    total = anime.episode_count
    # "Ep 5 of 12" next to another season's "Ep 5" is genuinely ambiguous — both
    # read as "the next episode is 5". Spelling out the total is what makes this
    # one legible as a finished season you are partway through.
    position = f"Ep {nxt:g}/{total}" if total else f"Ep {nxt:g}"
    # How many episodes are actually sitting there, rather than the words "new
    # episode" — which, on a real library, was the same two words repeated down
    # thirteen consecutive rows in the same dim grey. A third of the screen
    # spent saying one thing the green ● had already said, in a column that
    # could have been carrying a number that differs on every row.
    waiting = _waiting_count(anime, nxt, now)
    status = f"[dim]+{waiting}[/dim]" if waiting > 0 else ""
    return (
        Row(
            title=anime.title.preferred,
            glyph="[green]●[/green]",
            position=position,
            status=status,
            status_cells=len(f"+{waiting}") if waiting > 0 else 0,
            rank=RANK_READY,
        ),
        nxt,
    )


def _waiting_count(anime: Anime, nxt: float, now: datetime | None = None) -> int:
    """Episodes released and unwatched, counting from ``nxt``.

    Uses the airing schedule where there is one and the episode count otherwise,
    so an airing show says how many have actually dropped rather than how many
    are planned. Returns 0 when neither is known, which renders as nothing at
    all — better than a confident `+0`.
    """
    released = anime.aired_through(now)
    if released is None:
        released = anime.episode_count
    if not released:
        return 0
    return max(0, int(released) - int(nxt) + 1)


def _aired_of(aired: int, total: int | None) -> str:
    """The browse lists' episode column: "7/11 eps", or "7 eps" with no total.

    The unit is not decoration. Bare "7/11" sat directly under "12 eps" in the
    same column - two grammars for one kind of fact - and on its own it reads as
    a date: a season list showing 7/11, 8/12 and 9/14 looks like November,
    December and September before it looks like episode counts.

    Appended only when it fits the column, which is exactly where the ambiguity
    lives. A long-runner's "1139/1140" is nobody's idea of a date, and spending
    four cells to say so would truncate the number instead - the one row where
    the count is the interesting part.
    """
    if not total:
        return f"{aired} eps"
    bare = f"{aired}/{total}"
    return f"{bare} eps" if len(bare) + 4 <= POSITION_W else bare


def browse_cells(anime: Anime, now: datetime | None = None) -> Row:
    """A row for the browse lists — seasonal, trending, search results.

    These are shows you are not tracking, so there is no watch state to mark.
    What earns the columns instead is *how much exists* and *when the next one
    lands*, which is what the eye is actually looking for when scanning a season.
    """
    if anime.is_airing and anime.next_airing_episode and anime.next_airing_at:
        aired = anime.aired_through(now) or 0
        total = anime.episode_count
        return Row(
            title=anime.title.preferred,
            position=_aired_of(aired, total),
            status=f"Ep {anime.next_airing_episode} {countdown(anime.next_airing_at, now)}",
        )
    eps = anime.episode_count
    position = ("1 ep" if eps == 1 else f"{eps} eps") if eps else ""
    return Row(
        title=anime.title.preferred,
        position=position,
        status=f"[dim]{anime.year}[/dim]" if anime.year else "",
        status_cells=len(str(anime.year)) if anime.year else 0,
    )


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


def home_subtitle(anime: Anime, now: datetime | None = None) -> str:
    """Compact list-row subtitle. For an airing show it shows how many episodes
    have actually aired (``2/12``) and a live countdown to the next one — not the
    misleading planned total. Finished shows show total eps and year."""
    fmt = anime.format.value
    if anime.is_airing and anime.next_airing_episode and anime.next_airing_at:
        aired = anime.aired_through(now) or 0
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

    Deliberately not `continue_cells`' status reused at a smaller width. That
    string is built around a seven-cell progress bar and a column grid, neither
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
