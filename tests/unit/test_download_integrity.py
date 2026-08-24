"""A download that exits 0 is not necessarily a download you can watch.

Two failure modes reached `DownloadStatus.DONE` with a clean ffmpeg exit:

1. **A segment is dropped mid-transfer.** ffmpeg retries an HLS segment, gives
   up, skips it, and carries on. It says so at *warning* level; the exit code
   stays 0 and the command ran at `-loglevel error`, so nothing was printed at
   all. Reproduced by deleting one segment of a three-segment playlist: exit 0,
   no output, and a 4.04s file where 6.06s was expected.
2. **The host serves an error page instead of a stream**, and ffmpeg muxes a
   container with nothing playable in it.

A partial episode is worse than a failed one. A failure retries; a file sitting
in the downloads folder marked DONE is trusted, watched halfway, and only then
discovered to be truncated.
"""

from __future__ import annotations

import functools
import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from anime_sh.domain.models import Quality, Stream, StreamKind
from anime_sh.infra.downloader.ffmpeg import (
    _SEGMENT_LOST,
    DownloadError,
    FfmpegDownloader,
    build_ffmpeg_command,
    probe_media,
)

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")


def _make_segment(path: Path) -> None:
    """Two seconds of video-only MPEG-TS.

    mpeg2video with no audio track deliberately: every ffmpeg build has that
    encoder, whereas libx264 and a usable AAC encoder are both build options.
    This fixture has to work on the CI runners too.
    """
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10", "-t", "2",
         "-c:v", "mpeg2video", "-f", "mpegts", str(path)],
        check=True, capture_output=True, timeout=60,
    )


def _playlist(directory: Path, segments: list[str]) -> Path:
    body = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4"]
    for name in segments:
        body += ["#EXTINF:2.0,", name]
    body.append("#EXT-X-ENDLIST")
    path = directory / "play.m3u8"
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep pytest output readable
        pass


@pytest.fixture
def serve(tmp_path):
    """Serve tmp_path over real HTTP.

    The download path is not file-based: `build_ffmpeg_command` passes
    `-user_agent`, which ffmpeg only accepts for network protocols and rejects
    outright for a local path. Testing over HTTP also makes "the host dropped a
    segment" the honest thing it is in production — a 404 — rather than a
    missing local file.
    """
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        functools.partial(_QuietHandler, directory=str(tmp_path)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _stream(url: str) -> Stream:
    return Stream(url=url, kind=StreamKind.HLS, quality=Quality.UNKNOWN)


# --------------------------------------------------------------------------- #
# The guard on the mechanism itself
# --------------------------------------------------------------------------- #
def test_ffmpeg_is_asked_for_warnings_not_just_errors():
    """The whole detection scheme rests on this flag. ffmpeg reports a skipped
    segment at warning level and does not touch the exit code, so putting this
    back to "error" makes truncated downloads silent again — with every test
    below still passing, because they would never see the line."""
    cmd = build_ffmpeg_command("ffmpeg", _stream("http://x/play.m3u8"), Path("out.mp4"))
    assert "-loglevel" in cmd
    assert cmd[cmd.index("-loglevel") + 1] == "warning"


def test_the_pattern_matches_what_ffmpeg_actually_prints():
    """Copied verbatim from ffmpeg's output, not paraphrased."""
    assert _SEGMENT_LOST.search(
        "[in#0/hls @ 000001f645512200] Segment 1 of playlist 0 "
        "failed too many times, skipping"
    )


def test_a_retry_that_later_succeeds_is_not_treated_as_data_loss():
    """ffmpeg logs a failed attempt before retrying, and those retries routinely
    succeed. Counting that line would fail perfectly good downloads."""
    assert not _SEGMENT_LOST.search(
        "[in#0/hls @ 000001f645512200] Failed to open segment 1 of playlist 0"
    )


# --------------------------------------------------------------------------- #
# End to end, against real ffmpeg
# --------------------------------------------------------------------------- #
@needs_ffmpeg
async def test_an_intact_download_still_succeeds(tmp_path, serve):
    """The control. Without it, a verifier that rejects everything would look
    like a working fix."""
    for i in range(3):
        _make_segment(tmp_path / f"s{i}.ts")
    _playlist(tmp_path, [f"s{i}.ts" for i in range(3)])
    dest = tmp_path / "out.mp4"

    await FfmpegDownloader().download(_stream(f"{serve}/play.m3u8"), dest)

    assert dest.is_file()
    streams, duration = probe_media(shutil.which("ffprobe"), dest)
    assert streams >= 1
    assert duration == pytest.approx(6.0, abs=0.5)


@needs_ffmpeg
async def test_a_dropped_segment_fails_the_download_instead_of_truncating_it(
    tmp_path, serve
):
    """The original bug. ffmpeg exits 0 and writes a file two seconds short;
    anime-sh used to mark that DONE."""
    for i in range(3):
        _make_segment(tmp_path / f"s{i}.ts")
    _playlist(tmp_path, [f"s{i}.ts" for i in range(3)])
    (tmp_path / "s1.ts").unlink()  # the host 404s one segment mid-download
    dest = tmp_path / "out.mp4"

    with pytest.raises(DownloadError) as caught:
        await FfmpegDownloader().download(_stream(f"{serve}/play.m3u8"), dest)

    assert "dropped" in str(caught.value)
    assert not dest.exists(), (
        "the truncated file must be discarded — leaving it behind is how a "
        "partial episode gets mistaken for a complete one"
    )


@needs_ffmpeg
async def test_an_error_page_muxed_as_video_is_rejected(tmp_path, serve):
    """A host that answers with HTML instead of a stream.

    Honest note: this one already passed before the fix — ffmpeg cannot mux HTML
    and exits non-zero, so the existing exit-code check caught it. It is kept as
    a guard, not counted as a catch. It is also the case the original report
    described ("a 262-byte MP4 with zero streams, exit 0"), which did not
    reproduce: every streamless variant tried exits non-zero. The real
    clean-exit failure is the dropped segment above.
    """
    fake = tmp_path / "notavideo.ts"
    fake.write_bytes(b"<html><body>403 Forbidden</body></html>" * 40)
    _playlist(tmp_path, ["notavideo.ts"])
    dest = tmp_path / "out.mp4"

    with pytest.raises(DownloadError):
        await FfmpegDownloader().download(_stream(f"{serve}/play.m3u8"), dest)
    assert not dest.exists()


# --------------------------------------------------------------------------- #
# The probe helper
# --------------------------------------------------------------------------- #
@needs_ffmpeg
def test_probe_reports_zero_for_a_file_that_is_not_media(tmp_path):
    """Unreadable, unparseable and empty all have to answer "not playable"
    rather than raising, because the caller uses the answer to decide whether to
    keep the file."""
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"not an mp4")
    assert probe_media(shutil.which("ffprobe"), junk) == (0, 0.0)
    assert probe_media(shutil.which("ffprobe"), tmp_path / "missing.mp4") == (0, 0.0)


def test_probe_survives_a_missing_ffprobe(tmp_path):
    """ffprobe ships with ffmpeg, but a download must not explode if it is gone."""
    assert probe_media("definitely-not-a-real-binary", tmp_path / "x.mp4") == (0, 0.0)
