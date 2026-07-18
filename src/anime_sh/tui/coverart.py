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


def render_cover(data: bytes, cols: int = 22) -> Text | None:
    """Render image bytes to a half-block :class:`rich.text.Text`, ``cols`` wide,
    with rows chosen to preserve the poster aspect. None if unrenderable."""
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
    # Cells are ~twice as tall as wide, and each cell stacks 2 pixels, so a
    # column count of C maps to C px wide and needs C*(h/w) px tall → rows/2.
    px_h = max(2, int(cols * (h / w)))
    px_h += px_h % 2  # even, so every cell has a top and bottom pixel
    img = img.resize((cols, px_h))
    px = img.load()

    text = Text()
    for row in range(0, px_h, 2):
        for col in range(cols):
            tr, tg, tb = px[col, row]
            br, bg, bb = px[col, row + 1]
            text.append("▀", style=f"rgb({tr},{tg},{tb}) on rgb({br},{bg},{bb})")
        if row + 2 < px_h:
            text.append("\n")
    return text
