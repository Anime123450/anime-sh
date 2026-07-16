"""ffmpeg-based downloader.

HLS streams are muxed to MP4 with stream copy (no re-encode), which is fast and
lossless; direct MP4s are copied the same way. The stream's Referer/UA are
forwarded so the CDN doesn't reject variant playlists or segments.

Reality check: some hosts deliberately obstruct downloads (extensionless
segments, cross-origin 302 redirects that strip the referer, one-shot tokens).
Those play in mpv but can't be pulled with a plain ffmpeg copy — downloads, like
providers, are best-effort and degrade with an honest error rather than
pretending. Well-behaved HLS/MP4 hosts download fine.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Callable

from ...domain.errors import AnimeShError
from ...domain.models import Stream, StreamKind


class DownloadError(AnimeShError):
    pass


DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) "
    "Gecko/20100101 Firefox/150.0"
)


def build_ffmpeg_command(binary: str, stream: Stream, dest: Path) -> list[str]:
    """Pure command builder — unit-tested without spawning ffmpeg.

    Uses the *propagating* protocol flags (``-user_agent`` / ``-referer``) rather
    than ``-headers`` so the referer reaches HLS variant playlists and segments,
    not just the master. ``-extension_picky 0`` allows the extensionless segment
    URLs many CDNs now use. (Some hosts additionally redirect segments
    cross-origin, which drops the referer and defeats direct download — see the
    module note.)
    """
    cmd = [binary, "-y", "-hide_banner", "-loglevel", "error", "-stats"]
    cmd += ["-user_agent", DEFAULT_UA]
    referer = stream.headers.get("Referer") or stream.headers.get("referer")
    if referer:
        cmd += ["-referer", referer]
    if stream.kind is StreamKind.HLS:
        cmd += ["-extension_picky", "0"]  # allow extensionless HLS segments
    cmd += ["-i", stream.url, "-c", "copy"]
    if stream.kind is StreamKind.HLS:
        # Remux ADTS AAC from MPEG-TS HLS into an MP4 container.
        cmd += ["-bsf:a", "aac_adtstoasc"]
    # External subtitle tracks are intentionally not muxed — their URLs are on
    # other hosts with their own auth and would fail the whole download.
    cmd += [str(dest)]
    return cmd


class FfmpegDownloader:
    def __init__(self, binary: str = "ffmpeg") -> None:
        self._binary = binary

    def available(self) -> bool:
        return shutil.which(self._binary) is not None

    async def download(
        self, stream: Stream, dest: Path, *,
        on_line: Callable[[str], None] | None = None,
    ) -> None:
        binary = shutil.which(self._binary)
        if binary is None:
            raise DownloadError(f"{self._binary} not found on PATH")
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = build_ffmpeg_command(binary, stream, dest)

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        assert proc.stderr is not None
        tail: list[str] = []
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            tail = (tail + [line])[-5:]
            if on_line is not None:
                on_line(line)
        await proc.wait()
        if proc.returncode != 0:
            raise DownloadError(
                f"ffmpeg exited {proc.returncode}: {' / '.join(tail) or 'no output'}"
            )
