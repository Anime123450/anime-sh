"""Pure TUI presentation helpers: countdown, score badge, meta line, cover art."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anime_sh.domain.models import Anime, AnimeId, Format, Status, Title
from anime_sh.tui.coverart import render_cover
from anime_sh.tui.format import (
    countdown,
    episode_air_label,
    meta_line,
    next_episode_line,
    score_badge,
)

_NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _anime(**kw):
    base = dict(id=AnimeId(anilist=1), title=Title(romaji="Show"))
    base.update(kw)
    return Anime(**base)


def test_countdown_scales_by_magnitude():
    assert countdown(_NOW + timedelta(days=5, hours=3), _NOW) == "in 5d 3h"
    assert countdown(_NOW + timedelta(hours=2, minutes=10), _NOW) == "in 2h 10m"
    assert countdown(_NOW + timedelta(minutes=40), _NOW) == "in 40m"
    assert countdown(_NOW - timedelta(hours=1), _NOW) == "airing now"


def test_score_badge_color_thresholds():
    assert "green" in score_badge(91)
    assert "yellow" in score_badge(65)
    assert "red" in score_badge(40)
    assert score_badge(None) is None
    assert score_badge(0) is None


def test_meta_line_includes_studio_and_score():
    a = _anime(format=Format.TV, status=Status.RELEASING, episode_count=12,
               year=2026, studio="MADHOUSE", average_score=82)
    line = meta_line(a)
    assert "TV" in line and "Releasing" in line and "12 eps" in line
    assert "2026" in line and "MADHOUSE" in line and "82%" in line


def test_next_episode_line_only_when_airing_data_present():
    airing = _anime(next_airing_episode=3, next_airing_at=_NOW + timedelta(days=1))
    assert next_episode_line(airing, _NOW) == "Ep 3 in 1d 0h"
    assert next_episode_line(_anime(), _NOW) is None


def test_episode_air_label_projects_weekly_from_next_airing():
    a = _anime(status=Status.RELEASING, next_airing_episode=5,
               next_airing_at=_NOW + timedelta(days=2))
    assert episode_air_label(a, 5, _NOW) == "airs in 2d 0h"      # the next one
    assert episode_air_label(a, 7, _NOW) == "airs in 16d 0h"     # +2 weeks
    assert episode_air_label(a, 4, _NOW) is None                 # already aired
    assert episode_air_label(_anime(), 3, _NOW) is None          # no schedule


# -- cover art (Pillow-backed, graceful) ------------------------------------- #
def _png(w, h, color=(200, 40, 40)) -> bytes:
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def test_render_cover_produces_block_grid():
    from anime_sh.tui.coverart import _SEXTANTS

    art = render_cover(_png(60, 90), cols=10)
    assert art is not None
    rows = art.plain.split("\n")
    assert all(len(r) == 10 for r in rows)          # every row is `cols` wide
    assert all(ch in _SEXTANTS for ch in art.plain if ch != "\n")
    assert art.spans, "expected per-cell color styles"


def test_render_cover_preserves_color():
    # A solid poster renders that colour (a smooth block picks a single-colour
    # split, not a muddied two-colour one).
    art = render_cover(_png(30, 45, color=(200, 40, 40)), cols=8)
    assert art is not None
    assert "200,40,40" in str(art.spans[0].style)


def test_render_cover_snaps_edges_to_two_colors():
    # A red-over-blue split: a cell on the boundary is coloured with two
    # distinct colours (proof the split follows the edge, not a fixed threshold).
    from PIL import Image
    import io

    img = Image.new("RGB", (20, 40))
    for y in range(40):
        for x in range(20):
            img.putpixel((x, y), (220, 20, 20) if y < 20 else (20, 20, 220))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    art = render_cover(buf.getvalue(), cols=8)
    styles = [str(s.style) for s in art.spans]
    assert any(" on " in st and st.split(" on ")[0] != st.split(" on ")[1]
               for st in styles)


def test_progress_bar_fills_proportionally():
    from anime_sh.tui.format import progress_bar

    empty = progress_bar(0.0, 10)
    full = progress_bar(1.0, 10)
    half = progress_bar(0.5, 10)
    assert "━" not in empty and "─" in empty          # nothing filled
    assert "━" in full and "─" not in full            # fully filled
    assert "━" in half and "─" in half                # partly filled
    # Clamps out-of-range fractions instead of overflowing.
    assert progress_bar(2.0, 10) == full
    assert progress_bar(-1.0, 10) == empty


def test_a_short_bar_can_still_tell_two_low_percentages_apart():
    """The reason the fill ends in a half-width tip rather than a whole cell. On
    the seven-cell resume bar, 18% and 25% both round to one filled cell — so
    two genuinely different places in an episode drew an identical bar, and the
    one row on the screen whose job is to say "how far in" said the same thing
    about both."""
    from anime_sh.tui.format import progress_bar

    assert progress_bar(0.18, 7) != progress_bar(0.25, 7)


def test_the_bar_never_exceeds_its_width():
    """A partial cell is still a cell. Counting it as extra would push the
    percentage beside it out of the status column and rag the grid."""
    import re

    from anime_sh.tui.format import progress_bar

    for pct in range(0, 101):
        plain = re.sub(r"\[/?[^\]]+\]", "", progress_bar(pct / 100, 7))
        assert len(plain) == 7, f"{pct}% drew {len(plain)} cells"


def test_watch_summary_reads_watched_over_total():
    from anime_sh.tui.format import watch_summary

    assert "6/12" in watch_summary(6, 12) and "50%" in watch_summary(6, 12)
    assert "6 left" in watch_summary(6, 12)
    assert "complete" in watch_summary(12, 12)
    # Unknown total → a count, never a misleading bar.
    assert "░" not in watch_summary(3, None)
    # With a per-episode runtime, a rough time-left estimate is appended.
    assert "~2h" in watch_summary(6, 12, ep_minutes=20)  # 6 left × 20m = 120m


def test_sextant_table_maps_known_patterns():
    from anime_sh.tui.coverart import _SEXTANTS

    assert len(_SEXTANTS) == 64
    assert _SEXTANTS[0] == " "          # empty
    assert _SEXTANTS[63] == "█"         # full block
    assert _SEXTANTS[21] == "▌"         # left column → left half block
    assert _SEXTANTS[42] == "▐"         # right column → right half block
    assert _SEXTANTS[1] == "\U0001fb00"  # top-left only → first BLOCK SEXTANT
    assert len(set(_SEXTANTS)) == 64     # every pattern is a distinct glyph


def test_render_cover_returns_none_on_garbage():
    assert render_cover(b"not an image", cols=10) is None
    assert render_cover(b"", cols=10) is None
