"""A download longer than a few minutes must not die on its own progress output.

ffmpeg terminates each `-stats` report with CR, not LF, and only the final one
ends with a newline. Measured against ffmpeg 8.1 with the exact flags
`build_ffmpeg_command` uses: 20 seconds of realtime work produced 4485 bytes of
stderr containing 38 CRs and **one** LF — ~118 bytes, twice a second.

`async for line in proc.stderr` splits on LF only, so all of that accumulates
into a single unterminated line and asyncio's StreamReader gives up once it
passes its 64 KiB default buffer. 65536 / 118 / 2 is about **4.5 minutes** of
downloading, which an episode on a slow connection passes without trying.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from anime_sh.domain.models import Stream, StreamKind
from anime_sh.infra.downloader import ffmpeg as mod
from anime_sh.infra.downloader.ffmpeg import DownloadError, FfmpegDownloader

# Enough CR-separated reports to pass the 64 KiB line buffer, shaped like the
# real thing. The trailing newline is ffmpeg's final report.
_STATS_SCRIPT = (
    "import sys\n"
    "line = 'frame=   18 fps=0.0 q=16.0 size=       0KiB "
    "time=00:00:00.72 bitrate=   0.5kbits/s speed=1.36x elapsed=0:00:00.52    '\n"
    "sys.stderr.write(line + '\\r')\n" * 1 +
    "for _ in range(700):\n"
    "    sys.stderr.write(line + '\\r')\n"
    "sys.stderr.write(line + '\\n')\n"
    "open(sys.argv[1], 'wb').write(b'not really media')\n"
)


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Swap the ffmpeg command for a python one that prints the same stderr."""
    monkeypatch.setattr(mod.shutil, "which", lambda _name: sys.executable)

    def build(_binary, _stream, dest):
        return [sys.executable, "-c", _STATS_SCRIPT, str(dest)]

    monkeypatch.setattr(mod, "build_ffmpeg_command", build)
    # ffprobe would be asked about a file that is not media; skip it so the test
    # is about reading stderr and nothing else.
    monkeypatch.setattr(mod, "_ffprobe_for", lambda _b: None)


async def test_a_long_download_survives_its_own_progress_output(fake_ffmpeg, tmp_path: Path):
    """The bug: this raised ValueError out of the stderr loop, and the `finally`
    then deleted the .part file — an hour of downloading thrown away."""
    dest = tmp_path / "Ep 01.mp4"
    await FfmpegDownloader().download(
        Stream(url="https://example.invalid/a.m3u8", kind=StreamKind.HLS), dest
    )
    assert dest.is_file()


async def test_the_progress_reports_reach_the_line_callback(fake_ffmpeg, tmp_path: Path):
    """CR-terminated reports are lines; nothing ever saw them before, which is
    why the overrun went unnoticed."""
    seen: list[str] = []
    await FfmpegDownloader().download(
        Stream(url="https://example.invalid/a.m3u8", kind=StreamKind.HLS),
        tmp_path / "Ep 02.mp4",
        on_line=seen.append,
    )
    assert len(seen) > 600, f"only {len(seen)} progress line(s) got through"
    assert all(s.startswith("frame=") for s in seen)


async def test_a_lost_segment_is_still_caught_among_the_progress_noise(
    monkeypatch, tmp_path: Path
):
    """The one line that matters is interleaved with CR reports, so splitting
    only on LF could hide it — and it is what stops a truncated episode being
    recorded as a complete one."""
    monkeypatch.setattr(mod.shutil, "which", lambda _name: sys.executable)
    monkeypatch.setattr(mod, "_ffprobe_for", lambda _b: None)
    script = (
        "import sys\n"
        "sys.stderr.write('frame=  1 speed=1x    \\r')\n"
        "sys.stderr.write('[in#0/hls @ 0x1] Segment 1 of playlist 0 failed too "
        "many times, skipping\\n')\n"
        "sys.stderr.write('frame=  2 speed=1x    \\r')\n"
        "open(sys.argv[1], 'wb').write(b'truncated')\n"
    )
    monkeypatch.setattr(
        mod, "build_ffmpeg_command",
        lambda _b, _s, dest: [sys.executable, "-c", script, str(dest)],
    )
    dest = tmp_path / "Ep 03.mp4"
    with pytest.raises(DownloadError, match="dropped 1 segment"):
        await FfmpegDownloader().download(
            Stream(url="https://example.invalid/a.m3u8", kind=StreamKind.HLS), dest
        )
    assert not dest.exists()
