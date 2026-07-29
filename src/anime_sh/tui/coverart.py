"""Terminal cover-art rendering — optional, always graceful.

Renders a cover image with unicode **block sextants** (U+1FB00 range): each
character cell packs a 2×3 pixel grid, coloured by the least-error two-colour
split of its six pixels. That's 50% more vertical detail than a 2×2 quadrant
cell and 3× a plain ``▀`` half-block, so posters read noticeably sharper while
still needing nothing but a truecolor terminal (no Sixel/kitty protocol).

For terminals that *do* support a graphics protocol (Sixel on Windows Terminal
≥ 1.22, kitty, iTerm2) a true bitmap is sharper still — see the README's cover-art
note — but that is opt-in; this path is the universal default.

Pillow is an optional dependency (the ``[tui]`` extra). Every entry point returns
``None`` rather than raising if Pillow is missing or an image can't be decoded, so
the detail screen simply omits the art and never breaks.
"""

from __future__ import annotations

from rich.text import Text


def _graphics_disabled() -> bool:
    """Escape hatch: set ``ANIME_SH_NO_GRAPHICS=1`` to force the unicode-block
    render (e.g. if a terminal mishandles Sixel)."""
    import os

    return bool(os.environ.get("ANIME_SH_NO_GRAPHICS"))


def prime_graphics() -> None:
    """Trigger textual-image's terminal-capability probe.

    It queries the terminal (sends an escape, reads the reply), which only works
    *before* Textual starts its own IO threads — so the CLI calls this once at
    launch. A no-op if disabled, textual-image isn't installed, or the probe
    fails."""
    if _graphics_disabled():
        return
    try:
        import textual_image.renderable  # noqa: F401  (import runs the probe)
    except Exception:
        pass


def graphics_protocol_active() -> bool:
    """True when a *pixel* graphics protocol (Sixel / kitty / iTerm) was detected,
    so a true-bitmap cover will render — sharp, unlike the unicode-block fallback.
    Relies on :func:`prime_graphics` having run first."""
    if _graphics_disabled():
        return False
    try:
        from textual_image.renderable import Image
        return any(p in (Image.__module__ or "") for p in ("sixel", "tgp", "iterm"))
    except Exception:
        return False


def graphics_cover_widget(data: bytes, width_cells: int):
    """A textual-image widget that renders the cover as a real bitmap at
    ``width_cells`` wide, or None if that isn't possible (caller falls back to
    the unicode-block render)."""
    try:
        import io

        from PIL import Image as PILImage
        from textual_image.widget import Image

        img = PILImage.open(io.BytesIO(data)).convert("RGB")
        widget = Image(img)
        widget.styles.width = width_cells
        widget.styles.height = "auto"
        return widget
    except Exception:
        return None


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


def _build_sextants() -> list[str]:
    """Glyph per 6-bit mask (bit0 top-left, bit1 top-right, bit2 mid-left,
    bit3 mid-right, bit4 bottom-left, bit5 bottom-right).

    Patterns that already have a dedicated block glyph reuse it (empty → space,
    left/right column → half blocks, full → █); the rest map into the contiguous
    ``BLOCK SEXTANT`` range starting at U+1FB00."""
    special = {0: " ", 21: "▌", 42: "▐", 63: "█"}
    table: list[str] = []
    offset = 0
    for mask in range(64):
        if mask in special:
            table.append(special[mask])
        else:
            table.append(chr(0x1FB00 + offset))
            offset += 1
    return table


_SEXTANTS = _build_sextants()


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


def _best_cell(cell: list[tuple[int, int, int]]) -> tuple[str, tuple, tuple]:
    """Pick the sextant glyph + foreground/background pair that best matches a
    2×3 pixel block. Trying every two-colour split (rather than a fixed
    brightness threshold) keeps smooth areas smooth and snaps real edges sharp."""
    best_err = None
    best = (" ", cell[0], cell[0])
    for mask in range(64):
        fg_px = [cell[i] for i in range(6) if mask & (1 << i)]
        bg_px = [cell[i] for i in range(6) if not mask & (1 << i)]
        fg = _mean(fg_px) if fg_px else None
        bg = _mean(bg_px) if bg_px else None
        err = (_sqerr(fg_px, fg) if fg else 0) + (_sqerr(bg_px, bg) if bg else 0)
        if best_err is None or err < best_err:
            best_err = err
            best = (_SEXTANTS[mask], fg or bg, bg or fg)
    return best


def render_cover(data: bytes, cols: int = 48) -> Text | None:
    """Render image bytes to a sextant-block :class:`rich.text.Text`, ``cols``
    wide, aspect-preserved. Each character cell is a 2×3 pixel block coloured by
    the least-error two-colour split of its six pixels. None if unrenderable
    (missing Pillow / bad image)."""
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
    # Terminal cells are ~twice as tall as wide; each cell is 2 wide × 3 tall px.
    # rows = aspect * cols / 2 keeps the poster's proportions on screen.
    rows = max(1, round((h / w) * cols / 2))
    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None)
    size = (cols * 2, rows * 3)
    img = img.resize(size, resample) if resample else img.resize(size)
    px = img.load()

    text = Text()
    for cy in range(rows):
        for cx in range(cols):
            x, y = cx * 2, cy * 3
            cell = [
                px[x, y], px[x + 1, y],
                px[x, y + 1], px[x + 1, y + 1],
                px[x, y + 2], px[x + 1, y + 2],
            ]
            glyph, fg, bg = _best_cell(cell)
            text.append(glyph, style=f"rgb({fg[0]},{fg[1]},{fg[2]}) on rgb({bg[0]},{bg[1]},{bg[2]})")
        if cy + 1 < rows:
            text.append("\n")
    return text
