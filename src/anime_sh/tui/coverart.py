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


def render_cover(data: bytes, cols: int = 40) -> Text | None:
    """Render image bytes as truecolor half-blocks, ``cols`` wide,
    aspect-preserved. Each character cell is ``▀`` covering two vertically
    stacked pixels — the top pixel's colour as foreground, the bottom's as
    background — so **every pixel keeps its own full 24-bit colour** (no
    per-cell averaging, which is what muddied the old quadrant render). None if
    unrenderable (missing Pillow / bad image)."""
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
    # Each cell is 1px wide and 2px tall; terminal cells are ~twice as tall as
    # wide, so rows = aspect * cols / 2 keeps the poster's proportions on screen.
    rows = max(1, round((h / w) * cols / 2))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None)
    size = (cols, rows * 2)
    img = img.resize(size, resample) if resample else img.resize(size)
    px = img.load()

    text = Text()
    for cy in range(rows):
        for cx in range(cols):
            tr, tg, tb = px[cx, cy * 2]
            br, bg, bb = px[cx, cy * 2 + 1]
            text.append("▀", style=f"rgb({tr},{tg},{tb}) on rgb({br},{bg},{bb})")
        if cy + 1 < rows:
            text.append("\n")
    return text
