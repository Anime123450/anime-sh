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
import contextlib
import json
import re
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
    # "warning", not "error": losing a segment mid-download is reported by
    # ffmpeg at warning level and *not* through the exit code, so at "error"
    # a download that silently dropped a third of the episode looked
    # identical to a perfect one. See _SEGMENT_LOST.
    cmd = [binary, "-y", "-hide_banner", "-loglevel", "warning", "-stats"]
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


# ffmpeg retries a failed HLS segment and only gives up after several attempts.
# The retry line ("Failed to open segment N") is noise — it is routinely followed
# by a success. *This* line is the one that means data was actually lost, and it
# does not affect the exit code:
#
#     [in#0/hls @ ...] Segment 1 of playlist 0 failed too many times, skipping
#
# Verified against ffmpeg by removing one segment of a three-segment playlist:
# exit code 0, no output at "error" level, and a 4.04s file where 6.06s was
# expected. anime-sh recorded that as a completed download.
_SEGMENT_LOST = re.compile(r"failed too many times, skipping", re.IGNORECASE)


def _ffprobe_for(ffmpeg_binary: str) -> str | None:
    """The ffprobe that ships beside this ffmpeg, else any on PATH, else None.

    Preferring the sibling matters when several ffmpeg builds are installed —
    probing with a mismatched build is how you get a spurious "unplayable".
    """
    candidate = Path(ffmpeg_binary)
    sibling = candidate.with_name("ffprobe" + candidate.suffix)
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe")


def probe_media(ffprobe_binary: str, path: Path) -> tuple[int, float]:
    """``(stream_count, duration_seconds)`` for a local file. ``(0, 0.0)`` when
    the file is unreadable, unparseable, or carries no usable header — which is
    itself the answer we want, since that file is not playable."""
    import subprocess

    try:
        out = subprocess.run(
            [ffprobe_binary, "-v", "error", "-show_entries",
             "format=duration,nb_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        fmt = json.loads(out.stdout).get("format", {})
        return int(fmt.get("nb_streams") or 0), float(fmt.get("duration") or 0.0)
    except Exception:
        return 0, 0.0


def _signed(code: int | None) -> int | None:
    """Windows reports a negative exit status as a 32-bit unsigned value, so
    a plain failure surfaced to the user as "ffmpeg exited 4294967291"."""
    if code is not None and code >= 2 ** 31:
        return code - 2 ** 32
    return code


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
        lost_segments = 0
        try:
            async for raw in proc.stderr:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                if _SEGMENT_LOST.search(line):
                    lost_segments += 1
                tail = (tail + [line])[-5:]
                if on_line is not None:
                    on_line(line)
            await proc.wait()
        finally:
            # Abandoning the download (quit, Ctrl-C, a cancelled worker) used to
            # leave ffmpeg running headless, still writing to the destination
            # long after anime-sh was gone. Make the child die with us.
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
        if proc.returncode != 0:
            raise DownloadError(
                f"ffmpeg exited {_signed(proc.returncode)}: "
                f"{' / '.join(tail) or 'no output'}"
            )

        # Exit code 0 is not evidence that the file is watchable. Two failure
        # modes reach this point with a clean exit, and both used to be recorded
        # as completed downloads.
        self._verify(binary, dest, lost_segments)

    def _verify(self, ffmpeg_binary: str, dest: Path, lost_segments: int) -> None:
        """Fail a download whose artifact is not actually playable.

        A partial episode is worse than a failed one: a failure retries, while a
        file sitting in the downloads folder marked DONE is trusted, watched
        halfway, and only then discovered to be truncated. So a bad artifact is
        deleted rather than left behind to be mistaken for a good one.
        """
        if lost_segments:
            _discard(dest)
            raise DownloadError(
                f"the stream dropped {lost_segments} segment(s) mid-download, so "
                f"the file would have been missing about "
                f"{lost_segments * 6}s of video — discarded it. "
                f"Try again; if it keeps happening the host is rate-limiting you."
            )

        ffprobe = _ffprobe_for(ffmpeg_binary)
        if ffprobe is None:
            # ffprobe ships with ffmpeg, so this is close to unreachable. Not
            # worth failing a download that may well be fine.
            return
        streams, duration = probe_media(ffprobe, dest)
        if streams < 1 or duration <= 0:
            _discard(dest)
            raise DownloadError(
                "ffmpeg reported success but wrote a file with no playable "
                f"video ({streams} stream(s), {duration:g}s) — discarded it. "
                "The host most likely returned an error page instead of the "
                "stream."
            )


def _discard(dest: Path) -> None:
    with contextlib.suppress(OSError):
        dest.unlink()
