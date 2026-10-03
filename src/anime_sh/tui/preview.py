"""The hero — what the screen says about the row the cursor is on.

This started as a second list of upcoming episodes, which left a client for a
*visual* medium with no image on its main screen and no focal point anywhere. It
then became a narrow info strip, which was better but still read as a margin
note: 34 cells is not enough for a title and a synopsis, so it truncated the
first (`Skeleton Knight in Another World Se…`) and broke the second mid-word
(`Studi…`).

It is now the one part of the screen meant to be *looked at* rather than read:
a poster, the title at display weight, the facts that place the show, and a
single obvious thing to press. The schedule keeps the space underneath.

Pure functions over an already-loaded `Anime`. Like `upcoming`, this makes no
requests of its own; the one thing it cannot render from memory — the cover — is
fetched by the screen, cached, and debounced.

Line 0 is the title block (which may itself hold a wrapped second line) and line
1 is the facts, in that order and relied on as such: a panel that summarised a
show before it named one read as a paragraph that had lost its heading.
"""

from __future__ import annotations

from rich.cells import cell_len

from .format import countdown, progress_bar
from .rows import fit

#: How wide the progress bar is allowed to get. Past this it stops reading as a
#: measure of one episode and starts reading as a divider.
_BAR_MAX = 24


def _wrap(text: str, width: int, limit: int) -> list[str]:
    """`text` broken to `width` cells over at most `limit` lines, the last one
    ellipsized if there is more. Wrapped on words, because a synopsis broken
    mid-word reads as damage rather than as an excerpt."""
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if cell_len(candidate) <= width:
            line = candidate
            continue
        if line:
            lines.append(line)
        line = word
        if len(lines) == limit:
            break
    if line and len(lines) < limit:
        lines.append(line)
    if not lines:
        return []
    consumed = sum(len(part.split()) for part in lines)
    if consumed < len(words):
        lines[-1] = fit(lines[-1], width - 1).rstrip() + "…"
    return lines


def title_block(anime, width: int, *, limit: int = 2) -> str:
    """The show's name at display weight, wrapped rather than truncated.

    A title is the one string on the panel worth spending a second row on. The
    previous version cut it — `Skeleton Knight in Another World Se…` — which is
    the one piece of text on screen that a reader cannot reconstruct from
    context.
    """
    body = _wrap(anime.title.preferred, width, limit) or [""]
    return "\n".join(f"[b]{fit(line, width).rstrip()}[/b]" for line in body)


def facts(anime) -> str:
    """The one-line identity: format, length, year, score."""
    bits = [anime.format.value]
    if anime.episode_count:
        bits.append("1 ep" if anime.episode_count == 1 else f"{anime.episode_count} eps")
    if anime.year:
        bits.append(str(anime.year))
    line = " · ".join(b for b in bits if b)
    if anime.average_score:
        colour = ("green" if anime.average_score >= 75
                  else "yellow" if anime.average_score >= 60 else "red")
        line += f"   [{colour}]★ {anime.average_score}%[/{colour}]"
    return line


def status_chip(anime) -> str:
    """A shape plus a word for where the show is in its life.

    Both, never one: on a monochrome terminal a coloured dot alone says nothing,
    and the colour was doing all the work in the version before this.
    """
    if anime.is_airing:
        return "[$success]●[/] [dim]airing[/dim]"
    name = anime.status.value.replace("_", " ").lower()
    if name in ("finished", "unknown"):
        return "[dim]✓ complete[/dim]" if name == "finished" else ""
    return f"[dim]○ {name}[/dim]"


def action(anime, resume_episode: float | None, *, in_progress: bool) -> str:
    """What Enter does from here, named in the words the key press deserves.

    Set as a filled bar rather than a line of text. It is the only interactive-
    looking thing on the panel, and a screen that shows a resume percentage but
    never says how to act on it leaves the reader to guess — the action bar
    lists global keys, not what *this* row is offering.
    """
    if resume_episode is None:
        return "[dim] ⏎  open [/dim]"
    verb = "resume" if in_progress else "play"
    return f"[b $background on $accent] ▶  {verb} episode {resume_episode:g} [/]"


def lines(anime, width: int, *, resume_episode: float | None = None,
          fraction: float = 0.0, synopsis_lines: int = 4) -> list[str]:
    """The hero block, as markup lines at most ``width`` cells wide."""
    width = max(12, width)
    out: list[str] = [title_block(anime, width), f"[dim]{facts(anime)}[/dim]"]

    # Genres are the fastest way to tell whether a show is for you, and the
    # panel has the room — it was showing a synopsis and no way to place it.
    # Studio sits with them because it answers the same "what kind of thing is
    # this" question a paragraph of plot does not.
    # Three genres and a studio is 40 cells, which overflowed a 34-cell hero and
    # cut the studio to `Studi…`. The studio is dropped from the join when it
    # will not fit whole: a truncated proper noun is worse than an absent one.
    tags = list(anime.genres[:3])
    if anime.studio:
        if cell_len(" · ".join([*tags, anime.studio])) <= width:
            tags.append(anime.studio)
        elif cell_len(" · ".join([*tags[:2], anime.studio])) <= width:
            tags = [*tags[:2], anime.studio]
    if tags:
        out.append(f"[dim]{fit(' · '.join(tags), width).rstrip()}[/dim]")

    # Where the show is, and when the next one lands, on one line. These were
    # two separate stanzas with a blank between them, which spent three rows of
    # a panel that then had none left for the synopsis.
    chip = status_chip(anime)
    when = ""
    if anime.is_airing and anime.next_airing_episode and anime.next_airing_at:
        when = (f"[dim]Ep {anime.next_airing_episode} "
                f"{countdown(anime.next_airing_at)}[/dim]")
    if chip or when:
        out += ["", f"{chip}   {when}".rstrip()]

    if fraction > 0:
        pct = round(fraction * 100)
        bar = progress_bar(fraction, max(8, min(_BAR_MAX, width - 7)), color="cyan")
        out += ["", f"{bar}  [b]{pct}%[/b]"]

    if anime.synopsis:
        # Tags leak through AniList descriptions; they are markup to Textual and
        # would either style the panel or swallow the text after them.
        clean = (anime.synopsis.replace("<br>", " ").replace("<i>", "")
                 .replace("</i>", "").replace("<b>", "").replace("</b>", "")
                 .replace("[", "(").replace("]", ")"))
        body = _wrap(" ".join(clean.split()), width, synopsis_lines)
        if body:
            out += [""] + [f"[dim]{line}[/dim]" for line in body]

    out += ["", action(anime, resume_episode, in_progress=fraction > 0)]
    return out


def render(anime, width: int, **kwargs) -> str:
    """`lines`, joined — what the hero's Static is given."""
    return "\n".join(lines(anime, width, **kwargs))
