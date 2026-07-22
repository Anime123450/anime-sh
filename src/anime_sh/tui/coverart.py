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
# cover renders at double the resolution of plain ▀ half-blocks.
_QUADRANTS = " ▘▝▀▖▌▞▛▗▚▐▜▄▙▟█"


def _avg(pixels: list[tuple[int, int, int]]) -> tuple[int, int, int]:
    n = len(pixels)
    return (
        sum(p[0] for p in pixels) // n,
        sum(p[1] for p in pixels) // n,
        sum(p[2] for p in pixels) // n,
    )


def render_cover(data: bytes, cols: int = 24) -> Text | None:
    """Render image bytes to a quadrant-block :class:`rich.text.Text`, ``cols``
    wide, aspect-preserved. Each cell is a 2×2 pixel block split into a
    foreground/background pair by luminance — roughly double the sharpness of a
    half-block render. None if unrenderable (missing Pillow / bad image)."""
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
    img = img.resize((cols * 2, rows * 2), resample) if resample else img.resize((cols * 2, rows * 2))
    px = img.load()

    text = Text()
    for cy in range(rows):
        for cx in range(cols):
            quad = [
                px[cx * 2, cy * 2], px[cx * 2 + 1, cy * 2],
                px[cx * 2, cy * 2 + 1], px[cx * 2 + 1, cy * 2 + 1],
            ]
            lums = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in quad]
            thresh = sum(lums) / 4
            mask, fg, bg = 0, [], []
            for i, (p, l) in enumerate(zip(quad, lums)):
                if l >= thresh:
                    mask |= 1 << i
                    fg.append(p)
                else:
                    bg.append(p)
            fr, fgc, fb = _avg(fg or quad)
            br, bgc, bb = _avg(bg or fg or quad)
            text.append(_QUADRANTS[mask], style=f"rgb({fr},{fgc},{fb}) on rgb({br},{bgc},{bb})")
        if cy + 1 < rows:
            text.append("\n")
    return text
