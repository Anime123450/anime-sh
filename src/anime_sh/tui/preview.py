"""What Region C says about the row the cursor is on.

The rail used to be a second list: seven days of upcoming episodes, and nothing
else. That made the home screen three lists beside a fourth, with no focal point
anywhere on it and — in a client for a *visual* medium — not one image.

So the rail leads with the highlighted show instead: its poster, what it is, how
far through it you are, and what pressing Enter will do. The schedule keeps the
space underneath.

Pure functions over an already-loaded `Anime`. Like `upcoming`, this deliberately
makes no requests of its own; the one thing it cannot render from memory — the
cover image — is fetched by the screen, cached, and debounced.
"""

from __future__ import annotations

from .format import countdown, progress_bar
from .rows import fit


def _wrap(text: str, width: int, limit: int) -> list[str]:
    """`text` broken to `width` cells over at most `limit` lines, the last one
    ellipsized if there is more. Wrapped on words, because a synopsis broken
    mid-word reads as damage rather than as an excerpt."""
    words, lines, line = text.split(), [], ""
    for word in words:
        candidate = f"{line} {word}".strip()
        if len(candidate) <= width:
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


def action(anime, resume_episode: float | None, *, in_progress: bool) -> str:
    """What Enter does from here, named in the words the key press deserves.

    A screen that shows a resume percentage but never says how to act on it
    leaves the reader to guess; the footer lists global keys, not what this row
    in particular is offering.
    """
    if resume_episode is None:
        return "[dim]⏎ open[/dim]"
    verb = "resume" if in_progress else "play"
    return f"[b]⏎[/b] [accent]{verb} episode {resume_episode:g}[/accent]"


def lines(anime, width: int, *, resume_episode: float | None = None,
          fraction: float = 0.0, synopsis_lines: int = 4) -> list[str]:
    """The rail's preview block, as markup lines at most ``width`` cells wide."""
    width = max(12, width)
    out: list[str] = [f"[b]{fit(anime.title.preferred, width).rstrip()}[/b]",
                      f"[dim]{facts(anime)}[/dim]", ""]

    if fraction > 0:
        pct = round(fraction * 100)
        bar = progress_bar(fraction, min(16, width - 6), color="cyan")
        out += [f"{bar}  [b]{pct}%[/b]", ""]

    if anime.is_airing and anime.next_airing_episode and anime.next_airing_at:
        out.append(
            f"[dim]Ep {anime.next_airing_episode} "
            f"{countdown(anime.next_airing_at)}[/dim]"
        )
        out.append("")

    if anime.synopsis:
        # Tags leak through AniList descriptions; they are markup to Textual and
        # would either style the panel or swallow the text after them.
        clean = (anime.synopsis.replace("<br>", " ").replace("<i>", "")
                 .replace("</i>", "").replace("<b>", "").replace("</b>", "")
                 .replace("[", "(").replace("]", ")"))
        body = _wrap(" ".join(clean.split()), width, synopsis_lines)
        if body:
            out += [f"[dim]{line}[/dim]" for line in body] + [""]

    out.append(action(anime, resume_episode,
                      in_progress=fraction > 0))
    return out


def render(anime, width: int, **kwargs) -> str:
    """`lines`, joined — what the rail's preview Static is given."""
    return "\n".join(lines(anime, width, **kwargs))
