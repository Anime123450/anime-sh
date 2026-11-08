"""Pure presentation helpers for the TUI — formatted, testable, no widgets."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from rich.cells import cell_len, set_cell_size

from ..domain.models import Anime, Format, WatchProgress

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
    # Any progress at all gets at least the tip: 1% of a 22-cell bar is a fifth
    # of a cell, and a bar that drew nothing beside "1%" read as broken.
    tip = "╸" if full < width and (exact - full >= 0.5 or 0 < exact < 1) else ""
    rest = width - full - len(tip)
    # Closed with [/] so a theme token ($accent) works as well as a name.
    return f"[{color}]{'━' * full}{tip}[/][{track}]{'─' * rest}[/]"


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
        return f"{progress_bar(1.0, width, color='$success')}  [b $success]✓ complete[/]"
    frac = watched / total
    pct = round(frac * 100)
    left = total - watched
    tail = f"{pct}% · {left} left"
    if ep_minutes:
        tail += f" · ~{_human_duration(left * ep_minutes)}"
    return f"{progress_bar(frac, width, color='$primary')}  [b]{watched}/{total}[/b] [dim]· {tail}[/dim]"


def score_badge(score: int | None) -> str | None:
    """A colored ★ badge from a 0-100 AniList score (green ≥75, yellow ≥60)."""
    if not score:
        return None
    color = "$success" if score >= 75 else "$warning" if score >= 60 else "$error"
    # No space inside the chip: Rich wraps at any space (even a non-breaking
    # one), and a wrapped chip stranded the star at the end of a line.
    return f"[b $background on {color}] ★ {score}% [/]"


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
    # The badge is a chip with spaces inside it, and Rich wraps at any space:
    # on a narrow panel the star stayed on one line and the score went to the
    # next. On its own line it always stays whole.
    return f"{line}\n{badge}" if badge else line


#: The width a poster card's caption lines must fit. Kept here (and checked
#: against `cards.CARD_COLS` by a test) so the caption builders can be pure
#: functions with no widget import.
CARD_WIDTH = 18


def short_countdown(target: datetime, now: datetime | None = None, *,
                    units: int = 2) -> str:
    """"3h 12m" / "1d 7h" / "12m" until ``target`` — no "in", no "airing now".

    The card has fourteen cells and the countdown is the whole point of the
    line, so the filler word goes before any digit does. ``units=1`` keeps only
    the leading unit ("3h"), for the narrowest fallback. Empty when ``target``
    has already passed; the caller says what that means.
    """
    now = now or datetime.now(timezone.utc)
    secs = int((target - now).total_seconds())
    if secs <= 0:
        return ""
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        parts = [f"{d}d", f"{h}h"]
    elif h:
        parts = [f"{h}h", f"{m}m"]
    else:
        parts = [f"{max(m, 1)}m"]
    return " ".join(parts[:units])


def _pick(candidates: Sequence[str], width: int) -> str:
    """The first candidate (richest first) that fits ``width`` cells.

    A caption that does not fit is cut by the card mid-information — "ep 4 in
    12h 2…" — which reads as data rather than as damage. So every caption comes
    with shorter forms of itself, and the last resort is the shortest one cut,
    not the longest.
    """
    for cand in candidates:
        if cell_len(cand) <= width:
            return cand
    last = candidates[-1]
    return set_cell_size(last, max(0, width - 1)).rstrip() + "…" if width > 0 else ""


def airing_caption(episode: float, airing_at: datetime,
                   now: datetime | None = None, width: int = CARD_WIDTH) -> str:
    """The Airing-today card's line: which episode, and when in local time.

    "ep 5 · 18:30" while it is still to come; "ep 5 aired 09:00" once it has,
    so a glance along the shelf shows where today has got to.
    """
    now = now or datetime.now(timezone.utc)
    ep = int(episode) if float(episode).is_integer() else episode
    at = airing_at.astimezone().strftime("%H:%M")
    text = f"ep {ep} aired {at}" if airing_at <= now else f"ep {ep} · {at}"
    return text[:width]


def card_caption(anime: Anime, progress: WatchProgress | None = None,
                 now: datetime | None = None, width: int = CARD_WIDTH) -> str:
    """The one line of state under a poster. Plain text, at most ``width`` cells.

    A row has forty cells and spends them on a glyph, a position and a status;
    a card has fourteen and the poster above it has already said which show
    this is. So the caption answers only the question the poster cannot: what,
    if anything, is waiting for you here.

    Deliberately not the old list row's status reused at a smaller width. That
    string was built around a seven-cell progress bar and a column grid, neither
    of which exists under a card, and cutting it to fit produced `▁▁▁▁ 4` —
    which is not a shorter version of the information, it is damage. Every
    branch therefore lists its own shorter forms and takes the first that fits.
    """
    now = now or datetime.now(timezone.utc)
    if progress is not None and progress.resumable:
        ep = f"{progress.episode:g}"
        if progress.fraction > 0:
            pct = round(progress.fraction * 100)
            return _pick([f"ep {ep} · {pct}%", f"ep {ep} {pct}%", f"{pct}%"], width)
        return _pick([f"resume ep {ep}", f"ep {ep}"], width)
    nxt = anime.next_airing_episode
    if anime.is_airing and nxt and anime.next_airing_at:
        if progress is not None:
            aired = anime.aired_through(now) or 0
            if aired > progress.episode:
                # The state the home screen exists to surface: something has
                # aired that you have not seen. Said as a number of episodes
                # rather than a date, because the date is not the point.
                waiting = round(aired - progress.episode)
                if waiting == 1:
                    return _pick([f"ep {aired:g} ready", f"ep {aired:g} new"], width)
                return _pick([f"{waiting} eps ready", f"{waiting} new"], width)
        long = short_countdown(anime.next_airing_at, now)
        if not long:
            # The episode's air time has passed: it is on now, or it was and
            # this is a cached schedule nobody has refreshed.
            on_air = (now - anime.next_airing_at).total_seconds() <= 3 * 3600
            word = "airing" if on_air else "aired"
            return _pick([f"ep {nxt} · {word}", f"ep {nxt} {word}", f"ep {nxt}"], width)
        short = short_countdown(anime.next_airing_at, now, units=1)
        return _pick([f"ep {nxt} · {long}", f"ep {nxt} · {short}",
                      f"ep {nxt} {short}", f"{short}"], width)
    eps = anime.episode_count
    if eps:
        return "1 ep" if eps == 1 else _pick([f"{eps} eps", f"{eps}"], width)
    return str(anime.year) if anime.year else ""


def search_caption(anime: Anime, width: int = CARD_WIDTH) -> str:
    """The line under a search result: what kind of release this is.

    A franchise comes back as a dozen cards that differ only in being a season,
    a movie or an OVA, and the episode count alone cannot tell a 25-episode
    season from a 25-episode other season. Format and year can. ``TV`` is the
    default and the first thing dropped when the line is tight — a count of
    episodes already says it is a series — and a one-episode release does not
    repeat "1 ep" next to MOVIE.
    """
    fmt = None if anime.format is Format.UNKNOWN else anime.format.value
    year = str(anime.year) if anime.year else None
    eps = anime.episode_count
    eps_s = None
    if eps and not (eps == 1 and fmt in ("MOVIE", "SPECIAL", "MUSIC")):
        eps_s = "1 ep" if eps == 1 else f"{eps} eps"

    def join(*parts: str | None) -> str:
        return " · ".join(p for p in parts if p)

    cands = [join(fmt, year, eps_s)]
    if fmt == "TV":
        cands.append(join(year, eps_s))
    cands += [join(fmt, year), join(year, eps_s), year or "", join(fmt, eps_s), fmt or ""]
    cands = [c for c in dict.fromkeys(cands) if c]
    if not cands:
        return ""
    return _pick(cands, width)


# ---------------------------------------------------------------------------
# Titles on a shelf of siblings
# ---------------------------------------------------------------------------

_MARKER_RE = re.compile(
    r"\b(?:season\s*\d+|\d+(?:st|nd|rd|th)\s+season|part\s*\d+|cour\s*\d+|"
    r"final\s+season|movie|ova|oad|ona|special|s\d{1,2})\b",
    re.IGNORECASE,
)
_ARTICLE_RE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)
_SEASON_RE = re.compile(r"\bseason\s*(\d+)|\b(\d+)(?:st|nd|rd|th)\s+season", re.IGNORECASE)
_PART_RE = re.compile(r"\bpart\s*(\d+)", re.IGNORECASE)
_PUNCT = " -–—:·,"
#: Shortest head worth keeping in front of the ellipsis.
_MIN_HEAD = 2
#: A shared prefix shorter than this is a coincidence ("The …"), not a franchise.
_MIN_FRANCHISE = 5


def _end_cut(text: str, width: int) -> str:
    """The plain caption: cut at the end with an ellipsis, or as is."""
    if cell_len(text) <= width:
        return text
    if width <= 1:
        return "…"[:width]
    return set_cell_size(text, width - 1).rstrip() + "…"


def _tail_variants(tail: str) -> list[str]:
    plain = tail.strip(_PUNCT)
    bare = _ARTICLE_RE.sub("", plain)
    abbr = _SEASON_RE.sub(lambda m: f"S{m.group(1) or m.group(2)}", bare)
    abbr2 = _PART_RE.sub(lambda m: f"P{m.group(1)}", abbr)
    return list(dict.fromkeys(v for v in (plain, bare, abbr, abbr2) if v))


def _fit_tail(tail: str, budget: int, *, by_chars: bool = False) -> str:
    """The tail in at most ``budget`` cells, keeping its END.

    The end of a title is the part that distinguishes it, so when nothing
    shortens it enough the front is what goes — whole words first (unless
    ``by_chars``), then characters.
    """
    variants = _tail_variants(tail)
    for v in variants:
        if cell_len(v) <= budget:
            return v
    last = variants[-1]
    if not by_chars:
        words = last.split()
        while len(words) > 1:
            words.pop(0)
            if cell_len(" ".join(words)) <= budget:
                # A lone short word ("Cut") is no better than characters of
                # the phrase it came from ("ector's Cut" is worse; "Dir. Cut"
                # would be best) — keep at least 4 cells of tail.
                if cell_len(" ".join(words)) >= 4 or len(variants[-1].split()) <= 1:
                    return " ".join(words)
                break
        last = words[0] if words and len(words) == 1 else variants[-1]
    while last and cell_len(last) > budget:
        last = last[1:]
    return last.lstrip(_PUNCT)


def head_tail(title: str, tail: str, width: int, *, by_chars: bool = False) -> str:
    """``title`` as ``head…tail`` in at most ``width`` cells.

    ``tail`` is the part that tells this release from its siblings ("Season 3",
    "Frozen Bond"); the head is whatever of the franchise name is left over.
    """
    t = _fit_tail(tail, max(1, width - 1 - _MIN_HEAD), by_chars=by_chars)
    head_w = max(0, width - 1 - cell_len(t))
    head = set_cell_size(title, head_w).rstrip(_PUNCT) if head_w else ""
    return f"{head}…{t}"


def _franchise_prefix(title: str, others: Sequence[str]) -> str:
    """The longest leading run ``title`` shares with a sibling that is not
    merely an extension of it, backed off to a word boundary and to before the
    title's own season / part / movie marker. Empty when there is none.

    Character-level and case-folded, so ``World-`` and ``World:`` still count
    as the same franchise; backed off to a boundary so a coincidence of
    letters ("Re:ZERO -Starting" / "Re:ZERO kara") does not split a word; and
    stopped before the marker so "…World- Season 2" and "…World- Season 3" keep
    their number in the tail rather than in the shared prefix.
    """
    low = title.casefold()
    best = 0
    for other in others:
        n = 0
        for x, y in zip(low, other.casefold()):
            if x != y:
                break
            n += 1
        if n < len(title):
            best = max(best, n)
    m = _MARKER_RE.search(title)
    if m and m.start() > 0:
        best = min(best, m.start()) if best else m.start()
    # Back off to the start of the word the prefix ends inside.
    while 0 < best < len(title) and title[best - 1].isalnum() and title[best].isalnum():
        best -= 1
    prefix = title[:best].rstrip(_PUNCT)
    return prefix if len(prefix) >= _MIN_FRANCHISE else ""


def wrap_title(title: str, width: int = CARD_WIDTH, lines: int = 2) -> list[str]:
    """``title`` word-wrapped into exactly ``lines`` lines of ``width`` cells.

    Cards used to print one 14-cell line, so most titles came out as
    "A R…Special S2". Breaks between words; a word longer than a line is
    hard-cut; whatever does not fit in the last line is end-cut with an
    ellipsis. Short titles are padded with empty lines so every card's caption
    sits on the same row.
    """
    out: list[str] = []
    rest = title.strip()
    while rest and len(out) < lines - 1:
        if cell_len(rest) <= width:
            out.append(rest)
            rest = ""
            break
        words = rest.split(" ")
        line = ""
        used = 0
        for word in words:
            cand = f"{line} {word}" if line else word
            if cell_len(cand) > width:
                break
            line = cand
            used += 1
        if not line:  # one word wider than the line
            line = set_cell_size(rest, width)
            rest = rest[len(line):]
        else:
            rest = " ".join(words[used:])
        out.append(line)
        rest = rest.strip()
    if rest:
        out.append(_end_cut(rest, width))
    return out + [""] * (lines - len(out))


def shelf_titles(titles: Sequence[str], width: int = CARD_WIDTH) -> list[str]:
    """Card captions for one shelf, cut so that siblings stay distinguishable.

    Plain end-ellipsis is right for most shelves and wrong for a franchise:
    searching "re zero" returned eleven cards that all read `Re:ZERO -Star…`.
    So a title too long to show whole is cut ``head…tail`` — dropping the
    franchise prefix its siblings share and keeping the part that differs —
    when it has a shared franchise prefix with another title on the shelf, or
    carries a season / part / movie / OVA marker that an end-cut is the most
    likely to chop off. The head keeps the first few characters of the
    franchise; the tail keeps the end of the title.

    A title that fits whole is never altered, and a title that is itself the
    shared prefix (the first season) keeps the plain cut. After cutting, any
    captions that still collide are re-cut with more of the tail.
    """
    plain = [_end_cut(t, width) for t in titles]

    def build(by_chars: bool) -> list[str]:
        out: list[str] = []
        for i, title in enumerate(titles):
            if cell_len(title) <= width:
                out.append(title)
                continue
            others = [o for j, o in enumerate(titles) if j != i and o != title]
            prefix = _franchise_prefix(title, others)
            tail = title[len(prefix):] if prefix else ""
            if tail.strip(_PUNCT):
                out.append(head_tail(title, tail, width, by_chars=by_chars))
            else:
                out.append(plain[i])
        return out

    out = build(False)
    if len(set(out)) < len(out):
        alt = build(True)
        counts = Counter(out)
        out = [alt[i] if counts[o] > 1 else o for i, o in enumerate(out)]
    # A title with no season/part/movie marker gains nothing from head…tail
    # when its plain cut is unambiguous: season one of a franchise read
    # "Re:ZERO…World" where "Re:ZERO -Star…" says more. Among titles that would
    # share one plain cut, only the shortest (the franchise's base title) gets
    # it back, and only if no other card already prints that string.
    taken = Counter(out)
    by_plain: dict[str, list[int]] = {}
    for i, title in enumerate(titles):
        if out[i] != plain[i] and not _MARKER_RE.search(title):
            by_plain.setdefault(plain[i], []).append(i)
    for cut, idx in by_plain.items():
        if taken[cut]:
            continue
        # The franchise's base title is the one the most siblings extend.
        best = min(idx, key=lambda i: (
            -sum(o != titles[i] and o.casefold().startswith(titles[i].casefold().rstrip(_PUNCT))
                 for o in titles),
            len(titles[i])))
        taken[out[best]] -= 1
        out[best] = cut
        taken[cut] += 1
    return out


_TAG_RE = re.compile(r"<[^>]+>")
# AniList descriptions often end with an attribution and editor notes:
# "...happened since. (Source: Anime News Network) Notes: - The first...".
# Neither is part of the story, and both were rendered inline as more synopsis.
_TAIL_RE = re.compile(
    r"\s*(?:[\(\[]\s*Source\s*:[^\)\]]*[\)\]]|Source\s*:\s*\S.*$|Notes?\s*:.*$)",
    re.IGNORECASE | re.DOTALL,
)


def clean_synopsis(text: str | None) -> str:
    """A synopsis as prose: tags, source attribution and notes stripped,
    whitespace collapsed to single spaces. Safe for Textual markup (square
    brackets become parentheses)."""
    if not text:
        return ""
    s = _TAG_RE.sub(" ", text.replace("<br>", "\n"))
    m = _TAIL_RE.search(s)
    if m:
        s = s[: m.start()]
    s = " ".join(s.split())
    return s.replace("[", "(").replace("]", ")")
