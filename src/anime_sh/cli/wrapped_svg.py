"""Draw a :class:`Wrapped` as a self-contained SVG card.

Self-contained on purpose: no external fonts, no script, no network. The whole
point is a file someone can drop into a message or a README and have it render
the same everywhere — and GitHub strips scripted SVG, so anything clever would
quietly stop working exactly where it is most likely to be used.

Colours are anime-sh's own `midnight` theme, so the card looks like the app it
came from.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

from ..domain.wrapped import Wrapped

BG = "#0E1420"
SURFACE = "#161E2E"
PANEL = "#26314A"
PRIMARY = "#5CC8D7"
ACCENT = "#F2B45C"
TEXT = "#E6EAF2"
MUTED = "#8A94A8"

W, H = 900, 500
MONTHS = ("J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D")


def render(wrapped: Wrapped, *, title: str = "anime-sh") -> str:
    """Return the SVG document for this Wrapped."""
    heading = f"{wrapped.year} in anime" if wrapped.year else "Everything, ever"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'width="{W}" height="{H}" role="img" '
        f'aria-label="{escape(heading)}: {wrapped.episodes} episodes, '
        f'{wrapped.hours:g} hours, {wrapped.shows} shows">',
        "<style>"
        f"text{{font-family:'DejaVu Sans',Verdana,Geneva,sans-serif;fill:{TEXT}}}"
        f".m{{fill:{MUTED}}}.p{{fill:{PRIMARY}}}.a{{fill:{ACCENT}}}"
        ".big{font-size:54px;font-weight:700}"
        ".lbl{font-size:13px;letter-spacing:.14em}"
        ".row{font-size:17px}.small{font-size:12px}"
        "</style>",
        f'<rect width="{W}" height="{H}" rx="18" fill="{BG}"/>',
        f'<text x="40" y="58" style="font-size:26px;font-weight:700">{escape(heading)}</text>',
        f'<text x="40" y="82" class="m lbl">{escape(title.upper())}</text>',
    ]
    parts += _headline_numbers(wrapped)
    parts += _list_block(40, 200, "TOP SHOWS", wrapped.top_shows, PRIMARY)
    parts += _list_block(490, 200, "TOP GENRES", wrapped.top_genres, ACCENT)
    parts += _months(wrapped)
    parts += _footnotes(wrapped)
    parts.append("</svg>")
    return "".join(parts)


def _headline_numbers(w: Wrapped) -> list[str]:
    stats = (
        (f"{w.episodes:,}", "EPISODES"),
        (f"{w.hours:,g}", "HOURS"),
        (f"{w.shows:,}", "SHOWS"),
    )
    out = [f'<rect x="40" y="100" width="{W - 80}" height="86" rx="12" fill="{SURFACE}"/>']
    for i, (value, label) in enumerate(stats):
        x = 80 + i * 280
        out.append(f'<text x="{x}" y="150" class="big p">{value}</text>')
        out.append(f'<text x="{x}" y="172" class="m lbl">{label}</text>')
    return out


def _list_block(x: int, y: int, label: str, rows, colour: str) -> list[str]:
    out = [f'<text x="{x}" y="{y}" class="m lbl">{label}</text>']
    if not rows:
        return out + [f'<text x="{x}" y="{y + 30}" class="row m">nothing yet</text>']
    widest = max(count for _, count in rows) or 1
    for i, (name, count) in enumerate(rows):
        row_y = y + 32 + i * 30
        # A bar behind the text, scaled to the top entry — enough to read the
        # shape at a glance without needing an axis.
        bar = int(300 * count / widest)
        out.append(
            f'<rect x="{x}" y="{row_y - 16}" width="{bar}" height="22" rx="5" '
            f'fill="{colour}" opacity="0.16"/>'
        )
        out.append(f'<text x="{x + 8}" y="{row_y}" class="row">{escape(_clip(name))}</text>')
        out.append(
            f'<text x="{x + 310}" y="{row_y}" class="row m" text-anchor="end">{count}</text>'
        )
    return out


def _months(w: Wrapped) -> list[str]:
    if not any(w.by_month):
        return []
    top = max(w.by_month)
    base, height = 430, 54
    out = [f'<text x="40" y="378" class="m lbl">BY MONTH</text>']
    for i, count in enumerate(w.by_month):
        x = 40 + i * 30
        bar = max(2, int(height * count / top))
        out.append(
            f'<rect x="{x}" y="{base - bar}" width="18" height="{bar}" rx="3" '
            f'fill="{PRIMARY if count else PANEL}" opacity="{0.85 if count else 0.5}"/>'
        )
        out.append(
            f'<text x="{x + 9}" y="{base + 16}" class="small m" '
            f'text-anchor="middle">{MONTHS[i]}</text>'
        )
    return out


def _day_label(when) -> str:
    """"3 Mar", without a leading zero, on every platform.

    `%-d` is a glibc extension: it raises ValueError on Windows, which is where
    most of this project's users are.
    """
    return f"{when.day} {when:%b}"


def _footnotes(w: Wrapped) -> list[str]:
    lines = []
    if w.longest_streak > 1:
        ended = f" (to {_day_label(w.streak_ended)})" if w.streak_ended else ""
        lines.append(f"Longest streak: {w.longest_streak} days{ended}")
    if w.busiest_day and w.busiest_day_episodes > 1:
        lines.append(
            f"Biggest day: {_day_label(w.busiest_day)} — "
            f"{w.busiest_day_episodes} episodes"
        )
    if w.seconds >= 86400:
        lines.append(f"That is {w.days:g} days of screen time")
    out = []
    for i, line in enumerate(lines[:3]):
        out.append(f'<text x="490" y="{390 + i * 26}" class="row a">{escape(line)}</text>')
    return out


def _clip(name: str, limit: int = 26) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"
