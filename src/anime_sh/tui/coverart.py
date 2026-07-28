"""Terminal cover-art rendering — optional, always graceful.

Renders a cover image as unicode half-blocks: each character cell is two vertical
pixels via ``▀`` (upper half = foreground colour, lower half = background). Works
in any truecolor terminal, no Sixel/kitty protocol needed.

Pillow is an optional dependency (the ``[tui]`` extra). Every entry point returns
``None`` rather than raising if Pillow is missing or an image can't be decoded, so
the detail screen simply omits the art and never breaks.
"""

from __future__ import annotations

from rich.text import Text


async def fetch_cover(url: str) -> bytes | None:
    """Fetch cover bytes. Returns None on any failure (offline, 404, …)."""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code == 200 and resp.content:
                return resp.content
    except Exception:
        pass
    return None


# Quadrant blocks indexed by a 4-bit mask (bit0 top-left, bit1 top-right,
# bit2 bottom-left, bit3 bottom-right) — each cell packs a 2×2 pixel grid, so a
# cover renders at double the horizontal resolution of a plain ▀ half-block.
_QUADRANTS = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"


def _mean(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    n = len(pixels)
    return (
        sum(p[0] for p in pixels) // n,
        sum(p[1] for p in pixels) // n,
        sum(p[2] for p in pixels) // n,
    )


def _sqerr(pixels: list[tuple[int, int, int]], c: tuple[int, int, int]) -> int:
    return sum((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2
               for p in pixels)


def _best_cell(quad: list[tuple[int, int, int]]) -> tuple[str, tuple, tuple]:
    """Pick the quadrant glyph + foreground/background pair that best matches a
    2×2 pixel block. Trying every split (rather than a fixed brightness
    threshold) keeps smooth areas smooth and snaps real edges sharp — the
    difference between a crisp poster and a muddy one."""
    best_err = None
    best = (" ", quad[0], quad[0])
    for mask in range(16):
        fg_px = [quad[i] for i in range(4) if mask & (1 << i)]
        bg_px = [quad[i] for i in range(4) if not mask & (1 << i)]
        fg = _mean(fg_px) if fg_px else None
        bg = _mean(bg_px) if bg_px else None
        err = (_sqerr(fg_px, fg) if fg else 0) + (_sqerr(bg_px, bg) if bg else 0)
        if best_err is None or err < best_err:
            best_err = err
            best = (_QUADRANTS[mask], fg or bg, bg or fg)
    return best


def render_cover(data: bytes, cols: int = 48) -> Text | None:
    """Render image bytes to a quadrant-block :class:`rich.text.Text`, ``cols``
    wide, aspect-preserved. Each character cell is a 2×2 pixel block (double the
    horizontal detail of a half-block), coloured by the least-error two-colour
    split of its four pixels — sharp edges without the muddy averaging the old
    threshold split produced. None if unrenderable (missing Pillow / bad image)."""
    try:
        import io

        from PIL import Image
    except Exception:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None

    w, h = img.size
    if w == 0 or h == 0:
        return None
    # Terminal cells are ~twice as tall as wide; each cell is 2×2 px. Choosing
    # rows = aspect * cols / 2 keeps the poster's proportions on screen.
    rows = max(1, round((h / w) * cols / 2))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None)
    size = (cols * 2, rows * 2)
    img = img.resize(size, resample) if resample else img.resize(size)
    px = img.load()

    text = Text()
    for cy in range(rows):
        for cx in range(cols):
            quad = [
                px[cx * 2, cy * 2], px[cx * 2 + 1, cy * 2],
                px[cx * 2, cy * 2 + 1], px[cx * 2 + 1, cy * 2 + 1],
            ]
            glyph, fg, bg = _best_cell(quad)
            text.append(glyph, style=f"rgb({fg[0]},{fg[1]},{fg[2]}) on rgb({bg[0]},{bg[1]},{bg[2]})")
        if cy + 1 < rows:
            text.append("\n")
    return text
