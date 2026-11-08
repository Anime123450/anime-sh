"""The home screen re-rendered the hero cover on every arrow press (~400 ms at
24 columns with the old per-mask generator scans), so moving along a shelf
lagged. These pin the faster `_best_cell` to the old one's exact output and
check a repeat render is served from cache."""

import io
import random
import time

from anime_sh.tui import coverart
from anime_sh.tui.coverart import _SEXTANTS, _best_cell, render_cover


def _old_best_cell(cell):
    def mean(px):
        n = len(px)
        return (sum(p[0] for p in px) // n, sum(p[1] for p in px) // n,
                sum(p[2] for p in px) // n)

    def sqerr(px, c):
        return sum((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2
                   for p in px)

    best_err, best = None, (" ", cell[0], cell[0])
    for mask in range(64):
        fg_px = [cell[i] for i in range(6) if mask & (1 << i)]
        bg_px = [cell[i] for i in range(6) if not mask & (1 << i)]
        fg = mean(fg_px) if fg_px else None
        bg = mean(bg_px) if bg_px else None
        err = (sqerr(fg_px, fg) if fg else 0) + (sqerr(bg_px, bg) if bg else 0)
        if best_err is None or err < best_err:
            best_err, best = err, (_SEXTANTS[mask], fg or bg, bg or fg)
    return best


def test_fast_best_cell_matches_the_original_exactly():
    rng = random.Random(7)
    for _ in range(3000):
        cell = [tuple(rng.randrange(256) for _ in range(3)) for _ in range(6)]
        assert _best_cell(cell) == _old_best_cell(cell)
    flat = [(10, 10, 10)] * 6
    assert _best_cell(flat) == _old_best_cell(flat)


def _poster(seed=1):
    from PIL import Image
    rng = random.Random(seed)
    img = Image.new("RGB", (46, 65))
    img.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(46 * 65)])
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def test_repeat_render_is_cached_and_hero_size_is_fast():
    coverart._COVER_CACHE.clear()
    data = _poster()
    t = time.perf_counter()
    first = render_cover(data, cols=24)
    cold = time.perf_counter() - t
    t = time.perf_counter()
    again = render_cover(data, cols=24)
    warm = time.perf_counter() - t
    assert again is first or again.plain == first.plain
    assert warm < 0.005
    assert cold < 0.25, f"hero cover took {cold * 1000:.0f} ms"


def test_opaque_sixel_cover_keeps_its_cache_when_the_tint_changes():
    import pytest
    sx = pytest.importorskip("textual_image.widget.sixel")
    from PIL import Image

    assert coverart.pin_sixel_background()
    impl = sx._ImageSixelImpl

    class Fake:
        def __init__(self, image):
            self.image = image

    # Opaque: the colour behind it is irrelevant, so it must not vary.
    assert impl._get_background_rgba(Fake(Image.new("RGB", (2, 2)))) == coverart._PINNED_BACKGROUND
    # Transparent images still ask for the real background (falls through).
    rgba = Fake(Image.new("RGBA", (2, 2)))
    try:
        impl._get_background_rgba(rgba)
    except AttributeError:
        pass  # the original reads widget styles a bare fake doesn't have


def test_sixel_encodings_are_reused_for_identical_pixels():
    import pytest
    sx = pytest.importorskip("textual_image.widget.sixel")
    from PIL import Image

    coverart.pin_sixel_background()
    impl = sx._ImageSixelImpl
    img = Image.open(io.BytesIO(_poster(3))).convert("RGB")
    first = impl._image_to_sixels(None, img)
    t = time.perf_counter()
    again = impl._image_to_sixels(None, img.copy())
    assert again == first
    assert time.perf_counter() - t < 0.01
    other = Image.open(io.BytesIO(_poster(4))).convert("RGB")
    assert impl._image_to_sixels(None, other) != first
