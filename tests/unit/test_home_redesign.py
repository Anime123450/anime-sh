"""The 2026-11-09 home redesign: bigger posters, hero banner, Airing today."""
import io
from datetime import datetime, timedelta, timezone

import pytest

from anime_sh.tui import coverart
from anime_sh.tui.cards import CARD_COLS, CARD_ROWS
from anime_sh.tui.format import CARD_WIDTH, airing_caption
from anime_sh.tui.shell import SECTIONS


def test_posters_are_bigger_and_agree_with_the_caption_width():
    assert CARD_COLS == CARD_WIDTH == 18
    assert CARD_ROWS == coverart.cover_rows(18) + 4


def test_airing_today_sits_right_after_continue():
    assert [s.list_id for s in SECTIONS] == [
        "#continue", "#airing", "#favorites", "#seasonal", "#trending"]


def test_airing_caption_says_when_or_that_it_aired():
    now = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
    later = now + timedelta(hours=3)
    earlier = now - timedelta(hours=3)
    assert airing_caption(5.0, later, now) == f"ep 5 · {later.astimezone():%H:%M}"
    assert airing_caption(5.0, earlier, now) == f"ep 5 aired {earlier.astimezone():%H:%M}"
    assert len(airing_caption(1234.0, later, now)) <= CARD_WIDTH


def test_banner_is_cropped_to_exactly_the_cells_it_is_given():
    Image = pytest.importorskip("PIL.Image")
    for size in [(1900, 400), (460, 650), (300, 300)]:
        buf = io.BytesIO()
        Image.new("RGB", size, (200, 40, 90)).save(buf, "PNG")
        png = coverart.faded_banner(buf.getvalue(), 90, 17, (20, 18, 30))
        img = Image.open(io.BytesIO(png))
        assert img.size == (90 * 8, 17 * 16)
        # Left edge is the panel colour (faded), right side shows the picture.
        assert img.getpixel((0, 100)) == (20, 18, 30)
        r, _g, _b = img.getpixel((img.width - 1, 100))
        assert r > 120
        art = coverart.render_cover(png, cols=90)
        assert len(art.plain.splitlines()) == 17


def test_unreadable_banner_is_none():
    assert coverart.faded_banner(b"not an image", 40, 10, (0, 0, 0)) is None
